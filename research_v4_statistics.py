"""QuantBot v4 statistical audit: moving-block bootstrap and White-style reality check.
Uses prelock data only. This is post-selection inference, not pristine OOS.
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
import research_v4_tournament as v4

OUT=Path('data/v4_statistics.json')
START=pd.Timestamp('2021-01-01',tz='UTC'); END=v3.LOCK_DATE
B=2500; BLOCK=30; SEED=20260908


def block_indices(n,block,rng):
    k=math.ceil(n/block); starts=rng.integers(0,max(1,n-block+1),size=k)
    return np.concatenate([np.arange(s,min(s+block,n)) for s in starts])[:n]


def universe_test(px):
    c=v4.build(px); names=list(c)
    mat=[]
    for name in names:
        ret,_,_=audit.series(px,c[name],.0013)
        x=ret.loc[(ret.index>=START)&(ret.index<=END)].fillna(0.0)
        mat.append(x)
    df=pd.concat(mat,axis=1); df.columns=names; a=df.to_numpy(float); n=len(a)
    obs=a.mean(axis=0); obs_max=float(obs.max()); winner=names[int(np.argmax(obs))]
    centered=a-obs[None,:]
    rng=np.random.default_rng(SEED)
    rc=[]; t2_net=[]; t2_mean=[]; t2_sharpe=[]
    t2_i=names.index('T2_upup')
    for _ in range(B):
        ix=block_indices(n,BLOCK,rng)
        z=centered[ix]
        rc.append(float(z.mean(axis=0).max()))
        r=a[ix,t2_i]
        t2_net.append(float((np.prod(1+r)-1)*100))
        mu=float(r.mean()); sd=float(r.std(ddof=1)); t2_mean.append(mu*365.25*100)
        t2_sharpe.append(float(mu/sd*math.sqrt(365.25)) if sd>0 else 0.0)
    rc=np.asarray(rc); p=float((1+np.sum(rc>=obs_max))/(B+1))
    t2_net=np.asarray(t2_net); t2_mean=np.asarray(t2_mean); t2_sharpe=np.asarray(t2_sharpe)
    def q(x): return {'p05':float(np.quantile(x,.05)),'p50':float(np.quantile(x,.50)),'p95':float(np.quantile(x,.95))}
    return {
      'n_days':n,'candidate_count':len(names),'raw_best_by_daily_mean':winner,
      'observed_best_daily_mean_bps':obs_max*10000,'reality_check_pvalue':p,
      'T2':{'observed_net_pct':float((np.prod(1+a[:,t2_i])-1)*100),
            'observed_ann_arithmetic_pct':float(obs[t2_i]*365.25*100),
            'bootstrap_net_pct':q(t2_net),'bootstrap_ann_arithmetic_pct':q(t2_mean),'bootstrap_sharpe':q(t2_sharpe),
            'bootstrap_net_positive_fraction':float(np.mean(t2_net>0))},
      'gate':bool(p<=.10 and np.mean(t2_net>0)>=.95 and np.quantile(t2_mean,.05)>0)
    }


def main():
    cut=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC'); cur,_=v3.load_prices(); cur=cur.loc[cur.index<cut]; hs,_=snap.load(); hs=hs.loc[hs.index<cut]
    res={'current':universe_test(cur),'historical_snapshot':universe_test(hs)}
    passed=all(x['gate'] for x in res.values())
    state={'version':'quantbot-v4-statistical-audit','updated':datetime.now(timezone.utc).isoformat(),
           'method':'30-day moving-block bootstrap, 2500 draws; White-style reality-check max mean across 10 tournament candidates using centered returns; T2 bootstrap uncertainty.',
           'scope':'prelock 2021-01-01..2026-04-24 only; post-selection statistical audit, not fresh OOS.',
           'results':res,'statistics_support_t2':bool(passed),
           'interpretation':'T2_SURVIVES_BLOCK_BOOTSTRAP_AND_MULTIPLE_TESTING_SCREEN' if passed else 'T2_STATISTICAL_EVIDENCE_INSUFFICIENT',
           'limitations':['Reality-check approximation uses a fixed 30-day moving block and daily net-return series.','Strategies are highly correlated; the test is conservative/approximate, not a guarantee of future alpha.']}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps(state,indent=2))
if __name__=='__main__': main()
