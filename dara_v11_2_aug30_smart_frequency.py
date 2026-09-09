import json
from datetime import datetime, timezone
from pathlib import Path
import dara_v11_liquidity_transition_sep1_8 as r
import dara_v11_1_exhaustion_filter_sep1_8 as f

# Fresh one-day test requested by user: Tehran Aug 30, 2026.
# v11.2 deliberately raises intelligent opportunity frequency without changing risk/friction.
r.START=datetime(2026,8,30,0,0,tzinfo=r.TEHRAN)
r.END=datetime(2026,8,31,0,0,tzinfo=r.TEHRAN)
r.START_MS=int(r.START.astimezone(timezone.utc).timestamp()*1000)
r.END_MS=int(r.END.astimezone(timezone.utc).timestamp()*1000)
r.OUT=Path('data/dara_v11_2_aug30_smart_frequency.json')
r.MAX_TRADES_DAY=6
r.COOLDOWN=18
r.LOSS_LOCK=45

orig_liq=r.liquidity_transition
orig_pb=r.failed_pullback

# Keep the discovered exhaustion idea, but soften its hard threshold into a quality band.
def liquidity_v112(i,m,f5,f15):
    s=orig_liq(i,m,f5,f15)
    if not s: return None
    setup,side,stop,ci,score,d,tp=s
    d3=d.get('delta3',0.0); ed=d.get('event_delta',0.0); flip=d.get('flip_delta',0.0)
    prior=(d3<=-0.08) if side=='LONG' else (d3>=0.08)
    # best <=.10; allow .10-.14 only with a decisive control flip and stronger sweep participation
    exhausted=abs(ed)<=0.10
    borderline=(abs(ed)<=0.14 and abs(flip)>=0.20 and d.get('volr',0)>=1.25)
    if not (prior and (exhausted or borderline)):
        r.rej('liq_smart_exhaustion'); return None
    d={**d,'v11_2_quality':'exhausted' if exhausted else 'borderline_confirmed'}
    return setup,side,stop,ci,score,d,tp

# Secondary continuation engine: still requires failed counterflow, but accepts quiet-to-normal pullbacks.
def pullback_v112(i,m,f5,f15):
    s=orig_pb(i,m,f5,f15)
    if not s:return None
    setup,side,stop,ci,score,d,tp=s
    vol=d.get('volr',99); flip=abs(d.get('flip_delta',0))
    if vol>1.35:
        r.rej('pb_volume_cap'); return None
    if vol>1.10 and flip<0.22:
        r.rej('pb_borderline_weak_flip'); return None
    d={**d,'v11_2_quality':'quiet_pullback' if vol<=1.10 else 'normal_pullback_strong_flip'}
    return setup,side,stop,ci,score,d,tp

r.liquidity_transition=liquidity_v112
r.failed_pullback=pullback_v112
r.rejects.clear(); r.q.rejects.clear()

if __name__=='__main__':
    r.main()
    p=json.loads(r.OUT.read_text())
    p['version']='DARA-v11.2-Smart-Frequency-Aug30-Frozen'
    p['methodology_notes']=[
      'Fresh one-day Tehran Aug 30, 2026 test; parameters frozen before seeing Aug30 result.',
      'Historical full L2 unavailable; Binance USD-M 1m taker-buy delta is an aggressor-flow proxy.',
      'Risk 0.25%, modeled roundtrip friction 0.11%, max exposure 3x unchanged.',
      'Frequency raised intelligently: max 6 trades/day, 18m cooldown, 45m same-side loss lock; exhaustion and pullback filters softened conditionally.',
      'Profitable target floor remains 0.33% gross = 3x modeled roundtrip fee; adaptive targets may rise up to 0.55% = 5x fee. Normal profitable exits are not intentionally taken below the target floor.'
    ]
    p['requested_profit_rule']='Normal profit target >= 3x full modeled roundtrip fee (0.33%); adaptive target can reach 4x fee (0.44%) and up to 5x fee (0.55%). Stops/thesis-fail exits may close earlier when losing.'
    r.OUT.write_text(json.dumps(p,indent=2))
    print(json.dumps(p['overall'],indent=2))
    print(json.dumps(p.get('by_setup',{}),indent=2))
