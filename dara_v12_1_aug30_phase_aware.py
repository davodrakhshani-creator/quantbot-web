import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v11_liquidity_transition_sep1_8 as r
import dara_v12_aug30_multi_playbook as v12

TEHRAN=r.TEHRAN
START=datetime(2026,8,30,0,0,tzinfo=TEHRAN); END=datetime(2026,8,31,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000); E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v12_1_aug30_phase_aware.json')
MAX_TRADES=15;COOLDOWN=4;LOSS_LOCK=15;DAILY_STOP=-0.015

def ignition(i,m,f5,f15):
    if i<15 or i+1>=len(m):return None
    z=m[i];y=m[i+1];a5=f5.get(r.b.last_closed(z['t'],5));a15=f15.get(r.b.last_closed(z['t'],15))
    if not a5 or not a15:return None
    tp=v12.target_for(a5,a15)
    if tp is None:return None
    prev=m[i-3:i];vd=(z['c']/z['vwap15']-1)
    # Early sponsored move: near value, strong signed aggression, price begins to escape local micro-range.
    if a15['regime']!='DOWN' and a5['regime']!='DOWN' and abs(vd)<=0.0014:
        trig=z['delta']>=0.32 and z['c']>max(q['c'] for q in prev) and z['c']>=z['o'] and z['volr']>=0.12
        hold=y['c']>=z['c']*0.9995 and y['delta']>-0.15
        if trig and hold:
            stop=min([q['l'] for q in prev]+[z['l'],y['l']])*0.9996
            score=7+sum([z['delta']>=.5,z['volr']>=1.0,y['delta']>=.05,z['c']>max(q['h'] for q in prev)])
            return ('MOMENTUM_IGNITION','LONG',stop,i+1,score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'vwap_dist_pct':vd*100,'reg5':a5['regime'],'reg15':a15['regime']},tp)
    if a15['regime']!='UP' and a5['regime']!='UP' and abs(vd)<=0.0014:
        trig=z['delta']<=-0.32 and z['c']<min(q['c'] for q in prev) and z['c']<=z['o'] and z['volr']>=0.12
        hold=y['c']<=z['c']*1.0005 and y['delta']<0.15
        if trig and hold:
            stop=max([q['h'] for q in prev]+[z['h'],y['h']])*1.0004
            score=7+sum([z['delta']<=-.5,z['volr']>=1.0,y['delta']<=-.05,z['c']<min(q['l'] for q in prev)])
            return ('MOMENTUM_IGNITION','SHORT',stop,i+1,score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'vwap_dist_pct':vd*100,'reg5':a5['regime'],'reg15':a15['regime']},tp)
    return None

def reload(i,m,f5,f15):
    if i<20 or i+1>=len(m):return None
    z=m[i];y=m[i+1];a5=f5.get(r.b.last_closed(z['t'],5));a15=f15.get(r.b.last_closed(z['t'],15))
    if not a5 or not a15:return None
    tp=v12.target_for(a5,a15)
    if tp is None:return None
    vd=z['c']/z['vwap15']-1; pre=m[i-5:i]
    # In a live trend, aggressive counter-flow near/below value is a reload only if the next minute fails to extend it.
    if a5['regime']=='UP' and a15['regime']=='UP' and -0.0020<=vd<=0.0006:
        attack=z['delta']<=-0.30 and z['c']<=z['o']
        fail=y['delta']>=0.02 and y['c']>z['c'] and y['l']>=z['l']*0.9996
        if attack and fail:
            stop=min([q['l'] for q in pre]+[z['l'],y['l']])*0.9996
            score=8+sum([z['delta']<=-.5,y['delta']>=.12,z['volr']<=1.2,vd<0])
            return ('PULLBACK_RELOAD','LONG',stop,i+1,score,{'attack_delta':z['delta'],'flip_delta':y['delta'],'volr':z['volr'],'vwap_dist_pct':vd*100},tp)
    if a5['regime']=='DOWN' and a15['regime']=='DOWN' and -0.0006<=vd<=0.0020:
        attack=z['delta']>=0.30 and z['c']>=z['o']
        fail=y['delta']<=-0.02 and y['c']<z['c'] and y['h']<=z['h']*1.0004
        if attack and fail:
            stop=max([q['h'] for q in pre]+[z['h'],y['h']])*1.0004
            score=8+sum([z['delta']>=.5,y['delta']<=-.12,z['volr']<=1.2,vd>0])
            return ('PULLBACK_RELOAD','SHORT',stop,i+1,score,{'attack_delta':z['delta'],'flip_delta':y['delta'],'volr':z['volr'],'vwap_dist_pct':vd*100},tp)
    return None

def exhaustion_break(i,m,f5,f15):
    if i<20 or i+1>=len(m):return None
    z=m[i];y=m[i+1];a5=f5.get(r.b.last_closed(z['t'],5));a15=f15.get(r.b.last_closed(z['t'],15))
    if not a5 or not a15:return None
    tp=v12.target_for(a5,a15)
    if tp is None:return None
    pre=m[i-8:i];vd=z['c']/z['vwap15']-1
    # Counter-trend only after value is lost and two-minute aggression confirms the failure of the old trend.
    if a5['regime']=='UP' and a15['regime']=='UP':
        recent_high=max(q['h'] for q in pre); stretched=(recent_high-z['c'])/z['c']>=0.0015
        d2=(m[i-1]['delta']+z['delta'])/2
        breakdown=vd<=-0.0010 and d2<=-0.30 and z['c']<m[i-1]['c'] and y['c']<=z['c']*1.0003 and y['delta']<0.18
        if stretched and breakdown:
            stop=max(z['h'],m[i-1]['h'],y['h'])*1.0004
            score=9+sum([d2<=-.45,vd<=-.0015,y['delta']<=-.05])
            return ('EXHAUSTION_BREAK','SHORT',stop,i+1,score,{'d2':d2,'vwap_dist_pct':vd*100,'recent_high_dist_pct':(recent_high-z['c'])/z['c']*100},tp)
    if a5['regime']=='DOWN' and a15['regime']=='DOWN':
        recent_low=min(q['l'] for q in pre); stretched=(z['c']-recent_low)/z['c']>=0.0015
        d2=(m[i-1]['delta']+z['delta'])/2
        breakdown=vd>=0.0010 and d2>=0.30 and z['c']>m[i-1]['c'] and y['c']>=z['c']*0.9997 and y['delta']>-0.18
        if stretched and breakdown:
            stop=min(z['l'],m[i-1]['l'],y['l'])*0.9996
            score=9+sum([d2>=.45,vd>=.0015,y['delta']>=.05])
            return ('EXHAUSTION_BREAK','LONG',stop,i+1,score,{'d2':d2,'vwap_dist_pct':vd*100,'recent_low_dist_pct':(z['c']-recent_low)/z['c']*100},tp)
    return None

def liq(i,m,f5,f15):
    s=v12.liq(i,m,f5,f15)
    if not s:return None
    a=list(s);a[4]+=1;return tuple(a)

def candidates(i,m,f5,f15):
    out=[]
    for fn in (exhaustion_break,reload,ignition,liq):
        try:
            s=fn(i,m,f5,f15)
            if s:out.append(s)
        except Exception:pass
    return sorted(out,key=lambda x:x[4],reverse=True)

def main():
    raw=[];d=datetime(2026,8,29,tzinfo=timezone.utc)
    while d.date()<=datetime(2026,8,31,tzinfo=timezone.utc).date():raw+=r.b.get_daily(d);d+=timedelta(days=1)
    raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw);f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
    eq=100.;peak=100.;dd=0.;ts=[];last=-10**9;locks={'LONG':-10**9,'SHORT':-10**9};counts={};i=0
    while i<len(m)-3:
        if m[i]['t']<S:i+=1;continue
        if m[i]['t']>=E:break
        if len(ts)>=MAX_TRADES or eq-100<=DAILY_STOP*100 or i-last<COOLDOWN:i+=1;continue
        cs=candidates(i,m,f5,f15)
        for c in cs:counts[c[0]]=counts.get(c[0],0)+1
        if not cs:i+=1;continue
        setup,side,stop,ci,score,diag,tp=cs[0]
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m) or m[ei]['t']>=E:break
        sim=r.q.simulate(m,ei,side,stop,eq,tp)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n,mfe=sim
        if m[j]['t']>=E:break
        before=eq;eq+=net;peak=max(peak,eq);dd=max(dd,(peak-eq)/peak)
        et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
        ts.append({'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'stop_pct':round(sp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_after':round(eq,6),'diagnostics':diag})
        if net<0:locks[side]=j+LOSS_LOCK
        last=j;i=j+1
    w=[x for x in ts if x['net_pnl']>0];l=[x for x in ts if x['net_pnl']<=0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in l)
    by={}
    for nme in ['MOMENTUM_IGNITION','PULLBACK_RELOAD','EXHAUSTION_BREAK','LIQUIDITY_REVERSAL']:
        qx=[x for x in ts if x['setup']==nme];by[nme]={'trades':len(qx),'wins':sum(x['net_pnl']>0 for x in qx),'net':round(sum(x['net_pnl'] for x in qx),6)}
    out={'version':'DARA-v12.1-PhaseAware-Aug30-Development','period_tehran':[START.isoformat(),END.isoformat()],'starting_equity':100.0,'changes':['Removed late generic VWAP reclaim as dominant engine after v12 failure','Added early Momentum Ignition near value, trend Pullback Reload after counter-flow failure, and Exhaustion Break after value loss','Liquidity reversal retained','Profit target floor >=0.33% gross (=3x full 0.11% fee); prefers 0.44% (=4x fee) when volatility capacity permits'],'overall':{'final_equity':round(eq,6),'net_pnl':round(eq-100,6),'return_pct':round((eq/100-1)*100,3),'candidate_counts':counts,'trades':len(ts),'wins':len(w),'losses':len(l),'win_rate':round(100*len(w)/len(ts),1) if ts else None,'gross_pnl':round(sum(x['gross_pnl'] for x in ts),6),'modeled_costs':round(sum(x['cost'] for x in ts),6),'pf_net':round(gp/gl,2) if gl else (99.0 if gp else None),'max_dd_pct':round(dd*100,3)},'by_setup':by,'trades':ts,'methodology_notes':['This is explicitly in-sample development: Aug30 market-state diagnostics were inspected before v12.1 design. It is not validation.','No timeframe above 15m; historical full L2 unavailable; 1m taker-buy delta is aggressor-flow proxy.']}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out['overall'],indent=2));print(json.dumps(by,indent=2))
if __name__=='__main__':main()
