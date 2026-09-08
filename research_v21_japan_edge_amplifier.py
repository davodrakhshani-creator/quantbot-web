from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_v19_japan_tick_clone as v19
import research_v20_japan_event_clone as v20

STATE=Path('data/v21_japan_edge_amplifier_state.json')
HORIZONS=[15,30,60,120]
COSTS={'maker_maker':4.0,'maker_taker':7.5}
CONFIRM_SEC=8
CONFIRM_BPS=0.7
COOLDOWN=60


def candidate_events(y):
    s=v20.events(y)
    rows=[]; last={'jun':-10**9,'hansan':-10**9}
    for i in range(len(y)-125):
        side=int(s.side.iloc[i]); eng=s.engine.iloc[i]
        if side==0 or eng is None or i-last[eng]<COOLDOWN: continue
        last[eng]=i
        ei=i+1; entry=float(y.open.iloc[ei])
        # Japanese-style immediate feedback: keep only events showing expected move quickly.
        maxfav=-1e9
        for j in range(ei,min(ei+CONFIRM_SEC,len(y)-1)+1):
            if side==1: fav=(float(y.high.iloc[j])/entry-1)*1e4
            else: fav=(entry/float(y.low.iloc[j])-1)*1e4
            maxfav=max(maxfav,fav)
        if maxfav<CONFIRM_BPS: continue
        rec={'signal_i':i,'entry_i':ei,'day':str(y.index[i].date()),'engine':eng,'side':side,'entry':entry,'confirm_max_bps':maxfav}
        for h in HORIZONS:
            px=float(y.close.iloc[ei+h]); rec[f'gross_{h}']=side*(px/entry-1)*1e4
        rows.append(rec)
    return pd.DataFrame(rows)


def metrics(t,h,cost):
    if t is None or t.empty:return {'events':0,'gross_mean_bps':None,'net_mean_bps':None,'median_net_bps':None,'pf':None,'win_pct':None,'net_usd_100':0.0}
    gross=t[f'gross_{h}'].to_numpy(); net=gross-cost; w=net[net>0]; l=net[net<0]
    return {'events':int(len(t)),'gross_mean_bps':float(gross.mean()),'net_mean_bps':float(net.mean()),'median_net_bps':float(np.median(net)),
            'pf':float(w.sum()/abs(l.sum())) if len(l) else (999.0 if len(w) else None),'win_pct':float((net>0).mean()*100),'net_usd_100':float(net.sum()/100)}


def gather(days):
    xs=[]; per={}
    for d in days:
        y=v19.features(v19.fetch_day(d)); t=candidate_events(y); per[d]=int(len(t));
        if not t.empty: xs.append(t)
    return (pd.concat(xs,ignore_index=True) if xs else pd.DataFrame()),per


def main():
    fit,fit_counts=gather(v19.FIT_DAYS); val,val_counts=gather(v19.VAL_DAYS)
    grid={}
    for cost_name,cost in COSTS.items():
        grid[cost_name]={}
        for eng in ['all','jun','hansan']:
            tf=fit if eng=='all' else (fit[fit.engine==eng] if not fit.empty else pd.DataFrame())
            grid[cost_name][eng]={str(h):metrics(tf,h,cost) for h in HORIZONS}
    # Preselect on fit only, independently by cost model.
    selected={}
    validation={}
    for cost_name,cost in COSTS.items():
        best=None
        for eng in ['all','jun','hansan']:
            for h in HORIZONS:
                m=grid[cost_name][eng][str(h)]
                if m['events']<30 or m['net_mean_bps'] is None: continue
                score=m['net_mean_bps'] + 2.0*((m['pf'] or 0)-1)
                if best is None or score>best['score']: best={'engine':eng,'horizon_sec':h,'score':score,'fit':m}
        selected[cost_name]=best
        if best:
            tv=val if best['engine']=='all' else (val[val.engine==best['engine']] if not val.empty else pd.DataFrame())
            validation[cost_name]=metrics(tv,best['horizon_sec'],cost)
        else: validation[cost_name]=None
    out={'version':'quantbot-v21-japan-edge-amplifier','purpose':'PUBLIC_METHOD_SIGNAL_CONFIRMATION_PLUS_LONGER_HOLD_NO_LIVE','symbol':v19.SYMBOL,'source':'Binance Vision USD-M aggTrades -> 1s','confirm_sec':CONFIRM_SEC,'confirm_bps':CONFIRM_BPS,'horizons_sec':HORIZONS,'cost_models_bps':COSTS,'fit_event_counts':fit_counts,'validation_event_counts':val_counts,'fit_grid':grid,'selected_fit_only':selected,'validation':validation,'live_orders':False}
    passes={}
    for k,b in selected.items():
        vm=validation.get(k); passes[k]=bool(b and b['fit']['events']>=30 and b['fit']['net_mean_bps']>0 and b['fit']['pf']>=1.05 and vm and vm['events']>=15 and vm['net_mean_bps']>0 and vm['pf']>=1.02)
    out['passes']=passes
    out['interpretation']='AMPLIFIED_EDGE_CANDIDATE' if any(passes.values()) else 'NO_AMPLIFIED_JAPAN_EDGE'
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))

if __name__=='__main__': main()
