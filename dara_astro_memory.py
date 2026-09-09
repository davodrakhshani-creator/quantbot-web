"""DARA Astro Memory: converts pre-Sep-2026 exploratory calibration into weak timing votes.

Never an entry trigger. Intended for discovery/A-B evaluation until incremental
1m/5m/15m after-fee value is demonstrated out of sample.
"""
import json
from pathlib import Path
from dara_astro_features import snapshot

CAL=Path('data/dara_astro_calibration_2022_2026.json')

def snapshot_conditions(a):
    out={'MOON_PHASE:'+a['moon_phase_octant']}
    if a['eclipse_geometry_proxy']>=0.5:out.add('ECLIPSE_GEOMETRY_PROXY_HIGH')
    for body,z in a['retrograde'].items():out.add(f"{body}_{'RETROGRADE' if z['retrograde'] else 'DIRECT'}")
    for z in a['major_aspects']:out.add(f"ASPECT:{z['pair']}:{z['aspect']}")
    return out

def memory():
    q=json.loads(CAL.read_text())
    return {x['condition']:x for x in q.get('selected_recurrent_conditions',[])}

def context(dt):
    a=snapshot(dt); cond=snapshot_conditions(a); mem=memory()
    directional=0.0; activity=0.0; matched=[]
    for c in sorted(cond & set(mem)):
        z=mem[c]; rec={'condition':c,'votes':[]}
        for e in z['persistent_edges']:
            metric=e['metric']; sg=1 if e['direction']=='higher' else -1
            # Directional market proxies. Each recurring metric contributes one weak vote.
            if metric in ('return','buy_imbalance'):
                directional += sg
                rec['votes'].append({'type':'direction','metric':metric,'vote':sg})
            elif metric in ('volume_surprise','abs_return'):
                activity += sg
                rec['votes'].append({'type':'activity','metric':metric,'vote':sg})
        matched.append(rec)
    return {
      'directional_vote':directional,
      'activity_vote':activity,
      'direction_label':'LONG_BIAS' if directional>0 else ('SHORT_BIAS' if directional<0 else 'NEUTRAL'),
      'activity_label':'HIGHER' if activity>0 else ('LOWER' if activity<0 else 'NEUTRAL'),
      'matched':matched,
      'policy':'weak research prior only; never opens a trade by itself'
    }
