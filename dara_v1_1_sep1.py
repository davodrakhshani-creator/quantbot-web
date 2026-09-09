import json
from datetime import datetime, timezone
from pathlib import Path
import dara_v1_sep1 as b

OUT=Path('data/dara_v1_1_sep1.json')
MIN_STOP=0.0014  # 0.14%, noise/cost guard learned from v1.0


def confirmed_event(i,m1,f5,f15):
    # For BREAK_ACCEPT and SWEEP_RECLAIM, v1.1 requires one fully closed
    # follow-through minute before entry. Other rare event types retain v1.0 logic.
    if i < 2:
        return None
    prev_sig=b.event_signal(i-1,m1,f5,f15)
    if prev_sig and prev_sig[0] in ('BREAK_ACCEPT','SWEEP_RECLAIM'):
        setup,side,raw_stop=prev_sig
        e=m1[i-1]   # original event candle
        x=m1[i]     # closed confirmation candle
        vol=x['v']/x['medv']
        if setup=='BREAK_ACCEPT':
            if side=='LONG':
                level=e['prev20h']
                ok=(x['c']>level and x['c']>=e['c']*0.9998 and x['flow']>=1.20 and vol>=0.80 and x['l']>=level*0.9995)
            else:
                level=e['prev20l']
                ok=(x['c']<level and x['c']<=e['c']*1.0002 and x['flow']<=1/1.20 and vol>=0.80 and x['h']<=level*1.0005)
        else: # SWEEP_RECLAIM
            if side=='LONG':
                level=e['prev20l']; mid=(e['h']+e['l'])/2
                ok=(x['l']>=e['l'] and x['c']>level and x['c']>=mid and x['flow']>=1.20 and vol>=0.80)
            else:
                level=e['prev20h']; mid=(e['h']+e['l'])/2
                ok=(x['h']<=e['h'] and x['c']<level and x['c']<=mid and x['flow']<=1/1.20 and vol>=0.80)
        if ok:
            return (setup,side,raw_stop)
        return None
    # Keep the rare impulse/extreme modules unchanged; don't add hindsight filters to them.
    cur=b.event_signal(i,m1,f5,f15)
    if cur and cur[0] in ('IMPULSE_RETEST','EXTREME_REVERSION'):
        return cur
    return None


def simulate_v11(m1,entry_i,setup,side,raw_stop,equity):
    entry=m1[entry_i]['o']
    stop_pct=(entry-raw_stop)/entry if side=='LONG' else (raw_stop-entry)/entry
    # v1.0 losers both had ~0.122-0.123% structural stops; v1.1 rejects
    # structures too close to total friction/noise instead of artificially widening them.
    if stop_pct < MIN_STOP or stop_pct > 0.0035:
        return None
    if b.TP1/stop_pct < 1.5:
        return None
    return b.simulate(m1,entry_i,setup,side,raw_stop,equity)


def main():
    raw=[]
    for d in [datetime(2026,8,30,tzinfo=timezone.utc),datetime(2026,8,31,tzinfo=timezone.utc),datetime(2026,9,1,tzinfo=timezone.utc)]:
        raw += b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m1=b.minute_features(raw)
    f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)}
    f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)}
    equity=b.START_EQUITY; peak=equity; maxdd=0; trades=[]; i=0; last_exit=-10**9; consec=0; pause_reg=None
    rejected_noise=0
    while i<len(m1)-1:
        x=m1[i]
        if x['t']<b.START_MS: i+=1; continue
        if x['t']>=b.END_MS: break
        if len(trades)>=b.MAX_TRADES or equity<=b.START_EQUITY*(1+b.DAILY_STOP): break
        if i-last_exit<b.COOLDOWN: i+=1; continue
        a15=f15.get(b.last_closed(x['t'],15))
        if pause_reg is not None:
            if a15 and a15['regime']!=pause_reg: pause_reg=None; consec=0
            else: i+=1; continue
        sig=confirmed_event(i,m1,f5,f15)
        if not sig: i+=1; continue
        setup,side,raw_stop=sig; entry_i=i+1
        if entry_i>=len(m1) or m1[entry_i]['t']>=b.END_MS: break
        entry=m1[entry_i]['o']
        sp=(entry-raw_stop)/entry if side=='LONG' else (raw_stop-entry)/entry
        if sp<MIN_STOP:
            rejected_noise+=1; i+=1; continue
        sim=simulate_v11(m1,entry_i,setup,side,raw_stop,equity)
        if sim is None: i+=1; continue
        j,px,reason,gross,cost,net,added,notional=sim
        before=equity; equity+=net; peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak)
        et=datetime.fromtimestamp(m1[entry_i]['t']/1000,timezone.utc).astimezone(b.TEHRAN)
        xt=datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(b.TEHRAN)
        stop_pct=(m1[entry_i]['o']-raw_stop)/m1[entry_i]['o'] if side=='LONG' else (raw_stop-m1[entry_i]['o'])/m1[entry_i]['o']
        trades.append({'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m1[entry_i]['o'],2),'exit':round(px,2),'structural_stop_pct':round(stop_pct*100,3),'reason':reason,'added':added,'notional':round(notional,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)})
        if net>0: consec=0; pause_reg=None
        else:
            consec+=1
            if consec>=2: pause_reg=a15['regime'] if a15 else 'UNKNOWN'
        last_exit=j; i=j+1
    summary=b.summarize(trades,equity,peak,maxdd)
    summary['noise_guard_rejections']=rejected_noise
    payload={
      'version':'DARA-v1.1-FollowThrough-NoiseGuard-InSample',
      'period_tehran':[b.START.isoformat(),b.END.isoformat()],
      'symbol':'BTCUSDT USD-M perpetual',
      'source':'Binance official USD-M 1m futures klines; 5m/15m aggregated; taker-buy volume as aggressor-flow proxy; no historical L2 claimed',
      'changes_from_v1.0':[
        'BREAK_ACCEPT and SWEEP_RECLAIM require one additional closed 1m follow-through/hold confirmation before entry.',
        'Noise/Cost Guard rejects structural stop distances below 0.14% instead of widening the stop.',
        'Risk, TP1=0.33%, 50/50 runner, add-once, max exposure, daily stop, and all other event logic unchanged.'
      ],
      'methodology_notes':[
        'These changes were designed after inspecting the Sep-1 DARA v1.0 losses, so this rerun is explicitly in-sample and cannot validate the strategy.',
        'No parameter was changed after seeing the v1.1 rerun result.',
        'Stop is checked before target when both occur in one 1m bar.'
      ],
      'summary':summary,
      'trades':trades
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2)); print(json.dumps(trades,indent=2))

if __name__=='__main__': main()
