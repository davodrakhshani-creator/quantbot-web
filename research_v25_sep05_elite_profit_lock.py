from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_v19_japan_tick_clone as v19
import research_v20_japan_event_clone as v20

DAY='2026-09-05'
STATE=Path('data/v25_sep05_elite_profit_lock_state.json')
NOTIONAL=100.0

TIERS={
 'strict':{'jun_flow5':0.38,'jun_r10':3.5,'jun_r3':0.50,'han_flow2':0.30,'han_flow5':0.18,'han_r3':0.50,'han_accel':1.30,'tape_ratio':1.10,'cooldown':180},
 'elite': {'jun_flow5':0.48,'jun_r10':5.0,'jun_r3':0.70,'han_flow2':0.40,'han_flow5':0.25,'han_r3':0.75,'han_accel':1.55,'tape_ratio':1.20,'cooldown':300},
 'ultra': {'jun_flow5':0.58,'jun_r10':6.5,'jun_r3':0.90,'han_flow2':0.50,'han_flow5':0.32,'han_r3':1.00,'han_accel':1.80,'tape_ratio':1.35,'cooldown':480},
}
EXITS=[
 {'name':'MM_arm12_stop20','cost':4.0,'arm':12.0,'trail':5.0,'stop':20.0,'maxhold':1800},
 {'name':'MM_arm16_stop24','cost':4.0,'arm':16.0,'trail':6.0,'stop':24.0,'maxhold':2700},
 {'name':'MM_arm20_stop28','cost':4.0,'arm':20.0,'trail':7.0,'stop':28.0,'maxhold':3600},
 {'name':'MT_arm20_stop24','cost':7.5,'arm':20.0,'trail':6.0,'stop':24.0,'maxhold':2700},
 {'name':'MT_arm24_stop30','cost':7.5,'arm':24.0,'trail':8.0,'stop':30.0,'maxhold':3600},
]

def elite_signals(y,t):
    base=v20.events(y).copy()
    med_speed=y.speed15.rolling(600,min_periods=120).median()
    med_not=y.notional5.rolling(600,min_periods=120).median()
    tape=(y.speed15>=t['tape_ratio']*med_speed)&(y.notional5>=t['tape_ratio']*med_not)
    out=pd.DataFrame(index=y.index); out['engine']=None; out['side']=0
    bj=(base.engine=='jun')
    junq=(y.flow5.abs()>=t['jun_flow5'])&(y.r10.abs()>=t['jun_r10'])&(y.r3.abs()>=t['jun_r3'])&tape
    bh=(base.engine=='hansan')
    hanq=(y.flow2.abs()>=t['han_flow2'])&(y.flow5.abs()>=t['han_flow5'])&(y.r3.abs()>=t['han_r3'])&(y.accel>=t['han_accel'])&tape
    m=bj&junq; out.loc[m,'engine']='jun'; out.loc[m,'side']=base.loc[m,'side']
    m=bh&hanq; out.loc[m,'engine']='hansan'; out.loc[m,'side']=base.loc[m,'side']
    return out

def simulate(y,sigs,cfg,cooldown):
    rows=[]; i=0; n=len(y); idx=y.index; last=-10**9
    while i<n-2:
        side=int(sigs.side.iloc[i]); eng=sigs.engine.iloc[i]
        if side==0 or eng is None or i-last<cooldown:
            i+=1; continue
        last=i; ei=i+1; entry=float(y.open.iloc[ei]); armed=False; peak=-1e9
        xi=None; gross=None; reason=None; end=min(ei+cfg['maxhold'],n-1)
        for j in range(ei,end+1):
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
            xi=end; gross=side*(float(y.close.iloc[xi])/entry-1)*1e4; reason='time_or_eod_force_close'
        net=gross-cfg['cost']
        rows.append({'signal_ts':str(idx[i]),'engine':eng,'side':side,'gross_bps':gross,'net_bps':net,'armed':armed,'mfe_bps':peak,'reason':reason})
        i=max(xi+1,i+1)
    return pd.DataFrame(rows)

def met(t):
    if t.empty:return {'trades':0,'net_usd_100':0.0,'gross_mean_bps':None,'net_mean_bps':None,'pf':None,'win_pct':None,'stops':0,'profit_locks':0,'forced':0}
    a=t.net_bps.to_numpy(); w=a[a>0]; l=a[a<0]
    return {'trades':int(len(t)),'net_usd_100':float(a.sum()/100),'gross_mean_bps':float(t.gross_bps.mean()),'net_mean_bps':float(a.mean()),
            'pf':float(w.sum()/abs(l.sum())) if len(l) else (999.0 if len(w) else None),'win_pct':float((a>0).mean()*100),
            'stops':int((t.reason=='catastrophic_stop').sum()),'profit_locks':int((t.reason=='profit_lock').sum()),'forced':int((t.reason=='time_or_eod_force_close').sum()),
            'armed_pct':float(t.armed.mean()*100),'median_mfe_bps':float(t.mfe_bps.median()),'best_net_bps':float(t.net_bps.max()),'worst_net_bps':float(t.net_bps.min()),
            'jun_trades':int((t.engine=='jun').sum()),'hansan_trades':int((t.engine=='hansan').sum())}

def main():
    y=v19.features(v19.fetch_day(DAY)); out={'version':'quantbot-v25-sep05-elite-profit-lock','purpose':'ONE_DAY_ENTRY_QUALITY_DIAGNOSTIC_NO_LIVE','day':DAY,'starting_account_usd':100.0,'results':{},'live_orders':False}
    for tn,tier in TIERS.items():
        s=elite_signals(y,tier)
        for cfg in EXITS:
            key=f'{tn}__{cfg["name"]}'
            tr=simulate(y,s,cfg,tier['cooldown']); out['results'][key]={'tier':tn,'exit':cfg,'metrics':met(tr)}
    candidates=[(k,v['metrics']) for k,v in out['results'].items() if v['metrics']['trades']>=3]
    if candidates:
        k,m=max(candidates,key=lambda z:z[1]['net_usd_100']); out['best_one_day_only']={'name':k,'metrics':m,'ending_equity_usd':100+m['net_usd_100']}
    out['warning']='Single-day diagnostic only; selecting on Sep 5 overfits that day. Any survivor must be frozen before Sep 6 and other unseen-day checks.'
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))

if __name__=='__main__':main()
