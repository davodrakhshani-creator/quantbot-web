"""QuantBot v3 structural funding/basis carry research.

Public Binance spot + USD-M perpetual data only. No credentials and no live orders.
Economic convention is deliberately conservative: a fully allocated pair uses
+0.5 capital long spot and -0.5 capital short perpetual (combined gross 1.0x).
Positive Binance funding is income to the short-perpetual leg.

Candidate rules are fixed before inspecting their post-2026-04-24 carry results.
This block is distinct from the directional trend block, but the calendar holdout
is not claimed to be globally pristine because spot prices in that period were
already used by the prior trend audit.
"""
from __future__ import annotations
import io, json, math, time, zipfile
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import research_v3_independent as v3

OUT=Path('data/v3_carry_state.json')
SYMS=['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','LINKUSDT','LTCUSDT','BCHUSDT']
START='2020-01-01'
LOCK=pd.Timestamp('2026-04-24',tz='UTC')
BASE_COST=.0013
STRESS=.0040

def _get_json(path,params):
    urls=['https://fapi.binance.com'+path,'https://fapi1.binance.com'+path,'https://fapi2.binance.com'+path]
    last=None
    for u in urls:
        try:
            r=requests.get(u,params=params,timeout=20); r.raise_for_status(); x=r.json()
            if isinstance(x,list): return x
        except Exception as e: last=repr(e)
    raise RuntimeError(last or 'no futures response')

def perp_klines_api(sym):
    start=int(pd.Timestamp(START,tz='UTC').timestamp()*1000); end=int(datetime.now(timezone.utc).timestamp()*1000)
    rows=[]
    while start<end:
        x=_get_json('/fapi/v1/klines',dict(symbol=sym,interval='1d',startTime=start,endTime=end,limit=1500))
        if not x: break
        rows.extend(x); nxt=int(x[-1][0])+86400000
        if nxt<=start: break
        start=nxt
        if len(x)<1500: break
        time.sleep(.05)
    if not rows: raise RuntimeError('empty futures klines')
    d=pd.DataFrame(rows); idx=pd.to_datetime(d[0].astype('int64'),unit='ms',utc=True).dt.floor('D')
    return pd.Series(pd.to_numeric(d[4]).to_numpy(),index=pd.DatetimeIndex(idx),name=sym).loc[lambda s:~s.index.duplicated(keep='last')].sort_index()

def funding_api(sym):
    start=int(pd.Timestamp(START,tz='UTC').timestamp()*1000); end=int(datetime.now(timezone.utc).timestamp()*1000)
    rows=[]
    while start<end:
        x=_get_json('/fapi/v1/fundingRate',dict(symbol=sym,startTime=start,endTime=end,limit=1000))
        if not x: break
        rows.extend(x); nxt=int(x[-1]['fundingTime'])+1
        if nxt<=start: break
        start=nxt
        if len(x)<1000: break
        time.sleep(.05)
    if not rows: raise RuntimeError('empty funding')
    d=pd.DataFrame(rows)
    d['dt']=pd.to_datetime(d.fundingTime.astype('int64'),unit='ms',utc=True).dt.floor('D')
    d['rate']=pd.to_numeric(d.fundingRate,errors='coerce')
    return d.groupby('dt').rate.sum().sort_index().rename(sym)

def month_iter(start='2020-01-01'):
    a=pd.Timestamp(start); b=pd.Timestamp(datetime.now(timezone.utc).date()).replace(day=1)-pd.offsets.MonthBegin(1)
    cur=a.replace(day=1)
    while cur<=b:
        yield cur.year,cur.month; cur += pd.offsets.MonthBegin(1)

def archive_csv(url):
    r=requests.get(url,timeout=25)
    if r.status_code==404: return None
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name=z.namelist()[0]; raw=z.read(name)
    return pd.read_csv(io.BytesIO(raw))

def perp_klines_archive(sym):
    pieces=[]
    for y,m in month_iter(START):
        u=f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1d/{sym}-1d-{y:04d}-{m:02d}.zip'
        try:
            r=requests.get(u,timeout=20)
            if r.status_code==404: continue
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z: raw=z.read(z.namelist()[0])
            d=pd.read_csv(io.BytesIO(raw),header=None)
            if len(d) and not str(d.iloc[0,0]).isdigit(): d=d.iloc[1:]
            idx=pd.to_datetime(pd.to_numeric(d.iloc[:,0]),unit='ms',utc=True).dt.floor('D')
            pieces.append(pd.Series(pd.to_numeric(d.iloc[:,4]).to_numpy(),index=pd.DatetimeIndex(idx)))
        except Exception: continue
    if not pieces: raise RuntimeError('no archive futures klines')
    s=pd.concat(pieces).sort_index(); s=s[~s.index.duplicated(keep='last')]; s.name=sym; return s

def funding_archive(sym):
    pieces=[]
    for y,m in month_iter(START):
        u=f'https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{y:04d}-{m:02d}.zip'
        try:
            d=archive_csv(u)
            if d is None or d.empty: continue
            # Official archive currently uses calc_time, funding_interval_hours, last_funding_rate.
            cols={c.lower():c for c in d.columns}
            tc=cols.get('calc_time') or cols.get('fundingtime') or d.columns[0]
            rc=cols.get('last_funding_rate') or cols.get('fundingrate') or d.columns[-1]
            idx=pd.to_datetime(pd.to_numeric(d[tc]),unit='ms',utc=True).dt.floor('D')
            rate=pd.to_numeric(d[rc],errors='coerce')
            pieces.append(pd.Series(rate.values,index=idx))
        except Exception: continue
    if not pieces: raise RuntimeError('no archive funding')
    s=pd.concat(pieces).groupby(level=0).sum().sort_index(); s.name=sym; return s

def load():
    spots_all,spot_errors=v3.load_prices(); spots=spots_all.reindex(columns=SYMS)
    perps=[]; funds=[]; errors={}
    for sym in SYMS:
        try:
            try: p=perp_klines_api(sym); psrc='fapi'
            except Exception: p=perp_klines_archive(sym); psrc='vision_archive'
            try: f=funding_api(sym); fsrc='fapi'
            except Exception: f=funding_archive(sym); fsrc='vision_archive'
            perps.append(p); funds.append(f); errors[sym]={'perp_source':psrc,'fund_source':fsrc}
        except Exception as e: errors[sym]={'error':repr(e)}
    if len(perps)<5: raise RuntimeError(f'too few carry assets: {errors}')
    perp=pd.concat(perps,axis=1); fund=pd.concat(funds,axis=1)
    common=[c for c in perp.columns if c in spots.columns and c in fund.columns]
    return spots[common],perp[common],fund[common],errors

def weekly_mask(index):
    wk=pd.Series(index.isocalendar().year.to_numpy()*100+index.isocalendar().week.to_numpy(),index=index)
    return wk.ne(wk.shift())

def make_pos(score,eligible,k):
    pos=pd.DataFrame(0.0,index=score.index,columns=score.columns)
    for dt in score.index:
        ok=eligible.loc[dt].fillna(False)&score.loc[dt].notna()
        names=list(score.loc[dt,ok].sort_values(ascending=False).index[:k])
        if names: pos.loc[dt,names]=1/len(names)
    return pos.where(weekly_mask(score.index),np.nan).ffill().fillna(0.0)

def candidates(spot,perp,fund):
    idx=spot.index.union(perp.index).union(fund.index).sort_values()
    s=spot.reindex(idx); p=perp.reindex(idx); f=fund.reindex(idx).fillna(0.0)
    b=p/s-1
    t7=f.rolling(7,min_periods=5).sum(); t30=f.rolling(30,min_periods=20).sum()
    ann7=t7/7*365.25; ann30=t30/30*365.25
    out={}
    out['C1_7d_positive_top3']=make_pos(ann7,ann7>0,3)
    out['C2_30d_positive_top5']=make_pos(ann30,ann30>0,5)
    out['C3_30d_gt5pct_basispos_top3']=make_pos(ann30,(ann30>.05)&(b>0),3)
    out['C4_7d30d_positive_top5']=make_pos(ann30,(ann7>0)&(ann30>0),5)
    out['C5_30d_gt10pct_top5']=make_pos(ann30,ann30>.10,5)
    combo=ann30+12*b.clip(lower=-.02,upper=.02)
    out['C6_funding_plus_basis_top3']=make_pos(combo,(ann30>0)&(b>0),3)
    return out,s,p,f,b,ann30

def pnl(s,p,f,pos,cost):
    sr=s.pct_change(fill_method=None); pr=p.pct_change(fill_method=None)
    q=pos.shift(1).fillna(0.0)
    valid=sr.notna()&pr.notna()
    q=q.where(valid,0.0)
    market=(.5*q*sr.fillna(0)-.5*q*pr.fillna(0)).sum(axis=1)
    income=(.5*q*f.fillna(0)).sum(axis=1)
    to=q.diff().abs().sum(axis=1).fillna(0.0)
    net=market+income-cost*to
    ex=q.sum(axis=1)
    return net,to,ex,market,income

def diag(s,p,f,pos,start,end=None,cost=BASE_COST):
    net,to,ex,market,income=pnl(s,p,f,pos,cost)
    mask=net.index>=pd.Timestamp(start,tz='UTC')
    if end: mask &= net.index<=pd.Timestamp(end,tz='UTC')
    m=v3.metrics(net.loc[mask],to.loc[mask],cost)
    e=ex.loc[mask]; n=net.loc[mask]; active=e>1e-9
    m.update({'active_days':int(active.sum()),'active_day_pct':float(active.mean()*100) if len(active) else 0,
              'avg_pair_allocation':float(e.mean()) if len(e) else 0,
              'active_day_win_pct':float((n[active]>0).mean()*100) if active.any() else 0,
              'market_leg_sum_pct':float(market.loc[mask].sum()*100),
              'funding_income_sum_pct':float(income.loc[mask].sum()*100)})
    return m

def history_gate(h,folds,stress):
    pos=sum(x['net_pct']>0 and x['active_day_pct']>=10 for x in folds)
    return h['sharpe']>=1.0 and h['pf']>=1.15 and h['max_dd_pct']<=10 and pos>=4 and stress['net_pct']>0 and h['active_day_pct']>=20

def hold_gate(h,stress):
    return h['n_days']>=90 and h['net_pct']>0 and h['pf']>=1.10 and h['max_dd_pct']<=5 and stress['net_pct']>0 and h['active_day_pct']>=10

def main():
    spot,perp,fund,errors=load(); cands,s,p,f,b,ann30=candidates(spot,perp,fund)
    # selection data only through lock
    sel={}
    for name,pos in cands.items():
        h=diag(s,p,f,pos,'2021-01-01','2026-04-24',BASE_COST)
        stress=diag(s,p,f,pos,'2021-01-01','2026-04-24',STRESS)
        folds=[]
        for y in range(2021,2026):
            d=diag(s,p,f,pos,f'{y}-01-01',f'{y}-12-31',BASE_COST); d['year']=y; folds.append(d)
        meaningful=sum(x['net_pct']>0 and x['active_day_pct']>=10 for x in folds)
        score=2*meaningful+2*h['sharpe']+math.log(max(h['pf'],.01))-.08*h['max_dd_pct']+.02*stress['net_pct']
        sel[name]={'history':h,'stress40':stress,'annual_folds':folds,'history_gate':bool(history_gate(h,folds,stress)),'score':float(score)}
    winner=max(sel,key=lambda x:sel[x]['score'])
    pos=cands[winner]
    hold=diag(s,p,f,pos,'2026-04-25',None,BASE_COST)
    hold40=diag(s,p,f,pos,'2026-04-25',None,STRESS)
    robust=bool(sel[winner]['history_gate'] and hold_gate(hold,hold40))
    state={
      'version':'quantbot-v3-delta-neutral-carry','updated':datetime.now(timezone.utc).isoformat(),
      'method':'long spot + short USD-M perpetual; pair capital split 50/50; positive funding income to short; weekly rebalance; causal prior-day signal.',
      'public_method_context':'Structural crypto carry/funding research inspired only by public academic/exchange materials; no proprietary or secret strategy access.',
      'selection_lock':'2026-04-24','postlock_note':'Method-specific funding/basis data after the lock had not been used in prior v2 tests; calendar period is not claimed globally pristine because spot prices were viewed by the trend block.',
      'dataset':{'assets':list(s.columns),'start':str(s.index.min()),'end':str(s.index.max()),'sources':errors},
      'cost_convention':'13 bps per aggregate pair turnover base; 40 bps stress. Full pair allocation = +0.5 spot/-0.5 perp, combined gross 1x.',
      'pre_registered_candidates':list(cands.keys()),
      'candidate_results':sel,'selected':winner,'selected_history':sel[winner],
      'postlock':hold,'postlock_stress40':hold40,
      'robust_candidate':robust,'research_gate':'PROMOTE_TO_PAPER_TRADING_ONLY' if robust else 'DO_NOT_PROMOTE',
      'gates':{'history':'Sharpe>=1, PF>=1.15, DD<=10%, >=4/5 positive meaningful folds (>=10% active), stress positive, >=20% active.',
               'postlock':'n>=90, net>0, PF>=1.10, DD<=5%, stress positive, >=10% active.'},
      'limitations':['Perpetual funding and basis can change abruptly; historical carry is not guaranteed.','Daily closes approximate hedge PnL and omit intraday liquidation/margin path risk.','Exchange counterparty, collateral, tax, and operational risks are not modeled.','No interest yield on idle collateral is credited.','No leverage beyond combined 1x gross is assumed.']
    }
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'selected':winner,'history':sel[winner]['history'],'history_gate':sel[winner]['history_gate'],'postlock':hold,'postlock40':hold40,'robust':robust},indent=2))
if __name__=='__main__': main()
