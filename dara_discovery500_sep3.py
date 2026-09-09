import json, math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_discovery100_sep2 as d
import dara_discovery100_learned_sep2 as learned
import dara_v11_liquidity_transition_sep1_8 as r

TEHRAN=r.TEHRAN
START=datetime(2026,9,3,0,0,tzinfo=TEHRAN); END=datetime(2026,9,4,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000); E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_discovery500_sep3.json')
COST=0.0011; RISK=0.0002; MAX_LEV=3.0; MIN_STOP=0.0016; MAX_STOP=0.0032; NOBS=500

def ctx(z,f5,f15):
    return f5.get(r.b.last_closed(z['t'],5)), f15.get(r.b.last_closed(z['t'],15))

def tp_for(a5,a15):
    cap=min(0.0044,1.8*a15['atrp'],3.0*a5['atrp'])
    return 0.0044 if cap>=0.0044 else 0.0033

def mkstop(entry,raw,side):
    sp=(entry-raw)/entry if side=='LONG' else (raw-entry)/entry
    sp=max(MIN_STOP,min(MAX_STOP,sp))
    return entry*(1-sp) if side=='LONG' else entry*(1+sp)

def make(setup,side,i,m,f5,f15,score,diag,rawstop=None):
    if i+1>=len(m): return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    ei=i+1;entry=m[ei]['o'];tp=tp_for(a5,a15)
    if rawstop is None:
        look=m[max(0,i-4):i+1]
        rawstop=min(q['l'] for q in look)*0.9997 if side=='LONG' else max(q['h'] for q in look)*1.0003
    stop=mkstop(entry,rawstop,side)
    vd=(z['c']/z['vwap15']-1)*100
    return (setup,side,stop,ei,score,{'delta':z['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':vd,**diag},tp)

def flow_eff(i,m,f5,f15):
    if i<3:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    d3=sum(q['delta'] for q in m[i-2:i+1])/3; r3=z['c']/m[i-3]['c']-1; vol=z['volr']
    # Sep2 lesson: 1x-2x volume was toxic unless a true aligned break. Avoid it here.
    if 1.0<=vol<2.0:return None
    if d3>=.10 and r3>0 and a15['regime']!='DOWN':
        eff=min(2.0,abs(r3)/(abs(d3)+1e-9)*100)
        return make('FLOW_EFF','LONG',i,m,f5,f15,7+int(a5['regime']=='UP')+int(abs(d3)>=.35),{'delta3':d3,'ret3_pct':r3*100,'eff':eff})
    if d3<=-.10 and r3<0 and a15['regime']!='UP':
        eff=min(2.0,abs(r3)/(abs(d3)+1e-9)*100)
        return make('FLOW_EFF','SHORT',i,m,f5,f15,7+int(a5['regime']=='DOWN')+int(abs(d3)>=.35),{'delta3':d3,'ret3_pct':r3*100,'eff':eff})
    return None

def micro_break(i,m,f5,f15):
    if i<6:return None
    z=m[i];pre=m[i-6:i];hi=max(q['h'] for q in pre);lo=min(q['l'] for q in pre);a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    vol=z['volr'];rng=(hi-lo)/z['c']
    if 1.0<=vol<2.0:return None
    if z['c']>hi and z['delta']>.05 and a15['regime']=='UP':
        return make('MICRO_BREAK','LONG',i,m,f5,f15,9+int(a5['regime']=='UP'),{'range6_pct':rng*100},lo)
    if z['c']<lo and z['delta']<-.05 and a15['regime']=='DOWN':
        return make('MICRO_BREAK','SHORT',i,m,f5,f15,9+int(a5['regime']=='DOWN'),{'range6_pct':rng*100},hi)
    return None

def momentum_cont(i,m,f5,f15):
    if i<5:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    r3=z['c']/m[i-3]['c']-1; d2=(m[i-1]['delta']+z['delta'])/2;vol=z['volr']
    if 1.0<=vol<2.0:return None
    if r3>.0008 and d2>.08 and a15['regime']=='UP' and a5['regime']!='DOWN':
        return make('MOMENTUM_CONT','LONG',i,m,f5,f15,8+int(r3>.0015),{'ret3_pct':r3*100,'delta2':d2})
    if r3<-.0008 and d2<-.08 and a15['regime']=='DOWN' and a5['regime']!='UP':
        return make('MOMENTUM_CONT','SHORT',i,m,f5,f15,8+int(r3<-.0015),{'ret3_pct':r3*100,'delta2':d2})
    return None

def balance_probe(i,m,f5,f15):
    # New discovery family, not the failed raw sweep/pullback logic.
    if i<4:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15 or a15['regime']!='RANGE':return None
    vd=(z['c']/z['vwap15']-1); pos=(z['c']-z['l'])/max(z['h']-z['l'],1e-9)
    if vd>.0010 and z['delta']>.20 and pos<.65:
        return make('BALANCE_FADE','SHORT',i,m,f5,f15,6,{'stretch_pct':vd*100})
    if vd<-.0010 and z['delta']<-.20 and pos>.35:
        return make('BALANCE_FADE','LONG',i,m,f5,f15,6,{'stretch_pct':vd*100})
    return None

def state_probe(i,m,f5,f15):
    # Last-resort broad hypothesis for discovery only. Explicitly excludes Sep2 toxic raw sweep/pullback rules.
    if i<4:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    d3=sum(q['delta'] for q in m[i-2:i+1])/3;r3=z['c']/m[i-3]['c']-1;vol=z['volr'];vd=(z['c']/z['vwap15']-1)
    if 1.0<=vol<2.0:return None
    if a15['regime']=='UP': side='LONG' if d3>=-.08 else ('SHORT' if vd>.0015 and r3<0 else 'LONG')
    elif a15['regime']=='DOWN': side='SHORT' if d3<=.08 else ('LONG' if vd<-.0015 and r3>0 else 'SHORT')
    else:
        side='LONG' if d3 + r3*120 >=0 else 'SHORT'
    return make('STATE_PROBE',side,i,m,f5,f15,4,{'delta3':d3,'ret3_pct':r3*100})

def candidates(i,m,f5,f15):
    arr=[]
    for fn in (micro_break,momentum_cont,flow_eff,balance_probe):
        try:
            x=fn(i,m,f5,f15)
            if x:arr.append(x)
        except Exception:pass
    if not arr:
        x=state_probe(i,m,f5,f15)
        if x:arr.append(x)
    return sorted(arr,key=lambda x:x[4],reverse=True)

def simulate(m,ei,side,stop,tp,equity=100.0):
    entry=m[ei]['o'];sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<=0:return None
    n=min(MAX_LEV*equity,equity*RISK/(sp+COST));target=entry*(1+tp if side=='LONG' else 1-tp);floor=entry*(1+.0033 if side=='LONG' else 1-.0033)
    mfe=mae=0.;activated=False;end=min(len(m)-1,ei+60)
    for j in range(ei,end+1):
        b=m[j];fav=(b['h']/entry-1) if side=='LONG' else (entry/b['l']-1);adv=(entry/b['l']-1) if side=='LONG' else (b['h']/entry-1)
        mfe=max(mfe,fav);mae=max(mae,adv)
        # Profit floor only becomes active after a prior bar has demonstrated >=3x fee favorable excursion.
        if activated:
            floor_hit=b['l']<=floor if side=='LONG' else b['h']>=floor
            if floor_hit:
                gross=n*.0033;cost=n*COST;return j,floor,'3FEE_FLOOR',gross,cost,gross-cost,n,mfe,mae
        st=b['l']<=stop if side=='LONG' else b['h']>=stop;hit=b['h']>=target if side=='LONG' else b['l']<=target
        if st:
            ret=stop/entry-1 if side=='LONG' else entry/stop-1;gross=n*ret;cost=n*COST;return j,stop,'STOP',gross,cost,gross-cost,n,mfe,mae
        if hit:
            gross=n*tp;cost=n*COST;return j,target,'TP',gross,cost,gross-cost,n,mfe,mae
        if fav>=.0033: activated=True
        px=b['c'];ret=px/entry-1 if side=='LONG' else entry/px-1
        if j>=ei+2 and mfe<.0005 and ret<=-.00035:
            gross=n*ret;cost=n*COST;return j,px,'EARLY_NO_FOLLOW',gross,cost,gross-cost,n,mfe,mae
        if j>=ei+4 and mfe<.0010 and ret<=0:
            gross=n*ret;cost=n*COST;return j,px,'EARLY_WEAK_PROGRESS',gross,cost,gross-cost,n,mfe,mae
        if j>=ei+10 and ret<=0:
            gross=n*ret;cost=n*COST;return j,px,'THESIS_FAIL',gross,cost,gross-cost,n,mfe,mae
    px=m[end]['c'];ret=px/entry-1 if side=='LONG' else entry/px-1;gross=n*ret;cost=n*COST
    return end,px,'DIAG_TIMEOUT',gross,cost,gross-cost,n,mfe,mae

def stats(xs):
    w=[x for x in xs if x['net_pnl']>0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in xs if x['net_pnl']<=0)
    return {'n':len(xs),'wins':len(w),'wr':round(100*len(w)/len(xs),1) if xs else None,'gross':round(sum(x['gross_pnl'] for x in xs),6),'cost':round(sum(x['cost'] for x in xs),6),'net':round(sum(x['net_pnl'] for x in xs),6),'pf':round(gp/gl,2) if gl else (99.0 if gp else None),'mfe':round(sum(x['mfe_pct'] for x in xs)/len(xs),3) if xs else None,'mae':round(sum(x['mae_pct'] for x in xs)/len(xs),3) if xs else None}

def main():
    raw=[];dd=datetime(2026,9,2,tzinfo=timezone.utc)
    while dd.date()<=datetime(2026,9,4,tzinfo=timezone.utc).date():raw+=r.b.get_daily(dd);dd+=timedelta(days=1)
    raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw)
    f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
    day_idx=[i for i,z in enumerate(m) if S<=z['t']<E and i>=8 and i+2<len(m)]
    # 500 deterministic intraday slots: first learned candidate in each slot, otherwise a state probe near slot midpoint.
    obs=[];used=set();nslots=NOBS
    for sidx in range(nslots):
        lo=S + int((E-S)*sidx/nslots); hi=S + int((E-S)*(sidx+1)/nslots)
        inds=[i for i in day_idx if lo<=m[i]['t']<hi]
        if not inds:continue
        picks=[]
        for i in inds:
            cs=candidates(i,m,f5,f15)
            if cs:picks.append((cs[0][4],i,cs[0]))
        if picks:
            _,i,x=max(picks,key=lambda q:q[0])
        else:
            i=inds[len(inds)//2];x=state_probe(i,m,f5,f15)
            if not x:continue
        setup,side,stop,ei,score,diag,tp=x
        if ei>=len(m) or m[ei]['t']>=E:continue
        sim=simulate(m,ei,side,stop,tp,100.0)
        if not sim:continue
        j,px,why,gross,cost,net,n,mfe,mae=sim
        if m[j]['t']>=E:continue
        et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        key=et.isoformat()
        if key in used:continue
        used.add(key)
        sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
        obs.append({'n':len(obs)+1,'setup':setup,'side':side,'entry_time':key,'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'stop_pct':round(sp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'mae_pct':round(mae*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'diagnostics':diag,'source_slot':sidx})
    # If a few slots had no valid state, fill chronologically from unused eligible minutes.
    if len(obs)<NOBS:
        for i in day_idx:
            if len(obs)>=NOBS:break
            x=candidates(i,m,f5,f15)
            if not x:continue
            setup,side,stop,ei,score,diag,tp=x[0]
            et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN).isoformat()
            if et in used or m[ei]['t']>=E:continue
            sim=simulate(m,ei,side,stop,tp,100.0)
            if not sim:continue
            j,px,why,gross,cost,net,n,mfe,mae=sim
            if m[j]['t']>=E:continue
            used.add(et);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
            sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
            obs.append({'n':len(obs)+1,'setup':setup,'side':side,'entry_time':et,'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'stop_pct':round(sp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'mae_pct':round(mae*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'diagnostics':diag,'source_slot':'fill'})
    obs=sorted(obs,key=lambda x:x['entry_time'])[:NOBS]
    # Non-overlapping sequential subset, applying the same trades to one $100 account.
    seq=[];eq=100.;next_ok=''
    by_entry={x['entry_time']:x for x in obs}
    for x in obs:
        if next_ok and x['entry_time']<next_ok:continue
        # rescale pnl from independent $100 probe to current equity linearly (risk sizing is equity-proportional)
        scale=eq/100.;y=dict(x);y['equity_before']=round(eq,6);y['gross_pnl']=round(x['gross_pnl']*scale,6);y['cost']=round(x['cost']*scale,6);y['net_pnl']=round(x['net_pnl']*scale,6);eq+=y['net_pnl'];y['equity_after']=round(eq,6);seq.append(y);next_ok=x['exit_time']
    by_setup={};by_exit={};by_side={}
    for key,fn in [('setup',by_setup),('reason',by_exit),('side',by_side)]:
        vals=sorted(set(x[key] for x in obs))
        for v in vals: fn[v]=stats([x for x in obs if x[key]==v])
    failures={'NO_FOLLOW':[],'WEAK_PROGRESS':[],'FEE_INSUFFICIENT':[],'NEAR_TARGET_GIVEBACK':[],'OTHER_LOSS':[]}
    for x in obs:
        if x['net_pnl']>0:continue
        if x['mfe_pct']<.05:cat='NO_FOLLOW'
        elif x['mfe_pct']<.12:cat='WEAK_PROGRESS'
        elif x['mfe_pct']<.33:cat='FEE_INSUFFICIENT'
        elif x['reason'] not in ('TP','3FEE_FLOOR'):cat='NEAR_TARGET_GIVEBACK'
        else:cat='OTHER_LOSS'
        failures[cat].append(x)
    out={'version':'DARA-Discovery500-Sep3-LearnedFromSep1Sep2','period_tehran':[START.isoformat(),END.isoformat()],'purpose':'500 independent discovery futures probes across Sep3 to learn failure modes after applying Sep1/Sep2 lessons. Includes a separate non-overlapping sequential subset for deployability sanity.','rules':{'risk_per_probe':RISK,'modeled_roundtrip_fee':COST,'max_exposure':MAX_LEV,'min_stop_pct':MIN_STOP*100,'profit_rule':'Normal profit exits at 0.33% or 0.44% gross; 3x-fee floor activates only after a prior bar proves >=0.33% MFE','timeframes':'1m/5m/15m only','quarantined':'raw SWEEP_RECLAIM and PULLBACK_RELOAD','toxic_volume_rule':'avoid 1.0x-2.0x relative volume except none in this discovery engine'},'overall_500':stats(obs),'observations':len(obs),'sequential_subset':{'trades':len(seq),'final_equity':round(eq,6),'net':round(eq-100,6),'stats':stats(seq)},'by_setup':by_setup,'by_exit':by_exit,'by_side':by_side,'failure_taxonomy':{k:stats(v) for k,v in failures.items()},'trades':obs,'sequential_trades':seq,'notes':['500 discovery observations may overlap in time; they are independent research probes, not 500 simultaneously executable BTC positions.','Sequential subset is non-overlapping and equity-compounded.','Historical full L2 unavailable; Binance 1m taker-buy delta remains an aggressor-flow proxy.','Sep3 parameters were set from Sep1/Sep2 lessons before reading Sep3 results.']}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'overall_500':out['overall_500'],'sequential_subset':out['sequential_subset'],'by_setup':by_setup,'failure_taxonomy':out['failure_taxonomy']},indent=2))
if __name__=='__main__':main()
