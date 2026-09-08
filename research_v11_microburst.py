from __future__ import annotations

import io, json, zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
import numpy as np
import pandas as pd
import requests

SYMBOL='BTCUSDT'
START=pd.Timestamp('2024-01-01',tz='UTC')
END=pd.Timestamp('2026-08-31 23:59:59',tz='UTC')
SELECTION_END=pd.Timestamp('2026-04-24 23:59:59',tz='UTC')
HOLDOUT_START=pd.Timestamp('2026-04-25',tz='UTC')
BASE='https://data.binance.vision/data/futures/um/monthly/klines'
STATE=Path('data/v11_microburst_state.json')
COSTS=(7.0,12.0,20.0)

@dataclass(frozen=True)
class Spec:
    name:str
    flow_thr:float
    persist_thr:float
    accel_thr:float
    body_thr:float
    stop_atr:float
    take_atr:float
    max_hold:int
    cci_gate:float

SPECS=[
    Spec('B0_balanced',0.18,0.60,0.04,0.45,1.10,2.20,18,55),
    Spec('B1_strict',0.24,0.70,0.06,0.50,1.05,2.40,18,65),
    Spec('B2_extreme',0.30,0.80,0.08,0.55,1.00,2.60,15,75),
    Spec('B3_wide',0.20,0.65,0.04,0.45,1.20,2.80,24,60),
    Spec('B4_fast',0.16,0.60,0.05,0.50,0.95,1.90,12,55),
    Spec('B5_defensive',0.26,0.75,0.05,0.50,1.25,2.40,24,70),
]

def months(a,b):
    cur=pd.Timestamp(a.year,a.month,1,tz='UTC'); last=pd.Timestamp(b.year,b.month,1,tz='UTC')
    while cur<=last:
        yield cur; cur += pd.offsets.MonthBegin(1)

def fetch_month(m):
    ym=m.strftime('%Y-%m')
    url=f'{BASE}/{SYMBOL}/1m/{SYMBOL}-1m-{ym}.zip'
    r=requests.get(url,timeout=60)
    if r.status_code==404: return pd.DataFrame()
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw=pd.read_csv(z.open(z.namelist()[0]),header=None)
    cols=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
    raw=raw.iloc[:,:12]; raw.columns=cols
    for c in ['open_time','open','high','low','close','volume','taker_base','trades']:
        raw[c]=pd.to_numeric(raw[c],errors='coerce')
    raw=raw.dropna(subset=['open_time','open','high','low','close','volume','taker_base'])
    raw['ts']=pd.to_datetime(raw.open_time,unit='ms',utc=True)
    return raw.set_index('ts')[['open','high','low','close','volume','taker_base','trades']].sort_index()

def load_data():
    parts=[]
    for m in months(START,END):
        x=fetch_month(m)
        if not x.empty: parts.append(x)
    if not parts: raise RuntimeError('no 1m futures data')
    x=pd.concat(parts).sort_index(); x=x[~x.index.duplicated(keep='last')]
    return x[(x.index>=START)&(x.index<=END)]

def atr(df,n=14):
    pc=df.close.shift(1)
    tr=pd.concat([(df.high-df.low).abs(),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean()

def cci(df,n=20):
    tp=(df.high+df.low+df.close)/3
    ma=tp.rolling(n,min_periods=n).mean()
    md=tp.rolling(n,min_periods=n).apply(lambda v: np.mean(np.abs(v-v.mean())),raw=True)
    return (tp-ma)/(0.015*md.replace(0,np.nan))

def build_5m(m1):
    x=m1.copy()
    x['signed_base']=2*x.taker_base-x.volume
    x['flow1']=x.signed_base/x.volume.replace(0,np.nan)
    x['ret1']=x.close.pct_change()
    grp=x.resample('5min',label='left',closed='left')
    bars=grp.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','signed_base':'sum','trades':'sum'}).dropna()
    bars['flow']=bars.signed_base/bars.volume.replace(0,np.nan)
    persist=grp.flow1.apply(lambda s: float(np.mean(np.sign(s))) if len(s) else np.nan)
    accel=grp.flow1.apply(lambda s: float(s.tail(2).mean()-s.head(2).mean()) if len(s)>=4 else np.nan)
    flow_std=grp.flow1.std(ddof=0)
    micro_ret=grp.ret1.sum()
    bars['persist']=persist.reindex(bars.index)
    bars['accel']=accel.reindex(bars.index)
    bars['flow_std']=flow_std.reindex(bars.index)
    bars['micro_ret']=micro_ret.reindex(bars.index)
    bars['atr']=atr(bars)
    rng=(bars.high-bars.low).replace(0,np.nan)
    bars['body_frac']=(bars.close-bars.open).abs()/rng
    bars['close_loc']=(bars.close-bars.low)/rng
    typ=(bars.high+bars.low+bars.close)/3
    day=bars.index.floor('D')
    bars['vwap']=(typ*bars.volume).groupby(day).cumsum()/bars.volume.groupby(day).cumsum().replace(0,np.nan)
    bars['efficiency']=bars.micro_ret.abs()/(bars.flow.abs()+0.02)
    bars['hi12']=bars.high.shift(1).rolling(12,min_periods=12).max()
    bars['lo12']=bars.low.shift(1).rolling(12,min_periods=12).min()
    q=bars.resample('15min',label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    q['ef']=q.close.ewm(span=20,adjust=False).mean(); q['es']=q.close.ewm(span=60,adjust=False).mean(); q['cci']=cci(q,20)
    q['slope']=q.es-q.es.shift(4)
    reg=q[['close','ef','es','cci','slope']].reindex(bars.index,method='ffill')
    bars[['qclose','qef','qes','qcci','qslope']]=reg.to_numpy()
    return bars

def signal_frame(b,s):
    x=b.copy()
    long_reg=(x.qclose>x.qef)&(x.qef>x.qes)&(x.qslope>0)&(x.qcci>s.cci_gate)
    short_reg=(x.qclose<x.qef)&(x.qef<x.qes)&(x.qslope<0)&(x.qcci<-s.cci_gate)
    # Continuation requires agreement across direction, persistence, acceleration, price acceptance and regime.
    long_flow=(x.flow>=s.flow_thr)&(x.persist>=s.persist_thr)&(x.accel>=s.accel_thr)
    short_flow=(x.flow<=-s.flow_thr)&(x.persist<=-s.persist_thr)&(x.accel<=-s.accel_thr)
    # Reject extreme aggression that fails to move price; these are likely absorption/exhaustion bars.
    eff_med=x.efficiency.rolling(288,min_periods=96).median().shift(1)
    efficient=x.efficiency>=0.65*eff_med
    long_px=(x.close>x.vwap)&(x.close>x.hi12)&(x.body_frac>=s.body_thr)&(x.close_loc>=0.67)
    short_px=(x.close<x.vwap)&(x.close<x.lo12)&(x.body_frac>=s.body_thr)&(x.close_loc<=0.33)
    x['long_sig']=long_reg&long_flow&efficient&long_px
    x['short_sig']=short_reg&short_flow&efficient&short_px
    return x

def simulate(b,s,cost_bps,risk_frac=.0025,lev_cap=2.0,side_filter=0,collect=False):
    x=signal_frame(b,s)
    eq=peak=1.0; maxdd=0.0; pos=0; pending=0; pending_atr=np.nan
    entry=stop=target=np.nan; notional=0.0; entry_i=-1; cooldown=0; trades=[]
    def close_trade(i,px,reason):
        nonlocal eq,peak,maxdd,pos,entry,stop,target,notional,entry_i,cooldown
        gross=pos*(px/entry-1.0)*notional; exit_cost=(cost_bps/10000.0)*notional
        ret=gross-exit_cost; eq*=1+ret; peak=max(peak,eq); maxdd=min(maxdd,eq/peak-1)
        trades.append({'entry_ts':str(x.index[entry_i]),'exit_ts':str(x.index[i]),'day':str(x.index[entry_i].date()),'side':pos,'ret':ret,'notional':notional,'reason':reason})
        pos=0; entry=stop=target=np.nan; notional=0.0; entry_i=-1; cooldown=2
    for i in range(400,len(x)-1):
        r=x.iloc[i]; nxt=x.iloc[i+1]
        if cooldown>0: cooldown-=1
        if pos!=0:
            sh=(pos>0 and r.low<=stop) or (pos<0 and r.high>=stop)
            th=(pos>0 and r.high>=target) or (pos<0 and r.low<=target)
            if sh: close_trade(i,float(stop),'stop'); continue
            if th: close_trade(i,float(target),'target'); continue
            if i-entry_i>=s.max_hold: close_trade(i+1,float(nxt.open),'time'); continue
        if pos==0 and pending!=0:
            px=float(r.open)
            if np.isfinite(pending_atr) and pending_atr>0:
                pos=pending; entry=px; dist=s.stop_atr*pending_atr
                stop=entry-pos*dist; target=entry+pos*s.take_atr*pending_atr
                notional=min(lev_cap,risk_frac/max(dist/entry,1e-6))
                eq*=1-(cost_bps/10000.0)*notional; peak=max(peak,eq); maxdd=min(maxdd,eq/peak-1); entry_i=i
            pending=0; pending_atr=np.nan; continue
        if pos==0 and cooldown==0 and np.isfinite(r.atr) and r.atr>0:
            ls=bool(r.long_sig); ss=bool(r.short_sig)
            side=1 if ls and not ss else (-1 if ss and not ls else 0)
            if side_filter and side!=side_filter: side=0
            if side: pending=side; pending_atr=float(r.atr)
    if pos!=0: close_trade(len(x)-1,float(x.close.iloc[-1]),'end')
    t=pd.DataFrame(trades)
    if t.empty: return {'trades':0,'net_pct':0,'pf':0,'win_pct':0,'max_dd_pct':0,'avg_trade_bps':0,'longs':0,'shorts':0,'trade_rows':[] if collect else None}
    gains=t.loc[t.ret>0,'ret'].sum(); losses=-t.loc[t.ret<0,'ret'].sum()
    out={'trades':int(len(t)),'net_pct':float((eq-1)*100),'pf':float(gains/losses) if losses>0 else 99.0,'win_pct':float((t.ret>0).mean()*100),'max_dd_pct':float(-maxdd*100),'avg_trade_bps':float(t.ret.mean()*10000),'longs':int((t.side>0).sum()),'shorts':int((t.side<0).sum())}
    if collect: out['trade_rows']=t.to_dict('records')
    return out

def train_score(m):
    if m['trades']<80 or m['net_pct']<=0 or m['pf']<1.12 or m['max_dd_pct']>18: return -1e9
    return m['net_pct']+35*(m['pf']-1)+.1*m['win_pct']-.8*m['max_dd_pct']

def day100(trades):
    if not trades: return None
    t=pd.DataFrame(trades)
    # trade ret is fractional account return under conservative sizing; compound within each UTC day from $100.
    vals=[]
    for d,g in t.groupby('day'):
        ending=100.0
        peak=100.0; dd=0.0
        for r in g.ret:
            ending*=1+float(r); peak=max(peak,ending); dd=min(dd,ending/peak-1)
        vals.append({'day':d,'pnl':ending-100.0,'pct':ending-100.0,'trades':int(len(g)),'dd_pct':float(-dd*100)})
    v=pd.DataFrame(vals); p=v.pnl
    return {'active_days':int(len(v)),'median_pnl_usd':float(p.median()),'mean_pnl_usd':float(p.mean()),'p10_pnl_usd':float(p.quantile(.10)),'p90_pnl_usd':float(p.quantile(.90)),'best_day_usd':float(p.max()),'worst_day_usd':float(p.min()),'positive_day_pct':float((p>0).mean()*100),'median_trades_day':float(v.trades.median()),'max_daily_dd_pct':float(v.dd_pct.max()),'loss5_day_pct':float((p<=-5).mean()*100),'loss10_day_pct':float((p<=-10).mean()*100),'loss25_day_pct':float((p<=-25).mean()*100)}

def main():
    raw=load_data(); b=build_5m(raw); train=b[b.index<=SELECTION_END]; hold=b[b.index>=HOLDOUT_START]
    candidates={}; ranked=[]
    for s in SPECS:
        tr={str(c):simulate(train,s,c) for c in COSTS}; sc=train_score(tr['7.0'])
        candidates[s.name]={'spec':asdict(s),'train':tr,'score':sc}; ranked.append((sc,s.name))
    ranked.sort(reverse=True); selected=ranked[0][1] if ranked and ranked[0][0]>-1e8 else None
    holdout=None; gate=False; diag=None; daily=None
    if selected:
        s=next(z for z in SPECS if z.name==selected)
        h7=simulate(hold,s,7.0,collect=True); h12=simulate(hold,s,12.0); h20=simulate(hold,s,20.0)
        holdout={'7.0':{k:v for k,v in h7.items() if k!='trade_rows'},'12.0':h12,'20.0':h20}
        gate=(h7['trades']>=30 and h7['net_pct']>0 and h7['pf']>=1.15 and h7['max_dd_pct']<=15 and h12['net_pct']>0 and h20['net_pct']>0)
        diag={'long_only_7':simulate(hold,s,7.0,side_filter=1),'short_only_7':simulate(hold,s,7.0,side_filter=-1)}
        daily=day100(h7.get('trade_rows',[]))
    state={'version':'quantbot-v11-btc-microburst','purpose':'RESEARCH_ONLY_NO_LIVE','symbol':SYMBOL,'source':'Binance Vision USD-M BTCUSDT 1m aggregated to 5m with 15m regime','data_start':str(raw.index.min()),'data_end':str(raw.index.max()),'selection_end':str(SELECTION_END),'holdout_start':str(HOLDOUT_START),'candidate_count':len(SPECS),'selected':selected,'candidates':candidates,'holdout':holdout,'side_diagnostic':diag,'day100':daily,'holdout_gate_pass':bool(gate),'live_orders':False,'interpretation':'V11_MICROBURST_CANDIDATE' if gate else 'NO_V11_EDGE_YET'}
    STATE.parent.mkdir(parents=True,exist_ok=True); STATE.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps(state,indent=2))
if __name__=='__main__': main()
