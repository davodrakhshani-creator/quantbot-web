"""Execution realism audit for frozen QuantBot v4 T2.
Signal at completed close t; rebalance at next day's open, not magically at close.
Public daily OHLC only; no live orders. Post-selection stress test.
"""
from __future__ import annotations
import json, math, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import research_v3_independent as v3
import research_v3_snapshot_validation as snap
import research_v4_tournament as v4

OUT=Path('data/v4_execution.json'); START='2019-01-01'


def get_oc(sym):
    start=int(pd.Timestamp(START,tz='UTC').timestamp()*1000); end=int(datetime.now(timezone.utc).timestamp()*1000); rows=[]; sess=requests.Session()
    while start<end:
        batch=None
        for url in v3.ENDPOINTS:
            try:
                r=sess.get(url,params=dict(symbol=sym,interval='1d',startTime=start,endTime=end,limit=1000),timeout=20); r.raise_for_status(); x=r.json()
                if isinstance(x,list): batch=x; break
            except Exception: pass
        if not batch: break
        rows.extend(batch); nxt=int(batch[-1][0])+86400000
        if nxt<=start: break
        start=nxt
        if len(batch)<1000: break
        time.sleep(.03)
    if not rows: raise RuntimeError('no OHLC')
    d=pd.DataFrame(rows); idx=pd.to_datetime(d[0].astype('int64'),unit='ms',utc=True).dt.floor('D')
    x=pd.DataFrame({'open':pd.to_numeric(d[1],errors='coerce').to_numpy(),'close':pd.to_numeric(d[4],errors='coerce').to_numpy()},index=pd.DatetimeIndex(idx))
    return x.loc[~x.index.duplicated(keep='last')].sort_index()


def load(symbols):
    os=[]; cs=[]; errors={}
    for s in symbols:
        try:
            d=get_oc(s)
            if d.close.notna().sum()>=500: os.append(d.open.rename(s)); cs.append(d.close.rename(s))
            else: errors[s]='insufficient rows'
        except Exception as e: errors[s]=repr(e)
    close=pd.concat(cs,axis=1).sort_index(); op=pd.concat(os,axis=1).reindex(close.index)
    close=close.loc[close['BTCUSDT'].notna()]; op=op.reindex(close.index)
    return op,close,errors


def exec_series(op,cl,pos,cost):
    overnight=op/cl.shift(1)-1; intraday=cl/op-1
    # At open d, target from close d-1 is executed. Overnight into d was carried by target from close d-2.
    q_new=pos.shift(1).fillna(0.0); q_old=pos.shift(2).fillna(0.0)
    vo=overnight.notna(); vi=intraday.notna(); q_old=q_old.where(vo,0.0); q_new=q_new.where(vi,0.0)
    gross=(q_old*overnight.fillna(0)).sum(axis=1)+(q_new*intraday.fillna(0)).sum(axis=1)
    turnover=(q_new-q_old).abs().sum(axis=1)
    net=gross-cost*turnover
    ex=q_new.abs().sum(axis=1)
    return net,turnover,ex


def diag(op,cl,pos,start,end,cost):
    net,to,ex=exec_series(op,cl,pos,cost); mask=net.index>=pd.Timestamp(start,tz='UTC')
    if end: mask &= net.index<=pd.Timestamp(end,tz='UTC')
    m=v3.metrics(net.loc[mask],to.loc[mask],cost); e=ex.loc[mask]; n=net.loc[mask]; a=e>1e-12
    m.update({'active_days':int(a.sum()),'active_day_pct':float(a.mean()*100) if len(a) else 0.0,'active_day_win_pct':float((n[a]>0).mean()*100) if a.any() else 0.0})
    return m


def run(symbols):
    op,cl,err=load(symbols); cut=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC'); op=op.loc[op.index<cut]; cl=cl.loc[cl.index<cut]; common=[c for c in cl if c in op]; op=op[common]; cl=cl[common]
    pos=v4.build(cl)['T2_upup']
    pre={str(bps):diag(op,cl,pos,'2021-01-01','2026-04-24',bps) for bps in (.0013,.0040,.0075)}
    post={str(bps):diag(op,cl,pos,'2026-04-25',None,bps) for bps in (.0013,.0040,.0075)}
    gate=pre['0.0013']['net_pct']>0 and pre['0.004']['net_pct']>0 and pre['0.0075']['net_pct']>0 and pre['0.0013']['pf']>=1.05 and pre['0.0013']['max_dd_pct']<=20
    return {'loaded_assets':common,'errors':err,'prelock':pre,'reused_postlock_diagnostic':post,'prelock_execution_gate':bool(gate)}


def main():
    res={'current':run(v3.UNIVERSE),'historical_snapshot':run(snap.SYMS)}
    passed=all(x['prelock_execution_gate'] for x in res.values())
    state={'version':'quantbot-v4-next-open-execution-audit','updated':datetime.now(timezone.utc).isoformat(),
           'execution':'signal computed at close t; old target earns close-to-next-open overnight; new target is entered at next open and earns open-to-close intraday; turnover cost charged at next open.',
           'results':res,'next_open_supports_t2':bool(passed),'interpretation':'T2_SURVIVES_NEXT_OPEN_EXECUTION_STRESS' if passed else 'T2_CLOSE_EXECUTION_DEPENDENCE_DETECTED',
           'live_money_authorized':False,
           'limitations':['Daily open/close cannot model within-open spread, partial fills or order-book impact.','Postlock is reused diagnostic evidence only.']}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps(state,indent=2))
if __name__=='__main__': main()
