import json
from pathlib import Path
from collections import defaultdict

SRC=Path('data/dara_aug20_learned_replay_v3.json')
OUT=Path('data/dara_aug20_v3_loss_analysis.json')

def val(x,k,default=None):
    return x.get('diagnostics',{}).get(k,default)

def feat(x):
    d=x.get('diagnostics',{})
    return {
      'n':x.get('n'),'side':x.get('side'),'entry_time':x.get('entry_time'),'reason':x.get('reason'),
      'net':x.get('net_pnl'),'mfe':x.get('mfe_pct'),'mae':x.get('mae_pct'),
      'p':d.get('prototype_p'),'wd':d.get('prototype_winner_days'),'pattern_score':d.get('pattern_score'),
      'primary':d.get('pattern_primary_families'),'patterns':d.get('patterns'),'families':d.get('families'),
      'cap':d.get('capacity_score_pct'),'expected_move':d.get('expected_move_pct'),
      'reg5':d.get('reg5'),'reg15':d.get('reg15'),'volr':d.get('volr'),'delta':d.get('delta'),
      'dtd':d.get('dtd_pct'),'r3':d.get('r3_signed_pct'),'r6':d.get('r6_signed_pct'),
      'memory_strong':d.get('memory_strong'),'triple':d.get('triple_primary_confluence')
    }

def stats(xs):
    if not xs:return {}
    return {'n':len(xs),'wr':sum(x.get('net_pnl',0)>0 for x in xs)/len(xs),
            'net':sum(x.get('net_pnl',0) for x in xs),
            'mfe':sum(x.get('mfe_pct',0) for x in xs)/len(xs),
            'mae':sum(x.get('mae_pct',0) for x in xs)/len(xs)}

def bucket(name,fn,xs):
    g=defaultdict(list)
    for x in xs:g[str(fn(x))].append(x)
    return {k:stats(v) for k,v in g.items()}

def main():
    q=json.loads(SRC.read_text())
    xs=q['trades']
    losses=[x for x in xs if x.get('net_pnl',0)<=0]
    wins=[x for x in xs if x.get('net_pnl',0)>0]
    out={
      'losses':[feat(x) for x in losses],
      'wins':[feat(x) for x in wins],
      'buckets':{
        'delta':bucket('delta',lambda x: '<.05' if abs(val(x,'delta',0))<.05 else '.05-.15' if abs(val(x,'delta',0))<.15 else '.15-.35' if abs(val(x,'delta',0))<.35 else '>=.35',xs),
        'volr':bucket('volr',lambda x: '<.5' if val(x,'volr',0)<.5 else '.5-1' if val(x,'volr',0)<1 else '1-2' if val(x,'volr',0)<2 else '2-4' if val(x,'volr',0)<4 else '>=4',xs),
        'r3':bucket('r3',lambda x: '<.05' if val(x,'r3',0)<.05 else '.05-.15' if val(x,'r3',0)<.15 else '.15-.35' if val(x,'r3',0)<.35 else '>=.35',xs),
        'r6':bucket('r6',lambda x: '<.10' if val(x,'r6',0)<.10 else '.10-.25' if val(x,'r6',0)<.25 else '.25-.60' if val(x,'r6',0)<.60 else '>=.60',xs),
        'dtd':bucket('dtd',lambda x: '<1' if abs(val(x,'dtd',0))<1 else '1-2' if abs(val(x,'dtd',0))<2 else '>=2',xs),
        'primary':bucket('primary',lambda x: val(x,'primary',0),xs),
        'p':bucket('p',lambda x: '<.30' if val(x,'p',0)<.30 else '.30-.34' if val(x,'p',0)<.34 else '.34-.38' if val(x,'p',0)<.38 else '>=.38',xs),
      }
    }
    OUT.write_text(json.dumps(out,indent=2))
    print(json.dumps(out,indent=2))
if __name__=='__main__':main()
