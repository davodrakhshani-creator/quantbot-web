import json, statistics
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v10_microstructure_sep1 as v
import dara_v10_2_impact_absorption_sep1 as x

TEHRAN=v.TEHRAN
START=datetime(2026,9,3,0,0,tzinfo=TEHRAN)
END=datetime(2026,9,9,0,0,tzinfo=TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000)
END_MS=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v10_4_state_transition_sep3_8.json')
COST=v.COST;RISK=v.RISK;MAX_LEV=v.MAX_LEV
MIN_TP=0.0033;MAX_TP=0.0055;FIRST=0.40;RUNNER=0.60
MIN_STOP=0.0014;MAX_STOP=0.0030
MAX_TRADES_DAY=4;DAILY_STOP=-0.010;COOLDOWN=30;LOSS_LOCK=90;MAX_HOLD=150;TRAIL_BARS=3

rejects={}
def rej(k): rejects[k]=rejects.get(k,0)+1

def adaptive_tp(a5,a15):
    # Never take normal profit below 3x the 0.11% modeled roundtrip friction.
    cap=min(MAX_TP,1.70*a15['atrp'],3.0*a5['atrp'])
    return cap if cap>=MIN_TP else None

def local_range_median(m,i,n=20):
    rs=[(z['h']-z['l'])/max(z['c'],1e-12) for z in m[max(0,i-n):i]]
    return statistics.median(rs) if rs else 0.001

def continuation(i,m,f5,f15):
    if i<25 or i+1>=len(m):return None
    z=m[i]; y=m[i+1]; a5=f5.get(b.last_closed(z['t'],5)); a15=f15.get(b.last_closed(z['t'],15))
    if not a5 or not a15:return None
    tp=adaptive_tp(a5,a15)
    if tp is None: rej('continuation_capacity'); return None
    prev=m[i-4:i]
    if len(prev)<4:return None
    medr=local_range_median(m,i)

    if a15['regime']=='UP' and a5['regime']=='UP':
        st=x.impact_state(i,m,'LONG'); value=(a5['ema21']+z['vwap15'])/2
        touched=min(q['l'] for q in prev)<=value*1.0012
        held=min(q['c'] for q in prev)>=a15['ema21']*0.9975
        trig=z['c']>max(q['h'] for q in m[i-2:i])*1.00005 and z['c']>z['o'] and z['delta']>=0.10
        if not (touched and held and trig and st and st['informed']): rej('continuation_core'); return None
        displacement=(z['c']-min(q['l'] for q in prev))/z['c']
        no_chase=displacement<=max(0.0032,2.0*a5['atrp']) and ((z['h']-z['l'])/z['c']<=2.2*max(medr,1e-6))
        if not no_chase: rej('continuation_chase'); return None
        midpoint=(z['h']+z['l'])/2
        hold=y['l']>=min(z['l'],midpoint*0.9988) and y['c']>=midpoint and y['delta']>-0.18
        if not hold: rej('continuation_hold'); return None
        stop=min([q['l'] for q in prev]+[z['l'],y['l']])*0.9996
        return ('VALUE_TRANSITION','LONG',stop,i+1,6,{**st,'tp_pct':tp*100,'disp_pct':displacement*100,'hold_delta':y['delta']},tp)

    if a15['regime']=='DOWN' and a5['regime']=='DOWN':
        st=x.impact_state(i,m,'SHORT'); value=(a5['ema21']+z['vwap15'])/2
        touched=max(q['h'] for q in prev)>=value*0.9988
        held=max(q['c'] for q in prev)<=a15['ema21']*1.0025
        trig=z['c']<min(q['l'] for q in m[i-2:i])*0.99995 and z['c']<z['o'] and z['delta']<=-0.10
        if not (touched and held and trig and st and st['informed']): rej('continuation_core'); return None
        displacement=(max(q['h'] for q in prev)-z['c'])/z['c']
        no_chase=displacement<=max(0.0032,2.0*a5['atrp']) and ((z['h']-z['l'])/z['c']<=2.2*max(medr,1e-6))
        if not no_chase: rej('continuation_chase'); return None
        midpoint=(z['h']+z['l'])/2
        hold=y['h']<=max(z['h'],midpoint*1.0012) and y['c']<=midpoint and y['delta']<0.18
        if not hold: rej('continuation_hold'); return None
        stop=max([q['h'] for q in prev]+[z['h'],y['h']])*1.0004
        return ('VALUE_TRANSITION','SHORT',stop,i+1,6,{**st,'tp_pct':tp*100,'disp_pct':displacement*100,'hold_delta':y['delta']},tp)
    return None

def sweep(i,m,f5,f15):
    if i<25 or i+1>=len(m):return None
    z=m[i]; y=m[i+1]; a5=f5.get(b.last_closed(z['t'],5)); a15=f15.get(b.last_closed(z['t'],15))
    if not a5 or not a15:return None
    tp=adaptive_tp(a5,a15)
    if tp is None: rej('sweep_capacity'); return None
    rng=max(z['h']-z['l'],1e-9); body=abs(z['c']-z['o']); upper=z['h']-max(z['o'],z['c']); lower=min(z['o'],z['c'])-z['l']

    # Low sweep: do not fade when both 5m and 15m are strongly down.
    if not (a15['regime']=='DOWN' and a5['regime']=='DOWN'):
        pen=(z['prev15l']-z['l'])/z['prev15l'] if z['l']<z['prev15l'] else 0
        st=x.impact_state(i,m,'SHORT')
        event=pen>=0.0004 and z['c']>z['prev15l'] and lower>=max(body,0.28*rng) and z['delta']<=-0.08 and z['volr']>=1.10 and st and st['absorbed']
        flip=y['c']>z['prev15l'] and y['c']>=y['o'] and y['delta']>=0.06 and y['l']>=z['l']*0.9998
        if event and flip:
            return ('SWEEP_TRANSITION','LONG',z['l']*0.9995,i+1,6,{'penetration_pct':pen*100,'event_delta':z['delta'],'flip_delta':y['delta'],**st,'tp_pct':tp*100},tp)
    # High sweep: do not fade when both 5m and 15m are strongly up.
    if not (a15['regime']=='UP' and a5['regime']=='UP'):
        pen=(z['h']-z['prev15h'])/z['prev15h'] if z['h']>z['prev15h'] else 0
        st=x.impact_state(i,m,'LONG')
        event=pen>=0.0004 and z['c']<z['prev15h'] and upper>=max(body,0.28*rng) and z['delta']>=0.08 and z['volr']>=1.10 and st and st['absorbed']
        flip=y['c']<z['prev15h'] and y['c']<=y['o'] and y['delta']<=-0.06 and y['h']<=z['h']*1.0002
        if event and flip:
            return ('SWEEP_TRANSITION','SHORT',z['h']*1.0005,i+1,6,{'penetration_pct':pen*100,'event_delta':z['delta'],'flip_delta':y['delta'],**st,'tp_pct':tp*100},tp)
    rej('sweep_core')
    return None

def signal(i,m,f5,f15):
    q=sweep(i,m,f5,f15)
    if q:return q
    return continuation(i,m,f5,f15)

def simulate(m,ei,side,stop,equity,tp):
    entry=m[ei]['o']; sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<MIN_STOP or sp>MAX_STOP: rej('invalid_stop'); return None
    if tp/sp<1.25: rej('poor_reward_stop'); return None
    n=min(MAX_LEV*equity,equity*RISK/(sp+COST))
    if n<=0:return None
    target=entry*(1+tp if side=='LONG' else 1-tp); tp_done=False; realized=0.;remain=n;end=min(len(m)-1,ei+MAX_HOLD);mfe=0.
    for j in range(ei,end+1):
        bar=m[j]
        favorable=(bar['h']/entry-1) if side=='LONG' else (entry/bar['l']-1)
        mfe=max(mfe,favorable)
        if not tp_done:
            st=bar['l']<=stop if side=='LONG' else bar['h']>=stop
            hit=bar['h']>=target if side=='LONG' else bar['l']<=target
            if st:
                r=stop/entry-1 if side=='LONG' else entry/stop-1;gross=n*r;cost=n*COST
                return j,stop,'STOP',gross,cost,gross-cost,n,mfe
            # Scalp thesis failure: after 12 minutes, if price never progressed even ~one friction unit and is not profitable, cut.
            if j>=ei+12 and mfe<0.0012:
                cur=(bar['c']/entry-1) if side=='LONG' else (entry/bar['c']-1)
                if cur<=0:
                    gross=n*cur;cost=n*COST
                    return j,bar['c'],'VELOCITY_FAIL',gross,cost,gross-cost,n,mfe
            if hit:
                realized=FIRST*n*tp;remain=RUNNER*n;tp_done=True;stop=entry
        elif j>=ei+TRAIL_BARS:
            if side=='LONG':
                trail=max(entry,min(m[j-k]['l'] for k in range(1,TRAIL_BARS+1)))
                if bar['l']<=trail:
                    gross=realized+remain*(trail/entry-1);cost=n*COST
                    return j,trail,'TP+RUNNER',gross,cost,gross-cost,n,mfe
            else:
                trail=min(entry,max(m[j-k]['h'] for k in range(1,TRAIL_BARS+1)))
                if bar['h']>=trail:
                    gross=realized+remain*(entry/trail-1);cost=n*COST
                    return j,trail,'TP+RUNNER',gross,cost,gross-cost,n,mfe
    px=m[end]['c'];r=px/entry-1 if side=='LONG' else entry/px-1;gross=(realized+remain*r) if tp_done else n*r;cost=n*COST
    return end,px,'TIME',gross,cost,gross-cost,n,mfe

def summarize(ts,start,end,peak,maxdd):
    w=[t for t in ts if t['net_pnl']>0];l=[t for t in ts if t['net_pnl']<=0];gp=sum(t['net_pnl'] for t in w);gl=-sum(t['net_pnl'] for t in l)
    return {'start_equity':round(start,6),'final_equity':round(end,6),'net_pnl':round(end-start,6),'return_pct':round((end/start-1)*100,3),'trades':len(ts),'wins':len(w),'losses':len(l),'win_rate':round(100*len(w)/len(ts),1) if ts else None,'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'modeled_costs':round(sum(t['cost'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99.0 if gp else None),'max_dd_pct':round(maxdd*100,3)}

def main():
    raw=[];d=datetime(2026,9,2,tzinfo=timezone.utc)
    while d.date()<=datetime(2026,9,8,tzinfo=timezone.utc).date():raw+=b.get_daily(d);d+=timedelta(days=1)
    raw=sorted({r['t']:r for r in raw}.values(),key=lambda r:r['t']);m=v.enrich(raw);f5={r['t']:r for r in b.features(b.aggregate(raw,5),5)};f15={r['t']:r for r in b.features(b.aggregate(raw,15),15)}
    equity=100.;peak=100.;maxdd=0.;alltr=[];daily=[];day=None;daystart=100.;daypeak=100.;daydd=0.;daytr=[];daynet=0.;daycount=0;last=-10**9;locks={'LONG':-10**9,'SHORT':-10**9};i=0
    while i<len(m)-3:
        bar=m[i]
        if bar['t']<START_MS:i+=1;continue
        if bar['t']>=END_MS:break
        dk=datetime.fromtimestamp(bar['t']/1000,timezone.utc).astimezone(TEHRAN).strftime('%Y-%m-%d')
        if day is None:day=dk
        if dk!=day:
            daily.append(summarize(daytr,daystart,equity,daypeak,daydd)|{'day':day});day=dk;daystart=equity;daypeak=equity;daydd=0.;daytr=[];daynet=0.;daycount=0
        if daycount>=MAX_TRADES_DAY or daynet<=DAILY_STOP*daystart or i-last<COOLDOWN:i+=1;continue
        s=signal(i,m,f5,f15)
        if not s:i+=1;continue
        setup,side,stop,ci,score,diag,tp=s
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m) or m[ei]['t']>=END_MS:break
        sim=simulate(m,ei,side,stop,equity,tp)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n,mfe=sim;before=equity;equity+=net;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak);daypeak=max(daypeak,equity);daydd=max(daydd,(daypeak-equity)/daypeak);daynet+=net;daycount+=1
        et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN);sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
        tr={'day':dk,'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'tp_pct':round(tp*100,3),'structural_stop_pct':round(sp*100,3),'mfe_pct':round(mfe*100,3),'reason':why,'diagnostics':diag,'notional':round(n,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)};alltr.append(tr);daytr.append(tr)
        if net<0:locks[side]=j+LOSS_LOCK
        last=j;i=j+1
    if day is not None:daily.append(summarize(daytr,daystart,equity,daypeak,daydd)|{'day':day})
    # fill zero-trade dates
    byday={z['day']:z for z in daily};daily2=[];eq=100.
    for k in range(6):
        ds=(START+timedelta(days=k)).strftime('%Y-%m-%d')
        if ds in byday:daily2.append(byday[ds]);eq=byday[ds]['final_equity']
        else:daily2.append({'day':ds,'start_equity':eq,'final_equity':eq,'net_pnl':0.,'return_pct':0.,'trades':0,'wins':0,'losses':0,'win_rate':None,'gross_pnl':0.,'modeled_costs':0.,'pf_net':None,'max_dd_pct':0.})
    overall=summarize(alltr,100.,equity,peak,maxdd)
    bysetup={}
    for name in ['VALUE_TRANSITION','SWEEP_TRANSITION']:
        ts=[t for t in alltr if t['setup']==name];w=[t for t in ts if t['net_pnl']>0];l=[t for t in ts if t['net_pnl']<=0];gp=sum(t['net_pnl'] for t in w);gl=-sum(t['net_pnl'] for t in l)
        bysetup[name]={'trades':len(ts),'wins':len(w),'losses':len(l),'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None)}
    payload={'version':'DARA-v10.4-StateTransition-Adaptive','period_tehran':[START.isoformat(),END.isoformat()],'starting_equity':100.0,'changes':['Freshness/no-chase filter prevents entering after local impulse is already extended','One-minute hold confirmation after informed-flow trigger','Adaptive TP 0.33%-0.55% based only on 5m/15m ATR; never below 3x modeled roundtrip friction','Velocity thesis-stop after 12m when a scalp never achieves 0.12% MFE and is non-positive','Sweep/absorption remains a separate reversal engine and is modestly broadened without using >15m context'],'methodology_notes':['v10.4 was designed after Sep1-2 failures. Sep3-8 has been used by older DARA development, so it is not pristine OOS for the overall project.','Historical full L2 unavailable; 1m taker-buy volume remains aggressor-flow proxy.','No maker-fee advantage credited; 0.11% all-in modeled friction.'],'daily':daily2,'overall':overall,'by_setup':bysetup,'reject_counts':rejects,'trades':alltr};OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(payload,indent=2));print(json.dumps(overall,indent=2));print(json.dumps(bysetup,indent=2));print(json.dumps(rejects,indent=2))
if __name__=='__main__':main()
