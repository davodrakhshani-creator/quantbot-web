import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import dara_v1_sep1 as b
import dara_v1_2_sep1 as v12

TEHRAN=b.TEHRAN
START=datetime(2026,9,2,0,0,tzinfo=TEHRAN)
END=datetime(2026,9,3,0,0,tzinfo=TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000)
END_MS=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v1_2_sep2.json')
MIN_STOP=v12.MIN_STOP

def main():
    raw=[]
    for d in [datetime(2026,8,31,tzinfo=timezone.utc),datetime(2026,9,1,tzinfo=timezone.utc),datetime(2026,9,2,tzinfo=timezone.utc)]:
        raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m1=b.minute_features(raw)
    f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)}
    f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)}
    equity=b.START_EQUITY; peak=equity; maxdd=0.0; trades=[]; i=0; last_exit=-10**9; consec=0; pause_reg=None; rejected_noise=0
    while i<len(m1)-1:
        x=m1[i]
        if x['t']<START_MS: i+=1; continue
        if x['t']>=END_MS: break
        if len(trades)>=b.MAX_TRADES or equity<=b.START_EQUITY*(1+b.DAILY_STOP): break
        if i-last_exit<b.COOLDOWN: i+=1; continue
        a15=f15.get(b.last_closed(x['t'],15))
        if pause_reg is not None:
            if a15 and a15['regime']!=pause_reg: pause_reg=None; consec=0
            else: i+=1; continue
        sig=v12.event_v12(i,m1,f5,f15)
        if not sig: i+=1; continue
        setup,side,raw_stop=sig
        entry_i=i+1
        if entry_i>=len(m1) or m1[entry_i]['t']>=END_MS: break
        entry=m1[entry_i]['o']
        sp=(entry-raw_stop)/entry if side=='LONG' else (raw_stop-entry)/entry
        if sp<MIN_STOP:
            rejected_noise+=1; i+=1; continue
        sim=b.simulate(m1,entry_i,setup,side,raw_stop,equity)
        if sim is None: i+=1; continue
        j,px,reason,gross,cost,net,added,notional=sim
        # never let simulation spill beyond Tehran test day: force-close at last bar before END if necessary
        if m1[j]['t']>=END_MS:
            j=max(entry_i, next((k-1 for k in range(entry_i,len(m1)) if m1[k]['t']>=END_MS), len(m1)-1))
        before=equity; equity+=net; peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak)
        et=datetime.fromtimestamp(m1[entry_i]['t']/1000,timezone.utc).astimezone(TEHRAN)
        xt=datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        stop_pct=(entry-raw_stop)/entry if side=='LONG' else (raw_stop-entry)/entry
        trades.append({'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(entry,2),'exit':round(px,2),'structural_stop_pct':round(stop_pct*100,3),'reason':reason,'added':added,'notional':round(notional,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)})
        if net>0: consec=0; pause_reg=None
        else:
            consec+=1
            if consec>=2: pause_reg=a15['regime'] if a15 else 'UNKNOWN'
        last_exit=j; i=j+1
    summary=b.summarize(trades,equity,peak,maxdd); summary['noise_guard_rejections']=rejected_noise
    payload={'version':'DARA-v1.2-FROZEN-Sep2-OutOfSampleRelativeToV1.2','period_tehran':[START.isoformat(),END.isoformat()],'symbol':'BTCUSDT USD-M perpetual','rules_note':'Exact DARA v1.2 logic from Sep1 retained: 0.14% Noise/Cost Guard; BreakAccept native timing; Sweep one-minute follow-through; TP1 0.33% with 50/50 runner; one add after +1R; risk 0.25%; max exposure 3x; max 5 trades; daily stop -1%.','methodology_notes':['No strategy threshold changed for Sep2 after DARA v1.2 was fixed on Sep1.','Sep2 had been seen by older unrelated strategy versions, but not used to tune DARA v1.2.','Historical full L2 is unavailable; Binance 1m taker-buy volume remains the order-flow proxy.'],'summary':summary,'trades':trades}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2)); print(json.dumps(trades,indent=2))

if __name__=='__main__': main()
