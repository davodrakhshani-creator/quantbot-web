import os, time, threading, math
from datetime import datetime, timezone
import requests
from flask import Flask, jsonify, Response

app = Flask(__name__)

SYMBOL = "BTCUSDT"
INTERVALS = ["1", "5", "15", "60", "240"]
WEIGHTS = {"1":0.05,"5":0.15,"15":0.20,"60":0.25,"240":0.35}
API = "https://api.bybit.com/v5/market/kline"
TICKER_API = "https://api.bybit.com/v5/market/tickers"

state = {
    "running": True,
    "price": None,
    "signal": "NO_TRADE",
    "bull": 0.0,
    "bear": 0.0,
    "skeptic": 100.0,
    "agreement": 0.0,
    "equity": 10000.0,
    "day_start_equity": 10000.0,
    "position": None,
    "trades": [],
    "wins": 0,
    "losses": 0,
    "last_update": None,
    "last_error": None,
    "tf": {},
    "funding": None,
    "oi": None,
}
lock = threading.Lock()


def ema(values, span):
    if not values:
        return 0.0
    a = 2.0/(span+1.0)
    e = values[0]
    for v in values[1:]:
        e = a*v + (1-a)*e
    return e


def rsi(values, n=14):
    if len(values) < n+1:
        return 50.0
    gains = losses = 0.0
    for i in range(-n, 0):
        d = values[i]-values[i-1]
        if d >= 0: gains += d
        else: losses += -d
    ag = gains/n
    al = losses/n
    if al == 0: return 100.0
    rs = ag/al
    return 100 - 100/(1+rs)


def atr(rows, n=14):
    if len(rows) < n+1: return 0.0
    trs=[]
    for i in range(len(rows)-n, len(rows)):
        h,l,cprev = rows[i][1], rows[i][2], rows[i-1][3]
        trs.append(max(h-l, abs(h-cprev), abs(l-cprev)))
    return sum(trs)/len(trs)


def fetch_klines(interval):
    r = requests.get(API, params={"category":"linear","symbol":SYMBOL,"interval":interval,"limit":260}, timeout=12)
    r.raise_for_status()
    js = r.json()
    if js.get("retCode") != 0:
        raise RuntimeError(str(js))
    raw = js["result"]["list"]
    rows=[]
    for x in reversed(raw):
        rows.append((int(x[0]), float(x[2]), float(x[3]), float(x[4]), float(x[5])))
    return rows[:-1] if len(rows)>1 else rows


def fetch_ticker():
    r = requests.get(TICKER_API, params={"category":"linear","symbol":SYMBOL}, timeout=12)
    r.raise_for_status()
    js=r.json()
    d=js["result"]["list"][0]
    return float(d["markPrice"]), float(d.get("fundingRate") or 0), float(d.get("openInterest") or 0)


def analyze_tf(interval, rows):
    closes=[x[3] for x in rows]
    if len(closes)<220: return None
    e20,e50,e200=ema(closes[-220:],20),ema(closes[-220:],50),ema(closes[-220:],200)
    rv=rsi(closes,14)
    av=atr(rows,14)
    px=closes[-1]
    m3=(closes[-1]/closes[-4]-1)*100 if len(closes)>=4 else 0
    m12=(closes[-1]/closes[-13]-1)*100 if len(closes)>=13 else 0
    bull=bear=0.0
    if e20>e50>e200: bull+=40
    elif e20<e50<e200: bear+=40
    else:
        if e20>e50: bull+=18
        elif e20<e50: bear+=18
    if px>e20: bull+=10
    else: bear+=10
    if px>e50: bull+=10
    else: bear+=10
    if 52<=rv<=72: bull+=20
    elif 28<=rv<=48: bear+=20
    elif rv>72: bear+=6
    elif rv<28: bull+=6
    if m3>0 and m12>0: bull+=20
    elif m3<0 and m12<0: bear+=20
    direction = "BULL" if bull>bear else "BEAR" if bear>bull else "FLAT"
    return {"bull":min(100,bull),"bear":min(100,bear),"rsi":round(rv,1),"atr":av,"close":px,"dir":direction}


def decision():
    tf={}
    for iv in INTERVALS:
        a=analyze_tf(iv, fetch_klines(iv))
        if a: tf[iv]=a
    if len(tf)<5:
        return "NO_TRADE",0,0,100,0,tf,None,None,None
    bull=sum(tf[i]["bull"]*WEIGHTS[i] for i in INTERVALS)
    bear=sum(tf[i]["bear"]*WEIGHTS[i] for i in INTERVALS)
    side="LONG" if bull>bear else "SHORT"
    score=max(bull,bear); opp=min(bull,bear)
    aligned=sum(WEIGHTS[i] for i in INTERVALS if (side=="LONG" and tf[i]["bull"]>tf[i]["bear"]) or (side=="SHORT" and tf[i]["bear"]>tf[i]["bull"]))
    skeptic=0.0
    if score-opp<15: skeptic+=35
    if aligned<0.70: skeptic+=35
    px=tf["5"]["close"]
    av=tf["5"]["atr"]
    if av<=0: skeptic+=50
    action = side if score>=70 and (score-opp)>=15 and aligned>=0.70 and skeptic<=35 else "NO_TRADE"
    if action=="NO_TRADE": return action,score,opp,skeptic,aligned,tf,None,None,None
    dist=1.5*av
    if side=="LONG": sl,tp=px-dist,px+2*dist
    else: sl,tp=px+dist,px-2*dist
    return action,score,opp,skeptic,aligned,tf,px,sl,tp


def check_exit(price):
    p=state["position"]
    if not p: return
    reason=None
    if p["side"]=="LONG":
        if price<=p["sl"]: reason="STOP"
        elif price>=p["tp"]: reason="TAKE_PROFIT"
        pnl=(price-p["entry"])*p["qty"]
    else:
        if price>=p["sl"]: reason="STOP"
        elif price<=p["tp"]: reason="TAKE_PROFIT"
        pnl=(p["entry"]-price)*p["qty"]
    if not reason: return
    fee=(p["entry"]*p["qty"]+price*p["qty"])*0.0006
    net=pnl-fee
    state["equity"]+=net
    if net>=0: state["wins"]+=1
    else: state["losses"]+=1
    state["trades"].insert(0,{"side":p["side"],"entry":round(p["entry"],2),"exit":round(price,2),"pnl":round(net,2),"reason":reason,"time":datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")})
    state["trades"]=state["trades"][:30]
    state["position"]=None


def maybe_open(action, entry, sl, tp):
    if action not in ("LONG","SHORT") or state["position"] is not None: return
    risk_cash=state["equity"]*0.0025
    stop_dist=abs(entry-sl)
    if stop_dist<=0: return
    qty=min(risk_cash/stop_dist, state["equity"]/entry)
    if qty<=0: return
    state["position"]={"side":action,"entry":entry,"sl":sl,"tp":tp,"qty":qty,"opened":time.time()}


def worker():
    while True:
        try:
            with lock:
                running=state["running"]
            price,funding,oi=fetch_ticker()
            with lock:
                state["price"]=price; state["funding"]=funding; state["oi"]=oi
                check_exit(price)
            if running:
                action,score,opp,skeptic,agreement,tf,entry,sl,tp=decision()
                with lock:
                    state["signal"]=action
                    state["bull"]=round(score if action=="LONG" else opp if action=="SHORT" else max([v["bull"] for v in tf.values()] or [0]),1)
                    state["bear"]=round(opp if action=="LONG" else score if action=="SHORT" else max([v["bear"] for v in tf.values()] or [0]),1)
                    state["skeptic"]=round(skeptic,1)
                    state["agreement"]=round(agreement*100,1)
                    state["tf"]=tf
                    state["last_update"]=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    state["last_error"]=None
                    maybe_open(action,entry,sl,tp)
        except Exception as e:
            with lock:
                state["last_error"]=str(e)
                state["last_update"]=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        time.sleep(60)

threading.Thread(target=worker, daemon=True).start()

@app.get('/health')
def health():
    return jsonify({"ok":True})

@app.get('/api/state')
def api_state():
    with lock:
        s=dict(state)
    return jsonify(s)

@app.post('/api/toggle')
def toggle():
    with lock:
        state["running"] = not state["running"]
        v=state["running"]
    return jsonify({"running":v})

PAGE='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>QuantBot Paper</title><style>
:root{color-scheme:dark}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#0b0d12;color:#f4f7fb}.wrap{max-width:760px;margin:auto;padding:18px}.top{display:flex;justify-content:space-between;align-items:center}.muted{color:#8f98a8}.price{font-size:38px;font-weight:800;margin:10px 0}.card{background:#151922;border:1px solid #262c38;border-radius:18px;padding:16px;margin:12px 0}.signal{font-size:34px;font-weight:900}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.metric{background:#10141b;border-radius:14px;padding:13px}.metric b{font-size:22px;display:block;margin-top:4px}.btn{border:0;border-radius:14px;padding:13px 16px;font-weight:700;background:#fff;color:#111}.row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid #242a34}.row:last-child{border:0}.good{color:#4de08a}.bad{color:#ff6b7a}.warn{color:#ffd166}table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:8px 4px;text-align:left;border-bottom:1px solid #242a34}</style></head><body><div class="wrap">
<div class="top"><div><b>QuantBot</b><div class="muted">Paper Trading • BTCUSDT</div></div><button id="toggle" class="btn">Pause</button></div>
<div id="price" class="price">—</div>
<div class="card"><div class="muted">FINAL SIGNAL</div><div id="signal" class="signal">NO_TRADE</div><div id="updated" class="muted"></div></div>
<div class="grid"><div class="metric">Bull<b id="bull">—</b></div><div class="metric">Bear<b id="bear">—</b></div><div class="metric">Skeptic<b id="skeptic">—</b></div><div class="metric">Agreement<b id="agree">—</b></div></div>
<div class="card"><div class="row"><span>Paper equity</span><b id="equity">—</b></div><div class="row"><span>Funding</span><b id="funding">—</b></div><div class="row"><span>Open interest</span><b id="oi">—</b></div><div class="row"><span>Position</span><b id="pos">—</b></div></div>
<div class="card"><b>Timeframes</b><div id="tf"></div></div>
<div class="card"><b>Recent trades</b><div style="overflow:auto"><table><thead><tr><th>Side</th><th>Entry</th><th>Exit</th><th>PnL</th></tr></thead><tbody id="trades"></tbody></table></div></div>
<div id="err" class="muted"></div></div><script>
async function load(){try{const r=await fetch('/api/state');const s=await r.json();price.textContent=s.price?('$'+Number(s.price).toLocaleString()):'—';signal.textContent=s.signal;signal.className='signal '+(s.signal==='LONG'?'good':s.signal==='SHORT'?'bad':'warn');bull.textContent=s.bull;bear.textContent=s.bear;skeptic.textContent=s.skeptic;agree.textContent=s.agreement+'%';equity.textContent='$'+Number(s.equity).toFixed(2);funding.textContent=s.funding==null?'—':(Number(s.funding)*100).toFixed(4)+'%';oi.textContent=s.oi==null?'—':Number(s.oi).toLocaleString();updated.textContent=s.last_update||'';toggle.textContent=s.running?'Pause':'Start';pos.textContent=s.position?(s.position.side+' @ '+Number(s.position.entry).toFixed(0)):'None';let h='';for(const k of ['1','5','15','60','240']){const v=s.tf[k];if(v)h+=`<div class="row"><span>${k==='60'?'1H':k==='240'?'4H':k+'m'}</span><b class="${v.dir==='BULL'?'good':v.dir==='BEAR'?'bad':'warn'}">${v.dir} • RSI ${v.rsi}</b></div>`}tf.innerHTML=h;trades.innerHTML=(s.trades||[]).map(t=>`<tr><td>${t.side}</td><td>${t.entry}</td><td>${t.exit}</td><td class="${t.pnl>=0?'good':'bad'}">${t.pnl}</td></tr>`).join('');err.textContent=s.last_error?('Data error: '+s.last_error):''}catch(e){err.textContent='Connection error: '+e}}
toggle.addEventListener('click',async()=>{await fetch('/api/toggle',{method:'POST'});load()});load();setInterval(load,5000);
</script></body></html>'''

@app.get('/')
def home():
    return Response(PAGE, mimetype='text/html')

if __name__ == '__main__':
    port=int(os.environ.get('PORT','8080'))
    app.run(host='0.0.0.0', port=port)
