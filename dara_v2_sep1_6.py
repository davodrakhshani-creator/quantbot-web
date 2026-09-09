import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_v1_sep1 as b

TEHRAN=b.TEHRAN
START=datetime(2026,9,1,0,0,tzinfo=TEHRAN)
END=datetime(2026,9,7,0,0,tzinfo=TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000)
END_MS=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v2_sep1_6.json')
MIN_STOP=0.0014
MAX_STOP=0.0035
MAX_TRADES_DAY=3
DAILY_STOP=-0.0075
COOLDOWN_MIN=45
FAILED_BREAK_LOCK_MIN=90
TP1=b.TP1
COST=b.COST
RISK=b.RISK
MAX_LEV=b.MAX_LEV


def room_ok(x,side):
    if side=='LONG':
        if x['c']>=x['prev240h']: return True
        return (x['prev240h']-x['c'])/x['c']>=0.0055
    if x['c']<=x['prev240l']: return True
    return (x['c']-x['prev240l'])/x['c']>=0.0055


def breakout_candidate(i,m1,f5,f15):
    if i<250:return None
    x=m1[i]; a5=f5.get(b.last_closed(x['t'],5)); a15=f15.get(b.last_closed(x['t'],15))
    if not a5 or not a15:return None
    vol=x['v']/x['medv']
    # DARA v2 requires strict higher-timeframe agreement.
    if a5['regime']=='UP' and a15['regime']=='UP':
        level=x['prev20h']
        if x['c']>level*1.00035 and x['flow']>=1.60 and vol>=1.40 and x['body']>0 and x['c']>x['ema20'] and room_ok(x,'LONG'):
            return ('LONG',level,i)
    if a5['regime']=='DOWN' and a15['regime']=='DOWN':
        level=x['prev20l']
        if x['c']<level*0.99965 and x['flow']<=1/1.60 and vol>=1.40 and x['body']<0 and x['c']<x['ema20'] and room_ok(x,'SHORT'):
            return ('SHORT',level,i)
    return None


def resolve_acceptance(cand,m1,f5,f15):
    side,level,bi=cand
    # 1) HOLD: next two closed minutes must remain accepted beyond level.
    if bi+2>=len(m1):return None
    h1,h2=m1[bi+1],m1[bi+2]
    if side=='LONG':
        hold=(h1['c']>=level*0.9997 and h2['c']>=level*0.9997 and max(h1['h'],h2['h'])>level)
    else:
        hold=(h1['c']<=level*1.0003 and h2['c']<=level*1.0003 and min(h1['l'],h2['l'])<level)
    if not hold:return None

    # 2) RETEST: within next 8 minutes, revisit level but do not decisively lose it.
    ret=None
    for j in range(bi+3,min(bi+11,len(m1))):
        x=m1[j]
        if side=='LONG':
            touched=x['l']<=level*1.0012
            held=x['c']>=level*0.9995
        else:
            touched=x['h']>=level*0.9988
            held=x['c']<=level*1.0005
        if touched and held:
            ret=j;break
    if ret is None:return None

    # 3) RE-ACCELERATION: within 3 minutes after retest, price must push again with flow.
    r=m1[ret]
    for j in range(ret+1,min(ret+4,len(m1))):
        x=m1[j]; vol=x['v']/x['medv']; a5=f5.get(b.last_closed(x['t'],5)); a15=f15.get(b.last_closed(x['t'],15))
        if not a5 or not a15:continue
        if side=='LONG':
            ok=(a5['regime']=='UP' and a15['regime']=='UP' and x['c']>r['h'] and x['c']>x['o'] and x['flow']>=1.35 and vol>=1.0)
            if ok:
                raw_stop=min(r['l'],min(m1[k]['l'] for k in range(bi+1,j+1)))*0.9997
                return ('ACCEPTED_BREAK_RETEST','LONG',raw_stop,j)
        else:
            ok=(a5['regime']=='DOWN' and a15['regime']=='DOWN' and x['c']<r['l'] and x['c']<x['o'] and x['flow']<=1/1.35 and vol>=1.0)
            if ok:
                raw_stop=max(r['h'],max(m1[k]['h'] for k in range(bi+1,j+1)))*1.0003
                return ('ACCEPTED_BREAK_RETEST','SHORT',raw_stop,j)
    return None


def simulate_trade(m1,entry_i,side,raw_stop,equity):
    entry=m1[entry_i]['o']
    stop_pct=(entry-raw_stop)/entry if side=='LONG' else (raw_stop-entry)/entry
    if stop_pct<MIN_STOP or stop_pct>MAX_STOP:return None
    if TP1/stop_pct<1.5:return None
    notional=min(MAX_LEV*equity,equity*RISK/(stop_pct+COST))
    stop=raw_stop; target=entry*(1+TP1 if side=='LONG' else 1-TP1)
    tp_done=False; realized=0.0; remain=notional; end=min(len(m1)-1,entry_i+240)
    for j in range(entry_i,end+1):
        x=m1[j]
        if not tp_done:
            stop_hit=x['l']<=stop if side=='LONG' else x['h']>=stop
            tp_hit=x['h']>=target if side=='LONG' else x['l']<=target
            if stop_hit:
                r=(stop-entry)/entry if side=='LONG' else (entry-stop)/entry
                gross=notional*r; cost=notional*COST
                return j,stop,'STOP',gross,cost,gross-cost,notional
            if tp_hit:
                realized=0.5*notional*TP1
                remain=0.5*notional; tp_done=True; stop=entry
        else:
            if j>=entry_i+2:
                if side=='LONG':
                    trail=max(entry,min(m1[j-1]['l'],m1[j-2]['l']))
                    if x['l']<=trail:
                        gross=realized+remain*(trail/entry-1); cost=notional*COST
                        return j,trail,'TP1+RUNNER_TRAIL',gross,cost,gross-cost,notional
                else:
                    trail=min(entry,max(m1[j-1]['h'],m1[j-2]['h']))
                    if x['h']>=trail:
                        gross=realized+remain*(entry/trail-1); cost=notional*COST
                        return j,trail,'TP1+RUNNER_TRAIL',gross,cost,gross-cost,notional
    px=m1[end]['c']
    if tp_done:
        r=(px/entry-1) if side=='LONG' else (entry/px-1); gross=realized+remain*r
    else:
        r=(px/entry-1) if side=='LONG' else (entry/px-1); gross=notional*r
    cost=notional*COST
    return end,px,'TIME',gross,cost,gross-cost,notional


def summarize(trades,start_eq,end_eq,peak,maxdd):
    gp=sum(max(t['net_pnl'],0) for t in trades); gl=-sum(min(t['net_pnl'],0) for t in trades); wins=sum(t['net_pnl']>0 for t in trades)
    return {'start_equity':round(start_eq,6),'final_equity':round(end_eq,6),'net_pnl':round(end_eq-start_eq,6),'return_pct':round((end_eq/start_eq-1)*100,3) if start_eq else 0,'trades':len(trades),'wins':wins,'losses':len(trades)-wins,'win_rate':round(100*wins/len(trades),1) if trades else None,'gross_pnl':round(sum(t['gross_pnl'] for t in trades),6),'modeled_costs':round(sum(t['cost'] for t in trades),6),'pf_net':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),'max_dd_pct':round(maxdd*100,3)}


def main():
    raw=[]
    for d in [datetime(2026,8,30,tzinfo=timezone.utc),datetime(2026,8,31,tzinfo=timezone.utc),datetime(2026,9,1,tzinfo=timezone.utc),datetime(2026,9,2,tzinfo=timezone.utc),datetime(2026,9,3,tzinfo=timezone.utc),datetime(2026,9,4,tzinfo=timezone.utc),datetime(2026,9,5,tzinfo=timezone.utc),datetime(2026,9,6,tzinfo=timezone.utc)]: raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m1=b.minute_features(raw); f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)}; f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)}
    equity=100.0; peak=equity; maxdd=0; alltr=[]; daily=[]; last_exit=-10**9; day_key=None; day_start=equity; day_trades=[]; day_peak=equity; day_maxdd=0; day_count=0; day_realized=0.0; locks={'LONG':-10**9,'SHORT':-10**9}; i=0
    while i<len(m1)-20:
        x=m1[i]
        if x['t']<START_MS:i+=1;continue
        if x['t']>=END_MS:break
        loc=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(TEHRAN); dk=loc.strftime('%Y-%m-%d')
        if day_key is None: day_key=dk; day_start=equity; day_peak=equity; day_maxdd=0; day_count=0; day_realized=0; day_trades=[]
        if dk!=day_key:
            daily.append(summarize(day_trades,day_start,equity,day_peak,day_maxdd)|{'day':day_key})
            day_key=dk; day_start=equity; day_peak=equity; day_maxdd=0; day_count=0; day_realized=0; day_trades=[]
        if day_count>=MAX_TRADES_DAY or day_realized<=DAILY_STOP*day_start or i-last_exit<COOLDOWN_MIN:
            i+=1;continue
        cand=breakout_candidate(i,m1,f5,f15)
        if not cand:
            i+=1;continue
        side,level,bi=cand
        if i<locks[side]: i+=1;continue
        sig=resolve_acceptance(cand,m1,f5,f15)
        if not sig:
            i+=1;continue
        setup,side,raw_stop,confirm_i=sig; entry_i=confirm_i+1
        if entry_i>=len(m1) or m1[entry_i]['t']>=END_MS:break
        sim=simulate_trade(m1,entry_i,side,raw_stop,equity)
        if sim is None:
            i+=1;continue
        j,px,reason,gross,cost,net,notional=sim
        before=equity; equity+=net; peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak); day_peak=max(day_peak,equity); day_maxdd=max(day_maxdd,(day_peak-equity)/day_peak)
        et=datetime.fromtimestamp(m1[entry_i]['t']/1000,timezone.utc).astimezone(TEHRAN); xt=datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        stop_pct=(m1[entry_i]['o']-raw_stop)/m1[entry_i]['o'] if side=='LONG' else (raw_stop-m1[entry_i]['o'])/m1[entry_i]['o']
        tr={'day':dk,'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m1[entry_i]['o'],2),'exit':round(px,2),'structural_stop_pct':round(stop_pct*100,3),'reason':reason,'notional':round(notional,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)}
        alltr.append(tr); day_trades.append(tr); day_count+=1; day_realized+=net
        if net<0: locks[side]=j+FAILED_BREAK_LOCK_MIN
        last_exit=j; i=j+1
    if day_key is not None: daily.append(summarize(day_trades,day_start,equity,day_peak,day_maxdd)|{'day':day_key})
    # fill zero-trade days
    byday={d['day']:d for d in daily}; daily2=[]; run_eq=100.0
    for k in range(6):
        d=(START+timedelta(days=k)).strftime('%Y-%m-%d')
        if d in byday:
            daily2.append(byday[d]); run_eq=byday[d]['final_equity']
        else:
            daily2.append({'day':d,'start_equity':round(run_eq,6),'final_equity':round(run_eq,6),'net_pnl':0.0,'return_pct':0.0,'trades':0,'wins':0,'losses':0,'win_rate':None,'gross_pnl':0.0,'modeled_costs':0.0,'pf_net':None,'max_dd_pct':0.0})
    overall=summarize(alltr,100.0,equity,peak,maxdd)
    payload={'version':'DARA-v2.0-Acceptance-Retest-Frozen-Development','period_tehran':[START.isoformat(),END.isoformat()],'symbol':'BTCUSDT USD-M perpetual','source':'Binance official USD-M 1m futures klines; 5m/15m aggregated; taker-buy volume as aggressor-flow proxy; no historical L2 claimed','design':['Strict 5m+15m directional agreement','Break candidate requires flow+relative volume+room','Two-minute hold beyond level','Retest within 8 minutes','Re-acceleration with renewed flow before entry','0.14%-0.35% structural stop and TP1/stop >=1.5','After stopped breakout, same direction locked 90 minutes','Max 3 trades/day; daily stop -0.75%; cooldown 45m','TP1 +0.33% on 50%; 50% runner; add-to-winner disabled in v2 proof mode'],'methodology_notes':['DARA v2 was designed after reviewing Sep1-8 behavior of earlier versions, so Sep1-6 is a development/in-sample evaluation, not clean out-of-sample proof.','No DARA v2 threshold is changed during this Sep1-6 run.','Stop is checked before target when both occur in same 1m bar.'],'daily':daily2,'overall':overall,'trades':alltr}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8'); print(json.dumps(overall,indent=2)); print(json.dumps(daily2,indent=2)); print(json.dumps(alltr,indent=2))

if __name__=='__main__': main()
