from __future__ import annotations

import io, json, zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
import numpy as np
import pandas as pd
import requests

SYMBOL='BTCUSDT'
START=pd.Timestamp('2023-01-01',tz='UTC')
END=pd.Timestamp('2026-08-31 23:59:59',tz='UTC')
SELECTION_END=pd.Timestamp('2026-04-24 23:59:59',tz='UTC')
HOLDOUT_START=pd.Timestamp('2026-04-25',tz='UTC')
BASE='https://data.binance.vision/data/futures/um/monthly/klines'
STATE=Path('data/v8_multi_master_scalper_state.json')
COSTS=(7.0,12.0,20.0)  # one-way bps, charged both entry and exit

@dataclass(frozen=True)
class Spec:
    name:str
    mode:str
    vote_threshold:float
    flow_z:float
    stop_atr:float
    take_atr:float
    max_hold:int
    daily_stop_pct:float

SPECS=[
    Spec('M0_strict3','consensus',2.75,1.00,1.00,1.60,18,1.50),
    Spec('M1_weighted','consensus',2.30,0.90,1.05,1.70,24,1.50),
    Spec('M2_ultra_strict','consensus',3.15,1.20,0.95,1.55,15,1.25),
    Spec('M3_adaptive','adaptive',2.10,0.90,1.10,1.85,24,1.50),
    Spec('M4_defensive','adaptive',2.40,1.10,1.15,1.70,18,1.00),
    Spec('M5_fast','consensus',2.05,0.80,0.90,1.45,12,1.25),
]

def months(a,b):
    cur=pd.Timestamp(a.year,a.month,1,tz='UTC'); last=pd.Timestamp(b.year,b.month,1,tz='UTC')
    while cur<=last:
        yield cur; cur += pd.offsets.MonthBegin(1)

def fetch_month(m):
    ym=m.strftime('%Y-%m')
    url=f'{BASE}/{SYMBOL}/5m/{SYMBOL}-5m-{ym}.zip'
    r=requests.get(url,timeout=45)
    if r.status_code==404: return pd.DataFrame()
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw=pd.read_csv(z.open(z.namelist()[0]),header=None)
    cols=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
    raw=raw.iloc[:,:12]; raw.columns=cols
    for c in ['open_time','open','high','low','close','volume','quote_volume','trades','taker_base']:
        raw[c]=pd.to_numeric(raw[c],errors='coerce')
    raw=raw.dropna(subset=['open_time','open','high','low','close','volume','taker_base'])
    raw['ts']=pd.to_datetime(raw.open_time,unit='ms',utc=True)
    return raw.set_index('ts')[['open','high','low','close','volume','quote_volume','trades','taker_base']].sort_index()

def load_data():
    parts=[x for m in months(START,END) if not (x:=fetch_month(m)).empty]
    if not parts: raise RuntimeError('no data')
    x=pd.concat(parts); x=x[~x.index.duplicated(keep='last')].sort_index()
    return x[(x.index>=START)&(x.index<=END)]

def zscore(s,n,minp=None):
    if minp is None: minp=max(20,n//3)
    mu=s.rolling(n,min_periods=minp).mean().shift(1)
    sd=s.rolling(n,min_periods=minp).std(ddof=0).shift(1).replace(0,np.nan)
    return (s-mu)/sd

def atr(df,n=14):
    pc=df.close.shift(1)
    tr=pd.concat([(df.high-df.low).abs(),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean()

def feature_frame(df,flow_gate):
    x=df.copy()
    x['atr']=atr(x); x['atrp']=x.atr/x.close
    x['ret']=x.close.pct_change(); x['ret_z']=zscore(x.ret.abs(),96)
    x['vol_z']=zscore(np.log1p(x.volume),96)
    delta=2*x.taker_base-x.volume
    x['flow']=delta/x.volume.replace(0,np.nan)
    x['flow_z']=zscore(x.flow,96)
    typ=(x.high+x.low+x.close)/3
    day=x.index.floor('D')
    x['vwap']=(typ*x.volume).groupby(day).cumsum()/x.volume.groupby(day).cumsum().replace(0,np.nan)
    rng=(x.high-x.low).replace(0,np.nan)
    x['body_frac']=(x.close-x.open).abs()/rng
    x['close_loc']=(x.close-x.low)/rng
    x['hi12']=x.high.shift(1).rolling(12,min_periods=12).max()
    x['lo12']=x.low.shift(1).rolling(12,min_periods=12).min()
    x['hi24']=x.high.shift(1).rolling(24,min_periods=24).max()
    x['lo24']=x.low.shift(1).rolling(24,min_periods=24).min()

    q=x.resample('15min',label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    q['atr15']=atr(q); q['ef']=q.close.ewm(span=24,adjust=False).mean(); q['es']=q.close.ewm(span=72,adjust=False).mean()
    q['volz15']=zscore(np.log1p(q.volume),96)
    q['trend_strength']=(q.ef-q.es)/q.atr15.replace(0,np.nan)
    q['trend_dir']=np.where((q.close>q.ef)&(q.ef>q.es)&(q.trend_strength>0.20),1,np.where((q.close<q.ef)&(q.ef<q.es)&(q.trend_strength<-0.20),-1,0))
    q['active15']=q.volz15>-0.15
    q['range15']=q.trend_dir==0
    f=q[['trend_dir','active15','range15','trend_strength']].reindex(x.index,method='ffill')
    x[['trend_dir','active15','range15','trend_strength']]=f

    # Expert 1 — Rotter-inspired: short-horizon order-flow continuation, plus absorption flip.
    rot=np.zeros(len(x),dtype=float)
    long_cont=(x.flow_z>=flow_gate)&(x.vol_z>=0.20)&(x.close>x.vwap)&(x.close>x.open)
    short_cont=(x.flow_z<=-flow_gate)&(x.vol_z>=0.20)&(x.close<x.vwap)&(x.close<x.open)
    rot[long_cont]=1; rot[short_cont]=-1
    absorb_short=(x.flow_z>=flow_gate+0.5)&(x.close_loc<0.35)&(x.body_frac<0.55)
    absorb_long=(x.flow_z<=-(flow_gate+0.5))&(x.close_loc>0.65)&(x.body_frac<0.55)
    rot[absorb_short]=-1; rot[absorb_long]=1
    x['rotter']=rot

    # Expert 2 — Norden-inspired: identify aggressive flow that fails to move price (poorly placed orders),
    # or aligned flow that breaks a local level.
    nor=np.zeros(len(x),dtype=float)
    fail_buy=(x.flow_z>=flow_gate)&(x.close<=x.open)&(x.high>=x.hi12)&(x.close_loc<0.45)
    fail_sell=(x.flow_z<=-flow_gate)&(x.close>=x.open)&(x.low<=x.lo12)&(x.close_loc>0.55)
    follow_buy=(x.flow_z>=flow_gate)&(x.close>x.hi12)&(x.close_loc>0.65)
    follow_sell=(x.flow_z<=-flow_gate)&(x.close<x.lo12)&(x.close_loc<0.35)
    nor[fail_buy]=-1; nor[fail_sell]=1; nor[follow_buy]=1; nor[follow_sell]=-1
    x['norden']=nor

    # Expert 3 — Raschke-inspired: volume/activity first, then trade with expansion regime; otherwise abstain.
    ras=np.where(x.active15.fillna(False),x.trend_dir.fillna(0),0).astype(float)
    x['raschke']=ras

    # Expert 4 — Brooks-inspired: trend vs range, breakout/follow-through in trend, fade rejection in range.
    bro=np.zeros(len(x),dtype=float)
    trend_long=(x.trend_dir==1)&(x.body_frac>=0.55)&(x.close>x.hi12)&(x.close_loc>0.65)
    trend_short=(x.trend_dir==-1)&(x.body_frac>=0.55)&(x.close<x.lo12)&(x.close_loc<0.35)
    range_short=(x.range15==True)&(x.high>=x.hi24)&(x.close_loc<0.35)
    range_long=(x.range15==True)&(x.low<=x.lo24)&(x.close_loc>0.65)
    bro[trend_long]=1; bro[trend_short]=-1; bro[range_short]=-1; bro[range_long]=1
    x['brooks']=bro
    return x

def decide(row,spec):
    votes=np.array([row.rotter,row.norden,row.raschke,row.brooks],dtype=float)
    weights=np.array([1.20,1.05,0.80,1.00])
    if spec.mode=='adaptive':
        if int(row.trend_dir)==0:
            weights=np.array([1.10,1.20,0.20,1.20])
        else:
            weights=np.array([1.20,0.95,1.00,1.15])
    score=float(np.nansum(votes*weights))
    pos=float(np.nansum(weights[votes>0])); neg=float(np.nansum(weights[votes<0]))
    if score>=spec.vote_threshold and neg<0.8: return 1,score,votes
    if score<=-spec.vote_threshold and pos<0.8: return -1,score,votes
    return 0,score,votes

def simulate(df,spec,cost_bps,risk_frac=.0035,leverage_cap=2.0):
    x=feature_frame(df,spec.flow_z)
    eq=1.0; peak=1.0; maxdd=0.0
    pos=0; entry=stop=target=np.nan; notional=0.0; entry_i=None
    pending=0; pending_atr=np.nan; cooldown=0; trades=[]
    current_day=None; day_start_eq=1.0; day_locked=False

    def close_trade(i,px,reason):
        nonlocal eq,peak,maxdd,pos,entry,stop,target,notional,entry_i,cooldown
        gross=pos*(px/entry-1.0)*notional
        cost=(cost_bps/10000.0)*notional
        ret=gross-cost
        eq*=1+ret; peak=max(peak,eq); maxdd=min(maxdd,eq/peak-1)
        trades.append({'exit_ts':str(x.index[i]),'side':pos,'ret':ret,'gross':gross,'notional':notional,'reason':reason})
        pos=0; entry=stop=target=np.nan; notional=0.0; entry_i=None; cooldown=2

    start_i=400
    for i in range(start_i,len(x)-1):
        ts=x.index[i]; row=x.iloc[i]; nxt=x.iloc[i+1]
        d=ts.floor('D')
        if current_day is None or d!=current_day:
            current_day=d; day_start_eq=eq; day_locked=False
        if eq/day_start_eq-1 <= -spec.daily_stop_pct/100:
            day_locked=True
        if cooldown>0: cooldown-=1

        if pos!=0:
            stop_hit=(pos>0 and row.low<=stop) or (pos<0 and row.high>=stop)
            tgt_hit=(pos>0 and row.high>=target) or (pos<0 and row.low<=target)
            held=i-entry_i
            if stop_hit:
                close_trade(i,stop,'stop'); continue
            if tgt_hit:
                close_trade(i,target,'target'); continue
            if held>=spec.max_hold:
                close_trade(i+1,float(nxt.open),'time'); continue

        if pos==0 and pending!=0:
            px=float(row.open)
            if np.isfinite(pending_atr) and pending_atr>0:
                pos=pending; entry=px
                dist=spec.stop_atr*pending_atr
                stop=entry-pos*dist; target=entry+pos*spec.take_atr*pending_atr
                stop_pct=dist/entry
                notional=min(leverage_cap,risk_frac/max(stop_pct,1e-6))
                eq*=1-(cost_bps/10000.0)*notional
                peak=max(peak,eq); maxdd=min(maxdd,eq/peak-1); entry_i=i
            pending=0; pending_atr=np.nan
            continue

        if pos==0 and not day_locked and cooldown==0 and np.isfinite(row.atr) and row.atr>0:
            side,score,votes=decide(row,spec)
            if side!=0:
                pending=side; pending_atr=float(row.atr)

    if pos!=0: close_trade(len(x)-1,float(x.close.iloc[-1]),'eod')
    t=pd.DataFrame(trades)
    if t.empty:
        return {'trades':0,'net_pct':0,'pf':0,'win_pct':0,'max_dd_pct':0,'avg_trade_bps':0,'median_trade_bps':0,'longs':0,'shorts':0,'stop_pct':0,'target_pct':0,'time_pct':0}
    gains=t.loc[t.ret>0,'ret'].sum(); losses=-t.loc[t.ret<0,'ret'].sum()
    return {'trades':int(len(t)),'net_pct':float((eq-1)*100),'pf':float(gains/losses) if losses>0 else 99.0,
            'win_pct':float((t.ret>0).mean()*100),'max_dd_pct':float(-maxdd*100),'avg_trade_bps':float(t.ret.mean()*10000),
            'median_trade_bps':float(t.ret.median()*10000),'longs':int((t.side>0).sum()),'shorts':int((t.side<0).sum()),
            'stop_pct':float((t.reason=='stop').mean()*100),'target_pct':float((t.reason=='target').mean()*100),'time_pct':float((t.reason=='time').mean()*100)}

def score(m):
    if m['trades']<120 or m['net_pct']<=0 or m['pf']<1.12 or m['max_dd_pct']>20: return -1e9
    return m['net_pct']+30*(m['pf']-1)+.15*m['win_pct']-.8*m['max_dd_pct']

def main():
    df=load_data(); train=df[df.index<=SELECTION_END]; hold=df[df.index>=HOLDOUT_START]
    results={}
    for s in SPECS:
        base=simulate(train,s,COSTS[0]); stress12=simulate(train,s,COSTS[1]); stress20=simulate(train,s,COSTS[2])
        results[s.name]={'spec':asdict(s),'train_7bps':base,'train_12bps':stress12,'train_20bps':stress20,'score':score(base)}
    eligible=[k for k,v in results.items() if v['score']>-1e8 and v['train_12bps']['net_pct']>0 and v['train_20bps']['net_pct']>0]
    selected=max(eligible,key=lambda k:results[k]['score']) if eligible else max(results,key=lambda k:results[k]['score'])
    spec=next(s for s in SPECS if s.name==selected)
    h7=simulate(hold,spec,7.0); h12=simulate(hold,spec,12.0); h20=simulate(hold,spec,20.0)
    gate=(h7['trades']>=60 and h7['net_pct']>0 and h7['pf']>=1.15 and h7['max_dd_pct']<=12 and h12['net_pct']>0 and h20['net_pct']>0)
    out={'version':'quantbot-v8-multi-master-btc-scalper','symbol':SYMBOL,'timeframes':['5m','15m'],'selection_end':str(SELECTION_END),
         'holdout_start':str(HOLDOUT_START),'inspirations':{
             'Paul_Rotter':'order-flow/very-short-horizon/rapid opinion changes/daily stop discipline',
             'Gary_Norden':'market-making/order-flow/poorly-placed-order logic',
             'Linda_Raschke':'volume/activity first; expansion vs rotation regime; risk scales with volatility',
             'Al_Brooks':'trend-vs-range classification; breakout/follow-through vs range fade'},
         'candidate_count':len(SPECS),'results':results,'selected':selected,'holdout_7bps':h7,'holdout_12bps':h12,'holdout_20bps':h20,
         'holdout_gate':{'criteria':'trades>=60; net>0; PF>=1.15; DD<=12%; 12bps net>0; 20bps net>0','passed':bool(gate)},
         'live_orders':False,'status':'PAPER_CANDIDATE' if gate else 'REJECT_OR_RESEARCH_ONLY'}
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'selected':selected,'holdout_7bps':h7,'holdout_12bps':h12,'holdout_20bps':h20,'gate':gate},indent=2))

if __name__=='__main__': main()
