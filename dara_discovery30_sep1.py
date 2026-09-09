import json, math
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v11_liquidity_transition_sep1_8 as r

TEHRAN=r.TEHRAN
START=datetime(2026,9,1,0,0,tzinfo=TEHRAN); END=datetime(2026,9,2,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000); E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_discovery30_sep1.json')
COST=0.0011; RISK=0.0010; MAX_LEV=3.0; MAX_TRADES=30; MAX_HOLD=25; COOLDOWN=2
MIN_STOP=0.0012; MAX_STOP=0.0030

def tp_for(a5,a15):
    cap=min(0.0044,1.65*a15['atrp'],2.8*a5['atrp'])
    return 0.0044 if cap>=0.0044 else (0.0033 if cap>=0.0033 else None)

def mkstop(entry,raw,side):
    sp=(entry-raw)/entry if side=='LONG' else (raw-entry)/entry
    sp=max(MIN_STOP,min(MAX_STOP,sp))
    return entry*(1-sp) if side=='LONG' else entry*(1+sp)

def getctx(z,f5,f15):
    a5=f5.get(r.b.last_closed(z['t'],5)); a15=f15.get(r.b.last_closed(z['t'],15))
    return a5,a15

def sweep(i,m,f5,f15):
    if i<4 or i+1>=len(m): return None
    z=m[i]; y=m[i+1]; a5,a15=getctx(z,f5,f15)
    if not a5 or not a15:return None
    tp=tp_for(a5,a15)
    if tp is None:return None
    rng=max(z['h']-z['l'],1e-9); pos=(z['c']-z['l'])/rng
    if z['l']<z['prev15l'] and z['c']>z['prev15l'] and z['delta']<=-0.05 and pos>=0.45 and y['delta']>=0 and y['c']>z['c']:
        stop=mkstop(m[i+2]['o'] if i+2<len(m) else y['c'],z['l']*0.9995,'LONG')
        return ('SWEEP_RECLAIM','LONG',stop,i+1,8,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':(z['c']/z['vwap15']-1)*100},tp)
    if z['h']>z['prev15h'] and z['c']<z['prev15h'] and z['delta']>=0.05 and pos<=0.55 and y['delta']<=0 and y['c']<z['c']:
        stop=mkstop(m[i+2]['o'] if i+2<len(m) else y['c'],z['h']*1.0005,'SHORT')
        return ('SWEEP_RECLAIM','SHORT',stop,i+1,8,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':(z['c']/z['vwap15']-1)*100},tp)
    return None

def flow_pulse(i,m,f5,f15):
    if i<4 or i+1>=len(m):return None
    z=m[i];y=m[i+1];a5,a15=getctx(z,f5,f15)
    if not a5 or not a15:return None
    tp=tp_for(a5,a15)
    if tp is None:return None
    pre=m[i-3:i];vd=(z['c']/z['vwap15']-1)*100
    if z['delta']>=0.25 and z['c']>=z['o'] and y['delta']>-0.18 and y['c']>=z['c']*0.9994 and a15['regime']!='DOWN':
        raw=min(q['l'] for q in pre+[z,y])*0.9997;entry=m[i+2]['o'];stop=mkstop(entry,raw,'LONG')
        score=5+int(z['delta']>=.45)+int(z['volr']>=1)+int(a5['regime']=='UP')+int(z['c']>max(q['c'] for q in pre))
        return ('FLOW_PULSE','LONG',stop,i+1,score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':vd},tp)
    if z['delta']<=-0.25 and z['c']<=z['o'] and y['delta']<0.18 and y['c']<=z['c']*1.0006 and a15['regime']!='UP':
        raw=max(q['h'] for q in pre+[z,y])*1.0003;entry=m[i+2]['o'];stop=mkstop(entry,raw,'SHORT')
        score=5+int(z['delta']<=-.45)+int(z['volr']>=1)+int(a5['regime']=='DOWN')+int(z['c']<min(q['c'] for q in pre))
        return ('FLOW_PULSE','SHORT',stop,i+1,score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':vd},tp)
    return None

def micro_break(i,m,f5,f15):
    if i<8 or i+1>=len(m):return None
    z=m[i];y=m[i+1];a5,a15=getctx(z,f5,f15)
    if not a5 or not a15:return None
    tp=tp_for(a5,a15)
    if tp is None:return None
    pre=m[i-8:i]; hi=max(q['h'] for q in pre);lo=min(q['l'] for q in pre);vd=(z['c']/z['vwap15']-1)*100
    if z['c']>hi and z['delta']>=0.12 and y['c']>=hi*0.9995 and y['delta']>-0.2 and a15['regime']!='DOWN':
        entry=m[i+2]['o']; stop=mkstop(entry,min(z['l'],y['l'],hi)*0.9997,'LONG');score=6+int(z['volr']>=1)+int(a5['regime']=='UP')+int(z['delta']>=.35)
        return ('MICRO_BREAK','LONG',stop,i+1,score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':vd,'pre_range_pct':(hi-lo)/z['c']*100},tp)
    if z['c']<lo and z['delta']<=-0.12 and y['c']<=lo*1.0005 and y['delta']<0.2 and a15['regime']!='UP':
        entry=m[i+2]['o']; stop=mkstop(entry,max(z['h'],y['h'],lo)*1.0003,'SHORT');score=6+int(z['volr']>=1)+int(a5['regime']=='DOWN')+int(z['delta']<=-.35)
        return ('MICRO_BREAK','SHORT',stop,i+1,score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':vd,'pre_range_pct':(hi-lo)/z['c']*100},tp)
    return None

def vwap_cross(i,m,f5,f15):
    if i<3 or i+1>=len(m):return None
    z=m[i];y=m[i+1];p=m[i-1];a5,a15=getctx(z,f5,f15)
    if not a5 or not a15:return None
    tp=tp_for(a5,a15)
    if tp is None:return None
    if p['c']<p['vwap15'] and z['c']>z['vwap15'] and z['delta']>=0.15 and y['c']>=z['vwap15'] and a15['regime']!='DOWN':
        entry=m[i+2]['o'];stop=mkstop(entry,min(p['l'],z['l'],y['l'])*0.9997,'LONG');score=5+int(a5['regime']=='UP')+int(z['volr']>=1)+int(y['delta']>=.08)
        return ('VWAP_CROSS','LONG',stop,i+1,score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':(z['c']/z['vwap15']-1)*100},tp)
    if p['c']>p['vwap15'] and z['c']<z['vwap15'] and z['delta']<=-0.15 and y['c']<=z['vwap15'] and a15['regime']!='UP':
        entry=m[i+2]['o'];stop=mkstop(entry,max(p['h'],z['h'],y['h'])*1.0003,'SHORT');score=5+int(a5['regime']=='DOWN')+int(z['volr']>=1)+int(y['delta']<=-.08)
        return ('VWAP_CROSS','SHORT',stop,i+1,score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':(z['c']/z['vwap15']-1)*100},tp)
    return None

def pullback(i,m,f5,f15):
    if i<5 or i+1>=len(m):return None
    z=m[i];y=m[i+1];a5,a15=getctx(z,f5,f15)
    if not a5 or not a15:return None
    tp=tp_for(a5,a15)
    if tp is None:return None
    vd=(z['c']/z['vwap15']-1)*100
    if a5['regime']=='UP' and a15['regime']=='UP' and z['delta']<=-0.18 and y['delta']>=0.02 and y['c']>z['c'] and abs(vd)<=0.22:
        entry=m[i+2]['o'];stop=mkstop(entry,min(z['l'],y['l'])*0.9997,'LONG');score=7+int(z['delta']<=-.4)+int(y['delta']>=.15)+int(z['volr']<=1.3)
        return ('PULLBACK_RELOAD','LONG',stop,i+1,score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':vd},tp)
    if a5['regime']=='DOWN' and a15['regime']=='DOWN' and z['delta']>=0.18 and y['delta']<=-0.02 and y['c']<z['c'] and abs(vd)<=0.22:
        entry=m[i+2]['o'];stop=mkstop(entry,max(z['h'],y['h'])*1.0003,'SHORT');score=7+int(z['delta']>=.4)+int(y['delta']<=-.15)+int(z['volr']<=1.3)
        return ('PULLBACK_RELOAD','SHORT',stop,i+1,score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':vd},tp)
    return None

def all_candidates(i,m,f5,f15):
    arr=[]
    for fn in (sweep,pullback,micro_break,vwap_cross,flow_pulse):
        try:
            s=fn(i,m,f5,f15)
            if s:arr.append(s)
        except Exception:pass
    return sorted(arr,key=lambda x:x[4],reverse=True)

def simulate(m,ei,side,stop,equity,tp):
    entry=m[ei]['o'];sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<=0:return None
    n=min(MAX_LEV*equity,equity*RISK/(sp+COST));target=entry*(1+tp if side=='LONG' else 1-tp);end=min(len(m)-1,ei+MAX_HOLD);mfe=0.;mae=0.
    for j in range(ei,end+1):
        b=m[j];fav=(b['h']/entry-1) if side=='LONG' else (entry/b['l']-1);adv=(entry/b['l']-1) if side=='LONG' else (b['h']/entry-1);mfe=max(mfe,fav);mae=max(mae,adv)
        st=b['l']<=stop if side=='LONG' else b['h']>=stop;hit=b['h']>=target if side=='LONG' else b['l']<=target
        if st:
            ret=stop/entry-1 if side=='LONG' else entry/stop-1;gross=n*ret;cost=n*COST;return j,stop,'STOP',gross,cost,gross-cost,n,mfe,mae
        if hit:
            gross=n*tp;cost=n*COST;return j,target,'TP',gross,cost,gross-cost,n,mfe,mae
    px=m[end]['c'];ret=px/entry-1 if side=='LONG' else entry/px-1;gross=n*ret;cost=n*COST;return end,px,'TIME',gross,cost,gross-cost,n,mfe,mae

def main():
    raw=[];d=datetime(2026,8,31,tzinfo=timezone.utc)
    while d.date()<=datetime(2026,9,2,tzinfo=timezone.utc).date():raw+=r.b.get_daily(d);d+=timedelta(days=1)
    raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw);f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
    eq=100.;peak=100.;dd=0.;ts=[];counts={};last=-10**9;i=0
    while i<len(m)-3 and len(ts)<MAX_TRADES:
        if m[i]['t']<S:i+=1;continue
        if m[i]['t']>=E:break
        if i-last<COOLDOWN:i+=1;continue
        cs=all_candidates(i,m,f5,f15)
        for c in cs:counts[c[0]]=counts.get(c[0],0)+1
        if not cs:i+=1;continue
        setup,side,stop,ci,score,diag,tp=cs[0];ei=ci+1
        if ei>=len(m) or m[ei]['t']>=E:break
        sim=simulate(m,ei,side,stop,eq,tp)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n,mfe,mae=sim
        if m[j]['t']>=E:break
        before=eq;eq+=net;peak=max(peak,eq);dd=max(dd,(peak-eq)/peak)
        et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
        ts.append({'n':len(ts)+1,'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'stop_pct':round(sp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'mae_pct':round(mae*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(eq,6),'diagnostics':diag})
        last=j;i=j+1
    w=[x for x in ts if x['net_pnl']>0];l=[x for x in ts if x['net_pnl']<=0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in l)
    by={}
    for name in sorted(set(x['setup'] for x in ts)):
        q=[x for x in ts if x['setup']==name];by[name]={'trades':len(q),'wins':sum(x['net_pnl']>0 for x in q),'losses':sum(x['net_pnl']<=0 for x in q),'win_rate':round(100*sum(x['net_pnl']>0 for x in q)/len(q),1),'gross':round(sum(x['gross_pnl'] for x in q),6),'net':round(sum(x['net_pnl'] for x in q),6),'avg_mfe_pct':round(sum(x['mfe_pct'] for x in q)/len(q),3),'avg_mae_pct':round(sum(x['mae_pct'] for x in q)/len(q),3)}
    out={'version':'DARA-Discovery30-Sep1','period_tehran':[START.isoformat(),END.isoformat()],'purpose':'Deliberately lower-sensitivity discovery run to collect up to 30 sequential non-overlapping futures scalp observations; not a deployable strategy or validation.','rules':{'risk_per_trade':RISK,'modeled_roundtrip_fee':COST,'max_exposure':MAX_LEV,'max_hold_minutes':MAX_HOLD,'profit_target':'0.33% or 0.44% gross, never below 3x modeled full roundtrip fee','timeframes':'1m/5m/15m only'},'overall':{'final_equity':round(eq,6),'net_pnl':round(eq-100,6),'return_pct':round((eq/100-1)*100,3),'candidate_counts':counts,'trades':len(ts),'wins':len(w),'losses':len(l),'win_rate':round(100*len(w)/len(ts),1) if ts else None,'gross_pnl':round(sum(x['gross_pnl'] for x in ts),6),'modeled_costs':round(sum(x['cost'] for x in ts),6),'pf_net':round(gp/gl,2) if gl else (99.0 if gp else None),'max_dd_pct':round(dd*100,3)},'by_setup':by,'trades':ts,'notes':['Sep1 has been heavily studied before, so this is diagnostic/in-sample discovery only.','Historical full L2 unavailable; 1m taker-buy delta is aggressor-flow proxy.','No hindsight selection inside the run: candidates are evaluated chronologically, highest current score is taken, then trade is simulated before moving forward.']}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out['overall'],indent=2));print(json.dumps(by,indent=2))
if __name__=='__main__':main()
