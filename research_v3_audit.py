"""Adversarial audit of the frozen QuantBot v3 candidate.
This file does NOT re-select a winner. It stress-tests exactly the frozen E rule
from research_v3_independent.py and records fragility, activity and perturbation diagnostics.
Public data only; no live orders.
"""
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import research_v3_independent as v3

OUT=Path('data/v3_audit_state.json')
BASE=0.0013

def frozen_e(px, looks=(20,60,120,180), ma=200):
    m=[v3.momentum(px,n) for n in looks]
    score=.25*m[0]+.35*m[1]+.25*m[2]+.15*m[3]
    gate=px['BTCUSDT']>px['BTCUSDT'].rolling(ma).mean()
    return v3.normalize_selected(score,px,k=3,positive=True,freq='W',market_gate=gate)

def series(px,pos,cost=BASE,extra_delay=0):
    r=px.pct_change(fill_method=None).fillna(0.0)
    held=pos.shift(1+extra_delay).fillna(0.0)
    to=held.diff().abs().sum(axis=1).fillna(0.0)
    gross=(held*r).sum(axis=1)
    net=gross-cost*to
    exposure=held.abs().sum(axis=1)
    return net,to,exposure

def diag(px,pos,start,end=None,cost=BASE,extra_delay=0):
    net,to,ex=series(px,pos,cost,extra_delay)
    mask=net.index>=pd.Timestamp(start,tz='UTC')
    if end: mask &= net.index<=pd.Timestamp(end,tz='UTC')
    n=net.loc[mask]; t=to.loc[mask]; e=ex.loc[mask]
    m=v3.metrics(n,t,cost)
    active=e>1e-9
    active_n=n[active]
    nonzero=active_n[active_n!=0]
    m.update({
        'active_days':int(active.sum()),
        'active_day_pct':float(active.mean()*100) if len(active) else 0.0,
        'avg_gross_exposure':float(e.mean()) if len(e) else 0.0,
        'median_active_gross':float(e[active].median()) if active.any() else 0.0,
        'active_day_win_pct':float((active_n>0).mean()*100) if len(active_n) else 0.0,
        'nonzero_active_win_pct':float((nonzero>0).mean()*100) if len(nonzero) else 0.0,
        'nonzero_active_days':int(len(nonzero)),
    })
    return m

def rolling_windows(px,pos,start='2021-01-01',window=180,step=30,cost=BASE):
    net,_,ex=series(px,pos,cost)
    s=net.loc[net.index>=pd.Timestamp(start,tz='UTC')]
    ee=ex.reindex(s.index)
    out=[]
    for i in range(0,max(0,len(s)-window+1),step):
        x=s.iloc[i:i+window]; xe=ee.iloc[i:i+window]
        eq=(1+x).prod()-1
        out.append({'start':str(x.index[0].date()),'end':str(x.index[-1].date()),
                    'net_pct':float(eq*100),'active_day_pct':float((xe>1e-9).mean()*100)})
    meaningful=[z for z in out if z['active_day_pct']>=10]
    return {
        'window_days':window,'step_days':step,'count':len(out),
        'positive_fraction_all':float(np.mean([z['net_pct']>0 for z in out])) if out else 0.0,
        'meaningful_count':len(meaningful),
        'positive_fraction_meaningful':float(np.mean([z['net_pct']>0 for z in meaningful])) if meaningful else 0.0,
        'median_net_pct_meaningful':float(np.median([z['net_pct'] for z in meaningful])) if meaningful else 0.0,
        'worst_net_pct':float(min([z['net_pct'] for z in out],default=0.0)),
        'best_net_pct':float(max([z['net_pct'] for z in out],default=0.0)),
        'windows':out
    }

def perturbations(px):
    configs=[
        ('faster',(16,48,96,144),160),
        ('faster_ma200',(16,48,96,144),200),
        ('base_ma160',(20,60,120,180),160),
        ('base',(20,60,120,180),200),
        ('base_ma240',(20,60,120,180),240),
        ('slower_ma200',(24,72,144,216),200),
        ('slower',(24,72,144,216),240),
    ]
    out=[]
    for name,looks,ma in configs:
        p=frozen_e(px,looks,ma)
        h=diag(px,p,'2021-01-01','2026-04-24',BASE)
        o=diag(px,p,'2026-04-25',None,BASE)
        out.append({'name':name,'looks':looks,'ma':ma,'history_net_pct':h['net_pct'],
                    'history_sharpe':h['sharpe'],'history_dd_pct':h['max_dd_pct'],
                    'holdout_net_pct':o['net_pct'],'holdout_pf':o['pf'],
                    'holdout_active_day_pct':o['active_day_pct']})
    return out

def jackknife(px):
    gate=px['BTCUSDT']>px['BTCUSDT'].rolling(200).mean()
    out=[]
    for drop in px.columns:
        sub=px.drop(columns=[drop])
        m20,m60,m120,m180=[v3.momentum(sub,n) for n in (20,60,120,180)]
        score=.25*m20+.35*m60+.25*m120+.15*m180
        p=v3.normalize_selected(score,sub,k=3,positive=True,freq='W',market_gate=gate)
        h=diag(sub,p,'2021-01-01','2026-04-24',BASE)
        o=diag(sub,p,'2026-04-25',None,BASE)
        out.append({'excluded':drop,'history_net_pct':h['net_pct'],'history_sharpe':h['sharpe'],
                    'history_dd_pct':h['max_dd_pct'],'holdout_net_pct':o['net_pct'],
                    'holdout_pf':o['pf'],'holdout_active_day_pct':o['active_day_pct']})
    return out

def main():
    px,errors=v3.load_prices()
    pos=frozen_e(px)
    periods={
        'history_2021_to_lock':diag(px,pos,'2021-01-01','2026-04-24',BASE),
        'recent_prelock_2023_to_lock':diag(px,pos,'2023-01-01','2026-04-24',BASE),
        'strict_holdout':diag(px,pos,'2026-04-25',None,BASE),
        'strict_holdout_cost40':diag(px,pos,'2026-04-25',None,0.0040),
        'strict_holdout_cost75':diag(px,pos,'2026-04-25',None,0.0075),
        'strict_holdout_delay2_total':diag(px,pos,'2026-04-25',None,BASE,extra_delay=1),
        'strict_holdout_delay3_total':diag(px,pos,'2026-04-25',None,BASE,extra_delay=2),
    }
    yrs=[]
    for y in range(2021,2027):
        end=f'{y}-12-31' if y<2026 else '2026-04-24'
        d=diag(px,pos,f'{y}-01-01',end,BASE)
        d['year']=y; yrs.append(d)
    rolls=rolling_windows(px.loc[px.index<=v3.LOCK_DATE],pos.loc[pos.index<=v3.LOCK_DATE],window=180,step=30)
    perts=perturbations(px)
    jacks=jackknife(px)
    pert_hold_pos=sum(x['holdout_net_pct']>0 for x in perts)
    jack_hold_pos=sum(x['holdout_net_pct']>0 for x in jacks)
    # Adversarial audit gate is intentionally stricter and is not called pre-defined.
    # It asks that evidence not depend on a single parameterization or single asset.
    audit_pass=(
        periods['recent_prelock_2023_to_lock']['net_pct']>0 and
        periods['strict_holdout_cost75']['net_pct']>0 and
        periods['strict_holdout_delay2_total']['net_pct']>0 and
        rolls['positive_fraction_meaningful']>=0.65 and
        pert_hold_pos>=math.ceil(0.7*len(perts)) and
        jack_hold_pos>=math.ceil(0.8*len(jacks))
    )
    state={
        'version':'quantbot-v3-frozen-candidate-adversarial-audit',
        'updated':datetime.now(timezone.utc).isoformat(),
        'frozen_rule':'E_blend_top3_btc200_gate; weekly rebalance; 20/60/120/180 blend; top3 positive scores; BTC>200d SMA; inverse-vol; 12% vol target; <=1x gross.',
        'note':'This audit was defined after the initial v3 result and therefore is a post-selection stress test, not a new untouched OOS gate.',
        'dataset':{'assets':list(px.columns),'start':str(px.index.min()),'end':str(px.index.max()),'errors':errors},
        'periods':periods,'annual_folds':yrs,'rolling_180d':rolls,
        'parameter_perturbations':perts,
        'parameter_holdout_positive':f'{pert_hold_pos}/{len(perts)}',
        'leave_one_asset_out':jacks,
        'jackknife_holdout_positive':f'{jack_hold_pos}/{len(jacks)}',
        'audit_pass':bool(audit_pass),
        'interpretation':'AUDIT_SUPPORTS_PAPER_TRIAL' if audit_pass else 'FRAGILITY_DETECTED_DO_NOT_PROMOTE',
        'limitations':['Post-selection audit cannot restore untouched status once the 2026-04-25+ holdout has been viewed.',
                       'Current-liquid universe retains survivorship bias.','Daily bars omit intraday spread/slippage/outage effects.']
    }
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'audit_pass':audit_pass,'periods':periods,
                      'rolling_summary':{k:v for k,v in rolls.items() if k!='windows'},
                      'parameter_holdout_positive':state['parameter_holdout_positive'],
                      'jackknife_holdout_positive':state['jackknife_holdout_positive']},indent=2))
if __name__=='__main__': main()
