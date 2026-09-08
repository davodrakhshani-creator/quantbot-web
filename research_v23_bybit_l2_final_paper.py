from __future__ import annotations
import json, math, time, threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import websocket

STATE = Path("data/v23_bybit_l2_final_paper_state.json")
WS_URL = "wss://stream.bybit.com/v5/public/linear"
SYMBOL = "BTCUSDT"
RUN_SECONDS = 180
NOTIONAL = 100.0

# Bybit VIP0 base fees, conservative paper assumptions.
MAKER_BPS = 2.0
TAKER_BPS = 5.5
SLIPPAGE_BPS = 0.5
QUEUE_MULT = 1.35
MAX_SPREAD_BPS = 1.2
MIN_DEPTH_USD = 250000.0

# Japanese public-method-inspired context, NOT private strategy code.
CONFIRM_BPS = 0.8
HARD_STOP_BPS = 7.0
TARGET_BPS = 12.0
MAX_HOLD_SEC = 90
MIN_SIGNAL_GAP = 45

book = {"b":{}, "a":{}}
trades = deque(maxlen=20000)
obs = deque(maxlen=2000)
lock = threading.Lock()
last_signal_ts = 0.0

def nowiso():
    return datetime.now(timezone.utc).isoformat()

def load():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {
        "version":"quantbot-v23-bybit-l2-final-paper",
        "purpose":"FORWARD_L2_PAPER_NO_LIVE",
        "venue":"BYBIT_USDT_PERPETUAL",
        "symbol":SYMBOL,
        "live_orders":False,
        "runs":0,
        "observations":0,
        "signals":{"jun":0,"hansan":0},
        "paper_fills":0,
        "paper_trades":[],
        "created_at":nowiso(),
        "assumptions":{
            "maker_bps":MAKER_BPS,"taker_bps":TAKER_BPS,"slippage_bps":SLIPPAGE_BPS,
            "queue_mult":QUEUE_MULT,"notional_usd":NOTIONAL
        }
    }

def save(st):
    st["updated_at"] = nowiso()
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2))

def apply_book(msg):
    d = msg["data"]
    if msg["type"] == "snapshot":
        book["b"] = {float(p):float(q) for p,q in d["b"]}
        book["a"] = {float(p):float(q) for p,q in d["a"]}
    else:
        for side,key in [("b","b"),("a","a")]:
            for p,q in d[key]:
                p=float(p); q=float(q)
                if q == 0: book[side].pop(p,None)
                else: book[side][p]=q

def book_metrics():
    if not book["b"] or not book["a"]:
        return None
    bids = sorted(book["b"].items(), reverse=True)[:10]
    asks = sorted(book["a"].items())[:10]
    bb,bq=bids[0]; ba,aq=asks[0]
    mid=(bb+ba)/2
    spread=(ba-bb)/mid*1e4
    b3=sum(q for _,q in bids[:3]); a3=sum(q for _,q in asks[:3])
    b10=sum(q for _,q in bids); a10=sum(q for _,q in asks)
    imb3=(b3-a3)/(b3+a3) if b3+a3 else 0
    imb10=(b10-a10)/(b10+a10) if b10+a10 else 0
    micro=(ba*bq+bb*aq)/(bq+aq) if bq+aq else mid
    micro_bps=(micro-mid)/mid*1e4
    bid_usd=sum(p*q for p,q in bids)
    ask_usd=sum(p*q for p,q in asks)
    return dict(bb=bb,ba=ba,mid=mid,spread_bps=spread,imb3=imb3,imb10=imb10,
                micro_bps=micro_bps,bid_usd=bid_usd,ask_usd=ask_usd,bq=bq,aq=aq)

def flow(sec):
    tcut=time.time()-sec
    xs=[x for x in trades if x["wall"]>=tcut]
    buy=sum(x["usd"] for x in xs if x["side"]=="Buy")
    sell=sum(x["usd"] for x in xs if x["side"]=="Sell")
    tot=buy+sell
    f=(buy-sell)/tot if tot else 0
    if len(xs)>=2:
        r=(xs[-1]["p"]/xs[0]["p"]-1)*1e4
    else: r=0
    return {"flow":f,"usd":tot,"n":len(xs),"ret_bps":r}

def hist_mid(seconds_ago):
    cutoff=time.time()-seconds_ago
    for r in reversed(obs):
        if r["wall"]<=cutoff:
            return r["mid"]
    return None

def testa_ok(m, side):
    if m["spread_bps"]<=0 or m["spread_bps"]>MAX_SPREAD_BPS: return False
    if min(m["bid_usd"],m["ask_usd"])<MIN_DEPTH_USD: return False
    if side==1 and not (m["imb10"]>0.10 and m["micro_bps"]>0.03): return False
    if side==-1 and not (m["imb10"]<-0.10 and m["micro_bps"]<-0.03): return False
    return True

def choose_signal(m):
    global last_signal_ts
    if time.time()-last_signal_ts < MIN_SIGNAL_GAP: return None
    f2,f5,f15=flow(2),flow(5),flow(15)
    p3,p10=hist_mid(3),hist_mid(10)
    if p3 is None or p10 is None: return None
    r3=(m["mid"]/p3-1)*1e4
    r10=(m["mid"]/p10-1)*1e4
    accel=f2["n"]/max(f15["n"]/7.5,1)

    # Jun public-method clone: distortion then first snapback, with L2 support.
    if f5["flow"] < -0.30 and r10 < -2.5 and r3 > 0.5 and testa_ok(m,1):
        last_signal_ts=time.time(); return ("jun",1)
    if f5["flow"] > 0.30 and r10 > 2.5 and r3 < -0.5 and testa_ok(m,-1):
        last_signal_ts=time.time(); return ("jun",-1)

    # Hansan public-method clone: accelerated breakout continuation, L2 confirms.
    if f2["flow"]>0.25 and f5["flow"]>0.15 and r3>0.7 and accel>1.15 and testa_ok(m,1):
        last_signal_ts=time.time(); return ("hansan",1)
    if f2["flow"]<-0.25 and f5["flow"]<-0.15 and r3<-0.7 and accel>1.15 and testa_ok(m,-1):
        last_signal_ts=time.time(); return ("hansan",-1)
    return None

def traded_through(side, px, since, need_qty):
    qty=0.0
    for t in trades:
        if t["wall"]<since: continue
        if side==1 and t["side"]=="Sell" and t["p"]<=px: qty+=t["q"]
        if side==-1 and t["side"]=="Buy" and t["p"]>=px: qty+=t["q"]
    return qty>=need_qty

def summarize(st):
    xs=st["paper_trades"]
    if not xs:
        st["summary"]={"closed":0,"net_usd_100":0.0,"avg_net_bps":None,"win_pct":None,"pf":None}
        return
    net=[x["net_bps"] for x in xs]
    w=[x for x in net if x>0]; l=[x for x in net if x<0]
    pf=sum(w)/abs(sum(l)) if l else (999.0 if w else None)
    st["summary"]={"closed":len(net),"net_usd_100":sum(net)/100,
                   "avg_net_bps":sum(net)/len(net),
                   "win_pct":100*len(w)/len(net),"pf":pf}

def paper_trade(st, engine, side, m):
    entry_px=m["bb"] if side==1 else m["ba"]
    queue_qty=(m["bq"] if side==1 else m["aq"])*QUEUE_MULT
    created=time.time()

    # Conservative maker fill: opposite taker volume must trade through our price and queue.
    while time.time()-created<20:
        time.sleep(0.25)
        if traded_through(side, entry_px, created, queue_qty):
            break
    else:
        return

    st["paper_fills"]+=1
    filled=time.time()
    best_fav=-1e9
    reason="time"
    gross=0.0
    exit_px=None
    while time.time()-filled<MAX_HOLD_SEC:
        time.sleep(0.25)
        with lock:
            mm=book_metrics()
        if not mm: continue
        mark=mm["bb"] if side==1 else mm["ba"]
        cur=side*(mark/entry_px-1)*1e4
        best_fav=max(best_fav,cur)
        if cur<=-HARD_STOP_BPS:
            gross=cur; exit_px=mark; reason="stop_taker"; break
        if cur>=TARGET_BPS:
            # optimistic limit target is NOT assumed filled; require touch-through queue.
            tgt=entry_px*(1+side*TARGET_BPS/1e4)
            q=(mm["aq"] if side==1 else mm["bq"])*QUEUE_MULT
            if traded_through(-side, tgt, filled, q):
                gross=TARGET_BPS; exit_px=tgt; reason="target_maker"; break
    if exit_px is None:
        with lock:
            mm=book_metrics()
        if not mm: return
        exit_px=mm["bb"] if side==1 else mm["ba"]
        gross=side*(exit_px/entry_px-1)*1e4
        reason="time_taker"

    exit_cost=MAKER_BPS if reason=="target_maker" else TAKER_BPS+SLIPPAGE_BPS
    net=gross-MAKER_BPS-exit_cost
    st["paper_trades"].append({
        "engine":engine,"side":side,"entry_ts":nowiso(),"entry_px":entry_px,
        "exit_px":exit_px,"gross_bps":gross,"net_bps":net,
        "pnl_usd_100":NOTIONAL*net/1e4,"reason":reason
    })
    st["paper_trades"]=st["paper_trades"][-2000:]

def on_message(ws,msg):
    try:
        x=json.loads(msg)
    except Exception:
        return
    topic=x.get("topic","")
    with lock:
        if topic.startswith("orderbook.50."):
            apply_book(x)
        elif topic.startswith("publicTrade."):
            for t in x.get("data",[]):
                p=float(t["p"]); q=float(t["v"])
                trades.append({"wall":time.time(),"p":p,"q":q,"usd":p*q,"side":t["S"]})

def on_open(ws):
    ws.send(json.dumps({"op":"subscribe","args":[f"orderbook.50.{SYMBOL}",f"publicTrade.{SYMBOL}"]}))

def main():
    st=load(); st["runs"]=int(st.get("runs",0))+1
    ws=websocket.WebSocketApp(WS_URL,on_open=on_open,on_message=on_message)
    th=threading.Thread(target=ws.run_forever,kwargs={"ping_interval":20,"ping_timeout":10},daemon=True)
    th.start()
    start=time.time()
    while time.time()-start<RUN_SECONDS:
        time.sleep(0.5)
        with lock:
            m=book_metrics()
            if m:
                obs.append({**m,"wall":time.time()})
        if not m: continue
        st["observations"]=int(st.get("observations",0))+1
        sig=choose_signal(m)
        if sig:
            eng,side=sig
            st["signals"][eng]=int(st["signals"].get(eng,0))+1
            paper_trade(st,eng,side,m)
    ws.close()
    summarize(st)
    save(st)
    print(json.dumps(st,indent=2))

if __name__=="__main__":
    main()
