from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_v19_japan_tick_clone as v19
import research_v20_japan_event_clone as v20

DAY='2026-09-05'
STATE=Path('data/v24_sep05_profit_lock_state.json')
NOTIONAL=100.0
# Diagnostic only. No live orders.
# Profit exits are armed only after gross profit exceeds fee by a safety margin.
# Catastrophic stop and end-of-day force-close remain allowed to avoid infinite-risk bias.
CONFIGS=[
    {'name':'P0_mm_2xfee_stop12','cost':4.0,'arm':8.0,'trail':3.0,'stop':12.0,'maxhold':900},
    {'name':'P1_mm_2xfee_stop16','cost':4.0,'arm':10.0,'trail':4.0,'stop':16.0,'maxhold':1800},
    {'name':'P2_mm_3xfee_stop20','cost':4.0,'arm':12.0,'trail':5.0,'stop':20.0,'maxhold':1800},
    {'name':'P3_mt_2xfee_stop16','cost':7.5,'arm':15.0,'trail':5.0,'stop':16.0,'maxhold':1800},
    {'name':'P4_mt_2p5xfee_stop20','cost':7.5,'arm':19.0,'trail':6.0,'stop':20.0,'maxhold':2700},
    {'name':'P5_mt_3xfee_stop24','cost':7.5,'arm':23.0,'trail':7.0,'stop':24.0,'maxhold':3600},
]
COOLDOWN=90

def simulate(y, sigs, cfg):
    rows=[]; i=0; n=len(y); idx=y.index; last=-10**9
    while i<n-2:
        side=int(sigs.side.iloc[i]); eng=sigs.engine.iloc[i]
        if side==0 or eng is None or i-last<COOLDOWN:
            i+=1; continue
        last=i
        ei=i+1; entry=float(y.open.iloc[ei])
        armed=False; peak=-1e9; xi=None; gross=None; reason=None
        end=min(ei+cfg['maxhold'],n-1)
        j=ei
        while j<=end:
            hi=float(y.high.iloc[j]); lo=float(y.low.iloc[j]); close=float(y.close.iloc[j])
            fav_hi=(hi/entry-1)*1e4 if side==1 else (entry/lo-1)*1e4
            adverse=(entry/lo-1)*1e4 if side==1 else (hi/entry-1)*1e4
            peak=max(peak,fav_hi)
            # Catastrophic stop is always allowed.
            if adverse>=cfg['stop']:
                xi=j; gross=-cfg['stop']; reason='catastrophic_stop'; break
            if (not armed) and peak>=cfg['arm']:
                armed=True
            if armed:
                cur=side*(close/entry-1)*1e4
                # Profit-lock: after arming, trail from MFE but do not voluntarily exit below fee + 1 bp.
                lock_floor=max(cfg['cost']+1.0, peak-cfg['trail'])
                if cur<=lock_floor and cur>=cfg['cost']+1.0:
                    xi=j; gross=cur; reason='profit_lock'; break
            j+=1
        if xi is None:
            xi=end
            gross=side*(float(y.close.iloc[xi])/entry-1)*1e4
            reason='time_or_eod_force_close'
        net=gross-cfg['cost']
        rows.append({'signal_ts':str(idx[i]),'entry_ts':str(idx[ei]),'exit_ts':str(idx[xi]),'engine':eng,'side':side,
                     'gross_bps':gross,'net_bps':net,'armed':armed,'mfe_bps':peak,'reason':reason})
        i=max(xi+1,i+1)
    return pd.DataFrame(rows)

def metrics(t):
    if t.empty:
        return {'trades':0,'net_usd_100':0.0,'gross_mean_bps':None,'net_mean_bps':None,'pf':None,'win_pct':None,'stops':0,'profit_locks':0,'forced':0}
    a=t.net_bps.to_numpy(); w=a[a>0]; l=a[a<0]
    pf=float(w.sum()/abs(l.sum())) if len(l) else (999.0 if len(w) else None)
    return {'trades':int(len(t)),'net_usd_100':float(a.sum()/100),'gross_mean_bps':float(t.gross_bps.mean()),
            'net_mean_bps':float(a.mean()),'pf':pf,'win_pct':float((a>0).mean()*100),
            'stops':int((t.reason=='catastrophic_stop').sum()),'profit_locks':int((t.reason=='profit_lock').sum()),
            'forced':int((t.reason=='time_or_eod_force_close').sum()),'armed_pct':float(t.armed.mean()*100),
            'median_mfe_bps':float(t.mfe_bps.median()),'best_trade_net_bps':float(t.net_bps.max()),'worst_trade_net_bps':float(t.net_bps.min())}

def main():
    y=v19.features(v19.fetch_day(DAY)); s=v20.events(y)
    out={'version':'quantbot-v24-sep05-profit-lock','purpose':'ONE_DAY_DIAGNOSTIC_NO_LIVE','day':DAY,'symbol':'BTCUSDT',
         'starting_account_usd':100.0,'rules':'Japanese public-method event entries; fewer/wider catastrophic stops; profit exits only after gross profit materially exceeds fee; force-close at horizon/day end retained for risk realism',
         'configs':{},'live_orders':False}
    for cfg in CONFIGS:
        t=simulate(y,s,cfg); m=metrics(t); out['configs'][cfg['name']]={'params':cfg,'metrics':m}
    valid=[(name,d['metrics']) for name,d in out['configs'].items() if d['metrics']['trades']>0]
    if valid:
        best=max(valid,key=lambda x:x[1]['net_usd_100'])
        out['best_one_day_only']={'name':best[0],'metrics':best[1],'ending_equity_usd':100.0+best[1]['net_usd_100']}
    out['warning']='Sep 5 is a single diagnostic day and cannot establish robustness; any positive result must be frozen and checked on other untouched days.'
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))

if __name__=='__main__': main()
