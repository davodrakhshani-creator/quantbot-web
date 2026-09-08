"""QuantBot v5 fast-signal lane.

Separate research lane for higher signal frequency. It cannot replace T2 unless it
survives costs, delay and multiple universes. Selection ends 2026-04-24. No live orders.
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
import research_v5_coinbase_validation as cb

OUT=Path('data/v5_fastlane_state.json'); LOCK=v3.LOCK_DATE
BASE=.0013; C40=.0040; C75=.0075

def rb_n(index,n):
    s=pd.Series(False,index=index); s.iloc[::n]=True; return s

def make(px):
    m7,m14,m20,m28,m40,m56=[v3.momentum(px,n) for n in (7,14,20,28,40,56)]
    s1=.35*m7+.40*m14+.25*m28
    s2=.55*m14+.45*m28
    s3=.30*m14+.45*m28+.25*m56
    vol=v3.ann_vol(px,20).replace(0,np.nan)
    btc=px['BTCUSDT']; gate100=(btc>btc.rolling(100).mean())&(m20['BTCUSDT']>0)&(m40['BTCUSDT']>0)
    gate200=(btc>btc.rolling(200).mean())&(m20['BTCUSDT']>0)&(m40['BTCUSDT']>0)
    dual=(m14>0)&(m28>0)
    specs={
      'F0_weekly_14_28':(s2,gate100,None,3,7),
      'F1_3day_14_28':(s2,gate100,None,3,3),
      'F2_daily_top2':(s2,gate100,None,2,1),
      'F3_3day_7_14_28':(s1,gate100,None,3,3),
      'F4_3day_slowblend':(s3,gate100,None,3,3),
      'F5_3day_gate200':(s2,gate200,None,3,3),
      'F6_3day_assetdual':(s2,gate100,dual,3,3),
      'F7_daily_assetdual_top2':(s2,gate100,dual,2,1),
    }
    out={}
    for name,(score,gate,af,k,n) in specs.items():
        p=pd.DataFrame(0.0,index=px.index,columns=px.columns)
        for dt in px.index:
            if not bool(gate.loc[dt]): continue
            ok=score.loc[dt].notna()&px.loc[dt].notna()&vol.loc[dt].notna()&(score.loc[dt]>0)
            if af is not None: ok &= af.loc[dt].fillna(False)
            names=list(score.loc[dt,ok].sort_values(ascending=False).index[:k])
            if not names: continue
            inv=1/vol.loc[dt,names].clip(lower=.12); w=inv/inv.sum(); avg=float((w*vol.loc[dt,names]).sum())
            scalar=min(1.0,.10/max(avg,1e-9)); p.loc[dt,names]=w*scalar
        rb=rb_n(px.index,n); out[name]=p.where(rb,np.nan).ffill().fillna(0.0)
    return out

def ev(px,p):
    h=audit.diag(px,p,'2021-01-01','2026-04-24',BASE); s40=audit.diag(px,p,'2021-01-01','2026-04-24',C40); s75=audit.diag(px,p,'2021-01-01','2026-04-24',C75)
    d=audit.diag(px,p,'2021-01-01','2026-04-24',BASE,extra_delay=1)
    folds=[]
    for y in range(2021,2026):
        x=audit.diag(px,p,f'{y}-01-01',f'{y}-12-31',BASE); x['year']=y; folds.append(x)
    mf=[x for x in folds if x.get('active_day_pct',0)>=10]; pos=sum(x['net_pct']>0 for x in mf)
    r=audit.rolling_windows(px.loc[px.index<=LOCK],p.loc[p.index<=LOCK],window=90,step=30,cost=BASE)
    gate=(h['net_pct']>0 and h['sharpe']>=.65 and h['pf']>=1.12 and h['max_dd_pct']<=15 and s40['net_pct']>0 and s75['net_pct']>0 and d['net_pct']>0 and h.get('active_day_pct',0)>=20 and len(mf)>=3 and pos>=max(3,math.ceil(.75*len(mf))) and r.get('positive_fraction_meaningful',0)>=.60)
    score=2.5*h['pf']+1.8*h['sharpe']+2*r.get('positive_fraction_meaningful',0)-.08*h['max_dd_pct']+.01*s75['net_pct']-.015*h.get('turnover_units',0)
    return {'history':h,'stress40':s40,'stress75':s75,'delay2_total':d,'folds':folds,'rolling90':{k:v for k,v in r.items() if k!='windows'},'gate':bool(gate),'score':float(score)}

def post(px,p):
    return {'base':audit.diag(px,p,'2026-04-25',None,BASE),'stress40':audit.diag(px,p,'2026-04-25',None,C40),'stress75':audit.diag(px,p,'2026-04-25',None,C75)}

def main():
    now=datetime.now(timezone.utc); cut=pd.Timestamp(now.date(),tz='UTC')
    cur,err=v3.load_prices(); cur=cur.loc[cur.index<cut]; hs,hmeta=snap.load(); hs=hs.loc[hs.index<cut]
    us={'current_liquid':cur,'historical_snapshot':hs}; cs={u:make(px) for u,px in us.items()}; names=list(next(iter(cs.values())))
    res={}
    for n in names:
        per={u:ev(px,cs[u][n]) for u,px in us.items()}; res[n]={'universes':per,'gate_count':sum(int(x['gate']) for x in per.values()),'worst_score':min(x['score'] for x in per.values())}
    win=max(names,key=lambda n:(res[n]['gate_count'],res[n]['worst_score']))
    postd={u:post(px,cs[u][win]) for u,px in us.items()}
    cbpx,cberr,cbmeta=cb.load(); cbpx=cbpx.loc[cbpx.index<cut]; cbp=make(cbpx)[win]; cbd={'prelock':ev(cbpx,cbp),'postlock_reused':post(cbpx,cbp),'errors':cberr,'metadata':cbmeta}
    status='FAST_PAPER_CANDIDATE' if res[win]['gate_count']==2 and cbd['prelock']['gate'] else 'NO_ROBUST_FAST_EDGE'
    state={'version':'quantbot-v5-fastlane','updated':now.isoformat(),'goal':'Higher-frequency signal lane with strict cost/delay gates.','selection_end':'2026-04-24','selected':win,'candidate_results':res,'selected_postlock_reused':postd,'coinbase_diagnostic':cbd,'research_status':status,'live_money_authorized':False,'governance':['Separate from frozen T2 forward tracker.','No live orders.','Coinbase and postlock are diagnostic only.']}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps({'winner':win,'status':status,'gates':res[win]['gate_count'],'summary':{u:{'net':x['history']['net_pct'],'sharpe':x['history']['sharpe'],'pf':x['history']['pf'],'dd':x['history']['max_dd_pct'],'s75':x['stress75']['net_pct'],'turn':x['history'].get('turnover_units',0)} for u,x in res[win]['universes'].items()},'coinbase_gate':cbd['prelock']['gate']},indent=2))
if __name__=='__main__': main()
