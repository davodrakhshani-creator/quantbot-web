"""QuantBot v4 robustness tournament.

Purpose: compare a small pre-registered family of literature-inspired momentum/regime
variants across two independent universes. Selection uses only data <= 2026-04-24.
The already-viewed 2026-04-25+ period is diagnostic only, never called pristine OOS.
No live orders. Public market data only.
"""
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import research_v3_independent as v3
import research_v3_audit as audit
import research_v3_snapshot_validation as snap

OUT=Path('data/v4_tournament_state.json')
LOCK=v3.LOCK_DATE
BASE=.0013
C40=.0040
C75=.0075


def _weekly_hold(x: pd.Series, idx: pd.Index) -> pd.Series:
    rb=v3.rebalance_mask(idx,'W')
    return x.where(rb,np.nan).ffill().fillna(0.0)


def build(px: pd.DataFrame):
    m20,m60,m90,m120,m180,m240=[v3.momentum(px,n) for n in (20,60,90,120,180,240)]
    raw=.25*m20+.35*m60+.25*m120+.15*m180
    slow=.30*m60+.40*m120+.30*m240
    btc=px['BTCUSDT']
    btc200=btc>btc.rolling(200).mean()
    upup=(btc/btc.shift(60)-1>0)&(btc/btc.shift(120)-1>0)
    btcvol=btc.pct_change(fill_method=None).rolling(30,min_periods=30).std()*math.sqrt(365.25)
    btcvol80=btcvol.rolling(252,min_periods=126).quantile(.80).shift(1)
    vol_ok=btcvol<btcvol80

    # Cross-sectional dispersion of medium-horizon momentum. Threshold is causal: prior-day
    # rolling 80th percentile, so today's dispersion never changes its own threshold.
    disp=m120.std(axis=1,skipna=True)
    disp50=disp.rolling(252,min_periods=126).quantile(.50).shift(1)
    disp80=disp.rolling(252,min_periods=126).quantile(.80).shift(1)
    disp_ok=disp<disp80

    out={}
    out['T0_base_E']=v3.normalize_selected(raw,px,k=3,positive=True,freq='W',market_gate=btc200)
    out['T1_disp80']=v3.normalize_selected(raw,px,k=3,positive=True,freq='W',market_gate=(btc200&disp_ok))
    out['T2_upup']=v3.normalize_selected(raw,px,k=3,positive=True,freq='W',market_gate=(btc200&upup))
    out['T3_disp80_upup']=v3.normalize_selected(raw,px,k=3,positive=True,freq='W',market_gate=(btc200&upup&disp_ok))
    out['T4_vol80']=v3.normalize_selected(raw,px,k=3,positive=True,freq='W',market_gate=(btc200&vol_ok))
    out['T5_slow_disp80']=v3.normalize_selected(slow,px,k=3,positive=True,freq='W',market_gate=(btc200&disp_ok))
    out['T6_xs120_disp80']=v3.normalize_selected(m120,px,k=3,positive=True,freq='W',market_gate=(btc200&disp_ok))
    out['T7_top5_disp80']=v3.normalize_selected(raw,px,k=5,positive=True,freq='W',market_gate=(btc200&disp_ok))

    # Discrete dispersion risk scaling: 1x below trailing median, 0.5x between median and
    # 80th percentile, 0x above 80th. Scaling only refreshes weekly.
    scale=pd.Series(np.where(disp<disp50,1.0,np.where(disp<disp80,.5,0.0)),index=px.index)
    scale=_weekly_hold(scale,px.index)
    out['T8_disp_scaled']=out['T0_base_E'].mul(scale,axis=0)
    out['T9_ensemble']=.5*out['T1_disp80']+.5*out['T3_disp80_upup']
    return out


def eval_one(px,pos):
    hist=audit.diag(px,pos,'2021-01-01','2026-04-24',BASE)
    s40=audit.diag(px,pos,'2021-01-01','2026-04-24',C40)
    s75=audit.diag(px,pos,'2021-01-01','2026-04-24',C75)
    delay=audit.diag(px,pos,'2021-01-01','2026-04-24',BASE,extra_delay=1)
    folds=[]
    for y in range(2021,2026):
        d=audit.diag(px,pos,f'{y}-01-01',f'{y}-12-31',BASE); d['year']=y; folds.append(d)
    rolls=audit.rolling_windows(px.loc[px.index<=LOCK],pos.loc[pos.index<=LOCK],window=180,step=30,cost=BASE)
    meaningful=[f for f in folds if f.get('active_day_pct',0)>=10]
    pos_mean=sum(f['net_pct']>0 for f in meaningful)
    gate=(
        hist['net_pct']>0 and hist['sharpe']>=.50 and hist['pf']>=1.08 and hist['max_dd_pct']<=20 and
        s40['net_pct']>0 and delay['net_pct']>0 and hist['active_day_pct']>=15 and
        len(meaningful)>=3 and pos_mean>=max(3,math.ceil(.75*len(meaningful))) and
        rolls['meaningful_count']>=20 and rolls['positive_fraction_meaningful']>=.60
    )
    # Worst-case-oriented score; selection later maximizes the weaker-universe score.
    score=(2.0*hist['sharpe']+1.2*math.log(max(hist['pf'],.01))+.03*s40['net_pct']+
           3.0*rolls['positive_fraction_meaningful']-.05*hist['max_dd_pct']+
           .4*pos_mean-.15*max(0,-s75['net_pct']))
    return {
        'history':hist,'stress40':s40,'stress75':s75,'delay2_total':delay,'folds':folds,
        'rolling180':{k:v for k,v in rolls.items() if k!='windows'},
        'meaningful_positive_folds':f'{pos_mean}/{len(meaningful)}','gate':bool(gate),'score':float(score)
    }


def diagnostic_postlock(px,pos):
    return {
        'base':audit.diag(px,pos,'2026-04-25',None,BASE),
        'stress40':audit.diag(px,pos,'2026-04-25',None,C40),
        'stress75':audit.diag(px,pos,'2026-04-25',None,C75),
        'delay2_total':audit.diag(px,pos,'2026-04-25',None,BASE,extra_delay=1),
    }


def main():
    now=datetime.now(timezone.utc)
    cur,cur_errors=v3.load_prices(); cut=pd.Timestamp(now.date(),tz='UTC'); cur=cur.loc[cur.index<cut]
    hist,hmeta=snap.load(); hist=hist.loc[hist.index<cut]
    universes={'current_liquid':cur,'historical_2021_snapshot':hist}
    candidates={u:build(px) for u,px in universes.items()}
    names=list(next(iter(candidates.values())).keys())
    results={}
    for name in names:
        per={}
        for u,px in universes.items(): per[u]=eval_one(px,candidates[u][name])
        worst=min(per[u]['score'] for u in per)
        gates=sum(int(per[u]['gate']) for u in per)
        results[name]={'universes':per,'cross_universe_gate_count':gates,'worst_universe_score':float(worst)}
    # Never reward a candidate for a great survivor-biased universe if it fails the historical snapshot.
    winner=max(names,key=lambda n:(results[n]['cross_universe_gate_count'],results[n]['worst_universe_score']))
    post={u:diagnostic_postlock(px,candidates[u][winner]) for u,px in universes.items()}
    diag_pass=all(
        post[u]['base']['net_pct']>0 and post[u]['stress40']['net_pct']>0 and
        post[u]['delay2_total']['net_pct']>0 and post[u]['base']['active_day_pct']>=10
        for u in post
    )
    prelock_both=results[winner]['cross_universe_gate_count']==2
    state={
      'version':'quantbot-v4-robustness-tournament','updated':now.isoformat(),
      'protocol':{
        'candidate_count':len(names),'selection_end':'2026-04-24',
        'selection_rule':'maximize cross-universe gate count, then maximize worst-universe prelock score',
        'universes':['current-liquid 20-asset universe','fixed 2021 historical snapshot including later-delisted assets'],
        'costs':'13bps base, 40bps stress, 75bps extreme stress per turnover unit',
        'anti_overfit':'small pre-registered literature-inspired family; causal thresholds; no postlock tuning; worst-universe selection.',
        'postlock_status':'2026-04-25+ has already been viewed in prior research and is diagnostic only, not pristine OOS.',
        'fresh_forward':'Only the paper tracker beginning 2026-09-08 is pristine forward evidence.'
      },
      'public_research_inspiration':[
        'risk-managed cryptocurrency momentum','state-dependent UP-UP momentum',
        'cross-sectional dispersion as a momentum breakdown variable'
      ],
      'candidate_results':results,'selected':winner,'selected_postlock_diagnostic':post,
      'prelock_both_universes_pass':bool(prelock_both),'reused_postlock_diagnostic_pass':bool(diag_pass),
      'research_status':'STRONGER_PAPER_CANDIDATE' if prelock_both and diag_pass else 'NO_FINAL_ROBUST_EDGE_YET',
      'live_money_authorized':False,
      'data':{'current_assets':list(cur.columns),'current_errors':cur_errors,
              'historical_assets':list(hist.columns),'historical_metadata':hmeta},
      'limitations':['Historical snapshot is fixed rather than fully dynamic point-in-time membership.',
                     'Daily bars omit intraday spread, market impact, outages and liquidation path.',
                     'The reused postlock cannot become untouched again; fresh proof must come from forward paper data.']
    }
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'selected':winner,'prelock_both':prelock_both,'post_diag':diag_pass,
                      'status':state['research_status'],
                      'summary':{n:{'gates':results[n]['cross_universe_gate_count'],'worst_score':results[n]['worst_universe_score']} for n in names}},indent=2))
if __name__=='__main__': main()
