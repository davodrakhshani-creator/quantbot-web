"""QuantBot v3 cross-venue perpetual funding-spread research.

Public data only: Binance USD-M official archives + Hyperliquid public Info API.
No credentials, no live orders, no proprietary strategy claims.

Economic convention:
- A fully allocated pair has 0.5 notional long on the lower-funding venue and
  0.5 notional short on the higher-funding venue (combined gross = 1x).
- Funding and mark-to-market of both perp legs are included.
- Weekly rebalancing; signal uses only prior completed daily data.
- Selection period ends 2025-12-31. 2026-01-01..2026-08-31 is a method-specific
  chronological holdout, fixed before those funding-spread outcomes are evaluated.
"""
from __future__ import annotations
import io, json, math, time, zipfile
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import research_v3_independent as v3

OUT=Path('data/v3_crossvenue_state.json')
COINS=['BTC','ETH','SOL']
START=pd.Timestamp('2023-01-01',tz='UTC')
TRAIN_END=pd.Timestamp('2025-12-31',tz='UTC')
HOLD_START=pd.Timestamp('2026-01-01',tz='UTC')
END=pd.Timestamp('2026-08-31',tz='UTC')
BASE_COST=.0013
STRESS_COST=.0040
HL='https://api.hyperliquid.xyz/info'

def months(a=START,b=END):
    cur=pd.Timestamp(a).tz_convert(None).replace(day=1)
    end=pd.Timestamp(b).tz_convert(None).replace(day=1)
    while cur<=end:
        yield cur.year,cur.month
        cur += pd.offsets.MonthBegin(1)

def zip_csv(url, header='infer'):
    r=requests.get(url,timeout=25)
    if r.status_code==404: return None
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw=z.read(z.namelist()[0])
    return pd.read_csv(io.BytesIO(raw),header=header)

def binance_perp_daily(coin):
    sym=coin+'USDT'; out=[]
    for y,m in months():
        u=f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1d/{sym}-1d-{y:04d}-{m:02d}.zip'
        try:
            d=zip_csv(u,header=None)
            if d is None or d.empty: continue
            ts=pd.to_numeric(d.iloc[:,0],errors='coerce'); d=d.loc[ts.notna()].copy(); ts=pd.to_numeric(d.iloc[:,0])
            idx=pd.to_datetime(ts.astype('int64'),unit='ms',utc=True).dt.floor('D')
            out.append(pd.Series(pd.to_numeric(d.iloc[:,4],errors='coerce').values,index=idx))
        except Exception: continue
    if not out: raise RuntimeError(f'no Binance perp {coin}')
    s=pd.concat(out).sort_index(); s=s[~s.index.duplicated(keep='last')]; s.name=coin
    return s.loc[(s.index>=START)&(s.index<=END)]

def binance_funding_daily(coin):
    sym=coin+'USDT'; out=[]
    for y,m in months():
        u=f'https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{y:04d}-{m:02d}.zip'
        try:
            d=zip_csv(u,header='infer')
            if d is None or d.empty: continue
            cols={str(c).lower():c for c in d.columns}
            tc=cols.get('calc_time') or cols.get('fundingtime') or d.columns[0]
            rc=cols.get('last_funding_rate') or cols.get('fundingrate') or d.columns[-1]
            ts=pd.to_numeric(d[tc],errors='coerce'); rate=pd.to_numeric(d[rc],errors='coerce')
            ok=ts.notna()&rate.notna(); idx=pd.to_datetime(ts[ok].astype('int64'),unit='ms',utc=True).dt.floor('D')
            out.append(pd.Series(rate[ok].values,index=idx))
        except Exception: continue
    if not out: raise RuntimeError(f'no Binance funding {coin}')
    s=pd.concat(out).groupby(level=0).sum().sort_index(); s.name=coin
    return s.loc[(s.index>=START)&(s.index<=END)]

def hl_post(body):
    for i in range(4):
        try:
            r=requests.post(HL,json=body,timeout=25)
            r.raise_for_status(); return r.json()
        except Exception:
            if i==3: raise
            time.sleep(.8*(i+1))

def hl_daily(coin):
    body={'type':'candleSnapshot','req':{'coin':coin,'interval':'1d','startTime':int(START.timestamp()*1000),'endTime':int((END+pd.Timedelta(days=1)).timestamp()*1000-1)}}
    x=hl_post(body)
    if not x: raise RuntimeError(f'no HL candles {coin}')
    d=pd.DataFrame(x); idx=pd.to_datetime(pd.to_numeric(d['t']),unit='ms',utc=True).dt.floor('D')
    s=pd.Series(pd.to_numeric(d['c'],errors='coerce').values,index=idx,name=coin)
    return s[~s.index.duplicated(keep='last')].sort_index().loc[lambda z:(z.index>=START)&(z.index<=END)]

def hl_funding_daily(coin):
    cur=int(START.timestamp()*1000); end=int((END+pd.Timedelta(days=1)).timestamp()*1000-1); rows=[]
    while cur<=end:
        x=hl_post({'type':'fundingHistory','coin':coin,'startTime':cur,'endTime':end})
        if not x: break
        rows.extend(x)
        last=max(int(z['time']) for z in x); nxt=last+1
        if nxt<=cur: break
        cur=nxt
        if len(x)<500: break
        time.sleep(.08)
    if not rows: raise RuntimeError(f'no HL funding {coin}')
    d=pd.DataFrame(rows); ts=pd.to_numeric(d['time'],errors='coerce'); rate=pd.to_numeric(d['fundingRate'],errors='coerce')
    ok=ts.notna()&rate.notna(); idx=pd.to_datetime(ts[ok].astype('int64'),unit='ms',utc=True).dt.floor('D')
    s=pd.Series(rate[ok].values,index=idx).groupby(level=0).sum().sort_index(); s.name=coin
    return s.loc[(s.index>=START)&(s.index<=END)]

def load():
    bp=[];bf=[];hp=[];hf=[];meta={}
    for c in COINS:
        try:
            bpx=binance_perp_daily(c); bfr=binance_funding_daily(c); hpx=hl_daily(c); hfr=hl_funding_daily(c)
            bp.append(bpx);bf.append(bfr);hp.append(hpx);hf.append(hfr)
            meta[c]={'binance_px_rows':int(len(bpx)),'binance_funding_days':int(len(bfr)),'hl_px_rows':int(len(hpx)),'hl_funding_days':int(len(hfr))}
        except Exception as e: meta[c]={'error':repr(e)}
    if len(bp)<2: raise RuntimeError(f'too few assets {meta}')
    return [pd.concat(x,axis=1) for x in (bp,bf,hp,hf)],meta

def weekly(index):
    iso=index.isocalendar(); key=pd.Series(iso.year.to_numpy()*100+iso.week.to_numpy(),index=index)
    return key.ne(key.shift())

def make_pos(spread_ann, threshold, topk, require_7_30=False, spread7=None):
    # signed pair weight: + means short HL / long Binance; - means reverse.
    target=pd.DataFrame(0.0,index=spread_ann.index,columns=spread_ann.columns)
    for dt in spread_ann.index:
        s=spread_ann.loc[dt].dropna()
        if require_7_30 and spread7 is not None:
            common=s.index.intersection(spread7.columns)
            s=s.loc[common]
            same=np.sign(s)==np.sign(spread7.loc[dt,s.index])
            s=s.loc[same.fillna(False)]
        s=s.loc[s.abs()>=threshold]
        names=list(s.abs().sort_values(ascending=False).index[:topk])
        if names:
            # equal pair allocations; signed by which venue has higher funding
            target.loc[dt,names]=np.sign(s[names].values)/len(names)
    return target.where(weekly(target.index),np.nan).ffill().fillna(0.0)

def candidates(bf,hf):
    idx=bf.index.union(hf.index).sort_values(); b=bf.reindex(idx).fillna(0); h=hf.reindex(idx).fillna(0)
    spread=h-b
    a7=spread.rolling(7,min_periods=5).sum()/7*365.25
    a30=spread.rolling(30,min_periods=20).sum()/30*365.25
    return {
      'X1_7d_top1_no_threshold':make_pos(a7,0.0,1),
      'X2_7d_top1_3pct':make_pos(a7,.03,1),
      'X3_7d_top2_5pct':make_pos(a7,.05,2),
      'X4_30d_top1_3pct':make_pos(a30,.03,1),
      'X5_30d_top2_5pct':make_pos(a30,.05,2),
      'X6_7d30d_same_top2_3pct':make_pos(a30,.03,2,True,a7),
    }, spread, a7, a30

def pnl(bp,bf,hp,hf,pos,cost):
    idx=bp.index.union(hp.index).union(bf.index).union(hf.index).sort_values()
    bp=bp.reindex(idx); hp=hp.reindex(idx); bf=bf.reindex(idx).fillna(0); hf=hf.reindex(idx).fillna(0); pos=pos.reindex(idx).ffill().fillna(0)
    br=bp.pct_change(fill_method=None); hr=hp.pct_change(fill_method=None)
    q=pos.shift(1).fillna(0.0)
    valid=br.notna()&hr.notna(); q=q.where(valid,0.0)
    # +q = long Binance perp 0.5*q and short Hyperliquid perp 0.5*q
    market=(.5*q*br.fillna(0)-.5*q*hr.fillna(0)).sum(axis=1)
    funding=(.5*q*(hf-bf)).sum(axis=1)
    turnover=q.diff().abs().sum(axis=1).fillna(0.0)
    net=market+funding-cost*turnover
    exposure=q.abs().sum(axis=1)
    return net,turnover,exposure,market,funding

def metrics_period(bp,bf,hp,hf,pos,start,end,cost):
    n,t,e,m,f=pnl(bp,bf,hp,hf,pos,cost)
    mask=(n.index>=pd.Timestamp(start,tz='UTC'))&(n.index<=pd.Timestamp(end,tz='UTC'))
    z=n.loc[mask]; mt=v3.metrics(z,t.loc[mask],cost); act=e.loc[mask]>1e-9
    mt.update({'active_days':int(act.sum()),'active_day_pct':float(act.mean()*100) if len(act) else 0,
               'active_day_win_pct':float((z[act]>0).mean()*100) if act.any() else 0,
               'market_leg_sum_pct':float(m.loc[mask].sum()*100),'funding_income_sum_pct':float(f.loc[mask].sum()*100)})
    return mt

def hist_gate(h,folds,stress):
    pos=sum(x['net_pct']>0 and x['active_day_pct']>=20 for x in folds)
    return h['net_pct']>0 and h['sharpe']>=.8 and h['pf']>=1.12 and h['max_dd_pct']<=8 and pos>=2 and stress['net_pct']>0

def hold_gate(h,stress):
    return h['n_days']>=180 and h['net_pct']>0 and h['pf']>=1.10 and h['max_dd_pct']<=5 and h['active_day_pct']>=20 and stress['net_pct']>0

def main():
    (bp,bf,hp,hf),meta=load(); cands,spread,a7,a30=candidates(bf,hf)
    results={}
    for name,pos in cands.items():
        h=metrics_period(bp,bf,hp,hf,pos,'2023-06-01','2025-12-31',BASE_COST)
        s=metrics_period(bp,bf,hp,hf,pos,'2023-06-01','2025-12-31',STRESS_COST)
        folds=[]
        for y in (2023,2024,2025):
            st='2023-06-01' if y==2023 else f'{y}-01-01'; en=f'{y}-12-31'
            z=metrics_period(bp,bf,hp,hf,pos,st,en,BASE_COST);z['year']=y;folds.append(z)
        score=2*sum(x['net_pct']>0 for x in folds)+2*h['sharpe']+math.log(max(h['pf'],.01))-.1*h['max_dd_pct']+.03*s['net_pct']
        results[name]={'history':h,'stress40':s,'folds':folds,'history_gate':bool(hist_gate(h,folds,s)),'selection_score':float(score)}
    winner=max(results,key=lambda k:results[k]['selection_score']); pos=cands[winner]
    hold=metrics_period(bp,bf,hp,hf,pos,'2026-01-01','2026-08-31',BASE_COST)
    hold40=metrics_period(bp,bf,hp,hf,pos,'2026-01-01','2026-08-31',STRESS_COST)
    robust=bool(results[winner]['history_gate'] and hold_gate(hold,hold40))
    state={'version':'quantbot-v3-crossvenue-funding-spread','updated':datetime.now(timezone.utc).isoformat(),
      'method':'Delta-neutral cross-venue perp pair: long lower-funding venue, short higher-funding venue; 0.5+0.5 notional, combined gross <=1x; weekly causal signal.',
      'public_only':'Inspired by public funding-arbitrage literature and exchange APIs only; no secret/private-system access.',
      'data':{'assets':list(bp.columns),'metadata':meta,'start':'2023-01-01','train_end':'2025-12-31','holdout':'2026-01-01..2026-08-31','completed_months_only':True},
      'costs':'13 bps per aggregate pair turnover base, 40 bps stress; no leverage.',
      'candidates':results,'selected':winner,'selected_history':results[winner],
      'method_specific_holdout':hold,'holdout_stress40':hold40,'robust_candidate':robust,
      'research_gate':'PROMOTE_TO_PAPER_TRADING_ONLY' if robust else 'DO_NOT_PROMOTE',
      'limitations':['Cross-venue collateral/transfer, outage, liquidation, settlement and counterparty risks not fully modeled.','Daily closes approximate mark-to-market; intraday basis spikes can be larger.','Only three large assets and two venues are tested.','2026 holdout is method-specific, not globally unseen market history.']}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'selected':winner,'history':results[winner]['history'],'history_gate':results[winner]['history_gate'],'holdout':hold,'holdout40':hold40,'robust':robust},indent=2))
if __name__=='__main__': main()
