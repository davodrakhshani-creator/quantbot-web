import csv, json, math, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.kraken.com/0/public/"
STATE_PATH = Path("data/v13_state.json")
NUMERAI_PATH = Path("numerai_crypto_meta.csv")
HOLD_SECONDS = 120 * 60
FRICTION_BPS = 26.0
MAX_HISTORY = 300

def fetch_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent":"QuantBot-v1.3-GitHubActions/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def kraken(endpoint, **params):
    q = urllib.parse.urlencode(params)
    url = BASE + endpoint + (("?" + q) if q else "")
    j = fetch_json(url)
    if j.get("error"):
        raise RuntimeError("Kraken: " + "; ".join(j["error"]))
    return j["result"]

def first_key(d, exclude=("last",)):
    for k in d:
        if k not in exclude:
            return k
    raise KeyError("No data key")

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def ema(arr, n):
    if not arr:
        return 0.0
    a = 2/(n+1)
    e = arr[0]
    for x in arr[1:]:
        e = a*x + (1-a)*e
    return e

def rsi(arr, n=14):
    if len(arr) < n+1:
        return 50.0
    g = l = 0.0
    for i in range(len(arr)-n, len(arr)):
        d = arr[i] - arr[i-1]
        if d > 0:
            g += d
        else:
            l -= d
    if l == 0:
        return 100.0
    rs = g/l
    return 100 - 100/(1+rs)

def zscore(arr, n):
    if len(arr) < n:
        return 0.0
    a = arr[-n:]
    m = sum(a)/len(a)
    var = sum((x-m)**2 for x in a)/len(a)
    sd = math.sqrt(var) or 1.0
    return (a[-1]-m)/sd

def ret(arr, n):
    if len(arr) <= n or arr[-1-n] == 0:
        return 0.0
    return arr[-1]/arr[-1-n]-1

def breakout(arr, n):
    if len(arr) < n:
        return 0.0
    a = arr[-n:]
    lo, hi = min(a), max(a)
    if hi == lo:
        return 0.0
    return 2*((a[-1]-lo)/(hi-lo))-1

def load_numerai_rank():
    if not NUMERAI_PATH.exists():
        return None, None
    rows = list(csv.DictReader(NUMERAI_PATH.read_text(encoding="utf-8").splitlines()))
    btc = [r for r in rows if str(r.get("symbol","")).upper() in ("BTC","XBT")]
    if not btc:
        return None, None
    btc.sort(key=lambda r: r.get("date",""))
    r = btc[-1]
    try:
        return float(r["prediction"]), r.get("date")
    except Exception:
        return None, r.get("date")

def medium_score(btc15, eth15):
    if len(btc15) < 100 or len(eth15) < 20:
        return None
    last = btc15[-1]
    f = [
        clamp((ema(btc15[-80:],8)-ema(btc15[-80:],21))/(last*.002), -1, 1),
        clamp(ret(eth15,4)/.01, -1, 1),
        clamp((rsi(btc15,14)-50)/20, -1, 1),
        clamp(zscore(btc15,20)/2, -1, 1),
        clamp(ret(btc15,4)/.01, -1, 1),
        clamp(ret(btc15,1)/.005, -1, 1),
        clamp(breakout(btc15,20), -1, 1),
        clamp(zscore(btc15,96)/2, -1, 1),
        clamp(ret(eth15,16)/.03, -1, 1),
        clamp(ret(btc15,16)/.03, -1, 1),
    ]
    w = [-2,1.7,1.6,-1.4,-.9,-.7,.6,.4,.2,.2]
    den = sum(abs(x) for x in w)
    return sum(x*y for x,y in zip(f,w))/den

def micro_score(depth, trades, now):
    kd = first_key(depth)
    bb = [(float(x[0]), float(x[1])) for x in depth[kd].get("bids",[])[:10]]
    aa = [(float(x[0]), float(x[1])) for x in depth[kd].get("asks",[])[:10]]
    if not bb or not aa:
        return None
    bid, ask = bb[0][0], aa[0][0]
    mid = (bid+ask)/2
    bd = sum(x[1] for x in bb)
    ad = sum(x[1] for x in aa)
    ofi = (bd-ad)/(bd+ad) if bd+ad else 0.0
    kt = first_key(trades)
    recent = [t for t in trades[kt] if now - float(t[2]) <= 60]
    buy = sell = 0.0
    for t in recent:
        notional = float(t[0])*float(t[1])
        if t[3] == "b":
            buy += notional
        else:
            sell += notional
    flow = (buy-sell)/(buy+sell) if buy+sell else 0.0
    b1, a1 = bb[0][1], aa[0][1]
    mp = (ask*b1 + bid*a1)/(b1+a1) if b1+a1 else mid
    micro_bps = (mp-mid)/mid*10000 if mid else 0.0
    score = .42*clamp(ofi,-1,1) + .38*clamp(flow,-1,1) + .20*clamp(micro_bps/2,-1,1)
    spread_bps = (ask-bid)/mid*10000 if mid else 999.0
    return {"score":score,"book_imbalance":ofi,"aggressor_flow":flow,"microprice_bps":micro_bps,"spread_bps":spread_bps,"recent_trades":len(recent)}

def direction_from_score(x, pos=0.08, neg=-0.08):
    if x is None:
        return "WAIT"
    if x >= pos:
        return "BULLISH"
    if x <= neg:
        return "BEARISH"
    return "NEUTRAL"

def final_decision(med, micro, numerai):
    med_dir = direction_from_score(med, 0.08, -0.08)
    mic_dir = direction_from_score(micro["score"] if micro else None, 0.22, -0.22)
    if numerai is None:
        num_dir = "NEUTRAL"
    elif numerai >= 0.67:
        num_dir = "BULLISH"
    elif numerai <= 0.33:
        num_dir = "BEARISH"
    else:
        num_dir = "NEUTRAL"
    final = "NO TRADE"
    reason = "No clean medium+micro alignment."
    if med_dir == "BULLISH" and mic_dir == "BULLISH" and num_dir != "BEARISH":
        final = "LONG"; reason = "Medium and micro bullish; Numerai does not veto."
    elif med_dir == "BEARISH" and mic_dir == "BEARISH" and num_dir != "BULLISH":
        final = "SHORT"; reason = "Medium and micro bearish; Numerai does not veto."
    elif med_dir == "BEARISH" and mic_dir == "BEARISH" and num_dir == "BULLISH":
        reason = "SHORT vetoed by strong bullish Numerai prior."
    elif med_dir == "BULLISH" and mic_dir == "BULLISH" and num_dir == "BEARISH":
        reason = "LONG vetoed by strong bearish Numerai prior."
    dirs = [med_dir, mic_dir, num_dir]
    if final == "LONG":
        agreement = sum(1 for d in dirs if d == "BULLISH")
    elif final == "SHORT":
        agreement = sum(1 for d in dirs if d == "BEARISH")
    else:
        agreement = max(sum(1 for d in dirs if d == "BULLISH"), sum(1 for d in dirs if d == "BEARISH"))
    strength = 0.0
    if med is not None: strength += min(abs(med)/0.25,1.0)*0.45
    if micro: strength += min(abs(micro["score"])/0.5,1.0)*0.40
    if numerai is not None: strength += min(abs(numerai-0.5)/0.5,1.0)*0.15
    return final, med_dir, mic_dir, num_dir, agreement, round(100*strength), reason

def load_state():
    if STATE_PATH.exists():
        try: return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception: pass
    return {"version":"1.3-bg","candidate":{"direction":"NONE","count":0},"open_observation":None,"closed_results":[],"snapshots":[],"last_update":None,"metrics":{}}

def metrics(results):
    if not results:
        return {"closed":0,"win_rate":None,"avg_gross_bps":None,"avg_net_bps":None,"profit_factor":None}
    wins = [r for r in results if r["net_bps"] > 0]
    gp = sum(r["net_bps"] for r in results if r["net_bps"] > 0)
    gl = -sum(r["net_bps"] for r in results if r["net_bps"] < 0)
    return {"closed":len(results),"win_rate":round(100*len(wins)/len(results),2),"avg_gross_bps":round(sum(r["gross_bps"] for r in results)/len(results),2),"avg_net_bps":round(sum(r["net_bps"] for r in results)/len(results),2),"profit_factor":round(gp/gl,3) if gl else (99.0 if gp else 0.0)}

def utc_iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).isoformat().replace("+00:00","Z")

def main():
    now = time.time(); state = load_state()
    ticker = kraken("Ticker", pair="XBTUSD"); eth_ticker = kraken("Ticker", pair="ETHUSD")
    depth = kraken("Depth", pair="XBTUSD", count=25); trades = kraken("Trades", pair="XBTUSD")
    bo = kraken("OHLC", pair="XBTUSD", interval=15); eo = kraken("OHLC", pair="ETHUSD", interval=15)
    kt = first_key(ticker); ke = first_key(eth_ticker); kb = first_key(bo); kee = first_key(eo)
    btc_price = float(ticker[kt]["c"][0]); eth_price = float(eth_ticker[ke]["c"][0])
    btc15 = [float(x[4]) for x in bo[kb] if len(x)>4]; eth15 = [float(x[4]) for x in eo[kee] if len(x)>4]
    med = medium_score(btc15, eth15); micro = micro_score(depth, trades, now); num_rank, num_date = load_numerai_rank()
    final, med_dir, mic_dir, num_dir, agreement, confidence, reason = final_decision(med, micro, num_rank)
    open_obs = state.get("open_observation")
    if open_obs and now >= float(open_obs["opened_epoch"]) + HOLD_SECONDS:
        gross_bps = ((btc_price/float(open_obs["entry_price"]) - 1) if open_obs["side"]=="LONG" else (float(open_obs["entry_price"])/btc_price - 1))*10000
        net_bps = gross_bps - FRICTION_BPS
        state.setdefault("closed_results",[]).append({**open_obs,"closed_at":utc_iso(now),"exit_price":round(btc_price,2),"gross_bps":round(gross_bps,2),"net_bps":round(net_bps,2),"win_after_cost":net_bps>0})
        state["closed_results"] = state["closed_results"][-200:]; state["open_observation"] = None; state["candidate"]={"direction":"NONE","count":0}
    if state.get("open_observation") is None:
        cand = state.get("candidate") or {"direction":"NONE","count":0}
        if final in ("LONG","SHORT"):
            cand = {"direction":final,"count":int(cand.get("count",0))+1} if cand.get("direction")==final else {"direction":final,"count":1}
            if cand["count"] >= 2:
                state["open_observation"]={"side":final,"opened_at":utc_iso(now),"opened_epoch":int(now),"entry_price":round(btc_price,2),"medium_score":round(med,5) if med is not None else None,"micro_score":round(micro["score"],5) if micro else None,"numerai_rank":num_rank,"confidence":confidence,"agreement":agreement}
                cand={"direction":"NONE","count":0}
        else:
            cand={"direction":"NONE","count":0}
        state["candidate"]=cand
    snapshot={"at":utc_iso(now),"btc_price":round(btc_price,2),"eth_price":round(eth_price,2),"numerai_rank":num_rank,"numerai_date":num_date,"numerai_bias":num_dir,"medium_score":round(med,5) if med is not None else None,"medium_bias":med_dir,"micro_score":round(micro["score"],5) if micro else None,"micro_bias":mic_dir,"book_imbalance":round(micro["book_imbalance"],5) if micro else None,"aggressor_flow":round(micro["aggressor_flow"],5) if micro else None,"microprice_bps":round(micro["microprice_bps"],4) if micro else None,"spread_bps":round(micro["spread_bps"],4) if micro else None,"recent_trades":micro["recent_trades"] if micro else 0,"decision":final,"agreement":agreement,"confidence":confidence,"reason":reason}
    state["latest"]=snapshot; state.setdefault("snapshots",[]).append(snapshot); state["snapshots"]=state["snapshots"][-MAX_HISTORY:]; state["last_update"]=snapshot["at"]; state["metrics"]=metrics(state.get("closed_results",[]))
    m=state["metrics"]
    state["research_gate"]="PROMISING_PAPER_ONLY" if m["closed"]>=20 and m["avg_net_bps"] is not None and m["avg_net_bps"]>0 and m["profit_factor"]>=1.2 else ("FAILED_FORWARD_GATE" if m["closed"]>=20 else "COLLECTING")
    STATE_PATH.parent.mkdir(parents=True,exist_ok=True); STATE_PATH.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"decision":final,"btc":btc_price,"closed":state["metrics"]["closed"],"open":state.get("open_observation"),"gate":state["research_gate"]},ensure_ascii=False))

if __name__ == "__main__": main()
