import json
from datetime import datetime, timezone
from pathlib import Path
import dara_discovery100_sep2 as d

OUT=Path('data/dara_discovery100_learned_sep2.json')
# Post-hoc learning pass on Sep2. Explicitly in-sample.
d.RISK=0.0005
d.MIN_STOP=0.0016
d.MAX_STOP=0.0032


def learned_candidates(i,m,f5,f15,allow_probe=False):
    out=[]
    # Quarantine SWEEP_RECLAIM and PULLBACK_RELOAD after severe Sep2 failure rates.
    for fn in (d.micro_break,d.momentum_retest,d.flow_pulse):
        try:
            x=fn(i,m,f5,f15)
            if not x:continue
            setup,side,stop,ei,score,diag,tp=x
            vol=diag.get('volr',99); reg15=diag.get('reg15')
            aligned=(side=='LONG' and reg15=='UP') or (side=='SHORT' and reg15=='DOWN')
            entry=m[ei]['o']; sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
            # Avoid the toxic 1x-2x relative-volume bucket seen in Discovery100 unless this is a micro-break with clear 15m alignment.
            if 1.0<=vol<2.0 and not (setup=='MICRO_BREAK' and aligned):continue
            # Micro-breaks were strongest when 15m aligned. Flow may operate in transition but low/moderate volume is preferred.
            if setup=='MICRO_BREAK' and not aligned:continue
            if setup=='FLOW_PULSE' and vol>=1.0 and abs(diag.get('delta',0))<0.4:continue
            if sp<0.0016:continue
            out.append((setup,side,stop,ei,score+int(aligned),diag,tp))
        except Exception:pass
    return sorted(out,key=lambda x:x[4],reverse=True)

d.candidates=learned_candidates


def learned_sim(m,ei,side,stop,equity,tp):
    entry=m[ei]['o'];sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<=0:return None
    n=min(d.MAX_LEV*equity,equity*d.RISK/(sp+d.COST));target=entry*(1+tp if side=='LONG' else 1-tp)
    mfe=mae=0.;end=min(len(m)-1,ei+30)
    for j in range(ei,end+1):
        b=m[j];fav=(b['h']/entry-1) if side=='LONG' else (entry/b['l']-1);adv=(entry/b['l']-1) if side=='LONG' else (b['h']/entry-1)
        mfe=max(mfe,fav);mae=max(mae,adv)
        st=b['l']<=stop if side=='LONG' else b['h']>=stop;hit=b['h']>=target if side=='LONG' else b['l']<=target
        if st:
            ret=stop/entry-1 if side=='LONG' else entry/stop-1;gross=n*ret;cost=n*d.COST;return j,stop,'STOP',gross,cost,gross-cost,n,mfe,mae
        if hit:
            gross=n*tp;cost=n*d.COST;return j,target,'TP',gross,cost,gross-cost,n,mfe,mae
        px=b['c'];ret=px/entry-1 if side=='LONG' else entry/px-1
        # Early failure: the majority of Sep2 losers had almost no favorable excursion.
        if j>=ei+2 and mfe<0.0005 and ret<=-0.00035:
            gross=n*ret;cost=n*d.COST;return j,px,'EARLY_NO_FOLLOW',gross,cost,gross-cost,n,mfe,mae
        if j>=ei+4 and mfe<0.0010 and ret<=0:
            gross=n*ret;cost=n*d.COST;return j,px,'EARLY_WEAK_PROGRESS',gross,cost,gross-cost,n,mfe,mae
        # Once 3x fee has actually traded, protect against a full giveback. We still do not intentionally take a normal profit below 3x fee.
        if mfe>=0.0033 and ret<=0.0012:
            gross=n*ret;cost=n*d.COST;return j,px,'POST_3FEE_PROTECT',gross,cost,gross-cost,n,mfe,mae
        if j>=ei+10 and ret<=0:
            gross=n*ret;cost=n*d.COST;return j,px,'THESIS_FAIL',gross,cost,gross-cost,n,mfe,mae
    px=m[end]['c'];ret=px/entry-1 if side=='LONG' else entry/px-1;gross=n*ret;cost=n*d.COST
    return end,px,'DIAG_TIMEOUT',gross,cost,gross-cost,n,mfe,mae

d.simulate=learned_sim

if __name__=='__main__':
    old=d.OUT;d.OUT=OUT;d.MAX_TRADES=100
    d.main()
    p=json.loads(OUT.read_text())
    p['version']='DARA-Discovery100-Learned-Sep2-InSample'
    p['purpose']='Post-hoc replay after analyzing 100 Sep2 discovery trades; measures whether identified failure controls reduce damage. Not validation.'
    p['learned_changes']=[
      'Quarantine SWEEP_RECLAIM and PULLBACK_RELOAD after 5.9% and 7.7% win rates in raw Discovery100.',
      'Minimum structural stop raised to 0.16% to avoid friction-scale stops.',
      'Avoid 1x-2x relative-volume bucket except 15m-aligned micro-breaks.',
      'Micro-break requires 15m directional alignment.',
      'Early no-follow-through and weak-progress loss exits added.',
      'After price has traded >=0.33% favorable (3x modeled fee), a giveback protection state is activated.'
    ]
    p['notes'].append('Highly in-sample: these rules were designed after inspecting Sep2 Discovery100. Improvement cannot be treated as out-of-sample edge.')
    OUT.write_text(json.dumps(p,indent=2));print(json.dumps(p['overall'],indent=2));print(json.dumps(p['by_setup'],indent=2))
