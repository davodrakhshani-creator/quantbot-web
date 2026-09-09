import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v3_sep1_8 as v3

TEHRAN=b.TEHRAN
START=v3.START; END=v3.END; START_MS=v3.START_MS; END_MS=v3.END_MS
OUT=Path('data/dara_v4_sep1_8.json')
MAX_TRADES_DAY=4; DAILY_STOP=-0.0075; COOLDOWN=30; LOSS_LOCK=90


def age(mp,t,mins):
    k=b.last_closed(t,mins); cur=mp.get(k)
    if not cur:return 999
    reg=cur['regime']; n=0; step=mins*60000
    while n<20:
        z=mp.get(k-n*step)
        if not z or z['regime']!=reg:break
        n+=1
    return n


def fresh_trend(i,m1,f5,f15,f60):
    if i<250:return None
    x=m1[i]; a5=f5.get(b.last_closed(x['t'],5)); a15=f15.get(b.last_closed(x['t'],15)); a60=f60.get(b.last_closed(x['t'],60))
    if not a5 or not a15 or not a60:return None
    a15age=age(f15,x['t'],15); vol=x['v']/x['medv']; prev=m1[i-5:i]; ret60=x['c']/m1[i-60]['c']-1
    if len(prev)<5:return None
    if a15['regime']=='UP' and a60['regime']=='UP' and a15age<=6 and ret60>=0.0015:
        touched=min(z['l'] for z in prev)<=max(a5['ema21'],x['ema20'])*1.0010
        held=min(z['c'] for z in prev)>=a15['ema21']*0.9980
        reac=x['c']>x['prev5h'] and x['body']>0 and x['flow']>=1.60 and vol>=1.20 and x['c']>x['vwap240']
        if touched and held and reac and v3.room_ok(x,'LONG',0.0045):return ('FRESH_TREND','LONG',min(z['l'] for z in prev)*0.9996,i)
    if a15['regime']=='DOWN' and a60['regime']=='DOWN' and a15age<=6 and ret60<=-0.0015:
        touched=max(z['h'] for z in prev)>=min(a5['ema21'],x['ema20'])*0.9990
        held=max(z['c'] for z in prev)<=a15['ema21']*1.0020
        reac=x['c']<x['prev5l'] and x['body']<0 and x['flow']<=1/1.60 and vol>=1.20 and x['c']<x['vwap240']
        if touched and held and reac and v3.room_ok(x,'SHORT',0.0045):return ('FRESH_TREND','SHORT',max(z['h'] for z in prev)*1.0004,i)
    return None


def shock_retest(i,m1,f5,f15,f60):
    if i<250:return None
    x=m1[i]; a5=f5.get(b.last_closed(x['t'],5)); a15=f15.get(b.last_closed(x['t'],15)); a60=f60.get(b.last_closed(x['t'],60))
    if not a5 or not a15 or not a60:return None
    # completed 5m bar is exceptional impulse; current minute is retest/restart, not chase
    rng=max(a5['h']-a5['l'],1e-9); vol=x['v']/x['medv']
    if a5['body']>=0.0025 and a5['v']>=1.8*a5['medv'] and a5['flow']>=1.7 and a60['regime']!='DOWN':
        retr=(a5['h']-x['l'])/rng
        if .25<=retr<=.60 and x['body']>0 and x['flow']>=1.45 and vol>=1.1 and x['c']>x['prev5h']*0.9990 and v3.room_ok(x,'LONG',0.0045):
            return ('SHOCK_RETEST','LONG',x['l']*0.9996,i)
    if a5['body']<=-0.0025 and a5['v']>=1.8*a5['medv'] and a5['flow']<=1/1.7 and a60['regime']!='UP':
        retr=(x['h']-a5['l'])/rng
        if .25<=retr<=.60 and x['body']<0 and x['flow']<=1/1.45 and vol>=1.1 and x['c']<x['prev5l']*1.0010 and v3.room_ok(x,'SHORT',0.0045):
            return ('SHOCK_RETEST','SHORT',x['h']*1.0004,i)
    return None


def sweep(i,m1,f5,f15,f60):
    if i<250 or i+1>=len(m1):return None
    x=m1[i]; a15=f15.get(b.last_closed(x['t'],15)); a60=f60.get(b.last_closed(x['t'],60)); vol=x['v']/x['medv']; y=m1[i+1]
    if not a15 or not a60:return None
    # Fade only when the two HTFs are not strongly aligned in the sweep direction.
    if not (a15['regime']=='DOWN' and a60['regime']=='DOWN'):
        if x['l']<x['prev60l']*0.9994 and x['c']>x['prev60l'] and x['body']>0 and x['flow']>=1.7 and vol>=1.5:
            if y['l']>=x['l'] and y['c']>x['prev60l'] and y['flow']>=1.2 and v3.room_ok(y,'LONG',0.0038):return ('SWEEP_REVERSAL','LONG',x['l']*0.9996,i+1)
    if not (a15['regime']=='UP' and a60['regime']=='UP'):
        if x['h']>x['prev60h']*1.0006 and x['c']<x['prev60h'] and x['body']<0 and x['flow']<=1/1.7 and vol>=1.5:
            if y['h']<=x['h'] and y['c']<x['prev60h'] and y['flow']<=1/1.2 and v3.room_ok(y,'SHORT',0.0038):return ('SWEEP_REVERSAL','SHORT',x['h']*1.0004,i+1)
    return None


def signal(i,m1,f5,f15,f60):
    for fn in (shock_retest,fresh_trend,sweep):
        s=fn(i,m1,f5,f15,f60)
        if s:return s
    return None


def main():
    raw=[]
    for d in [datetime(2026,8,30,tzinfo=timezone.utc)+timedelta(days=k) for k in range(10)]:raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t']);m1=v3.enrich_m1(raw)
    f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)};f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)};f60={x['t']:x for x in b.features(b.aggregate(raw,60),60)}
    equity=100.;peak=100.;maxdd=0.;alltr=[];daily=[];day=None;day_start=100.;day_peak=100.;daydd=0;daytr=[];daynet=0.;daycount=0;last_exit=-10**9;locks={'LONG':-10**9,'SHORT':-10**9};i=0
    while i<len(m1)-5:
        x=m1[i]
        if x['t']<START_MS:i+=1;continue
        if x['t']>=END_MS:break
        dk=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(TEHRAN).strftime('%Y-%m-%d')
        if day is None:day=dk
        if dk!=day:
            daily.append(v3.summarize(daytr,day_start,equity,day_peak,daydd)|{'day':day});day=dk;day_start=equity;day_peak=equity;daydd=0;daytr=[];daynet=0;daycount=0
        if daycount>=MAX_TRADES_DAY or daynet<=DAILY_STOP*day_start or i-last_exit<COOLDOWN:i+=1;continue
        s=signal(i,m1,f5,f15,f60)
        if not s:i+=1;continue
        setup,side,stop,ci=s
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m1) or m1[ei]['t']>=END_MS:break
        sim=v3.simulate(m1,ei,side,stop,equity)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n=sim;before=equity;equity+=net;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak);day_peak=max(day_peak,equity);daydd=max(daydd,(day_peak-equity)/day_peak)
        et=datetime.fromtimestamp(m1[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(TEHRAN);sp=(m1[ei]['o']-stop)/m1[ei]['o'] if side=='LONG' else (stop-m1[ei]['o'])/m1[ei]['o']
        tr={'day':dk,'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m1[ei]['o'],2),'exit':round(px,2),'structural_stop_pct':round(sp*100,3),'reason':why,'notional':round(n,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)};alltr.append(tr);daytr.append(tr);daynet+=net;daycount+=1
        if net<0:locks[side]=j+LOSS_LOCK
        last_exit=j;i=j+1
    if day is not None:daily.append(v3.summarize(daytr,day_start,equity,day_peak,daydd)|{'day':day})
    by={x['day']:x for x in daily};daily2=[];eq=100.
    for k in range(8):
        d=(START+timedelta(days=k)).strftime('%Y-%m-%d')
        if d in by:daily2.append(by[d]);eq=by[d]['final_equity']
        else:daily2.append({'day':d,'start_equity':round(eq,6),'final_equity':round(eq,6),'net_pnl':0.,'return_pct':0.,'trades':0,'wins':0,'losses':0,'win_rate':None,'gross_pnl':0.,'modeled_costs':0.,'pf_net':None,'max_dd_pct':0.})
    overall=v3.summarize(alltr,100.,equity,peak,maxdd);stats={}
    for name in ['FRESH_TREND','SHOCK_RETEST','SWEEP_REVERSAL']:
        ts=[t for t in alltr if t['setup']==name];w=[t for t in ts if t['net_pnl']>0];l=[t for t in ts if t['net_pnl']<=0];gp=sum(t['net_pnl'] for t in w);gl=-sum(t['net_pnl'] for t in l);stats[name]={'trades':len(ts),'wins':len(w),'losses':len(l),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl>0 else (99 if gp>0 else None)}
    payload={'version':'DARA-v4.0-FreshTrend-MultiEvent-Development','design':['Fresh 15m trend only: age <=6 bars','1h momentum must agree with fresh trend','No direct breakout chasing','Shock impulse-retest added as rare continuation event','60m liquidity sweep reversal retained','Risk 0.25%; stop 0.14%-0.32%; TP1 +0.33%, 50/50 runner','90m same-side lock after loss'],'methodology_notes':['Designed after inspecting v3 Sep1-8, therefore fully in-sample development.','No v4 threshold changed during this run.'],'daily':daily2,'overall':overall,'by_setup':stats,'trades':alltr}
    OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(payload,indent=2));print(json.dumps(overall,indent=2));print(json.dumps(stats,indent=2));print(json.dumps(daily2,indent=2))
if __name__=='__main__':main()
