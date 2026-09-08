"""QuantBot v3 independent diversified research.
Public market data only; no credentials or live orders.

Design:
- Pre-register a fixed family of diversified long/cash strategies.
- Use data through 2026-04-24 only for candidate selection/history diagnostics.
- Freeze the winning rule, then evaluate 2026-04-25 onward as a new chronological holdout.
- Include turnover costs and 40 bps/unit stress.
- Never promote when data/robustness gates fail.
"""
from __future__ import annotations
import json, math, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests

STATE = Path("data/v3_independent_state.json")
BASE_COST = 0.0013  # 13 bps per unit turnover
STRESS_COST = 0.0040  # 40 bps per unit turnover
START = "2019-01-01"
LOCK_DATE = pd.Timestamp("2026-04-24", tz="UTC")
HOLDOUT_START = pd.Timestamp("2026-04-25", tz="UTC")
TARGET_VOL = 0.12
UNIVERSE = [
    "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","ADAUSDT","SOLUSDT","DOGEUSDT",
    "TRXUSDT","LTCUSDT","LINKUSDT","BCHUSDT","ETCUSDT","XLMUSDT","DOTUSDT",
    "AVAXUSDT","UNIUSDT","ATOMUSDT","NEARUSDT","FILUSDT","AAVEUSDT"
]
ENDPOINTS = [
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
    "https://api1.binance.com/api/v3/klines",
]

def get_klines(symbol: str) -> pd.Series:
    start_ms = int(pd.Timestamp(START, tz="UTC").timestamp()*1000)
    end_ms = int(datetime.now(timezone.utc).timestamp()*1000)
    rows = []
    session = requests.Session()
    while start_ms < end_ms:
        params = dict(symbol=symbol, interval="1d", startTime=start_ms, endTime=end_ms, limit=1000)
        batch = None
        last_err = None
        for url in ENDPOINTS:
            try:
                r = session.get(url, params=params, timeout=20)
                r.raise_for_status()
                got = r.json()
                if isinstance(got, list):
                    batch = got
                    break
            except Exception as e:
                last_err = repr(e)
        if not batch:
            if rows:
                break
            raise RuntimeError(f"{symbol}: no data; last={last_err}")
        rows.extend(batch)
        nxt = int(batch[-1][0]) + 86400000
        if nxt <= start_ms:
            break
        start_ms = nxt
        if len(batch) < 1000:
            break
        time.sleep(0.05)
    if not rows:
        raise RuntimeError(f"{symbol}: empty")
    d = pd.DataFrame(rows)
    idx = pd.to_datetime(d[0].astype("int64"), unit="ms", utc=True).dt.floor("D")
    s = pd.Series(pd.to_numeric(d[4], errors="coerce").values, index=idx, name=symbol)
    return s[~s.index.duplicated(keep="last")].sort_index()

def load_prices():
    series, errors = [], {}
    for s in UNIVERSE:
        try:
            x = get_klines(s)
            if x.notna().sum() >= 500:
                series.append(x)
            else:
                errors[s] = f"only {x.notna().sum()} rows"
        except Exception as e:
            errors[s] = repr(e)
    if len(series) < 8:
        raise RuntimeError(f"Too few assets downloaded: {len(series)}; {errors}")
    px = pd.concat(series, axis=1).sort_index()
    px = px.loc[px["BTCUSDT"].notna()]
    return px, errors

def ann_vol(px, n=30):
    return px.pct_change(fill_method=None).rolling(n, min_periods=n).std()*math.sqrt(365.25)

def momentum(px, n):
    return px/px.shift(n)-1

def rebalance_mask(index, freq):
    if freq == "D":
        return pd.Series(True, index=index)
    if freq == "W":
        iso = pd.Series(index.isocalendar().week.to_numpy(), index=index)
        yr = pd.Series(index.year, index=index)
        key = yr.astype(str)+"-"+iso.astype(str)
        return key.ne(key.shift(1))
    if freq == "M":
        key = pd.Series(index.year*100+index.month, index=index)
        return key.ne(key.shift(1))
    raise ValueError(freq)

def normalize_selected(score, px, k=None, positive=True, vol_window=30, freq="W",
                       market_gate=None, breadth_gate=None):
    vol = ann_vol(px, vol_window).replace(0, np.nan)
    pos = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    for dt in px.index:
        sc = score.loc[dt].copy()
        ok = sc.notna() & px.loc[dt].notna() & vol.loc[dt].notna()
        if positive:
            ok &= sc > 0
        if market_gate is not None and not bool(market_gate.loc[dt]):
            ok[:] = False
        if breadth_gate is not None and not bool(breadth_gate.loc[dt]):
            ok[:] = False
        names = list(sc[ok].sort_values(ascending=False).index)
        if k is not None:
            names = names[:k]
        if names:
            inv = 1/vol.loc[dt, names].clip(lower=0.10)
            w = inv/inv.sum()
            avg_vol = float((w*vol.loc[dt,names]).sum())
            scalar = min(1.0, TARGET_VOL/max(avg_vol, 1e-9))
            pos.loc[dt, names] = w*scalar
    rb = rebalance_mask(px.index, freq)
    out = pos.where(rb, np.nan).ffill().fillna(0.0)
    return out

def equal_long_cash(signal, px, freq="W", market_gate=None):
    vol = ann_vol(px,30).replace(0,np.nan)
    pos = pd.DataFrame(0.0,index=px.index,columns=px.columns)
    for dt in px.index:
        ok = signal.loc[dt].fillna(False) & px.loc[dt].notna() & vol.loc[dt].notna()
        if market_gate is not None and not bool(market_gate.loc[dt]):
            ok[:] = False
        names = list(px.columns[ok])
        if names:
            inv = 1/vol.loc[dt,names].clip(lower=.10)
            w=inv/inv.sum()
            avg_vol=float((w*vol.loc[dt,names]).sum())
            scalar=min(1.0,TARGET_VOL/max(avg_vol,1e-9))
            pos.loc[dt,names]=w*scalar
    rb=rebalance_mask(px.index,freq)
    return pos.where(rb,np.nan).ffill().fillna(0.0)

def candidates(px):
    m20,m60,m90,m120,m180,m240 = [momentum(px,n) for n in (20,60,90,120,180,240)]
    score_fast = np.sign(m20)+np.sign(m60)+np.sign(m120)
    score_slow = np.sign(m60)+np.sign(m120)+np.sign(m240)
    raw_blend = 0.25*m20 + 0.35*m60 + 0.25*m120 + 0.15*m180
    btc_sma200 = px["BTCUSDT"].rolling(200).mean()
    btc_gate = px["BTCUSDT"] > btc_sma200
    breadth = (m120 > 0).sum(axis=1) / m120.notna().sum(axis=1).replace(0,np.nan)
    breadth_gate = breadth >= 0.50
    high252 = px.rolling(252,min_periods=180).max()
    closeness = px/high252 - 1

    out = {}
    out["A_fast_tsmom_all_weekly"] = normalize_selected(score_fast,px,k=None,positive=True,freq="W")
    out["B_slow_tsmom_all_weekly"] = normalize_selected(score_slow,px,k=None,positive=True,freq="W")
    out["C_dual90_180_top5_weekly"] = normalize_selected(0.5*m90+0.5*m180,px,k=5,positive=True,freq="W")
    out["D_blend_top5_weekly"] = normalize_selected(raw_blend,px,k=5,positive=True,freq="W")
    out["E_blend_top3_btc200_gate"] = normalize_selected(raw_blend,px,k=3,positive=True,freq="W",market_gate=btc_gate)
    out["F_120_top5_breadth_gate"] = normalize_selected(m120,px,k=5,positive=True,freq="W",breadth_gate=breadth_gate)
    out["G_180_top5_monthly"] = normalize_selected(m180,px,k=5,positive=True,freq="M")
    out["H_near_52w_high_top5"] = normalize_selected(closeness,px,k=5,positive=True,freq="W")
    sig = (m60>0)&(m120>0)&(m240>0)
    out["I_triple_trend_all_weekly"] = equal_long_cash(sig,px,freq="W")
    out["J_triple_trend_btc_gate"] = equal_long_cash(sig,px,freq="W",market_gate=btc_gate)
    out["K_ensemble_A_C_E"] = (out["A_fast_tsmom_all_weekly"]+out["C_dual90_180_top5_weekly"]+out["E_blend_top3_btc200_gate"])/3
    return out

def pnl_series(px, pos, cost):
    r = px.pct_change(fill_method=None).fillna(0.0)
    p = pos.shift(1).fillna(0.0)
    turnover = p.diff().abs().sum(axis=1).fillna(0.0)
    gross = (p*r).sum(axis=1)
    pnl = gross - cost*turnover
    return pnl, turnover, gross

def metrics(ret, turnover=None, cost=None):
    ret=pd.Series(ret).replace([np.inf,-np.inf],np.nan).fillna(0.0)
    if len(ret)==0:
        return dict(n_days=0,net_pct=0,ann_pct=0,sharpe=0,max_dd_pct=0,pf=0,
                    avg_daily_bps=0,positive_day_pct=0)
    eq=(1+ret).cumprod()
    dd=1-eq/eq.cummax()
    pos=ret[ret>0].sum(); neg=-ret[ret<0].sum()
    sd=ret.std(ddof=1)
    years=max(len(ret)/365.25,1/365.25)
    m=dict(
        n_days=int(len(ret)),
        net_pct=float((eq.iloc[-1]-1)*100),
        ann_pct=float((eq.iloc[-1]**(1/years)-1)*100) if eq.iloc[-1]>0 else -100.0,
        sharpe=float(ret.mean()/sd*math.sqrt(365.25)) if sd and sd>0 else 0.0,
        max_dd_pct=float(dd.max()*100),
        pf=float(pos/max(neg,1e-12)),
        avg_daily_bps=float(ret.mean()*10000),
        positive_day_pct=float((ret>0).mean()*100),
    )
    if turnover is not None:
        m["turnover_units"]=float(pd.Series(turnover).sum())
        if cost is not None:
            m["cost_pct"]=float(pd.Series(turnover).sum()*cost*100)
    return m

def slice_metrics(px,pos,start,end=None,cost=BASE_COST):
    pnl,to,gross=pnl_series(px,pos,cost)
    mask=pnl.index>=pd.Timestamp(start,tz="UTC")
    if end is not None:
        mask &= pnl.index<=pd.Timestamp(end,tz="UTC")
    return metrics(pnl.loc[mask],to.loc[mask],cost)

def annual_folds(px,pos,years,cost=BASE_COST):
    out=[]
    for y in years:
        a=f"{y}-01-01"; b=f"{y}-12-31"
        m=slice_metrics(px,pos,a,b,cost)
        m["year"]=y
        out.append(m)
    return out

def history_score(hist, folds, stress):
    posfold=sum(1 for x in folds if x["net_pct"]>0)
    fold_sharpes=[x["sharpe"] for x in folds]
    return (
        2.0*posfold
        + 2.0*hist["sharpe"]
        + 1.5*math.log(max(hist["pf"],0.01))
        + 0.02*stress["net_pct"]
        - 0.04*hist["max_dd_pct"]
        + 0.5*np.median(fold_sharpes)
    )

def historical_gate(hist, folds, stress):
    posfold=sum(1 for x in folds if x["net_pct"]>0)
    return (
        hist["net_pct"]>0
        and hist["sharpe"]>=0.70
        and hist["pf"]>=1.12
        and hist["max_dd_pct"]<=25.0
        and posfold>=4
        and np.mean([x["net_pct"] for x in folds])>0
        and stress["net_pct"]>0
    )

def final_holdout_gate(h, hstress):
    return (
        h["n_days"]>=90
        and h["net_pct"]>0
        and h["pf"]>=1.05
        and h["max_dd_pct"]<=15.0
        and hstress["net_pct"]>0
    )

def main():
    px, errors=load_prices()
    cands=candidates(px)
    locked_px=px.loc[px.index<=LOCK_DATE]
    results={}
    fold_years=[2021,2022,2023,2024,2025]
    for name,pos in cands.items():
        locked_pos=pos.loc[locked_px.index]
        hist=slice_metrics(locked_px,locked_pos,"2021-01-01","2026-04-24",BASE_COST)
        stress=slice_metrics(locked_px,locked_pos,"2021-01-01","2026-04-24",STRESS_COST)
        folds=annual_folds(locked_px,locked_pos,fold_years,BASE_COST)
        score=history_score(hist,folds,stress)
        results[name]=dict(history=hist,history_stress40=stress,folds=folds,
                           history_gate=bool(historical_gate(hist,folds,stress)),
                           selection_score=float(score))
    winner=max(results,key=lambda k:results[k]["selection_score"])
    frozen_pos=cands[winner]
    hold=slice_metrics(px,frozen_pos,"2026-04-25",None,BASE_COST)
    hold_stress=slice_metrics(px,frozen_pos,"2026-04-25",None,STRESS_COST)
    hold_pass=final_holdout_gate(hold,hold_stress)
    hist_pass=results[winner]["history_gate"]
    robust=bool(hist_pass and hold_pass)

    state={
        "version":"quantbot-v3-independent",
        "updated":datetime.now(timezone.utc).isoformat(),
        "status":"ROBUST_EDGE_FOUND" if robust else "CONTINUE_NO_PROMOTION",
        "research_protocol":{
            "selection_lock":"All candidate selection uses data <= 2026-04-24.",
            "new_holdout":"Winning rule frozen before 2026-04-25+ evaluation.",
            "execution":"signal at close t; position applies to next daily bar",
            "base_cost":"13 bps per unit turnover",
            "stress_cost":"40 bps per unit turnover",
            "target_vol":"12% annualized, no gross leverage above 1x",
            "candidate_count":len(cands),
            "method_note":"Candidates are public-method-inspired trend/momentum/risk-control variants; no private or secret strategy access."
        },
        "dataset":{
            "source":"Binance public market-data API (spot daily klines)",
            "requested_assets":UNIVERSE,
            "loaded_assets":list(px.columns),
            "asset_errors":errors,
            "start":str(px.index.min()),
            "end":str(px.index.max()),
            "rows":int(len(px)),
            "strict_holdout_start":"2026-04-25"
        },
        "predefined_gates":{
            "history":"Sharpe>=0.70, PF>=1.12, maxDD<=25%, >=4/5 positive annual folds, positive mean fold return, positive at 40bps/unit stress.",
            "new_holdout":"n>=90 days, net>0, PF>=1.05, maxDD<=15%, positive at 40bps/unit stress.",
            "promotion":"Both historical and new-holdout gates must pass."
        },
        "candidate_results":results,
        "selected_candidate":winner,
        "selected_history":results[winner],
        "strict_new_holdout":hold,
        "strict_new_holdout_stress40":hold_stress,
        "robust_candidate":robust,
        "research_gate":"PROMOTE_TO_PAPER_TRADING_ONLY" if robust else "DO_NOT_PROMOTE",
        "limitations":[
            "Current-liquid-asset universe creates survivorship/selection bias; delisted historical assets are not included.",
            "Spot-close bars do not model intraday slippage, spread spikes, exchange outages, or tax.",
            "Recent strict holdout is only the post-2026-04-24 period, so statistical power is limited.",
            "No claim is made about proprietary/secret systems; methods are public-domain trend/momentum inspirations.",
            "A robust pass, if any, supports paper trading first, not live-money deployment."
        ]
    }
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state,indent=2),encoding="utf-8")
    print(json.dumps({
        "loaded":len(px.columns),"rows":len(px),"winner":winner,
        "history":results[winner]["history"],"history_gate":hist_pass,
        "holdout":hold,"holdout_stress":hold_stress,"robust":robust
    },indent=2))

if __name__=="__main__":
    main()
