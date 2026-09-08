from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_v19_japan_tick_clone as v19
import research_v20_japan_event_clone as v20

DAY='2026-09-05'
STATE=Path('data/v26_sep05_full_patience_state.json')

CONFIGS=[
 {'name':'BASE_MM_arm12_stop20','tier':'base','cost':4.0,'arm':12.0,'trail':5.0,'stop':20.0,'cooldown':120},
 {'name':'BASE_MM_arm16_stop28','tier':'base','cost':4.0,'arm':16.0,'trail':6.0,'stop':28.0,'cooldown':180},
 {'name':'BASE_MT_arm20_stop28','tier':'base','cost':7.5,'arm':20.0,'trail':7.0,'stop':28.0,'cooldown':180},
 {'name':'STRICT_MM_arm12_stop20','tier':'strict','cost':4.0,'arm':12.0,'trail':5.0,'stop':20.0,'cooldown':180},
 {'name':'STRICT_MM_arm16_stop28','tier':'strict','cost':4.0,'arm':16.0,'trail':6.0,'stop':28.0,'cooldown':240},
 {'name':'STRICT_MT_arm20_stop30','tier':'strict','cost':7.5,'arm':20.0,'trail':7.0,'stop':30.0,'cooldown':240},
]

def strict_mask(y,base):
    med_speed=y.speed15.rolling(600,min_periods=120).median()
    med_not=y.notional5.rolling(600,min_periods=120).median()
    tape=(y.speed15>=1.10*med_speed)&(y.notional5>=1.10*med_not)
    j=(base.engine=='jun')&(y.flow5.abs()>=0.38)&(y.r10.abs()>=3.5)&(y.r3.abs()>=0.50)&tape
    h=(base.engine=='hansan')&(y.flow2.abs()>=0.30)&(y.flow5.abs()>=0.18)&(y.r3.abs()>=0.50)&(y.accel>=1.30)&tape
    out=pd.DataFrame(index=y.index); out['engine']=None; out['side']=0
    m=j|h; out.loc[m,'engine']=base.loc[m,'engine']; out.loc[m,'side']=base.loc[m,'side']
    return out

def simulate(y,sigs,cfg):
    rows=[]; i=0; n=len(y); idx=y.index; last=-10**9
    while i<n-2:
        side=int(sigs.side.iloc[i]); eng=sigs.engine.iloc[i]
        if side==0 or eng is None or i-last<cfg['cooldown']:
            i+=1; continue
        last=i; ei=i+1; entry=float(y.open.iloc[ei]); armed=False; peak=-1e9
        xi=None; gross=None; reason=None
        # No intraday time exit: hold until profit lock, catastrophic stop, or end of day.
        for j in range(ei,n):
            hi=float(y.high.iloc[j]); lo=float(y.low.iloc[j]); close=float(y.close.iloc[j])
            fav=(hi/entry-1)*1e4 if side==1 else (entry/lo-1)*1e4
            adverse=(entry/lo-1)*1e4 if side==1 else (hi/entry-1)*1e4
            peak=max(peak,fav)
            if adverse>=cfg['stop']:
                xi=j; gross=-cfg['stop']; reason='catastrophic_stop'; break
            if not armed and peak>=cfg['arm']: armed=True
            if armed:
                cur=side*(close/entry-1)*1e4
                floor=max(cfg['cost']+2.0,peak-cfg['trail'])
                if cur<=floor and cur>=cfg['cost']+2.0:
                    xi=j; gross=cur; reason='profit_lock'; break
        if xi is None:
            xi=n-1; gross=side*(float(y.close.iloc[xi])/entry-1)*1e4; reason='end_of_day_force_close'
        net=gross-cfg['cost']
        rows.append({'entry_ts':str(idx[ei]),'exit_ts':str(idx[xi]),'engine':eng,'side':side,'gross_bps':gross,'net_bps':net,'mfe_bps':peak,'armed':armed,'reason':reason})
        i=xi+1
    return pd.DataFrame(rows)

def met(t):
    if t.empty:return {'trades':0,'net_usd_100':0.0,'net_mean_bps':None,'pf':None,'win_pct':None,'stops':0,'profit_locks':0,'eod':0}
    a=t.net_bps.to_numpy(); w=a[a>0]; l=a[a<0]
    pf=float(w.sum()/abs(l.sum())) if len(l) else (999.0 if len(w) else None)
    return {'trades':int(len(t)),'net_usd_100':float(a.sum()/100),'ending_equity_usd':float(100+a.sum()/100),
            'gross_mean_bps':float(t.gross_bps.mean()),'net_mean_bps':float(a.mean()),'pf':pf,'win_pct':float((a>0).mean()*100),
            'stops':int((t.reason=='catastrophic_stop').sum()),'profit_locks':int((t.reason=='profit_lock').sum()),'eod':int((t.reason=='end_of_day_force_close').sum()),
            'armed_pct':float(t.armed.mean()*100),'median_mfe_bps':float(t.mfe_bps.median()),'best_net_bps':float(t.net_bps.max()),'worst_net_bps':float(t.net_bps.min()),
            'jun_trades':int((t.engine=='jun').sum()),'hansan_trades':int((t.engine=='hansan').sum())}

def main():
    y=v19.features(v19.fetch_day(DAY)); base=v20.events(y); strict=strict_mask(y,base)
    out={'version':'quantbot-v26-sep05-full-patience','purpose':'ONE_DAY_NO_INTRADAY_TIME_EXIT_DIAGNOSTIC_NO_LIVE','day':DAY,'starting_account_usd':100.0,'results':{},'live_orders':False,
         'rule':'Profit-taking only after gross profit materially exceeds fee; otherwise hold until catastrophic stop or end of 24h day.'}
    for cfg in CONFIGS:
        sig=base if cfg['tier']=='base' else strict
        tr=simulate(y,sig,cfg); out['results'][cfg['name']]={'params':cfg,'metrics':met(tr),'trades_detail':tr.to_dict('records')}
    valid=[(k,v['metrics']) for k,v in out['results'].items() if v['metrics']['trades']>0]
    if valid:
        k,m=max(valid,key=lambda z:z[1]['net_usd_100']); out['best_one_day_only']={'name':k,'metrics':m}
    out['warning']='Single-day diagnostic only. Catastrophic stops remain mandatory; refusing all losing exits would create unlimited holding/liquidation bias. Any survivor must be frozen and tested on unseen days.'
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))

if __name__=='__main__':main()
