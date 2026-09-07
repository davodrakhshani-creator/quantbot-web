import json, math, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PRODUCTS = ["BTC-USD", "ETH-USD"]
GRANULARITY = 300  # 5 minutes
DAYS = 60
FRICTION_BPS = 26.0
HORIZON_BARS_15M = 8  # 120 minutes
OUT = Path("data/fast_replay.json")
UA = "QuantBot-fast-replay/1.0"


def fetch_json(url, tries=6):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            err = e
            time.sleep(min(5, 0.6 * (2 ** i)))
    raise RuntimeError(f"fetch failed: {url} :: {err}")


def fetch_candles(product, days=DAYS):
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    rows = {}
    cursor = start
    # Coinbase Exchange public candles: max 300 bars/request.
    step = timedelta(seconds=GRANULARITY * 290)
    while cursor < end:
        chunk_end = min(end, cursor + step)
        q = urllib.parse.urlencode({
            "granularity": GRANULARITY,
            "start": cursor.isoformat().replace("+00:00", "Z"),
            "end": chunk_end.isoformat().replace("+00:00", "Z"),
        })
        url = f"https://api.exchange.coinbase.com/products/{product}/candles?{q}"
        data = fetch_json(url)
        if not isinstance(data, list):
            raise RuntimeError(f"unexpected Coinbase response for {product}: {data}")
        for x in data:
            if isinstance(x, list) and len(x) >= 6:
                ts = int(x[0])
                rows[ts] = {
                    "t": ts, "low": float(x[1]), "high": float(x[2]),
                    "open": float(x[3]), "close": float(x[4]), "volume": float(x[5])
                }
        cursor = chunk_end
        time.sleep(0.14)
    out = [rows[k] for k in sorted(rows)]
    return out


def aggregate_15m(rows5):
    groups = {}
    for r in rows5:
        k = (r["t"] // 900) * 900
        groups.setdefault(k, []).append(r)
    out = []
    for k in sorted(groups):
        g = sorted(groups[k], key=lambda x: x["t"])
        if len(g) < 2:
            continue
        out.append({
            "t": k, "open": g[0]["open"], "high": max(x["high"] for x in g),
            "low": min(x["low"] for x in g), "close": g[-1]["close"],
            "volume": sum(x["volume"] for x in g), "sub": g,
        })
    return out


def ema(vals, n):
    if not vals: return 0.0
    a = 2.0 / (n + 1.0)
    e = vals[0]
    for v in vals[1:]: e = a * v + (1 - a) * e
    return e


def rsi(vals, n=14):
    if len(vals) < n + 1: return 50.0
    g = l = 0.0
    for i in range(len(vals)-n, len(vals)):
        d = vals[i] - vals[i-1]
        if d > 0: g += d
        else: l -= d
    if l == 0: return 100.0
    rs = g / l
    return 100 - 100 / (1 + rs)


def zscore(vals, n):
    if len(vals) < n: return 0.0
    a = vals[-n:]
    m = sum(a) / n
    sd = math.sqrt(sum((x-m)**2 for x in a) / n) or 1.0
    return (a[-1] - m) / sd


def ret(vals, n):
    if len(vals) <= n or vals[-1-n] == 0: return 0.0
    return vals[-1] / vals[-1-n] - 1.0


def breakout(vals, n):
    if len(vals) < n: return 0.0
    a = vals[-n:]; lo = min(a); hi = max(a)
    return 0.0 if hi == lo else 2 * ((a[-1] - lo) / (hi - lo)) - 1


def clamp(x, a=-1.0, b=1.0): return max(a, min(b, x))


def medium_score(btc, eth):
    if len(btc) < 100 or len(eth) < 20: return None
    last = btc[-1]
    f = [
        clamp((ema(btc[-80:],8)-ema(btc[-80:],21))/(last*.002)),
        clamp(ret(eth,4)/.01), clamp((rsi(btc,14)-50)/20), clamp(zscore(btc,20)/2),
        clamp(ret(btc,4)/.01), clamp(ret(btc,1)/.005), clamp(breakout(btc,20)), clamp(zscore(btc,96)/2),
        clamp(ret(eth,16)/.03), clamp(ret(btc,16)/.03)
    ]
    w = [-2,1.7,1.6,-1.4,-.9,-.7,.6,.4,.2,.2]
    den = sum(abs(x) for x in w)
    return sum(x*y for x,y in zip(f,w)) / den


def micro_proxy(bar15, btc15, eth15, vol_hist):
    # Historical L2/trade aggressor data is not available here. This proxy uses
    # 5m candle body/range + signed volume + close location + cross-market move.
    sub = bar15["sub"]
    signed_vol = 0.0; tot_vol = 0.0; body_strengths = []
    for c in sub:
        rng = max(c["high"]-c["low"], c["close"]*1e-8)
        body = (c["close"]-c["open"]) / rng
        body_strengths.append(clamp(body))
        sgn = 1 if c["close"] > c["open"] else (-1 if c["close"] < c["open"] else 0)
        signed_vol += sgn * c["volume"]
        tot_vol += c["volume"]
    flow = signed_vol / tot_vol if tot_vol else 0.0
    last = sub[-1]
    rng = max(last["high"]-last["low"], last["close"]*1e-8)
    close_pos = clamp(2*((last["close"]-last["low"])/rng)-1)
    body = sum(body_strengths)/len(body_strengths) if body_strengths else 0.0
    bm = clamp(ret(btc15,1)/.004)
    em = clamp(ret(eth15,1)/.004)
    # volume surprise changes conviction, not direction
    v = bar15["volume"]
    if len(vol_hist) >= 24:
        a = vol_hist[-24:]; mv = sum(a)/len(a); sd = math.sqrt(sum((x-mv)**2 for x in a)/len(a)) or 1.0
        vz = clamp((v-mv)/sd/3)
    else: vz = 0.0
    directional_vol = vz * (1 if flow >= 0 else -1)
    s = .32*flow + .22*close_pos + .18*body + .12*bm + .10*em + .06*directional_vol
    return clamp(s)


def align(btc15, eth15):
    emap = {x["t"]:x for x in eth15}
    return [(b, emap[b["t"]]) for b in btc15 if b["t"] in emap]


def build_features(pairs):
    out=[]; bc=[]; ec=[]; vols=[]
    for b,e in pairs:
        bc.append(b["close"]); ec.append(e["close"])
        m = medium_score(bc,ec)
        x = micro_proxy(b,bc,ec,vols) if len(bc)>2 else 0.0
        vols.append(b["volume"])
        if m is not None:
            out.append({"t":b["t"],"price":b["close"],"m":m,"x":x})
    return out


def stats(trades):
    if not trades:
        return {"trades":0,"return_pct":0,"win_rate":None,"pf":None,"avg_net_bps":None,"max_dd_pct":0,"long":0,"short":0}
    eq=1.0; peak=1.0; maxdd=0.0; gp=gl=0.0; wins=0
    for t in trades:
        r=t["net_bps"]/10000.0; eq*=1+r; peak=max(peak,eq); maxdd=max(maxdd,(peak-eq)/peak)
        if t["net_bps"]>0: wins+=1; gp+=t["net_bps"]
        elif t["net_bps"]<0: gl-=t["net_bps"]
    return {
        "trades":len(trades),"return_pct":round((eq-1)*100,3),"win_rate":round(wins/len(trades)*100,1),
        "pf":round(gp/gl,2) if gl else (99.0 if gp else 0.0),
        "avg_net_bps":round(sum(t["net_bps"] for t in trades)/len(trades),2),"max_dd_pct":round(maxdd*100,3),
        "long":sum(t["side"]=="LONG" for t in trades),"short":sum(t["side"]=="SHORT" for t in trades)
    }


def simulate(feat, mthr, xthr, start=0, end=None, numerai_overlay=False):
    if end is None: end=len(feat)
    trades=[]; persist_dir=None; persist_n=0; i=max(start,0)
    while i < min(end, len(feat)-HORIZON_BARS_15M):
        f=feat[i]; sig=None
        if f["m"]>=mthr and f["x"]>=xthr: sig="LONG"
        elif f["m"]<=-mthr and f["x"]<=-xthr: sig="SHORT"
        # Current Numerai BTC rank is 99.5th percentile. This scenario is a
        # stress overlay only, NOT a valid historical replay of Numerai.
        if numerai_overlay and sig=="SHORT": sig=None
        if sig:
            if persist_dir==sig: persist_n+=1
            else: persist_dir=sig; persist_n=1
        else:
            persist_dir=None; persist_n=0
        if sig and persist_n>=2:
            entry=f["price"]; j=i+HORIZON_BARS_15M; exitp=feat[j]["price"]
            gross=(exitp/entry-1)*10000 if sig=="LONG" else (entry/exitp-1)*10000
            trades.append({"t":f["t"],"side":sig,"entry":round(entry,2),"exit":round(exitp,2),"gross_bps":round(gross,2),"net_bps":round(gross-FRICTION_BPS,2)})
            i=j; persist_dir=None; persist_n=0
        else:
            i+=1
    return trades


def choose(feat, start, end):
    candidates=[]
    for mt in (0.10,0.12,0.14,0.16):
        for xt in (0.14,0.18,0.22,0.26):
            tr=simulate(feat,mt,xt,start,end,False); s=stats(tr)
            # Conservative score: require activity and reward PF/avg net, penalize DD.
            activity=min(s["trades"],20)/20
            pf=min((s["pf"] or 0),3)
            avg=(s["avg_net_bps"] or -100)/100
            score=(pf-1)*1.5 + avg + s["return_pct"]/10 - s["max_dd_pct"]/10 + activity*.15
            if s["trades"]<6: score-=3
            candidates.append((score,mt,xt,s))
    candidates.sort(reverse=True,key=lambda z:z[0])
    return candidates[0], candidates[:8]


def main():
    btc5=fetch_candles("BTC-USD"); eth5=fetch_candles("ETH-USD")
    pairs=align(aggregate_15m(btc5),aggregate_15m(eth5)); feat=build_features(pairs)
    if len(feat)<1000: raise RuntimeError(f"not enough aligned features: {len(feat)}")
    split=int(len(feat)*0.70)
    best, top=choose(feat,0,split); _,mt,xt,train_stats=best
    oos=simulate(feat,mt,xt,split,len(feat),False); oos_stats=stats(oos)
    oos_num=simulate(feat,mt,xt,split,len(feat),True); oos_num_stats=stats(oos_num)
    # Three expanding-window walk-forward tests.
    folds=[]
    cuts=[0.55,0.65,0.75]
    tests=[(0.55,0.65),(0.65,0.75),(0.75,0.85)]
    for train_frac,(a,b) in zip(cuts,tests):
        te=int(len(feat)*train_frac); ts=int(len(feat)*a); ee=int(len(feat)*b)
        bb,_=choose(feat,0,te); _,fm,fx,ftrain=bb
        ft=simulate(feat,fm,fx,ts,ee,False); fs=stats(ft)
        folds.append({"m_threshold":fm,"x_threshold":fx,"train":ftrain,"test":fs})
    positive_folds=sum(1 for f in folds if f["test"]["trades"]>=3 and f["test"]["return_pct"]>0 and (f["test"]["pf"] or 0)>1)
    avg_fold=sum(f["test"]["return_pct"] for f in folds)/len(folds)
    bh=(feat[-1]["price"]/feat[split]["price"]-1)*100
    promising=(oos_stats["trades"]>=12 and oos_stats["return_pct"]>0 and (oos_stats["pf"] or 0)>=1.2 and (oos_stats["avg_net_bps"] or -1)>0 and positive_folds>=2 and avg_fold>0)
    payload={
        "version":"1.3-fast-proxy-replay","generated_at":datetime.now(timezone.utc).isoformat(),
        "scope":{"days":DAYS,"granularity":"5m source / 15m decision","aligned_feature_rows":len(feat),"friction_bps":FRICTION_BPS,"hold_minutes":120},
        "important_limit":"This is a rapid proxy replay, not a true v1.3 microstructure backtest. Historical Level-2 order book and aggressor-flow data are replaced by candle/volume proxies. Current Numerai rank is NOT used to select the core model.",
        "selected":{"m_threshold":mt,"micro_proxy_threshold":xt,"train":train_stats},
        "oos_core":oos_stats,
        "oos_core_last_trades":oos[-12:],
        "oos_current_numerai_stress_overlay":oos_num_stats,
        "numerai_overlay_warning":"Uses today's 99.5th-percentile BTC Numerai rank only as a short-veto stress scenario; this is not historically valid and is not used for model selection.",
        "oos_buy_hold_pct":round(bh,3),
        "walk_forward":folds,
        "walk_forward_positive":positive_folds,
        "walk_forward_avg_return_pct":round(avg_fold,3),
        "proxy_gate":"PROMISING_PROXY" if promising else "REJECT_PROXY",
        "next_action":"Continue true forward microstructure collection" if promising else "Do not promote; redesign thresholds/features before relying on forward collection"
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps({"gate":payload["proxy_gate"],"oos":oos_stats,"wf_positive":positive_folds,"wf_avg":avg_fold},indent=2))

if __name__=="__main__": main()
