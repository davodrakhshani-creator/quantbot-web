import json, statistics
from datetime import datetime,timedelta,timezone
from pathlib import Path
import seven_trader_styles_sep1_8_tp3fee as s

TEHRAN=s.TEHRAN
START=datetime(2026,8,30,0,0,tzinfo=TEHRAN)
ENTRY_END=datetime(2026,8,31,0,0,tzinfo=TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000)
ENTRY_END_MS=int(ENTRY_END.astimezone(timezone.utc).timestamp()*1000)
LOAD_START_MS=START_MS-6*60*60*1000
LOAD_END_MS=ENTRY_END_MS+4*60*60*1000
OUT=Path('data/dara_v13_microtrigger_aug30.json')
COST=0.0011; RISK=0.0025; MAX_LEV=3.0
MIN_TP=3.5*COST; MAX_TP=0.0055
MIN_STOP=0.0014; MAX_STOP=0.0030
MAX_TRADES=8; COOLDOWN_BARS=120 # 10 minutes on 5s bars
LOSS_LOCK_BARS=360 # 30 minutes
DAILY_STOP=-0.010
FIRST=0.50; RUNNER=0.50; MAX_HOLD_BARS=2160 # 3 hours
rejects={}
def rej(k): rejects[k]=rejects.get(k,0)+1

def atrp(bars,n=14):
    if len(bars)<n+1:return 0.0
    xs=[]
    for i in range(-n,0):
        x=bars[i]; p=bars[i-1]['c']; xs.append(max(x['h']-x['l'],abs(x['h']-p),abs(x['l']-p))/max(x['c'],1e-12))
    return sum(xs)/len(xs)

def ctx_before(t,bars,span_ms,n=40):
    key=(t//span_ms)*span_ms-span_ms
    # bars are sorted and modest; reverse scan is fine for one-day test
    out=[]
    for x in reversed(bars):
        if x['t']<=key:
            out.append(x)
            if len(out)>=n:break
    return list(reversed(out))

def regime(arr):
    if not arr:return 'RANGE'
    z=arr[-1]
    if z['ema9']>z['ema21']*1.00025:return 'UP'
    if z['ema9']<z['ema21']*0.99975:return 'DOWN'
    return 'RANGE'

def tp_for(c5,c15):
    a5=atrp(c5); a15=atrp(c15)
    cap=min(MAX_TP,3.0*a5,1.7*a15)
    return cap if cap>=MIN_TP else MIN_TP

def micro_stats(bars,i,n=6):
    xs=bars[max(0,i-n+1):i+1]
    buy=sum(x['buy'] for x in xs); sell=sum(x['sell'] for x in xs); v=buy+sell
    delta=(buy-sell)/max(v,1e-12)
    progress=(xs[-1]['c']/xs[0]['o']-1) if xs else 0
    return delta,progress,v

def signal(i,b5s,b1m,b5m,b15m):
    if i<80 or i+2>=len(b5s):return None
    z=b5s[i]; y=b5s[i+1]; w=b5s[i+2]
    if not (START_MS<=z['t']<ENTRY_END_MS):return None
    c1=ctx_before(z['t'],b1m,60000,20); c5=ctx_before(z['t'],b5m,300000,30); c15=ctx_before(z['t'],b15m,900000,30)
    if len(c1)<10 or len(c5)<10 or len(c15)<10:return None
    r5=regime(c5); r15=regime(c15); tp=tp_for(c5,c15)
    d30,p30,v30=micro_stats(b5s,i,6)
    medv=max(z.get('medv',1e-12),1e-12)
    prev12=b5s[i-12:i]; prev36=b5s[i-36:i]
    hi12=max(x['h'] for x in prev12); lo12=min(x['l'] for x in prev12)
    hi36=max(x['h'] for x in prev36); lo36=min(x['l'] for x in prev36)
    one=c1[-1]; value=(one['ema21']+c5[-1]['ema21'])/2

    # Tier A: 1-3 minute liquidity sweep with flow exhaustion and two-step control flip.
    low_sweep=z['l']<lo36*0.99995 and z['c']>lo12 and d30<=-0.18
    high_sweep=z['h']>hi36*1.00005 and z['c']<hi12 and d30>=0.18
    event_delta=(z['buy']-z['sell'])/max(z['v'],1e-12)
    ydelta=(y['buy']-y['sell'])/max(y['v'],1e-12); wdelta=(w['buy']-w['sell'])/max(w['v'],1e-12)
    exhausted=abs(event_delta)<=0.16 or abs(p30)<=0.00045
    if low_sweep and exhausted and ydelta>=0.12 and wdelta>=0.08 and w['c']>z['c'] and not (r5=='DOWN' and r15=='DOWN'):
        stop=min(z['l'],lo36)*0.9995
        return ('MICRO_SWEEP_REVERSAL','LONG',stop,i+2,{'d30':d30,'p30':p30,'event_delta':event_delta,'y_delta':ydelta,'w_delta':wdelta,'r5':r5,'r15':r15,'tp_pct':tp*100},tp)
    if high_sweep and exhausted and ydelta<=-0.12 and wdelta<=-0.08 and w['c']<z['c'] and not (r5=='UP' and r15=='UP'):
        stop=max(z['h'],hi36)*1.0005
        return ('MICRO_SWEEP_REVERSAL','SHORT',stop,i+2,{'d30':d30,'p30':p30,'event_delta':event_delta,'y_delta':ydelta,'w_delta':wdelta,'r5':r5,'r15':r15,'tp_pct':tp*100},tp)

    # Tier B: absorption burst near 1m/5m value edge without a textbook sweep.
    # Large aggressive 30s pressure but little progress, followed by two 5s bars of opposite control.
    dist=(z['c']-value)/z['c']
    if d30<=-0.30 and abs(p30)<=0.00055 and z['v']>=1.2*medv and abs(dist)<=0.0025 and ydelta>=0.15 and wdelta>=0.10 and w['c']>y['c']:
        if not (r5=='DOWN' and r15=='DOWN'):
            stop=min(x['l'] for x in b5s[i-12:i+3])*0.9995
            return ('ABSORPTION_FLIP','LONG',stop,i+2,{'d30':d30,'p30':p30,'dist_value_pct':dist*100,'y_delta':ydelta,'w_delta':wdelta,'r5':r5,'r15':r15,'tp_pct':tp*100},tp)
    if d30>=0.30 and abs(p30)<=0.00055 and z['v']>=1.2*medv and abs(dist)<=0.0025 and ydelta<=-0.15 and wdelta<=-0.10 and w['c']<y['c']:
        if not (r5=='UP' and r15=='UP'):
            stop=max(x['h'] for x in b5s[i-12:i+3])*1.0005
            return ('ABSORPTION_FLIP','SHORT',stop,i+2,{'d30':d30,'p30':p30,'dist_value_pct':dist*100,'y_delta':ydelta,'w_delta':wdelta,'r5':r5,'r15':r15,'tp_pct':tp*100},tp)

    # Tier C: aligned 5m/15m trend, 1m counterflow pullback to value, micro control resumes.
    one_delta=(one['buy']-one['sell'])/max(one['v'],1e-12)
    if r5=='UP' and r15=='UP' and one_delta<=-0.10 and one['v']<=1.25*one['medv'] and one['l']<=value*1.0015 and one['c']>=c15[-1]['ema21']*0.998:
        if ydelta>=0.14 and wdelta>=0.10 and w['c']>max(y['c'],z['c']) and w['c']<=value*1.0035:
            stop=min(one['l'],min(x['l'] for x in b5s[i-12:i+3]))*0.9995
            return ('MICRO_FAILED_PULLBACK','LONG',stop,i+2,{'one_delta':one_delta,'one_volr':one['v']/max(one['medv'],1e-12),'y_delta':ydelta,'w_delta':wdelta,'r5':r5,'r15':r15,'tp_pct':tp*100},tp)
    if r5=='DOWN' and r15=='DOWN' and one_delta>=0.10 and one['v']<=1.25*one['medv'] and one['h']>=value*0.9985 and one['c']<=c15[-1]['ema21']*1.002:
        if ydelta<=-0.14 and wdelta<=-0.10 and w['c']<min(y['c'],z['c']) and w['c']>=value*0.9965:
            stop=max(one['h'],max(x['h'] for x in b5s[i-12:i+3]))*1.0005
            return ('MICRO_FAILED_PULLBACK','SHORT',stop,i+2,{'one_delta':one_delta,'one_volr':one['v']/max(one['medv'],1e-12),'y_delta':ydelta,'w_delta':wdelta,'r5':r5,'r15':r15,'tp_pct':tp*100},tp)
    rej('no_signal');return None

def simulate(bars,ei,side,stop,equity,tp):
    entry=bars[ei]['o']; sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<MIN_STOP: stop=entry*(1-MIN_STOP if side=='LONG' else 1+MIN_STOP); sp=MIN_STOP
    if sp>MAX_STOP:rej('stop_too_wide');return None
    if tp<MIN_TP:tp=MIN_TP
    if tp/sp<1.25:rej('rr');return None
    notional=min(MAX_LEV*equity,equity*RISK/(sp+COST)); target=entry*(1+tp if side=='LONG' else 1-tp)
    tpdone=False;realized=0.;remain=notional;mfe=0.;end=min(len(bars)-1,ei+MAX_HOLD_BARS)
    for j in range(ei,end+1):
        x=bars[j];fav=(x['h']/entry-1) if side=='LONG' else (entry/x['l']-1);mfe=max(mfe,fav)
        if not tpdone:
            st=x['l']<=stop if side=='LONG' else x['h']>=stop; hit=x['h']>=target if side=='LONG' else x['l']<=target
            if st:
                rr=stop/entry-1 if side=='LONG' else entry/stop-1;gross=notional*rr;cost=notional*COST
                return j,stop,'STOP',gross,cost,gross-cost,notional,mfe
            # after 3 minutes, abandon only if thesis never progressed and current PnL is non-positive
            if j>=ei+36 and mfe<0.0010:
                cur=(x['c']/entry-1) if side=='LONG' else (entry/x['c']-1)
                if cur<=0:
                    gross=notional*cur;cost=notional*COST
                    return j,x['c'],'MICRO_VELOCITY_FAIL',gross,cost,gross-cost,notional,mfe
            if hit:
                realized=FIRST*notional*tp;remain=RUNNER*notional;tpdone=True;stop=entry
        elif j>=ei+12: # allow 1 minute after TP before 30s trailing structure
            if side=='LONG':
                trail=max(entry,min(bars[j-k]['l'] for k in range(1,7)))
                if x['l']<=trail:
                    gross=realized+remain*(trail/entry-1);cost=notional*COST
                    return j,trail,'TP3.5FEE+RUNNER',gross,cost,gross-cost,notional,mfe
            else:
                trail=min(entry,max(bars[j-k]['h'] for k in range(1,7)))
                if x['h']>=trail:
                    gross=realized+remain*(entry/trail-1);cost=notional*COST
                    return j,trail,'TP3.5FEE+RUNNER',gross,cost,gross-cost,notional,mfe
    x=bars[end];rr=x['c']/entry-1 if side=='LONG' else entry/x['c']-1;gross=(realized+remain*rr) if tpdone else notional*rr;cost=notional*COST
    return end,x['c'],'TIME',gross,cost,gross-cost,notional,mfe

def main():
    raw=[]
    d=datetime(2026,8,29,tzinfo=timezone.utc)
    while d.date()<=datetime(2026,8,31,tzinfo=timezone.utc).date():raw+=s.fetch_agg_utc_date(d);d+=timedelta(days=1)
    raw.sort(key=lambda x:x[0]);b5s=s.prepare(s.agg5s(raw,LOAD_START_MS,LOAD_END_MS));b1m=s.prepare(s.aggregate(b5s,60));b5m=s.prepare(s.aggregate(b5s,300));b15m=s.prepare(s.aggregate(b5s,900))
    equity=100.;peak=100.;maxdd=0.;tr=[];last=-10**9;locks={'LONG':-10**9,'SHORT':-10**9};daynet=0.;i=80
    while i<len(b5s)-5:
        x=b5s[i]
        if x['t']<START_MS:i+=1;continue
        if x['t']>=ENTRY_END_MS:break
        if len(tr)>=MAX_TRADES or daynet<=DAILY_STOP*100 or i-last<COOLDOWN_BARS:i+=1;continue
        sg=signal(i,b5s,b1m,b5m,b15m)
        if not sg:i+=1;continue
        setup,side,stop,ci,diag,tp=sg
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(b5s) or b5s[ei]['t']>=ENTRY_END_MS:break
        sim=simulate(b5s,ei,side,stop,equity,tp)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n,mfe=sim;before=equity;equity+=net;daynet+=net;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak)
        et=datetime.fromtimestamp(b5s[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(b5s[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        sp=(b5s[ei]['o']-stop)/b5s[ei]['o'] if side=='LONG' else (stop-b5s[ei]['o'])/b5s[ei]['o']
        tr.append({'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(b5s[ei]['o'],2),'exit':round(px,2),'tp_pct':round(tp*100,3),'profit_floor_pct':round(MIN_TP*100,3),'stop_pct':round(sp*100,3),'mfe_pct':round(mfe*100,3),'reason':why,'diagnostics':diag,'notional':round(n,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)})
        if net<0:locks[side]=j+LOSS_LOCK_BARS
        last=j;i=j+1
    w=[x for x in tr if x['net_pnl']>0];l=[x for x in tr if x['net_pnl']<=0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in l)
    overall={'start_equity':100.0,'final_equity':round(equity,6),'net_pnl':round(equity-100,6),'return_pct':round((equity/100-1)*100,3),'trades':len(tr),'wins':len(w),'losses':len(l),'win_rate':round(100*len(w)/len(tr),1) if tr else None,'gross_pnl':round(sum(x['gross_pnl'] for x in tr),6),'modeled_costs':round(sum(x['cost'] for x in tr),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None),'max_dd_pct':round(maxdd*100,3)}
    by={}
    for name in ['MICRO_SWEEP_REVERSAL','ABSORPTION_FLIP','MICRO_FAILED_PULLBACK']:
        xs=[x for x in tr if x['setup']==name];ww=[x for x in xs if x['net_pnl']>0];ll=[x for x in xs if x['net_pnl']<=0];g=sum(x['net_pnl'] for x in ww);h=-sum(x['net_pnl'] for x in ll)
        by[name]={'trades':len(xs),'wins':len(ww),'losses':len(ll),'net_pnl':round(sum(x['net_pnl'] for x in xs),6),'pf_net':round(g/h,2) if h else (99 if g else None)}
    payload={'version':'DARA-v13-MicroTrigger-Aug30-Development','period_tehran':[START.isoformat(),ENTRY_END.isoformat()],'starting_equity':100.0,'architecture':['Decision context remains 1m/5m/15m','5s aggTrades micro-bars used only for execution/flow trigger inside the 1m decision horizon','Three playbooks: micro sweep reversal, absorption flip, micro failed pullback'],'rules':['Max 8 entries/day, 10m cooldown, 30m same-side loss lock','0.25% full-stop risk incl 0.11% modeled friction, max 3x notional, -1% daily stop','No normal profitable exit before +0.385% gross move = 3.5x modeled round-trip friction','50% first take profit + 50% runner; losing thesis may exit early only when non-positive'],'methodology_notes':['v13 was created after v12 Aug30 revealed candle-level opportunity starvation, so Aug30 is now development data rather than blind validation.','Historical full L2 unavailable; raw Binance futures aggTrades provide actual aggressor-side trade flow but not resting-book depth.'],'overall':overall,'by_setup':by,'reject_counts':rejects,'trades':tr}
    OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(payload,indent=2));print(json.dumps(overall,indent=2));print(json.dumps(by,indent=2));print(json.dumps(rejects,indent=2))
if __name__=='__main__':main()
