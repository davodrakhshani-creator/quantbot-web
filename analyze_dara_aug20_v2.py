import json
from collections import defaultdict
from pathlib import Path
q=json.loads(Path('data/dara_aug20_learned_replay_v2.json').read_text());xs=q['trades']

def st(a):
 if not a:return {'n':0}
 w=[x for x in a if x['net_pnl']>0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in a if x['net_pnl']<=0)
 return {'n':len(a),'w':len(w),'wr':round(100*len(w)/len(a),1),'gross':round(sum(x['gross_pnl'] for x in a),6),'fee':round(sum(x['cost'] for x in a),6),'net':round(sum(x['net_pnl'] for x in a),6),'pf':round(gp/gl,2) if gl else 99,'mfe':round(sum(x['mfe_pct'] for x in a)/len(a),3),'mae':round(sum(x['mae_pct'] for x in a)/len(a),3)}
def grp(fn):
 d=defaultdict(list)
 for x in xs:d[str(fn(x))].append(x)
 return {k:st(v) for k,v in sorted(d.items())}
def pb(p):
 if p<.28:return '.25-.28'
 if p<.32:return '.28-.32'
 if p<.36:return '.32-.36'
 return '>=.36'
def vb(v):
 if v<.5:return '<.5'
 if v<1:return '.5-1'
 if v<2:return '1-2'
 if v<4:return '2-4'
 return '>=4'
def db(d):
 d=abs(d)
 if d<.2:return '<.2'
 if d<.4:return '.2-.4'
 if d<.6:return '.4-.6'
 return '>=.6'
def cap(c):
 if c<.33:return '<.33'
 if c<.44:return '.33-.44'
 if c<.60:return '.44-.60'
 return '>=.60'
out={'all':st(xs),'by_reason':grp(lambda x:x['reason']),'by_p':grp(lambda x:pb(x['diagnostics']['prototype_p'])),'by_winner_days':grp(lambda x:x['diagnostics']['prototype_winner_days']),'by_hour4':grp(lambda x:f"{int(x['entry_time'][11:13])//4*4:02d}-{int(x['entry_time'][11:13])//4*4+3:02d}"),'by_vol':grp(lambda x:vb(x['diagnostics']['volr'])),'by_delta':grp(lambda x:db(x['diagnostics']['delta'])),'by_capacity':grp(lambda x:cap(x['diagnostics']['capacity_score_pct']/100)),'by_pattern_primary':grp(lambda x:x['diagnostics']['pattern_primary_families']),'by_pattern_score':grp(lambda x:round(x['diagnostics']['pattern_score'],1)),'by_dtd_band':grp(lambda x:'<1%' if abs(x['diagnostics']['dtd_pct'])<1 else '1-2%' if abs(x['diagnostics']['dtd_pct'])<2 else '>=2%')}
print(json.dumps(out,indent=2));Path('data/dara_aug20_v2_analysis.json').write_text(json.dumps(out,indent=2))
