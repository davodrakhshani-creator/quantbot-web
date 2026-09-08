from __future__ import annotations

import io, json, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import research_v11_microburst as v11

SYMBOL='BTCUSDT'
START=pd.Timestamp('2025-01-01',tz='UTC')
FIT_END=pd.Timestamp('2025-12-31 23:59:59',tz='UTC')
VAL_START=pd.Timestamp('2026-01-01',tz='UTC')
VAL_END=pd.Timestamp('2026-04-24 23:59:59',tz='UTC')
HOLDOUT_START=pd.Timestamp('2026-04-25',tz='UTC')
END=pd.Timestamp('2026-08-31 23:59:59',tz='UTC')
BASE='https://data.binance.vision/data/futures/um/monthly/aggTrades'
STATE=Path('data/v15_microstructure_edge_screen_state.json')
COSTS=(7.0,12.0,20.0)  # one-way bps
HORIZONS=(1,3,6)  # 5m, 15m, 30m after next-bar entry


def months(a,b):
    cur=pd.Timestamp(a.year,a.month,1,tz='UTC'); last=pd.Timestamp(b.year,b.month,1,tz='UTC')
    while cur<=last:
        yield cur; cur += pd.offsets.MonthBegin(1)


def fetch_month_10s(m):
    ym=m.strftime('%Y-%m')
    url=f'{BASE}/{SYMBOL}/{SYMBOL}-aggTrades-{ym}.zip'
    r=requests.get(url,timeout=120)
    if r.status_code==404: return pd.DataFrame()
    r.raise_for_status(); parts=[]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name=z.namelist()[0]
        for raw in pd.read_csv(z.open(name),header=None,chunksize=400000,low_memory=False):
            if raw.shape[1]<7: continue
            raw=raw.iloc[:,:7].copy(); raw.columns=['id','price','qty','first','last','time','buyer_maker']
            for c in ['price','qty','time']: raw[c]=pd.to_numeric(raw[c],errors='coerce')
            raw=raw.dropna(subset=['price','qty','time'])
            if raw.empty: continue
            raw['ts']=pd.to_datetime(raw.time,unit='ms',utc=True)
            raw['quote']=raw.price*raw.qty
            bm=raw.buyer_maker.astype(str).str.lower().isin(['true','1'])
            raw['signed_quote']=np.where(bm,-raw.quote,raw.quote)
            raw['bucket']=raw.ts.dt.floor('10s'); raw['n']=1
            g=raw.groupby('bucket',sort=True)
            p=g.agg(open=('price','first'),high=('price','max'),low=('price','min'),close=('price','last'),quote=('quote','sum'),signed_quote=('signed_quote','sum'),trades=('n','sum'),max_trade_quote=('quote','max'))
            parts.append(p)
    if not parts: return pd.DataFrame()
    x=pd.concat(parts).sort_index()
    return x.groupby(level=0,sort=True).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),quote=('quote','sum'),signed_quote=('signed_quote','sum'),trades=('trades','sum'),max_trade_quote=('max_trade_quote','max'))


def micro_month_to_5m(x):
    if x.empty: return x
    x=x.copy(); x['flow10']=x.signed_quote/x.quote.replace(0,np.nan); x['ret10_bps']=(x.close/x.open-1.0)*10000
    x['aligned10']=np.sign(x.flow10)*x.ret10_bps
    grp=x.resample('5min',label='left',closed='left')
    b=grp.agg({'open':'first','high':'max','low':'min','close':'last','quote':'sum','signed_quote':'sum','trades':'sum','max_trade_quote':'max'}).dropna()
    b['flow']=b.signed_quote/b.quote.replace(0,np.nan)
    b['persist']=grp.flow10.apply(lambda s: float(np.nanmean(np.sign(s))) if len(s) else np.nan).reindex(b.index)
    b['burst']=grp.flow10.apply(lambda s: float(np.nanmax(np.abs(s))) if len(s) else np.nan).reindex(b.index)
    b['first60_flow']=grp.flow10.apply(lambda s: float(s.head(6).mean()) if len(s)>=6 else np.nan).reindex(b.index)
    b['last60_flow']=grp.flow10.apply(lambda s: float(s.tail(6).mean()) if len(s)>=6 else np.nan).reindex(b.index)
    b['accel']=b.last60_flow-b.first60_flow
    b['aligned_impact']=grp.aligned10.mean().reindex(b.index)
    b['adverse_frac']=grp.aligned10.apply(lambda s: float(np.mean(s<0)) if len(s) else np.nan).reindex(b.index)
    b['concentration']=b.max_trade_quote/b.quote.replace(0,np.nan)
    b['ret_bps']=(b.close/b.open-1.0)*10000
    rng=(b.high-b.low).replace(0,np.nan)
    b['close_loc']=(b.close-b.low)/rng
    b['upper_wick']=(b.high-b[['open','close']].max(axis=1))/rng
    b['lower_wick']=(b[['open','close']].min(axis=1)-b.low)/rng
    return b


def load_5m():
    parts=[]
    for m in months(START,END):
        x=fetch_month_10s(m)
        if not x.empty: parts.append(micro_month_to_5m(x))
    if not parts: raise RuntimeError('no aggTrades')
    b=pd.concat(parts).sort_index(); b=b[~b.index.duplicated(keep='last')]
    b=b[(b.index>=START)&(b.index<=END)]
    b['atr']=v11.atr(b)
    b['hi12']=b.high.shift(1).rolling(12,min_periods=12).max(); b['lo12']=b.low.shift(1).rolling(12,min_periods=12).min()
    typ=(b.high+b.low+b.close)/3; day=b.index.floor('D')
    b['vwap']=(typ*b.quote).groupby(day).cumsum()/b.quote.groupby(day).cumsum().replace(0,np.nan)
    q=b.resample('15min',label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last','quote':'sum'}).dropna()
    q['ef']=q.close.ewm(span=20,adjust=False).mean(); q['es']=q.close.ewm(span=60,adjust=False).mean(); q['cci']=v11.cci(q,20); q['slope']=q.es-q.es.shift(4)
    reg=q[['close','ef','es','cci','slope']].reindex(b.index,method='ffill'); b[['qclose','qef','qes','qcci','qslope']]=reg.to_numpy()
    b['strong_up']=(b.qclose>b.qef)&(b.qef>b.qes)&(b.qslope>0)&(b.qcci>80)
    b['strong_dn']=(b.qclose<b.qef)&(b.qef<b.qes)&(b.qslope<0)&(b.qcci<-80)
    return b


def thresholds(fit):
    # Quantile cutoffs learned only on 2025 FIT; frozen for validation and untouched holdout.
    return {
      'flow80':float(fit.flow.abs().quantile(.80)), 'flow90':float(fit.flow.abs().quantile(.90)),
      'persist75':float(fit.persist.abs().quantile(.75)), 'burst80':float(fit.burst.quantile(.80)),
      'impact60':float(fit.aligned_impact.quantile(.60)), 'impact25':float(fit.aligned_impact.quantile(.25)),
      'adverse75':float(fit.adverse_frac.quantile(.75)), 'accel75':float(fit.accel.abs().quantile(.75)),
    }


def rule_masks(x,t):
    # Each rule returns desired side +1/-1/0 from fully completed 5m information.
    flow_side=np.sign(x.flow).fillna(0)
    trend_side=pd.Series(np.where(x.strong_up,1,np.where(x.strong_dn,-1,0)),index=x.index)
    high_flow=x.flow.abs()>=t['flow80']; extreme=x.flow.abs()>=t['flow90']; persistent=x.persist.abs()>=t['persist75']; burst=x.burst>=t['burst80']
    accepted=(x.aligned_impact>=t['impact60'])&(x.adverse_frac<.50)
    toxic=(x.aligned_impact<=t['impact25'])&(x.adverse_frac>=t['adverse75'])
    accel=x.accel.abs()>=t['accel75']
    breakout_up=(x.close>x.hi12)&(x.close>x.vwap)&(x.close_loc>=.68); breakout_dn=(x.close<x.lo12)&(x.close<x.vwap)&(x.close_loc<=.32)
    failed_up=(x.high>x.hi12)&(x.close<x.hi12)&(x.upper_wick>=.25); failed_dn=(x.low<x.lo12)&(x.close>x.lo12)&(x.lower_wick>=.25)
    rules={}
    # 1) accepted flow continuation in same strong trend
    m=high_flow&persistent&accepted&(flow_side==trend_side)&(trend_side!=0)
    rules['E0_trend_accept']=pd.Series(np.where(m,trend_side,0),index=x.index)
    # 2) extreme burst continuation only if price breaks and accepts
    up=extreme&burst&accepted&(flow_side>0)&breakout_up; dn=extreme&burst&accepted&(flow_side<0)&breakout_dn
    rules['E1_break_accept']=pd.Series(np.where(up,1,np.where(dn,-1,0)),index=x.index)
    # 3) toxic flow: aggression moves price opposite => fade
    m=high_flow&persistent&toxic
    rules['E2_toxic_fade']=pd.Series(np.where(m,-flow_side,0),index=x.index)
    # 4) failed local break under extreme flow => fade
    su=extreme&burst&(flow_side>0)&failed_up; sl=extreme&burst&(flow_side<0)&failed_dn
    rules['E3_failed_break']=pd.Series(np.where(su,-1,np.where(sl,1,0)),index=x.index)
    # 5) late acceleration continuation when accepted
    m=high_flow&accel&accepted&(np.sign(x.accel)==flow_side)
    rules['E4_late_accel']=pd.Series(np.where(m,flow_side,0),index=x.index)
    # 6) last-minute flow reversal against 5m net flow => follow reversal only outside strong trend
    rev=(np.sign(x.last60_flow)!=flow_side)&(x.last60_flow.abs()>=t['flow80'])&(~(x.strong_up|x.strong_dn))
    rules['E5_last60_flip']=pd.Series(np.where(rev,np.sign(x.last60_flow),0),index=x.index)
    return rules


def evaluate(x,side,h,cost):
    # Signal at bar t; enter at t+1 open; exit at close of the h-th bar after entry.
    entry=x.open.shift(-1); exitp=x.close.shift(-h)
    valid=(side!=0)&entry.notna()&exitp.notna()
    if not valid.any(): return {'events':0,'mean_net_bps':0,'median_net_bps':0,'win_pct':0,'gross_mean_bps':0}
    gross=side[valid]*(exitp[valid]/entry[valid]-1.0)*10000
    net=gross-2*cost
    return {'events':int(len(net)),'mean_net_bps':float(net.mean()),'median_net_bps':float(net.median()),'win_pct':float((net>0).mean()*100),'gross_mean_bps':float(gross.mean()),'p10_net_bps':float(net.quantile(.10)),'p90_net_bps':float(net.quantile(.90))}


def score_fit(m):
    if m['events']<200 or m['gross_mean_bps']<=4 or m['mean_net_bps']<=0: return -1e9
    return m['mean_net_bps']+0.05*m['win_pct']


def main():
    b=load_5m(); fit=b[(b.index>=START)&(b.index<=FIT_END)]; val=b[(b.index>=VAL_START)&(b.index<=VAL_END)]; hold=b[b.index>=HOLDOUT_START]
    t=thresholds(fit); rf=rule_masks(fit,t); rv=rule_masks(val,t); rh=rule_masks(hold,t)
    candidates={}; ranked=[]
    for name,side in rf.items():
        for h in HORIZONS:
            key=f'{name}_h{h}'
            fm={str(c):evaluate(fit,side,h,c) for c in COSTS}; sc=score_fit(fm['7.0'])
            candidates[key]={'rule':name,'horizon_5m_bars':h,'fit':fm,'score':sc}; ranked.append((sc,key))
    ranked.sort(reverse=True); selected=ranked[0][1] if ranked and ranked[0][0]>-1e8 else None
    validation=None; holdout=None; gate=False
    if selected:
        base=candidates[selected]['rule']; h=candidates[selected]['horizon_5m_bars']
        validation={str(c):evaluate(val,rv[base],h,c) for c in COSTS}
        v7=validation['7.0']
        val_gate=(v7['events']>=50 and v7['mean_net_bps']>0 and v7['gross_mean_bps']>14 and validation['12.0']['mean_net_bps']>0)
        if val_gate:
            holdout={str(c):evaluate(hold,rh[base],h,c) for c in COSTS}
            h7=holdout['7.0']
            gate=(h7['events']>=30 and h7['mean_net_bps']>0 and holdout['12.0']['mean_net_bps']>0 and holdout['20.0']['mean_net_bps']>0)
    state={'version':'quantbot-v15-10s-microstructure-edge-screen','purpose':'EDGE_DISCOVERY_NO_LIVE','symbol':SYMBOL,
           'source':'Binance USD-M aggTrades -> 10s microstructure -> 5m signal / 15m regime','fit_end':str(FIT_END),'validation':f'{VAL_START}..{VAL_END}',
           'holdout_start':str(HOLDOUT_START),'thresholds_fit_only':t,'candidate_count':len(candidates),'candidates':candidates,'selected':selected,
           'validation_result':validation,'holdout':holdout,'holdout_gate_pass':bool(gate),'live_orders':False,
           'interpretation':'V15_EDGE_SURVIVES' if gate else ('V15_VALIDATION_STAGE' if selected else 'NO_V15_FIT_EDGE')}
    STATE.parent.mkdir(parents=True,exist_ok=True); STATE.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps(state,indent=2))

if __name__=='__main__': main()
