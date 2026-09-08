from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_v19_japan_tick_clone as v19

STATE=Path('data/v20_japan_event_clone_state.json')
COSTS={'maker_maker':4.0,'maker_taker':7.5}


def events(y):
    # Jun: arm on a true distortion, enter only on the FIRST snapback transition.
    dl=((y.flow5<-0.30)&(y.r10<-2.5)).rolling(6,min_periods=1).max().shift(1).fillna(0)>0
    ds=((y.flow5>0.30)&(y.r10>2.5)).rolling(6,min_periods=1).max().shift(1).fillna(0)>0
    jl=dl&(y.r3>0.35)&(y.r3.shift(1)<=0.35)&(y.flow2>0.05)&y.tape_ok
    js=ds&(y.r3<-0.35)&(y.r3.shift(1)>=-0.35)&(y.flow2<-0.05)&y.tape_ok
    # Hansan: enter only on first crossing of the completed local level.
    cross_hi=(y.close>y.hi8)&(y.close.shift(1)<=y.hi8.shift(1))
    cross_lo=(y.close<y.lo8)&(y.close.shift(1)>=y.lo8.shift(1))
    hl=cross_hi&(y.trend15>=0)&(y.flow2>0.20)&(y.flow5>0.12)&(y.r3>0.30)&(y.accel>1.15)&y.tape_ok
    hs=cross_lo&(y.trend15<=0)&(y.flow2<-0.20)&(y.flow5<-0.12)&(y.r3<-0.30)&(y.accel>1.15)&y.tape_ok
    out=pd.DataFrame(index=y.index); out['engine']=None; out['side']=0
    out.loc[jl,'engine']='jun'; out.loc[jl,'side']=1; out.loc[js,'engine']='jun'; out.loc[js,'side']=-1
    m=(out.side==0)&hl; out.loc[m,'engine']='hansan'; out.loc[m,'side']=1
    m=(out.side==0)&hs; out.loc[m,'engine']='hansan'; out.loc[m,'side']=-1
    return out


def simulate(y,sigs,cost):
    rows=[]; i=0; n=len(y); idx=y.index; last_engine_ts={'jun':-10**9,'hansan':-10**9}
    while i<n-2:
        side=int(sigs.side.iloc[i]); eng=sigs.engine.iloc[i]
        if side==0 or eng is None or i-last_engine_ts[eng]<30: i+=1; continue
        last_engine_ts[eng]=i; ei=i+1; entry=float(y.open.iloc[ei]); target=9.0 if eng=='jun' else 12.0; maxhold=28 if eng=='jun' else 24
        xi=min(ei+maxhold,n-1); reason='time'; gross=side*(float(y.close.iloc[xi])/entry-1)*1e4
        for j in range(ei,min(ei+maxhold,n-1)+1):
            hi=float(y.high.iloc[j]); lo=float(y.low.iloc[j])
            stop=(lo<=entry*(1-7e-4)) if side==1 else (hi>=entry*(1+7e-4))
            tgt=(hi>=entry*(1+target/1e4)) if side==1 else (lo<=entry*(1-target/1e4))
            if stop: xi=j; gross=-7.0; reason='hard_stop'; break
            if tgt: xi=j; gross=target; reason='target'; break
            if j-ei>=8:
                fav=side*(float(y.close.iloc[j])/entry-1)*1e4
                if fav<0.7: xi=j; gross=fav; reason='immediate_invalidation'; break
        rows.append({'day':str(idx[i].date()),'engine':eng,'side':side,'gross_bps':gross,'net_bps':gross-cost,'reason':reason})
        i=xi+2
    return pd.DataFrame(rows)


def met(t):
    if t is None or t.empty:return {'trades':0,'gross_mean_bps':None,'net_mean_bps':None,'pf':None,'win_pct':None,'net_usd_100':0.0}
    a=t.net_bps.to_numpy(); w=a[a>0]; l=a[a<0]
    return {'trades':int(len(t)),'gross_mean_bps':float(t.gross_bps.mean()),'net_mean_bps':float(t.net_bps.mean()),'pf':float(w.sum()/abs(l.sum())) if len(l) else (999.0 if len(w) else None),'win_pct':float((a>0).mean()*100),'net_usd_100':float(a.sum()/100)}


def run(days):
    results={k:[] for k in COSTS}; per={}
    for d in days:
        y=v19.features(v19.fetch_day(d)); s=events(y); per[d]={}
        for name,c in COSTS.items():
            t=simulate(y,s,c); results[name].append(t); per[d][name]={'all':met(t),'jun':met(t[t.engine=='jun']) if not t.empty else met(None),'hansan':met(t[t.engine=='hansan']) if not t.empty else met(None)}
    agg={}
    for name,lst in results.items():
        z=pd.concat([x for x in lst if not x.empty],ignore_index=True) if any(not x.empty for x in lst) else pd.DataFrame()
        agg[name]={'all':met(z),'jun':met(z[z.engine=='jun']) if not z.empty else met(None),'hansan':met(z[z.engine=='hansan']) if not z.empty else met(None)}
    return agg,per


def main():
    fit,fitper=run(v19.FIT_DAYS); val,valper=run(v19.VAL_DAYS)
    out={'version':'quantbot-v20-japan-event-clone','purpose':'EVENT_DRIVEN_PUBLIC_METHOD_CLONE_NO_LIVE','symbol':v19.SYMBOL,'source':'Binance Vision USD-M aggTrades -> 1s','fit_days':v19.FIT_DAYS,'validation_days':v19.VAL_DAYS,'cost_models_bps':COSTS,'fit':fit,'validation':val,'fit_per_day':fitper,'validation_per_day':valper,'live_orders':False}
    mmf=fit['maker_maker']['all']; mmv=val['maker_maker']['all']; mtf=fit['maker_taker']['all']; mtv=val['maker_taker']['all']
    out['maker_maker_candidate']=bool(mmf['trades']>=30 and mmf['net_mean_bps'] is not None and mmf['net_mean_bps']>0 and mmf['pf']>=1.05 and mmv['trades']>=15 and mmv['net_mean_bps'] is not None and mmv['net_mean_bps']>0)
    out['maker_taker_candidate']=bool(mtf['trades']>=30 and mtf['net_mean_bps'] is not None and mtf['net_mean_bps']>0 and mtf['pf']>=1.05 and mtv['trades']>=15 and mtv['net_mean_bps'] is not None and mtv['net_mean_bps']>0)
    out['interpretation']='MAKER_TAKER_EDGE' if out['maker_taker_candidate'] else ('DUAL_MAKER_RESEARCH_CANDIDATE' if out['maker_maker_candidate'] else 'NO_EVENT_CLONE_EDGE')
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))

if __name__=='__main__': main()
