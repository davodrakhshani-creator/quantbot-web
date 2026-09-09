import json
from pathlib import Path
import dara_v11_liquidity_transition_sep1_8 as r

OUT=Path('data/dara_v11_1_exhaustion_filter_sep1_8.json')
orig_liq=r.liquidity_transition
orig_pb=r.failed_pullback

# v11.1 keeps v11 architecture/risk/exit frozen and changes only two entry-quality facts
# learned from v11 diagnostics:
# 1) A reversal sweep is valid only when prior 3m aggression exists but event-minute delta has decelerated toward neutral.
# 2) A continuation pullback must be relatively low-volume; high-volume counterflow is treated as a possible real reversal.

def liquidity_transition_v111(i,m,f5,f15):
    s=orig_liq(i,m,f5,f15)
    if not s: return None
    setup,side,stop,ci,score,diag,tp=s
    d3=diag.get('delta3',0.0); ed=diag.get('event_delta',0.0)
    prior_pressure=(d3<=-0.10) if side=='LONG' else (d3>=0.10)
    delta_exhausted=abs(ed)<=0.10
    if not (prior_pressure and delta_exhausted):
        r.rej('liq_exhaustion_filter'); return None
    diag={**diag,'delta_exhausted':True,'v11_1_rule':'3m pressure persists but event-minute delta must collapse toward neutral'}
    return (setup,side,stop,ci,score,diag,tp)

def failed_pullback_v111(i,m,f5,f15):
    s=orig_pb(i,m,f5,f15)
    if not s: return None
    setup,side,stop,ci,score,diag,tp=s
    # Healthy correction: imbalance may be strong, but participation must not expand.
    # v11 already requires volr >=0.90; v11.1 caps it at 1.10.
    if diag.get('volr',99)>1.10:
        r.rej('pb_high_volume_filter'); return None
    diag={**diag,'low_volume_pullback':True,'v11_1_rule':'counterflow relative volume <= 1.10'}
    return (setup,side,stop,ci,score,diag,tp)

r.liquidity_transition=liquidity_transition_v111
r.failed_pullback=failed_pullback_v111
r.OUT=OUT
r.rejects.clear()
r.q.rejects.clear()

if __name__=='__main__':
    r.main()
    p=json.loads(OUT.read_text())
    p['version']='DARA-v11.1-Exhaustion-Quality-Frozen'
    p['change_from_v11']=[
        'Liquidity reversal requires directional 3m pressure but abs(event-minute delta) <= 0.10, proving aggression decelerated at the sweep rather than remained forceful.',
        'Failed pullback requires relative volume <= 1.10; high-volume counterflow is treated as potential true regime reversal, not a healthy pullback.'
    ]
    p['methodology_notes']=[
        'v11.1 was explicitly designed after inspecting v11 Sep1-8 trades. This rerun is highly in-sample and is diagnostic/development evidence only, not validation.',
        'Historical full L2 is unavailable; Binance 1m taker-buy delta remains an aggressor-flow proxy.',
        'Risk, 0.11% modeled friction, 3x max notional, adaptive TP >=0.33%, velocity fail, runner logic, trade/day and daily-stop rules are unchanged from v11.'
    ]
    OUT.write_text(json.dumps(p,indent=2))
    print(json.dumps(p['overall'],indent=2))
    print(json.dumps(p['by_setup'],indent=2))
    print(json.dumps(p['reject_counts'],indent=2))
