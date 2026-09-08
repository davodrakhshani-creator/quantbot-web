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
SELECT_END=pd.Timestamp('2026-04-24 23:59:59',tz='UTC')
HOLD_START=pd.Timestamp('2026-04-25',tz='UTC')
BASE='https://data.binance.vision/data/futures/um/monthly/klines'
STATE=Path('data/v10_fast_screen_state.json')
COSTS=(7.0,12.0,20.0)

@dataclass(frozen=True)
class Spec:
    name:str; flow_z:float; vol_z:float; breakout:int; stop_atr:float; take_atr:float; hold:int; cci:float; persist:int

SPECS=[
    Spec('Q0_sparse',1.35,.60,12,1.05,2.20,18,55,2),
    Spec('Q1_strict',1.60,.80,12,1.05,2.35,18,65,2),
    Spec('Q2_extreme',2.00,1.00,12,1.00,2.50,18,75,2),
    Spec('Q3_wide',1.50,.70,24,1.15,2.70,24,60,2),
    Spec('Q4_persist3',1.25,.55,12,1.10,2.40,24,55,3),
    Spec('Q5_strict24',1.75,.90,24,1.10,2.80,24,70,2),
]

def months(a,b):
    cur=pd.Timestamp(a.year,a.month,1,tz='UTC'); last=pd.Timestamp(b.year,b.month,1,tz='UTC')
    while cur<=last:
        yield cur; cur += pd.offsets.MonthBegin(1)

def load():
    parts=[]
    for m in months(START,END):
        ym=m.strftime('%Y-%m'); url=f'{BASE}/{SYMBOL}/5m/{SYMBOL}-5m-{ym}.zip'
        r=requests.get(url,timeout=45)
        if r.status_code==404: continue
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            raw=pd.read_csv(z.open(z.namelist()[0]),header=None)
        raw=raw.iloc[:,:12]
        raw.columns=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
        for c in ['open_time','open','high','low','close','volume','taker_base']:
            raw[c]=pd.to_numeric(raw[c],errors='coerce')
        raw=raw.dropna(subset=['open_time','open','high','low','close','volume','taker_base'])
        raw['ts']=pd.to_datetime(raw.open_time,unit='ms',utc=True)
        parts.append(raw.set_index('ts')[['open','high','low','close','volume','taker_base']])
    x=pd.concat(parts).sort_index(); x=x[~x.index.duplicated(keep='last')]
    return x[(x.index>=START)&(x.index<=END)]

def atr(x,n=14):
    pc=x.close.shift(1)
    tr=pd.concat([(x.high-x.low).abs(),(x.high-pc).abs(),(x.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean()

def cci(x,n=20):
    tp=(x.high+x.low+x.close)/3; ma=tp.rolling(n,min_periods=n).mean(); md=tp.rolling(n,min_periods=n).apply(lambda a: np.mean(np.abs(a-a.mean())),raw=True)
    return (tp-ma)/(0.015*md.replace(0,np.nan))

def features(x):
    y=x.copy(); y['atr']=atr(y); y['rng']=(y.high-y.low).replace(0,np.nan)
    typ=(y.high+y.low+y.close)/3; day=y.index.floor('D'); y['vwap']=(typ*y.volume).groupby(day).cumsum()/y.volume.groupby(day).cumsum().replace(0,np.nan)
    y['flow']=((2*y.taker_base-y.volume)/y.volume.replace(0,np.nan)).clip(-1,1)
    w=288
    fm=y.flow.rolling(w,min_periods=96).mean().shift(1); fs=y.flow.rolling(w,min_periods=96).std(ddof=0).shift(1).replace(0,np.nan)
    y['flow_z']=(y.flow-fm)/fs
    lv=np.log1p(y.volume); vm=lv.rolling(w,min_periods=96).mean().shift(1); vs=lv.rolling(w,min_periods=96).std(ddof=0).shift(1).replace(0,np.nan)
    y['vol_z']=(lv-vm)/vs
    y['body']=(y.close-y.open).abs()/y.rng; y['loc']=(y.close-y.low)/y.rng
    y['rmed']=y.rng.rolling(48,min_periods=24).median().shift(1)
    q=y.resample('15min',label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    q['ef']=q.close.ewm(span=20,adjust=False).mean(); q['es']=q.close.ewm(span=50,adjust=False).mean(); q['cci']=cci(q,20); q['slope']=q.es-q.es.shift(4)
    reg=q[['close','ef','es','cci','slope']].reindex(y.index,method='ffill')
    y[['qclose','qef','qes','qcci','qslope']]=reg.to_numpy()
    return y

def signals(y,s):
    hi=y.high.shift(1).rolling(s.breakout,min_periods=s.breakout).max(); lo=y.low.shift(1).rolling(s.breakout,min_periods=s.breakout).min()
    fb=y.flow_z>=s.flow_z; fs=y.flow_z<=-s.flow_z
    if s.persist==2:
        pb=(fb.astype(int)+fb.shift(1).fillna(False).astype(int)+fb.shift(2).fillna(False).astype(int))>=2
        ps=(fs.astype(int)+fs.shift(1).fillna(False).astype(int)+fs.shift(2).fillna(False).astype(int))>=2
    else:
        pb=fb & fb.shift(1).fillna(False) & fb.shift(2).fillna(False)
        ps=fs & fs.shift(1).fillna(False) & fs.shift(2).fillna(False)
    lr=(y.qclose>y.qef)&(y.qef>y.qes)&(y.qslope>0)&(y.qcci>s.cci)
    sr=(y.qclose<y.qef)&(y.qef<y.qes)&(y.qslope<0)&(y.qcci<-s.cci)
    exp=y.rng>=1.10*y.rmed; vol=y.vol_z>=s.vol_z
    long=lr&pb&vol&exp&(y.close>hi)&(y.close>y.vwap)&(y.body>=.55)&(y['loc']>=.72)
    short=sr&ps&vol&exp&(y.close<lo)&(y.close<y.vwap)&(y.body>=.55)&(y['loc']<=.28)
    return long.to_numpy(),short.to_numpy()

def raw_trades(y,s):
    L,S=signals(y,s); n=len(y); rows=[]; pos=0; entry=stop=target=0.; notional=0.; entry_i=-1; pending=0; pat=0.; cool=0
    for i in range(600,n-1):
        r=y.iloc[i]; nxt=y.iloc[i+1]
        if cool>0: cool-=1
        if pos:
            stop_hit=(pos>0 and r.low<=stop) or (pos<0 and r.high>=stop)
            tgt_hit=(pos>0 and r.high>=target) or (pos<0 and r.low<=target)
            held=i-entry_i
            if stop_hit or tgt_hit or held>=s.hold:
                px=stop if stop_hit else (target if tgt_hit else float(nxt.open))
                gross=pos*(px/entry-1.0)*notional
                rows.append((gross,notional,pos,'stop' if stop_hit else ('target' if tgt_hit else 'time')))
                pos=0; cool=2; continue
        if pos==0 and pending:
            px=float(r.open); dist=s.stop_atr*pat
            if dist>0 and np.isfinite(dist):
                pos=pending; entry=px; stop=entry-pos*dist; target=entry+pos*s.take_atr*pat
                notional=min(2.0,.0025/max(dist/entry,1e-6)); entry_i=i
            pending=0; continue
        if pos==0 and cool==0 and np.isfinite(r.atr) and r.atr>0:
            if bool(L[i]) ^ bool(S[i]): pending=1 if L[i] else -1; pat=float(r.atr)
    if pos:
        px=float(y.close.iloc[-1]); rows.append((pos*(px/entry-1.0)*notional,notional,pos,'end'))
    return rows

def metrics(rows,cost_bps):
    if not rows: return {'trades':0,'net_pct':0,'pf':0,'win_pct':0,'max_dd_pct':0,'avg_trade_bps':0,'longs':0,'shorts':0}
    eq=peak=1.; dd=0.; rets=[]
    for gross,n,s,r in rows:
        ret=gross-2*(cost_bps/10000.)*n
        rets.append(ret); eq*=1+ret; peak=max(peak,eq); dd=min(dd,eq/peak-1)
    a=np.array(rets); gains=a[a>0].sum(); losses=-a[a<0].sum()
    return {'trades':len(rows),'net_pct':(eq-1)*100,'pf':float(gains/losses) if losses>0 else 99.,'win_pct':float((a>0).mean()*100),'max_dd_pct':float(-dd*100),'avg_trade_bps':float(a.mean()*10000),'longs':sum(1 for z in rows if z[2]>0),'shorts':sum(1 for z in rows if z[2]<0)}

def score(m):
    if m['trades']<80 or m['net_pct']<=0 or m['pf']<1.12 or m['max_dd_pct']>18: return -1e9
    return m['net_pct']+30*(m['pf']-1)+.15*m['win_pct']-.8*m['max_dd_pct']

def run_side(y,s,side,cost):
    rows=[r for r in raw_trades(y,s) if (r[2]>0 if side=='long' else r[2]<0)]
    return metrics(rows,cost)

def main():
    x=features(load()); train=x[x.index<=SELECT_END]; hold=x[x.index>=HOLD_START]
    cand={}; eligible=[]
    for s in SPECS:
        rows=raw_trades(train,s); mm={str(c):metrics(rows,c) for c in COSTS}; sc=score(mm['7.0'])
        cand[s.name]={'spec':asdict(s),'train':mm,'score':sc}
        if sc>-1e8 and mm['12.0']['net_pct']>0 and mm['20.0']['net_pct']>0: eligible.append(s.name)
    sel=max(eligible,key=lambda k:cand[k]['score']) if eligible else None
    holdout=None; gate=False; side_diag=None
    if sel:
        s=next(z for z in SPECS if z.name==sel); rows=raw_trades(hold,s); ho={str(c):metrics(rows,c) for c in COSTS}
        m7,m12,m20=ho['7.0'],ho['12.0'],ho['20.0']
        gate=(m7['trades']>=30 and m7['net_pct']>0 and m7['pf']>=1.15 and m7['max_dd_pct']<=15 and m12['net_pct']>0 and m20['net_pct']>0)
        holdout=ho
        side_diag={'long_7bps':run_side(hold,s,'long',7.0),'short_7bps':run_side(hold,s,'short',7.0)}
    out={'version':'quantbot-v10-fast-screen','purpose':'FAST_SCREEN_ONLY_NOT_LIVE_AUTHORIZATION','symbol':SYMBOL,'timeframes':{'entry':'5m','regime':'15m'},'selection_end':str(SELECT_END),'holdout_start':str(HOLD_START),'candidate_count':len(SPECS),'selected':sel,'candidates':cand,'holdout':holdout,'side_diagnostic':side_diag,'holdout_gate_pass':gate,'live_orders':False,'interpretation':'SCREEN_PASS_RUN_FULL_AUDIT' if gate else 'NO_FAST_SCREEN_EDGE_YET'}
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))

if __name__=='__main__': main()
