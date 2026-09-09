import json
from pathlib import Path
import dara_v13_microtrigger_aug30 as d

OUT=Path('data/dara_v14_control_persistence_aug30.json')
orig=d.signal

def persistent_signal(i,b5s,b1m,b5m,b15m):
    s=orig(i,b5s,b1m,b5m,b15m)
    if not s:return None
    setup,side,stop,ci,diag,tp=s
    # Require enough live volatility before paying scalping friction.
    c5=d.ctx_before(b5s[i]['t'],b5m,300000,30); c15=d.ctx_before(b5s[i]['t'],b15m,900000,30)
    if d.atrp(c5)<0.0010 or d.atrp(c15)<0.0018:
        d.rej('activity_gate');return None
    # Wait 30 seconds after the initial micro flip. This is observable confirmation, not look-ahead:
    # the strategy simply enters later, after the confirmation bars have closed.
    end=ci+6
    if end+1>=len(b5s):return None
    xs=b5s[ci+1:end+1]
    buy=sum(x['buy'] for x in xs);sell=sum(x['sell'] for x in xs);delta=(buy-sell)/max(buy+sell,1e-12)
    base=b5s[ci]['c'];close=xs[-1]['c'];progress=(close/base-1) if side=='LONG' else (base/close-1)
    if side=='LONG':
        ok=delta>=0.12 and progress>=0.0007 and sum(1 for x in xs if x['buy']>x['sell'])>=4
        if not ok:d.rej('long_persistence');return None
        stop=min(stop,min(x['l'] for x in b5s[max(0,i-12):end+1]))*0.9998
    else:
        ok=delta<=-0.12 and progress>=0.0007 and sum(1 for x in xs if x['sell']>x['buy'])>=4
        if not ok:d.rej('short_persistence');return None
        stop=max(stop,max(x['h'] for x in b5s[max(0,i-12):end+1]))*1.0002
    diag={**diag,'confirm30_delta':delta,'confirm30_progress_pct':progress*100,'v14':'30s persistent control + >=0.07% price impact + activity gate'}
    return (setup,side,stop,end,diag,tp)

d.signal=persistent_signal
d.OUT=OUT
d.MAX_TRADES=6
d.COOLDOWN_BARS=360 # 30 minutes: do not churn the same micro-noise pocket
d.LOSS_LOCK_BARS=720 # 60 minutes same-side lock after a loss
d.rejects.clear()

if __name__=='__main__':
    d.main()
    p=json.loads(OUT.read_text())
    p['version']='DARA-v14-Control-Persistence-Aug30-Development'
    p['change_from_v13']=[
        'Candidate micro reversal/pullback must be followed by 30 seconds of persistent opposite-side control before entry.',
        'Confirmation must move price at least 0.07% in the intended direction; flow flip without price impact is rejected.',
        '5m ATR must be >=0.10% and 15m ATR >=0.18% to avoid paying friction in dead micro-noise.',
        'Max 6 trades/day, 30m cooldown, 60m same-side lock after loss.'
    ]
    p['rules']=[
        'Decision context 1m/5m/15m; 5s aggTrades only for intra-minute execution confirmation.',
        '0.25% full-stop risk including 0.11% modeled friction, max 3x notional, -1% daily stop.',
        'No normal profitable exit before +0.385% gross move = 3.5x modeled round-trip friction.',
        '50% first TP + 50% runner; negative thesis may exit early only when non-positive.'
    ]
    p['methodology_notes']=[
        'Aug30 is development data for v14 because v13 Aug30 diagnostics were inspected before this revision.',
        'Historical full L2 remains unavailable; raw Binance futures aggTrades give actual aggressor-side trade flow but not resting liquidity.'
    ]
    OUT.write_text(json.dumps(p,indent=2))
    print(json.dumps(p['overall'],indent=2));print(json.dumps(p['by_setup'],indent=2));print(json.dumps(p['reject_counts'],indent=2))
