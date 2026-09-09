import json
from collections import defaultdict
from pathlib import Path
P=Path('data/dara_v14_sep4_opportunity_sweep.json')
O=Path('data/dara_v14_sep4_context_analysis.json')
p=json.loads(P.read_text());xs=p['opportunities']

def stat(a):
 w=[x for x in a if x['net_pnl']>0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in a if x['net_pnl']<=0)
 return {'n':len(a),'wins':len(w),'wr':round(100*len(w)/len(a),1) if a else None,'gross':round(sum(x['gross_pnl'] for x in a),6),'cost':round(sum(x['cost'] for x in a),6),'net':round(sum(x['net_pnl'] for x in a),6),'pf':round(gp/gl,2) if gl else (99.0 if gp else None),'mfe':round(sum(x['mfe_pct'] for x in a)/len(a),3) if a else None}
def group(fn):
 d=defaultdict(list)
 for x in xs:d[str(fn(x))].append(x)
 return {k:stat(v) for k,v in sorted(d.items())}

def vol(x):
 v=x['diagnostics'].get('volr',0)
 return '<.5' if v<.5 else '.5-1' if v<1 else '1-2' if v<2 else '2-4' if v<4 else '>=4'
def vd(x):
 v=abs(x['diagnostics'].get('vwap_dist',0))
 return '<.05' if v<.05 else '.05-.12' if v<.12 else '.12-.25' if v<.25 else '>=.25'
out={'by_hour':group(lambda x:f"{x['diagnostics'].get('hour',-1):02d}"),'by_reg15':group(lambda x:x['diagnostics'].get('reg15')),'by_reg5_reg15':group(lambda x:x['diagnostics'].get('reg5')+'_'+x['diagnostics'].get('reg15')),'by_volume':group(vol),'by_vwap_abs':group(vd),'by_setup_side':group(lambda x:x['setup']+'_'+x['side'])}
O.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))