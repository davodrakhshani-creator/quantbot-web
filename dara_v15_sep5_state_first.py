import json
from datetime import datetime, timezone
from pathlib import Path
import dara_v14_sep4_opportunity_sweep as v

# Fresh Sep5 development/experience day. Rules below are frozen before seeing Sep5 results.
v.START=datetime(2026,9,5,0,0,tzinfo=v.TEHRAN)
v.END=datetime(2026,9,6,0,0,tzinfo=v.TEHRAN)
v.S=int(v.START.astimezone(timezone.utc).timestamp()*1000)
v.E=int(v.END.astimezone(timezone.utc).timestamp()*1000)
v.OUT=Path('data/dara_v15_sep5_state_first.json')
v.RISK=0.0002

# Lessons locked from Sep1-4:
# - raw sweep/reclaim, raw pullback, old flow-efficiency remain quarantined
# - tiny stops remain banned; full modeled RT friction stays 0.11%
# - 3x-fee profit floor and early failure detection remain active
# - setup quality is regime-dependent; choose state first, then playbook
# - shorts are not mirror images of longs
# - extreme volume can be late/FOMO; prefer moderate expansion when possible
# - clock/session is context only, never a stand-alone gate

orig_micro=v.micro_break
orig_comp=v.compression_release
orig_imp=v.impulse_retest
orig_value=v.value_escape

def _state(i,m,f5,f15):
    z=m[i];a5,a15=v.ctx(z,f5,f15)
    if not a5 or not a15:return None
    vd=(z['c']/z['vwap15']-1)*100
    vol=z['volr'];delta=z['delta']
    r3=(z['c']/m[i-3]['c']-1)*100 if i>=3 else 0.0
    # State labels are directional/contextual, not trade signals by themselves.
    if a15['regime']=='UP' and a5['regime']=='UP': st='TREND_UP'
    elif a15['regime']=='DOWN' and a5['regime']=='DOWN': st='TREND_DOWN'
    elif a15['regime']=='RANGE' and a5['regime']=='DOWN': st='TRANSITION_DOWN'
    elif a15['regime']=='RANGE' and a5['regime']=='UP': st='TRANSITION_UP'
    elif a15['regime']=='UP' and a5['regime']=='RANGE': st='UP_PAUSE'
    elif a15['regime']=='DOWN' and a5['regime']=='RANGE': st='DOWN_PAUSE'
    else: st='BALANCE'
    return st,vol,vd,delta,r3,a5,a15

def _tag(x,st):
    if not x:return None
    setup,side,stop,ei,score,diag,tp=x
    diag={**diag,'market_state':st}
    return setup,side,stop,ei,score,diag,tp

def candidates(i,m,f5,f15):
    s=_state(i,m,f5,f15)
    if not s:return []
    st,vol,vd,delta,r3,a5,a15=s
    out=[]

    # Hard learned anti-noise zones. Sep4 showed .5-1x and >=4x were often weak;
    # we do not ban them universally, but require stronger structure there.
    moderate=(2.0<=vol<4.0)
    quiet=(vol<1.0)
    extreme=(vol>=4.0)

    if st=='TREND_UP':
        # Prefer retest/reload over blind chasing. Micro-break only if fresh + moderate expansion.
        x=orig_imp(i,m,f5,f15)
        if x and (moderate or (quiet and abs(vd)>=0.12)): out.append(_tag(x,st))
        x=orig_micro(i,m,f5,f15)
        if x:
            rng=x[5].get('range8_pct',9)
            fresh=(rng<=0.24 and 0.08<=abs(vd)<=0.32)
            if fresh and moderate: out.append(_tag(x,st))
        x=orig_value(i,m,f5,f15)
        if x and 0.20<=abs(vd)<=0.45 and vol<4.0: out.append(_tag(x,st))

    elif st=='TREND_DOWN':
        # Sep3/4 shorts were far weaker than longs. Demand renewed downside control and avoid raw micro-break chase.
        x=orig_imp(i,m,f5,f15)
        if x and x[1]=='SHORT' and moderate and delta<=-0.18: out.append(_tag(x,st))
        x=orig_comp(i,m,f5,f15)
        if x and x[1]=='SHORT' and moderate and abs(vd)>=0.10: out.append(_tag(x,st))
        x=orig_value(i,m,f5,f15)
        if x and x[1]=='SHORT' and moderate and abs(vd)>=0.22: out.append(_tag(x,st))

    elif st=='TRANSITION_DOWN':
        # Best Sep4 state sample was 5m DOWN inside 15m RANGE; use transition/escape logic, not trend chase.
        x=orig_comp(i,m,f5,f15)
        if x and x[1]=='SHORT' and vol<4.0: out.append(_tag(x,st))
        x=orig_value(i,m,f5,f15)
        if x and x[1]=='SHORT' and abs(vd)>=0.18 and vol<4.0: out.append(_tag(x,st))
        # A failed continuation can also resolve upward; only allow long on strong displacement away from value.
        if vd<=-0.25 and delta>0.18 and r3>0:
            x=orig_imp(i,m,f5,f15)
            if x and x[1]=='LONG': out.append(_tag(x,st))

    elif st=='TRANSITION_UP':
        x=orig_comp(i,m,f5,f15)
        if x and x[1]=='LONG' and vol<4.0: out.append(_tag(x,st))
        x=orig_value(i,m,f5,f15)
        if x and x[1]=='LONG' and abs(vd)>=0.18 and vol<4.0: out.append(_tag(x,st))
        if vd>=0.25 and delta<-0.18 and r3<0:
            x=orig_imp(i,m,f5,f15)
            if x and x[1]=='SHORT': out.append(_tag(x,st))

    elif st in ('UP_PAUSE','DOWN_PAUSE'):
        # Pause states: only compression release or retest; no direct micro-break chase.
        x=orig_comp(i,m,f5,f15)
        if x and vol<4.0: out.append(_tag(x,st))
        x=orig_imp(i,m,f5,f15)
        if x and vol<4.0: out.append(_tag(x,st))

    else:  # BALANCE
        # In balance, only genuine compression release; do not invent directional flow trades near VWAP.
        x=orig_comp(i,m,f5,f15)
        if x and moderate and abs(vd)>=0.08: out.append(_tag(x,st))

    # Deduplicate and state-score. Prioritize transition/retest over generic break.
    uniq=[];seen=set()
    bonuses={'IMPULSE_RETEST':3,'COMPRESSION_RELEASE':2,'VALUE_ESCAPE':1,'MICRO_BREAK':0}
    for x in out:
        if not x:continue
        key=(x[0],x[1],x[3])
        if key in seen:continue
        seen.add(key)
        setup,side,stop,ei,score,diag,tp=x
        # penalize extreme FOMO even if a legacy setup admitted it
        score=score+bonuses.get(setup,0)-int(extreme)
        uniq.append((setup,side,stop,ei,score,diag,tp))
    return sorted(uniq,key=lambda x:x[4],reverse=True)

v.candidates=candidates

if __name__=='__main__':
    v.main()
    p=json.loads(v.OUT.read_text())
    p['version']='DARA-v15-Sep5-StateFirst-Frozen'
    p['purpose']='Sep5 experience day after Sep1-4 learning. Every state-qualified opportunity is tested; separate sequential account path retained.'
    p['learned_from_sep1_4']=[
      'State-first playbook routing replaces setup-first selection.',
      'Raw sweep/reclaim, raw pullback and old flow-efficiency remain quarantined.',
      'Micro-break is no longer globally preferred; only fresh moderate-volume trend-up breaks are admitted.',
      'Short logic is asymmetric and requires stronger downside control/transition context.',
      'Extreme >=4x relative volume is treated as possible late/FOMO participation, not automatic confirmation.',
      'Clock/session is diagnostic context only, not a hard entry rule.',
      'Early no-follow, weak-progress exits, minimum 0.16% structural stop and 3x-fee protection remain active.'
    ]
    v.OUT.write_text(json.dumps(p,indent=2))
    print(json.dumps(p.get('opportunity_inventory',{}),indent=2))
    print(json.dumps(p.get('sequential',{}),indent=2))
    print(json.dumps(p.get('by_setup',{}),indent=2))
