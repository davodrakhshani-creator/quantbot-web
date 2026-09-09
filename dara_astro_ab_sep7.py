import json
from datetime import datetime
from pathlib import Path
from dara_astro_memory import context

SRC=Path('data/dara_v18_prototype_memory_sep7.json')
OUT=Path('data/dara_astro_ab_sep7.json')

def valid(x):return x.get('reason') in ('TP','3FEE_FLOOR') and x.get('net_pnl',0)>0

def stats(xs):
    if not xs:return {'n':0,'valid_positive':0,'wr':None,'gross':0,'cost':0,'net':0}
    return {'n':len(xs),'valid_positive':sum(valid(x) for x in xs),'wr':round(100*sum(valid(x) for x in xs)/len(xs),1),'gross':round(sum(x['gross_pnl'] for x in xs),6),'cost':round(sum(x['cost'] for x in xs),6),'net':round(sum(x['net_pnl'] for x in xs),6)}

def tag(x,c):
    v=c['directional_vote']
    if v==0:return 'NEUTRAL'
    desired='LONG' if v>0 else 'SHORT'
    return 'ALIGNED' if x['side']==desired else 'OPPOSED'

def run(xs):
    out=[]
    for x in xs:
        c=context(datetime.fromisoformat(x['entry_time']))
        out.append({**x,'astro':c,'astro_alignment':tag(x,c)})
    groups={k:[x for x in out if x['astro_alignment']==k] for k in ('ALIGNED','OPPOSED','NEUTRAL')}
    active=[x for x in out if x['astro']['activity_vote']>0]
    calm=[x for x in out if x['astro']['activity_vote']<0]
    return {'all':stats(out),'by_alignment':{k:stats(v) for k,v in groups.items()},'higher_activity_vote':stats(active),'lower_activity_vote':stats(calm),'rows':out}

def main():
    q=json.loads(SRC.read_text())
    opp=run(q.get('opportunities',[]));seq=run(q.get('sequential_trades',[]))
    out={'version':'DARA-Astro-AB-Sep7-v1','astro_training':'BTC daily 2022-08/2026 only; Sep7 unseen by astro calibration','base_model':'DARA v18 Prototype Memory Sep7','warning':'Tiny one-day incremental A/B; diagnostic only, not proof. No thresholds were tuned on Sep7.','opportunities':opp,'sequential':seq}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'opp':{k:v for k,v in opp.items() if k!='rows'},'seq':{k:v for k,v in seq.items() if k!='rows'}},indent=2))
if __name__=='__main__':main()
