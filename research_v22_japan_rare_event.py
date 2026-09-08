from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_v19_japan_tick_clone as v19
import research_v20_japan_event_clone as v20

STATE=Path('data/v22_japan_rare_event_state.json')
COSTS={'maker_maker':4.0,'maker_taker':7.5}
HORIZONS=[30,60,120,300,600]
CONFIRM_SEC=8
CONFIRM_BPS=0.7
COOLDOWN=90

QUALITY={
    'base': {
        'jun': dict(flow5=.30,r10=2.5,r3=.35,notional=1.00,speed=1.00),
        'hansan': dict(flow2=.20,flow5=.12,r3=.30,accel=1.15,breakout=.0,notional=1.00,speed=1.00),
    },
    'strict': {
        'jun': dict(flow5=.38,r10=3.5,r3=.50,notional=1.25,speed=1.10),
        'hansan': dict(flow2=.30,flow5=.18,r3=.50,accel=1.35,breakout=.5,notional=1.25,speed=1.10),
    },
    'elite': {
        'jun': dict(flow5=.48,r10=5.0,r3=.70,notional=1.50,speed=1.20),
        'hansan': dict(flow2=.40,flow5=.25,r3=.75,accel=1.60,breakout=1.0,notional=1.50,speed=1.20),
    },
}


def prep_quality(y):
    z=y.copy()
    z['notional5_med']=z.notional5.rolling(300,min_periods=60).median()
    z['speed15_med']=z.speed15.rolling(300,min_periods=60).median()
    z['notional_ratio']=z.notional5/z.notional5_med.replace(0,np.nan)
    z['speed_ratio']=z.speed15/z.speed15_med.replace(0,np.nan)
    z['breakout_up_bps']=(z.close/z.hi8-1)*1e4
    z['breakout_dn_bps']=(z.lo8/z.close-1)*1e4
    return z


def quality_ok(y,i,eng,side,tier):
    q=QUALITY[tier][eng]
    r=y.iloc[i]
    if not np.isfinite(r.notional_ratio) or not np.isfinite(r.speed_ratio): return False
    if r.notional_ratio<q['notional'] or r.speed_ratio<q['speed']: return False
    if eng=='jun':
        return abs(float(r.flow5))>=q['flow5'] and abs(float(r.r10))>=q['r10'] and abs(float(r.r3))>=q['r3']
    br=float(r.breakout_up_bps if side==1 else r.breakout_dn_bps)
    return abs(float(r.flow2))>=q['flow2'] and abs(float(r.flow5))>=q['flow5'] and abs(float(r.r3))>=q['r3'] and float(r.accel)>=q['accel'] and br>=q['breakout']


def confirmed_events(y,tier):
    s=v20.events(y)
    rows=[]; last={'jun':-10**9,'hansan':-10**9}
    n=len(y); idx=y.index
    for i in range(n-620):
        side=int(s.side.iloc[i]); eng=s.engine.iloc[i]
        if side==0 or eng is None or i-last[eng]<COOLDOWN: continue
        if not quality_ok(y,i,eng,side,tier): continue
        last[eng]=i
        signal_px=float(y.close.iloc[i])
        confirm_i=None
        # No lookahead entry: confirmation must happen first; entry is NEXT second.
        for j in range(i+1,min(i+CONFIRM_SEC,n-2)+1):
            fav=side*(float(y.close.iloc[j])/signal_px-1)*1e4
            if fav>=CONFIRM_BPS:
                confirm_i=j; break
        if confirm_i is None: continue
        entry_i=confirm_i+1
        entry=float(y.open.iloc[entry_i])
        rec={
            'day':str(idx[i].date()),'signal_ts':str(idx[i]),'confirm_ts':str(idx[confirm_i]),
            'entry_ts':str(idx[entry_i]),'engine':eng,'side':side,'tier':tier,
            'signal_px':signal_px,'entry':entry,'notional_ratio':float(y.notional_ratio.iloc[i]),
            'speed_ratio':float(y.speed_ratio.iloc[i]),'flow5':float(y.flow5.iloc[i]),
            'r10':float(y.r10.iloc[i]),'r3':float(y.r3.iloc[i]),'accel':float(y.accel.iloc[i]) if np.isfinite(y.accel.iloc[i]) else None,
        }
        for h in HORIZONS:
            xi=min(entry_i+h,n-1)
            rec[f'gross_{h}']=side*(float(y.close.iloc[xi])/entry-1)*1e4
        rows.append(rec)
    return pd.DataFrame(rows)


def metrics(t,h,cost):
    if t is None or t.empty:
        return {'events':0,'gross_mean_bps':None,'net_mean_bps':None,'median_net_bps':None,'pf':None,'win_pct':None,'net_usd_100':0.0}
    gross=t[f'gross_{h}'].to_numpy(dtype=float); net=gross-cost
    w=net[net>0]; l=net[net<0]
    return {
        'events':int(len(t)),'gross_mean_bps':float(gross.mean()),'net_mean_bps':float(net.mean()),
        'median_net_bps':float(np.median(net)),'pf':float(w.sum()/abs(l.sum())) if len(l) else (999.0 if len(w) else None),
        'win_pct':float((net>0).mean()*100),'net_usd_100':float(net.sum()/100),
    }


def gather(days):
    allrows=[]; perday={}
    for d in days:
        y=prep_quality(v19.features(v19.fetch_day(d)))
        perday[d]={}
        for tier in QUALITY:
            t=confirmed_events(y,tier)
            perday[d][tier]=int(len(t))
            if not t.empty: allrows.append(t)
    return (pd.concat(allrows,ignore_index=True) if allrows else pd.DataFrame()),perday


def day_consistency(t,h,cost):
    if t is None or t.empty:return {'positive_days':0,'days':0,'positive_day_pct':0.0}
    vals=[]
    for _,g in t.groupby('day'):
        vals.append(float((g[f'gross_{h}']-cost).mean()))
    return {'positive_days':int(sum(v>0 for v in vals)),'days':int(len(vals)),'positive_day_pct':float(100*sum(v>0 for v in vals)/len(vals)) if vals else 0.0}


def main():
    fit,fit_counts=gather(v19.FIT_DAYS)
    grid={}; selected={}; validation={}
    for cname,cost in COSTS.items():
        grid[cname]={}; best=None
        for tier in QUALITY:
            grid[cname][tier]={}
            for eng in ['all','jun','hansan']:
                tf=fit[(fit.tier==tier)] if not fit.empty else pd.DataFrame()
                if eng!='all' and not tf.empty: tf=tf[tf.engine==eng]
                grid[cname][tier][eng]={}
                for h in HORIZONS:
                    m=metrics(tf,h,cost); cons=day_consistency(tf,h,cost); m['consistency']=cons
                    grid[cname][tier][eng][str(h)]=m
                    if m['events']<30 or m['net_mean_bps'] is None: continue
                    # Fit-only robust selection: reward net/PF, require representation across days.
                    if cons['days']<5: continue
                    score=m['net_mean_bps']+2*((m['pf'] or 0)-1)+0.02*cons['positive_day_pct']
                    cand={'tier':tier,'engine':eng,'horizon_sec':h,'score':float(score),'fit':m}
                    if best is None or score>best['score']: best=cand
        selected[cname]=best

    # Validation is opened only for a fit survivor with positive net, PF and multi-day stability.
    val_needed={k:bool(b and b['fit']['net_mean_bps']>0 and (b['fit']['pf'] or 0)>=1.05 and b['fit']['consistency']['positive_day_pct']>=50) for k,b in selected.items()}
    val=pd.DataFrame(); val_counts={}
    if any(val_needed.values()):
        val,val_counts=gather(v19.VAL_DAYS)
    for cname,cost in COSTS.items():
        b=selected[cname]
        if not val_needed[cname] or b is None:
            validation[cname]=None; continue
        tv=val[val.tier==b['tier']]
        if b['engine']!='all': tv=tv[tv.engine==b['engine']]
        vm=metrics(tv,b['horizon_sec'],cost); vm['consistency']=day_consistency(tv,b['horizon_sec'],cost)
        validation[cname]=vm

    passes={}
    for cname,b in selected.items():
        vm=validation[cname]
        passes[cname]=bool(b and vm and b['fit']['events']>=30 and b['fit']['net_mean_bps']>0 and (b['fit']['pf'] or 0)>=1.05 and vm['events']>=15 and vm['net_mean_bps']>0 and (vm['pf'] or 0)>=1.02)

    out={
        'version':'quantbot-v22-japan-rare-event','purpose':'NO_LOOKAHEAD_RARE_EVENT_PUBLIC_METHOD_RESEARCH_NO_LIVE',
        'symbol':v19.SYMBOL,'source':'Binance Vision USD-M aggTrades -> 1s','cost_models_bps':COSTS,
        'confirm_sec':CONFIRM_SEC,'confirm_bps':CONFIRM_BPS,'horizons_sec':HORIZONS,'quality_tiers':QUALITY,
        'fit_counts':fit_counts,'fit_grid':grid,'selected_fit_only':selected,'validation_opened':val_needed,
        'validation_counts':val_counts,'validation':validation,'passes':passes,'live_orders':False,
        'methodology_fix':'v21 confirmation lookahead removed: entry occurs one second AFTER first observed confirmation',
        'interpretation':'RARE_EVENT_EDGE_CANDIDATE' if any(passes.values()) else 'NO_RARE_EVENT_EDGE_YET'
    }
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))

if __name__=='__main__': main()
