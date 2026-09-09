import json
from pathlib import Path
import dara_v12_1_aug30_phase_aware as s

OUT=Path('data/dara_v12_2_aug30_fresh_ignition.json')
s.OUT=OUT
orig=s.ignition

def fresh_ignition(i,m,f5,f15):
    x=orig(i,m,f5,f15)
    if not x:return None
    setup,side,stop,ci,score,d,tp=x
    z=m[i]; a5=f5.get(s.r.b.last_closed(z['t'],5)); a15=f15.get(s.r.b.last_closed(z['t'],15))
    prev5=f5.get(s.r.b.last_closed(z['t']-5*60*1000,5))
    pre=m[i-8:i]
    if not a5 or not a15 or not prev5 or len(pre)<8:return None
    if side=='LONG':
        micro_break=z['c']>max(q['h'] for q in pre)*1.00002
        fresh=(a5['regime']=='RANGE' and a15['regime']=='UP') or (a5['regime']=='UP' and prev5['regime']!='UP' and a15['regime']!='DOWN')
    else:
        micro_break=z['c']<min(q['l'] for q in pre)*0.99998
        fresh=(a5['regime']=='RANGE' and a15['regime']=='DOWN') or (a5['regime']=='DOWN' and prev5['regime']!='DOWN' and a15['regime']!='UP')
    # Need actual participation on the first ignition bar; later low-volume follow-through is not a new entry.
    if not(fresh and micro_break and z['volr']>=1.15):return None
    d={**d,'fresh_transition':True,'micro_range_break':True}
    return setup,side,stop,ci,score+2,d,tp

s.ignition=fresh_ignition
if __name__=='__main__':
    s.main()
    p=json.loads(OUT.read_text())
    p['version']='DARA-v12.2-FreshIgnition-Aug30-Development'
    p['changes'].append('Momentum ignition now requires a fresh 5m state transition or RANGE within 15m trend, an 8-minute micro-range break, and relative volume >=1.15; mature-trend ignition signals are quarantined.')
    p['methodology_notes'][0]='Highly in-sample development: v12.2 was designed after inspecting v12.1 Aug30 trades. Not validation.'
    OUT.write_text(json.dumps(p,indent=2))
    print(json.dumps(p['overall'],indent=2))
