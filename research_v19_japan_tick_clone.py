from __future__ import annotations

import io, json, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests

SYMBOL='BTCUSDT'
BASE='https://data.binance.vision/data/futures/um/daily/aggTrades'
STATE=Path('data/v19_japan_tick_clone_state.json')
FIT_DAYS=['2025-01-15','2025-03-15','2025-05-15','2025-07-15','2025-09-15','2025-11-15','2026-01-15','2026-03-15']
VAL_DAYS=['2026-05-15','2026-06-15','2026-07-15','2026-08-15']
COST_BPS=7.5  # intended maker-entry + taker-exit + slippage hurdle; no fill claim


def fetch_day(day):
    url=f'{BASE}/{SYMBOL}/{SYMBOL}-aggTrades-{day}.zip'
    r=requests.get(url,timeout=120); r.raise_for_status()
    parts=[]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name=z.namelist()[0]
        for raw in pd.read_csv(z.open(name),header=None,chunksize=500000,low_memory=False):
            if raw.shape[1]<7: continue
            raw=raw.iloc[:,:7].copy(); raw.columns=['id','price','qty','first','last','time','buyer_maker']
            for c in ['price','qty','time']: raw[c]=pd.to_numeric(raw[c],errors='coerce')
            raw=raw.dropna(subset=['price','qty','time'])
            if raw.empty: continue
            raw['ts']=pd.to_datetime(raw.time,unit='ms',utc=True); raw['sec']=raw.ts.dt.floor('1s')
            raw['quote']=raw.price*raw.qty
            bm=raw.buyer_maker.astype(str).str.lower().isin(['true','1'])
            raw['signed']=np.where(bm,-raw.quote,raw.quote); raw['n']=1
            g=raw.groupby('sec',sort=True).agg(open=('price','first'),high=('price','max'),low=('price','min'),close=('price','last'),quote=('quote','sum'),signed=('signed','sum'),trades=('n','sum'))
            parts.append(g)
    x=pd.concat(parts).sort_index()
    x=x.groupby(level=0,sort=True).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),quote=('quote','sum'),signed=('signed','sum'),trades=('trades','sum'))
    full=pd.date_range(x.index.min().floor('s'),x.index.max().ceil('s'),freq='1s',tz='UTC')
    x=x.reindex(full)
    x[['open','high','low','close']]=x[['open','high','low','close']].ffill()
    x[['quote','signed','trades']]=x[['quote','signed','trades']].fillna(0)
    x['day']=day
    return x


def features(x):
    y=x.copy(); q=y.quote
    for n in [2,5,15]:
        rs=y.signed.rolling(n,min_periods=n).sum(); rq=q.rolling(n,min_periods=n).sum()
        y[f'flow{n}']=rs/rq.replace(0,np.nan); y[f'speed{n}']=y.trades.rolling(n,min_periods=n).sum()/n
        y[f'notional{n}']=rq
    y['r3']=(y.close/y.close.shift(3)-1)*1e4; y['r10']=(y.close/y.close.shift(10)-1)*1e4
    y['accel']=y.speed2/y.speed15.replace(0,np.nan)
    # 15m regime from completed 15m bars only.
    q15=y.close.resample('15min',label='right',closed='right').last().dropna().to_frame('c')
    q15['e20']=q15.c.ewm(span=20,adjust=False).mean(); q15['e50']=q15.c.ewm(span=50,adjust=False).mean()
    q15['trend']=np.where((q15.c>q15.e20)&(q15.e20>q15.e50),1,np.where((q15.c<q15.e20)&(q15.e20<q15.e50),-1,0))
    y['trend15']=q15.trend.reindex(y.index,method='ffill').fillna(0)
    # Completed 5m local levels, previous 8 bars.
    b5=y.resample('5min',label='right',closed='right').agg({'high':'max','low':'min'}).dropna()
    b5['hi8']=b5.high.shift(1).rolling(8,min_periods=8).max(); b5['lo8']=b5.low.shift(1).rolling(8,min_periods=8).min()
    y['hi8']=b5.hi8.reindex(y.index,method='ffill'); y['lo8']=b5.lo8.reindex(y.index,method='ffill')
    # Testa-style tape quality proxy only (historical L2 unavailable): require active tape.
    y['tape_ok']=(y.speed15>=y.speed15.rolling(300,min_periods=60).median())&(y.notional5>=y.notional5.rolling(300,min_periods=60).median())
    return y


def signal_rows(y):
    jun_long=(y.flow5<-0.22)&(y.r10<-1.5)&(y.r3>0.25)&y.tape_ok
    jun_short=(y.flow5>0.22)&(y.r10>1.5)&(y.r3<-0.25)&y.tape_ok
    hans_long=(y.trend15>=0)&(y.close>y.hi8)&(y.flow2>0.15)&(y.flow5>0.10)&(y.r3>0.30)&(y.accel>1.10)&y.tape_ok
    hans_short=(y.trend15<=0)&(y.close<y.lo8)&(y.flow2<-0.15)&(y.flow5<-0.10)&(y.r3<-0.30)&(y.accel>1.10)&y.tape_ok
    out=pd.DataFrame(index=y.index); out['engine']=None; out['side']=0
    out.loc[jun_long,'engine']='jun'; out.loc[jun_long,'side']=1
    out.loc[jun_short,'engine']='jun'; out.loc[jun_short,'side']=-1
    # If both fire same second, Hansan continuation wins only when Jun absent.
    hmask=(out.side==0)&hans_long; out.loc[hmask,'engine']='hansan'; out.loc[hmask,'side']=1
    hmask=(out.side==0)&hans_short; out.loc[hmask,'engine']='hansan'; out.loc[hmask,'side']=-1
    return out


def simulate(y,sigs):
    rows=[]; i=0; idx=y.index; n=len(y)
    while i<n-2:
        side=int(sigs.side.iloc[i]) if pd.notna(sigs.side.iloc[i]) else 0
        eng=sigs.engine.iloc[i]
        if side==0 or eng is None: i+=1; continue
        entry_i=i+1; entry=float(y.open.iloc[entry_i]); target=9.0 if eng=='jun' else 12.0; maxhold=28 if eng=='jun' else 24
        exit_i=min(entry_i+maxhold,n-1); reason='time'; gross=side*(float(y.close.iloc[exit_i])/entry-1)*1e4
        for j in range(entry_i,min(entry_i+maxhold,n-1)+1):
            hi=float(y.high.iloc[j]); lo=float(y.low.iloc[j])
            stop_hit=(lo<=entry*(1-7e-4)) if side==1 else (hi>=entry*(1+7e-4))
            tgt_hit=(hi>=entry*(1+target/1e4)) if side==1 else (lo<=entry*(1-target/1e4))
            if stop_hit:
                exit_i=j; gross=-7.0; reason='hard_stop'; break
            if tgt_hit:
                exit_i=j; gross=target; reason='target'; break
            if j-entry_i>=8:
                fav=side*(float(y.close.iloc[j])/entry-1)*1e4
                if fav<0.7:
                    exit_i=j; gross=fav; reason='immediate_invalidation'; break
        net=gross-COST_BPS
        rows.append({'signal_ts':str(idx[i]),'entry_ts':str(idx[entry_i]),'exit_ts':str(idx[exit_i]),'day':str(idx[i].date()),'engine':eng,'side':side,'gross_bps':gross,'net_bps':net,'reason':reason})
        i=exit_i+2
    return pd.DataFrame(rows)


def metrics(t):
    if t is None or t.empty:return {'trades':0,'gross_mean_bps':None,'net_mean_bps':None,'pf_net':None,'win_net_pct':None,'net_usd_100':0.0}
    nets=t.net_bps.to_numpy(); wins=nets[nets>0]; losses=nets[nets<0]
    pf=float(wins.sum()/abs(losses.sum())) if len(losses) else (999.0 if len(wins) else None)
    return {'trades':int(len(t)),'gross_mean_bps':float(t.gross_bps.mean()),'net_mean_bps':float(t.net_bps.mean()),'pf_net':pf,'win_net_pct':float((t.net_bps>0).mean()*100),'net_usd_100':float(t.net_bps.sum()/10000*100)}


def run_days(days):
    alltr=[]; per={}
    for d in days:
        x=features(fetch_day(d)); s=signal_rows(x); t=simulate(x,s); per[d]={'all':metrics(t),'jun':metrics(t[t.engine=='jun']) if not t.empty else metrics(None),'hansan':metrics(t[t.engine=='hansan']) if not t.empty else metrics(None)}
        if not t.empty: alltr.append(t)
    z=pd.concat(alltr,ignore_index=True) if alltr else pd.DataFrame()
    return z,per


def main():
    fit,fit_per=run_days(FIT_DAYS); val,val_per=run_days(VAL_DAYS)
    out={'version':'quantbot-v19-japan-tick-clone','purpose':'PUBLIC_METHOD_CLONE_TICK_SCREEN_NO_LIVE','symbol':SYMBOL,'source':'Binance Vision USD-M daily aggTrades -> 1s tape','cost_hurdle_bps':COST_BPS,'fit_days':FIT_DAYS,'validation_days':VAL_DAYS,
         'method_map':{'jun':'5s aggressor-flow distortion + 10s stretch + 3s snapback + fast invalidation','hansan':'local 5m level break + 2s/5s aggressor flow + acceleration + fast invalidation','testa':'historical tape-activity EV proxy; true L2 filter reserved for forward paper'},
         'fit':{'all':metrics(fit),'jun':metrics(fit[fit.engine=='jun']) if not fit.empty else metrics(None),'hansan':metrics(fit[fit.engine=='hansan']) if not fit.empty else metrics(None),'per_day':fit_per},
         'validation':{'all':metrics(val),'jun':metrics(val[val.engine=='jun']) if not val.empty else metrics(None),'hansan':metrics(val[val.engine=='hansan']) if not val.empty else metrics(None),'per_day':val_per},
         'pass':False,'live_orders':False}
    fm=out['fit']['all']; vm=out['validation']['all']
    out['pass']=bool(fm['trades']>=50 and fm['net_mean_bps'] is not None and fm['net_mean_bps']>0 and fm['pf_net']>=1.10 and vm['trades']>=20 and vm['net_mean_bps'] is not None and vm['net_mean_bps']>0 and vm['pf_net']>=1.05)
    out['interpretation']='JAPAN_TICK_CLONE_EDGE' if out['pass'] else 'NO_DEFENSIBLE_JAPAN_TICK_EDGE_YET'
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))

if __name__=='__main__': main()
