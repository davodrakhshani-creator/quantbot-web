import json
from datetime import datetime, timezone
from pathlib import Path
import dara_v1_sep1 as b

OUT=Path('data/dara_v1_2_sep1.json')
MIN_STOP=0.0014


def event_v12(i,m1,f5,f15):
    # BREAK_ACCEPT keeps native v1.0 timing; noise guard will reject too-tight structures.
    cur=b.event_signal(i,m1,f5,f15)
    if cur and cur[0]=='BREAK_ACCEPT':
        return cur
    # SWEEP_RECLAIM gets one closed follow-through minute because failed-break entries
    # were the most fragile form in v1.0.
    if i>=2:
        prev=b.event_signal(i-1,m1,f5,f15)
        if prev and prev[0]=='SWEEP_RECLAIM':
            setup,side,raw_stop=prev; e=m1[i-1]; x=m1[i]; vol=x['v']/x['medv']
            if side=='LONG':
                level=e['prev20l']; mid=(e['h']+e['l'])/2
                ok=(x['l']>=e['l'] and x['c']>level and x['c']>=mid and x['flow']>=1.15 and vol>=0.70)
            else:
                level=e['prev20h']; mid=(e['h']+e['l'])/2
                ok=(x['h']<=e['h'] and x['c']<level and x['c']<=mid and x['flow']<=1/1.15 and vol>=0.70)
            if ok:return (setup,side,raw_stop)
            return None
    # Rare modules unchanged.
    if cur and cur[0] in ('IMPULSE_RETEST','EXTREME_REVERSION'):
        return cur
    return None


def main():
    raw=[]
    for d in [datetime(2026,8,30,tzinfo=timezone.utc),datetime(2026,8,31,tzinfo=timezone.utc),datetime(2026,9,1,tzinfo=timezone.utc)]: raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m1=b.minute_features(raw); f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)}; f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)}
    equity=b.START_EQUITY; peak=equity; maxdd=0; trades=[]; i=0; last_exit=-10**9; consec=0; pause_reg=None; rejected_noise=0
    while i<len(m1)-1:
        x=m1[i]
        if x['t']<b.START_MS:i+=1;continue
        if x['t']>=b.END_MS:break
        if len(trades)>=b.MAX_TRADES or equity<=b.START_EQUITY*(1+b.DAILY_STOP):break
        if i-last_exit<b.COOLDOWN:i+=1;continue
        a15=f15.get(b.last_closed(x['t'],15))
        if pause_reg is not None:
            if a15 and a15['regime']!=pause_reg:pause_reg=None;consec=0
            else:i+=1;continue
        sig=event_v12(i,m1,f5,f15)
        if not sig:i+=1;continue
        setup,side,raw_stop=sig; entry_i=i+1
        if entry_i>=len(m1) or m1[entry_i]['t']>=b.END_MS:break
        entry=m1[entry_i]['o']; sp=(entry-raw_stop)/entry if side=='LONG' else (raw_stop-entry)/entry
        if sp<MIN_STOP:
            rejected_noise+=1;i+=1;continue
        sim=b.simulate(m1,entry_i,setup,side,raw_stop,equity)
        if sim is None:i+=1;continue
        j,px,reason,gross,cost,net,added,notional=sim
        before=equity;equity+=net;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak)
        et=datetime.fromtimestamp(m1[entry_i]['t']/1000,timezone.utc).astimezone(b.TEHRAN);xt=datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(b.TEHRAN)
        stop_pct=(m1[entry_i]['o']-raw_stop)/m1[entry_i]['o'] if side=='LONG' else (raw_stop-m1[entry_i]['o'])/m1[entry_i]['o']
        trades.append({'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m1[entry_i]['o'],2),'exit':round(px,2),'structural_stop_pct':round(stop_pct*100,3),'reason':reason,'added':added,'notional':round(notional,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)})
        if net>0:consec=0;pause_reg=None
        else:
            consec+=1
            if consec>=2:pause_reg=a15['regime'] if a15 else 'UNKNOWN'
        last_exit=j;i=j+1
    summary=b.summarize(trades,equity,peak,maxdd);summary['noise_guard_rejections']=rejected_noise
    payload={'version':'DARA-v1.2-NoiseGuard-SelectiveConfirm-InSample','period_tehran':[b.START.isoformat(),b.END.isoformat()],'symbol':'BTCUSDT USD-M perpetual','changes_from_v1.1':['Removed mandatory follow-through for BREAK_ACCEPT because it suppressed valid momentum entries.','Kept 0.14% Noise/Cost Guard for all entries.','Kept one-minute follow-through only for SWEEP_RECLAIM.','All risk, TP1, runner, add-once, max exposure and daily controls unchanged.'],'methodology_notes':['v1.2 was designed after reviewing v1.0 and v1.1 on Sep-1, so this is in-sample optimization only.','No parameter changed after seeing v1.2 result.'],'summary':summary,'trades':trades}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps(summary,indent=2));print(json.dumps(trades,indent=2))

if __name__=='__main__':main()
