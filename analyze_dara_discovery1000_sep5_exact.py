import json
from collections import defaultdict
from pathlib import Path

SRC=Path('data/dara_discovery1000_sep5_exact.json')
OUT=Path('data/dara_discovery1000_sep5_exact_analysis.json')
p=json.loads(SRC.read_text());xs=p['trades']

def stat(a):
    w=[x for x in a if x['net_pnl']>0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in a if x['net_pnl']<=0)
    return {'n':len(a),'wins':len(w),'wr':round(100*len(w)/len(a),1) if a else None,'gross':round(sum(x['gross_pnl'] for x in a),6),'cost':round(sum(x['cost'] for x in a),6),'net':round(sum(x['net_pnl'] for x in a),6),'pf':round(gp/gl,2) if gl else (99.0 if gp else None),'avg_mfe':round(sum(x['mfe_pct'] for x in a)/len(a),3) if a else None,'avg_mae':round(sum(x['mae_pct'] for x in a)/len(a),3) if a else None}
def group(fn):
    d=defaultdict(list)
    for x in xs:d[str(fn(x))].append(x)
    return {k:stat(v) for k,v in d.items()}

def volb(x):
    v=x['diagnostics'].get('volr',0)
    return '<.5' if v<.5 else '.5-1' if v<1 else '1-2' if v<2 else '2-4' if v<4 else '>=4'
def delt(x):
    z=abs(x['diagnostics'].get('delta',0))
    return '<.2' if z<.2 else '.2-.4' if z<.4 else '.4-.6' if z<.6 else '>=.6'
def vwapb(x):
    z=abs(x['diagnostics'].get('vwap_dist',0))
    return '<.03' if z<.03 else '.03-.05' if z<.05 else '.05-.10' if z<.10 else '.10-.20' if z<.20 else '>=.20'

def h4(x):
    h=x['diagnostics'].get('hour',0);return f'{(h//4)*4:02d}-{(h//4)*4+3:02d}'

def combo(x):return f"{x['diagnostics'].get('state')}|{x['side']}|{h4(x)}|{volb(x)}|{vwapb(x)}"

out={
 'overall':stat(xs),
 'by_exit':group(lambda x:x['reason']),
 'by_state_side':group(lambda x:f"{x['diagnostics'].get('state')}|{x['side']}"),
 'by_setup_state':group(lambda x:f"{x['setup']}|{x['diagnostics'].get('state')}"),
 'by_hour4_side':group(lambda x:f"{h4(x)}|{x['side']}"),
 'by_delta':group(delt),
 'by_volume':group(volb),
 'by_vwap_fine':group(vwapb),
 'top_contexts':{},
 'winner_profile':{}
}
# contexts with at least 8 samples, sort by gross then WR
cg=defaultdict(list)
for x in xs:cg[combo(x)].append(x)
rank=[]
for k,a in cg.items():
    if len(a)>=8:
        s=stat(a);rank.append((s['gross'],s['wr'],k,s))
rank=sorted(rank,reverse=True)[:20]
out['top_contexts']={k:s for _,__,k,s in rank}
w=[x for x in xs if x['net_pnl']>0]
out['winner_profile']={
 'n':len(w),
 'setup_counts':dict(__import__('collections').Counter(x['setup'] for x in w)),
 'state_counts':dict(__import__('collections').Counter(x['diagnostics'].get('state') for x in w)),
 'side_counts':dict(__import__('collections').Counter(x['side'] for x in w)),
 'hour4_counts':dict(__import__('collections').Counter(h4(x) for x in w)),
 'exit_counts':dict(__import__('collections').Counter(x['reason'] for x in w)),
 'median_mfe':sorted(x['mfe_pct'] for x in w)[len(w)//2] if w else None,
 'median_mae':sorted(x['mae_pct'] for x in w)[len(w)//2] if w else None,
}
# Compare primary playbooks only vs diagnostic fallback
primary=[x for x in xs if x['setup']!='STATE_HYPOTHESIS'];fallback=[x for x in xs if x['setup']=='STATE_HYPOTHESIS']
out['primary_only']=stat(primary);out['fallback_only']=stat(fallback)
OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
