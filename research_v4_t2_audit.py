"""Post-selection adversarial audit for QuantBot v4 T2_upup.
Not pristine OOS: stresses parameter neighborhoods and leave-one-asset-out dependence.
"""
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import research_v3_independent as v3
import research_v3_audit as audit
import research_v3_snapshot_validation as snap

OUT=Path('data/v4_t2_audit.json'); BASE=.0013; C40=.0040


def t2(px,looks=(20,60,120,180),ma=200,up=(60,120),k=3):
    m=[v3.momentum(px,n) for n in looks]
    raw=.25*m[0]+.35*m[1]+.25*m[2]+.15*m[3]
    btc=px['BTCUSDT']; gate=(btc>btc.rolling(ma).mean())&(btc/btc.shift(up[0])-1>0)&(btc/btc.shift(up[1])-1>0)
    return v3.normalize_selected(raw,px,k=k,positive=True,freq='W',market_gate=gate)


def pre_gate(px,pos):
    h=audit.diag(px,pos,'2021-01-01','2026-04-24',BASE); s=audit.diag(px,pos,'2021-01-01','2026-04-24',C40)
    rolls=audit.rolling_windows(px.loc[px.index<=v3.LOCK_DATE],pos.loc[pos.index<=v3.LOCK_DATE],window=180,step=30,cost=BASE)
    folds=[]
    for y in range(2021,2026):
        d=audit.diag(px,pos,f'{y}-01-01',f'{y}-12-31',BASE); d['year']=y; folds.append(d)
    meaningful=[x for x in folds if x['active_day_pct']>=10]; posfold=sum(x['net_pct']>0 for x in meaningful)
    gate=h['net_pct']>0 and h['sharpe']>=.5 and h['pf']>=1.08 and h['max_dd_pct']<=20 and s['net_pct']>0 and h['active_day_pct']>=15 and len(meaningful)>=3 and posfold>=max(3,math.ceil(.75*len(meaningful))) and rolls['positive_fraction_meaningful']>=.60
    return {'history':h,'stress40':s,'rolling':{k:v for k,v in rolls.items() if k!='windows'},'folds':folds,'gate':bool(gate)}


def post(px,pos):
    return {'base':audit.diag(px,pos,'2026-04-25',None,BASE),'stress40':audit.diag(px,pos,'2026-04-25',None,C40),'delay2':audit.diag(px,pos,'2026-04-25',None,BASE,extra_delay=1)}


def main():
    cut=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC'); cur,_=v3.load_prices(); cur=cur.loc[cur.index<cut]; hs,_=snap.load(); hs=hs.loc[hs.index<cut]
    universes={'current':cur,'snapshot':hs}
    configs=[
      ('base',(20,60,120,180),200,(60,120),3),
      ('fast',(16,48,96,144),200,(60,120),3),('slow',(24,72,144,216),200,(60,120),3),
      ('ma160',(20,60,120,180),160,(60,120),3),('ma240',(20,60,120,180),240,(60,120),3),
      ('up45_90',(20,60,120,180),200,(45,90),3),('up90_180',(20,60,120,180),200,(90,180),3),
      ('top5',(20,60,120,180),200,(60,120),5),
      ('slow_ma240',(24,72,144,216),240,(90,180),3),('fast_ma160',(16,48,96,144),160,(45,90),3)
    ]
    perts=[]
    for name,looks,ma,up,k in configs:
        u={}
        for un,px in universes.items():
            p=t2(px,looks,ma,up,k); u[un]={'pre':pre_gate(px,p),'post':post(px,p)}
        both=all(u[x]['pre']['gate'] for x in u)
        post40=all(u[x]['post']['stress40']['net_pct']>0 for x in u)
        perts.append({'name':name,'looks':looks,'ma':ma,'up':up,'k':k,'universes':u,'pre_both_gate':both,'post40_positive_both':post40})
    # Leave-one-asset-out uses exact base T2 and measures postlock diagnostic plus prelock sign.
    jacks={}
    for un,px in universes.items():
        arr=[]
        for drop in px.columns:
            if drop=='BTCUSDT': continue
            sub=px.drop(columns=[drop]); p=t2(sub)
            h=audit.diag(sub,p,'2021-01-01','2026-04-24',BASE); o=audit.diag(sub,p,'2026-04-25',None,C40)
            arr.append({'excluded':drop,'history_net_pct':h['net_pct'],'history_sharpe':h['sharpe'],'post40_net_pct':o['net_pct'],'post40_pf':o['pf']})
        jacks[un]=arr
    pert_pre=sum(x['pre_both_gate'] for x in perts); pert_post=sum(x['post40_positive_both'] for x in perts)
    jack_summary={u:{'count':len(a),'history_positive':sum(x['history_net_pct']>0 for x in a),'post40_positive':sum(x['post40_net_pct']>0 for x in a)} for u,a in jacks.items()}
    pass_audit=(pert_pre>=7 and pert_post>=7 and all(s['history_positive']>=math.ceil(.9*s['count']) and s['post40_positive']>=math.ceil(.8*s['count']) for s in jack_summary.values()))
    state={'version':'quantbot-v4-t2-postselection-audit','updated':datetime.now(timezone.utc).isoformat(),
           'note':'Defined after T2 selection; this is fragility testing, not pristine OOS.',
           'parameter_perturbations':perts,'parameter_prelock_both_pass':f'{pert_pre}/{len(perts)}','parameter_post40_both_positive':f'{pert_post}/{len(perts)}',
           'leave_one_asset_out':jacks,'jackknife_summary':jack_summary,
           'audit_supports_t2':bool(pass_audit),'interpretation':'T2_NEIGHBORHOOD_AND_ASSET_ROBUSTNESS_SUPPORTED' if pass_audit else 'T2_FRAGILITY_DETECTED',
           'live_money_authorized':False}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps({'audit':pass_audit,'pert_pre':state['parameter_prelock_both_pass'],'pert_post':state['parameter_post40_both_positive'],'jack':jack_summary},indent=2))
if __name__=='__main__': main()
