from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path
import requests
import pandas as pd
import numpy as np

STATE=Path("data/v23b_last24h_replay_state.json")
BASE="https://api.bybit.com"
SYMBOL="BTCUSDT"

# Exact user-local window: 2026-09-08 00:00 to 2026-09-09 00:00 Asia/Tehran
START_UTC=pd.Timestamp("2026-09-07 20:30:00", tz="UTC")
END_UTC=pd.Timestamp("2026-09-08 20:30:00", tz="UTC")

# Conservative execution: next 1m open, 7.5 bps round-trip cost.
COST_BPS=7.5
STOP_BPS=7.0
TARGET_BPS=14.0
MAX_HOLD_MIN=15
START_EQUITY=100.0

def fetch_1m():
    rows=[]
    cur=int(START_UTC.timestamp()*1000)
    end=int(END_UTC.timestamp()*1000)
    while cur<end:
        r=requests.get(BASE+"/v5/market/kline",params={
            "category":"linear","symbol":SYMBOL,"interval":"1",
            "start":cur,"end":end,"limit":1000
        },timeout=30)
        r.raise_for_status()
        x=r.json()
        if x.get("retCode")!=0: raise RuntimeError(x)
        part=x["result"]["list"]
        if not part: break
        for z in part:
            ts=int(z[0])
            rows.append((ts,*map(float,z[1:7])))
        mx=max(int(z[0]) for z in part)
        if mx<=cur: break
        cur=mx+60_000
    df=pd.DataFrame(rows,columns=["ms","open","high","low","close","volume","turnover"])
    df=df.drop_duplicates("ms").sort_values("ms")
    df["ts"]=pd.to_datetime(df.ms,unit="ms",utc=True)
    df=df.set_index("ts")
    return df[(df.index>=START_UTC)&(df.index<END_UTC)]

def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def prepare(m):
    x=m.copy()
    x["ret3"]=(x.close/x.close.shift(3)-1)*1e4
    x["ret10"]=(x.close/x.close.shift(10)-1)*1e4
    x["range_bps"]=(x.high/x.low-1)*1e4
    x["body_bps"]=(x.close/x.open-1)*1e4
    x["vol_med60"]=x.turnover.rolling(60,min_periods=30).median()
    x["vol_ratio"]=x.turnover/x.vol_med60.replace(0,np.nan)
    x["atr15"]=((x.high-x.low)/x.close*1e4).rolling(15,min_periods=10).mean()

    # Completed 15m trend regime.
    q=x.resample("15min",label="right",closed="right").agg({"open":"first","high":"max","low":"min","close":"last","turnover":"sum"}).dropna()
    q["e20"]=ema(q.close,20); q["e50"]=ema(q.close,50)
    q["trend"]=np.where((q.close>q.e20)&(q.e20>q.e50),1,np.where((q.close<q.e20)&(q.e20<q.e50),-1,0))
    x["trend15"]=q.trend.reindex(x.index,method="ffill").fillna(0)

    # Previous completed 5m local levels.
    b=x.resample("5min",label="right",closed="right").agg({"high":"max","low":"min","close":"last","turnover":"sum"}).dropna()
    b["hi6"]=b.high.shift(1).rolling(6,min_periods=4).max()
    b["lo6"]=b.low.shift(1).rolling(6,min_periods=4).min()
    x["hi6"]=b.hi6.reindex(x.index,method="ffill")
    x["lo6"]=b.lo6.reindex(x.index,method="ffill")
    return x

def signals(x):
    out=pd.DataFrame(index=x.index); out["engine"]=None; out["side"]=0
    # Testa-style candle proxy: only active/turbulent tape.
    tape=(x.vol_ratio>=1.20)&(x.atr15>=x.atr15.rolling(120,min_periods=30).median())

    # Jun public-method-inspired proxy: distortion then snapback.
    jl=(x.ret10<-18)&(x.ret3>3.0)&(x.ret3.shift(1)<=3.0)&(x.vol_ratio>=1.3)&tape
    js=(x.ret10>18)&(x.ret3<-3.0)&(x.ret3.shift(1)>=-3.0)&(x.vol_ratio>=1.3)&tape
    out.loc[jl,"engine"]="jun"; out.loc[jl,"side"]=1
    out.loc[js,"engine"]="jun"; out.loc[js,"side"]=-1

    # Hansan public-method-inspired proxy: first breakout + acceleration.
    cross_hi=(x.close>x.hi6)&(x.close.shift(1)<=x.hi6.shift(1))
    cross_lo=(x.close<x.lo6)&(x.close.shift(1)>=x.lo6.shift(1))
    hl=cross_hi&(x.trend15>=0)&(x.body_bps>4)&(x.vol_ratio>=1.35)&tape
    hs=cross_lo&(x.trend15<=0)&(x.body_bps<-4)&(x.vol_ratio>=1.35)&tape
    m=(out.side==0)&hl; out.loc[m,"engine"]="hansan"; out.loc[m,"side"]=1
    m=(out.side==0)&hs; out.loc[m,"engine"]="hansan"; out.loc[m,"side"]=-1
    return out

def simulate(x,s):
    rows=[]; i=0; n=len(x); last=-999
    while i<n-2:
        side=int(s.side.iloc[i]); eng=s.engine.iloc[i]
        if side==0 or eng is None or i-last<5:
            i+=1; continue
        last=i
        ei=i+1
        entry=float(x.open.iloc[ei])
        exit_i=min(ei+MAX_HOLD_MIN,n-1)
        gross=side*(float(x.close.iloc[exit_i])/entry-1)*1e4
        reason="time"
        for j in range(ei, min(ei+MAX_HOLD_MIN,n-1)+1):
            hi=float(x.high.iloc[j]); lo=float(x.low.iloc[j])
            stop=(lo<=entry*(1-STOP_BPS/1e4)) if side==1 else (hi>=entry*(1+STOP_BPS/1e4))
            tgt=(hi>=entry*(1+TARGET_BPS/1e4)) if side==1 else (lo<=entry*(1-TARGET_BPS/1e4))
            # Pessimistic if both touched: stop first.
            if stop:
                gross=-STOP_BPS; exit_i=j; reason="stop"; break
            if tgt:
                gross=TARGET_BPS; exit_i=j; reason="target"; break
            # Japanese immediate invalidation: after 3 min, if not moving our way, exit.
            if j-ei>=3:
                cur=side*(float(x.close.iloc[j])/entry-1)*1e4
                if cur<1.0:
                    gross=cur; exit_i=j; reason="immediate_invalid"; break
        net=gross-COST_BPS
        rows.append({
            "signal_ts":str(x.index[i]),"engine":eng,"side":"LONG" if side==1 else "SHORT",
            "entry":entry,"gross_bps":gross,"net_bps":net,"reason":reason
        })
        i=exit_i+1
    return pd.DataFrame(rows)

def stats(t):
    if t.empty:
        return {"trades":0,"wins":0,"losses":0,"win_pct":None,"gross_mean_bps":None,"net_mean_bps":None,"pf":None,"ending_equity":START_EQUITY,"net_pct":0.0}
    a=t.net_bps.to_numpy()
    w=a[a>0]; l=a[a<0]
    pf=float(w.sum()/abs(l.sum())) if len(l) else (999.0 if len(w) else None)
    # 100% notional, simple additive per trade (no leverage)
    net_pct=float(a.sum()/100.0)
    return {
        "trades":int(len(t)),"wins":int((a>0).sum()),"losses":int((a<=0).sum()),
        "win_pct":float((a>0).mean()*100),
        "gross_mean_bps":float(t.gross_bps.mean()),
        "net_mean_bps":float(a.mean()),"pf":pf,
        "net_pct":net_pct,"ending_equity":START_EQUITY*(1+net_pct/100)
    }

def main():
    x=prepare(fetch_1m())
    s=signals(x)
    t=simulate(x,s)
    out={
        "version":"v23b-last24h-candle-replay",
        "purpose":"FAST_DIAGNOSTIC_REPLAY_NOT_ROBUSTNESS_PROOF",
        "symbol":SYMBOL,
        "window_tehran":"2026-09-08 00:00 -> 2026-09-09 00:00",
        "window_utc":[str(START_UTC),str(END_UTC)],
        "bars_1m":int(len(x)),
        "cost_bps_roundtrip":COST_BPS,
        "start_equity_usd":START_EQUITY,
        "overall":stats(t),
        "jun":stats(t[t.engine=="jun"]) if not t.empty else stats(pd.DataFrame()),
        "hansan":stats(t[t.engine=="hansan"]) if not t.empty else stats(pd.DataFrame()),
        "trades":t.to_dict("records"),
        "warning":"One 24h diagnostic replay on candle proxies. Not equivalent to true L2 forward paper and not evidence for live-money robustness."
    }
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(out,indent=2))
    print(json.dumps(out,indent=2))

if __name__=="__main__":
    main()
