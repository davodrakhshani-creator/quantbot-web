import json, math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_v14_sep4_opportunity_sweep as v
import dara_v11_liquidity_transition_sep1_8 as r

TEHRAN=v.TEHRAN
START=datetime(2026,9,5,0,0,tzinfo=TEHRAN); END=datetime(2026,9,6,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000); E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_discovery1000_sep5_learned.json')
COST=0.0011; RISK=0.0001; MAX_LEV=3.0; MIN_STOP=0.0016; MAX_STOP=0.0032; NOBS=1000

# Learned BEFORE this replay from Sep1-4 plus the first Sep5 v15 pass:
# - raw sweep/reclaim, raw pullback, old flow-efficiency stay quarantined
# - raw compression break is now quarantined after 0/5 with avg MFE ~0.01% on Sep5
# - no tiny stops; early no-follow and weak-progress exits remain
# - 3x fee protection remains once prior-bar MFE proves >=0.33%
# - state first, direction asymmetric, no blind mirrored shorts
# - accepted break requires hold outside the range, not one-bar poke


def ctx(z,f5,f15):
    return f5.get(r.b.last_closed(z['t'],5)), f15.get(r.b.last_closed(z['t'],15))

def mkstop(entry,raw,side):
    sp=(entry-raw)/entry if side=='LONG' else (raw-entry)/entry
    sp=max(MIN_STOP,min(MAX_STOP,sp))
    return entry*(1-sp) if side=='LONG' else entry*(1+sp)

def tp_for(a5,a15):
    cap=min(.0044,1.8*a15['atrp'],3.0*a5['atrp'])
    return .0044 if cap>=.0044 else .0033

def state(i,m,f5,f15):
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    if a15['regime']=='UP' and a5['regime']=='UP': st='TREND_UP'
    elif a15['regime']=='DOWN' and a5['regime']=='DOWN': st='TREND_DOWN'
    elif a15['regime']=='RANGE' and a5['regime']=='UP': st='TRANSITION_UP'
    elif a15['regime']=='RANGE' and a5['regime']=='DOWN': st='TRANSITION_DOWN'
    elif a15['regime']=='UP' and a5['regime']=='RANGE': st='UP_PAUSE'
    elif a15['regime']=='DOWN' and a5['regime']=='RANGE': st='DOWN_PAUSE'
    else: st='BALANCE'
    return st,a5,a15

def make(setup,side,i,m,f5,f15,score,diag,rawstop=None):
    if i+1>=len(m):return None
    z=m[i];ss=state(i,m,f5,f15)
    if not ss:return None
    st,a5,a15=ss;ei=i+1;entry=m[ei]['o']
    if rawstop is None:
        look=m[max(0,i-5):i+1]
        rawstop=min(x['l'] for x in look)*.9997 if side=='LONG' else max(x['h'] for x in look)*1.0003
    stop=mkstop(entry,rawstop,side)
    vd=(z['c']/z['vwap15']-1)*100
    h=datetime.fromtimestamp(z['t']/1000,timezone.utc).astimezone(TEHRAN).hour
    d={'delta':z['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'state':st,'vwap_dist':vd,'hour':h,**diag}
    return (setup,side,stop,ei,score,d,tp_for(a5,a15))

def accepted_break(i,m,f5,f15):
    # Requires prior bar break + current bar acceptance/hold outside the old range.
    if i<10:return None
    ss=state(i,m,f5,f15)
    if not ss:return None
    st,a5,a15=ss;z=m[i];p=m[i-1];pre=m[i-10:i-1]
    hi=max(x['h'] for x in pre);lo=min(x['l'] for x in pre);rng=(hi-lo)/z['c'];vol=z['volr']
    if rng>.0035:return None
    # long: previous close breaks, current low mostly holds above edge and closes above it
    if p['c']>hi and z['c']>hi and z['l']>hi*.9995 and z['delta']>-.08:
        if st in ('TREND_UP','TRANSITION_UP','UP_PAUSE') and not (vol>=4 and abs(z['delta'])>.65):
            return make('ACCEPTED_BREAK','LONG',i,m,f5,f15,12+int(st=='TREND_UP'),{'range9_pct':rng*100,'edge':hi,'accept_depth_pct':(z['l']/hi-1)*100},lo)
    # short requires stronger agreement/asymmetry
    if p['c']<lo and z['c']<lo and z['h']<lo*1.0005 and z['delta']<.08:
        if st in ('TREND_DOWN','TRANSITION_DOWN','DOWN_PAUSE') and a5['regime']=='DOWN':
            return make('ACCEPTED_BREAK','SHORT',i,m,f5,f15,13+int(st=='TREND_DOWN'),{'range9_pct':rng*100,'edge':lo,'accept_depth_pct':(lo/z['h']-1)*100},hi)
    return None

def retest_renewal(i,m,f5,f15):
    if i<6:return None
    ss=state(i,m,f5,f15)
    if not ss:return None
    st,a5,a15=ss;z=m[i];p=m[i-1];p2=m[i-2]
    r4=z['c']/m[i-4]['c']-1;vol=z['volr'];vd=(z['c']/z['vwap15']-1)
    # long: prior two bars pause/retrace, current regains control; avoid chase near explosive volume.
    if st in ('TREND_UP','UP_PAUSE','TRANSITION_UP') and r4>-.0010:
        pause=(p['c']<=p['o'] or p['delta']<-.05) and (p2['c']<=p2['o'] or p2['delta']<.05)
        renew=z['c']>z['o'] and z['delta']>.12 and z['c']>p['h']*.9997
        if pause and renew and vol<4 and abs(vd)>=.00035:
            return make('RETEST_RENEWAL','LONG',i,m,f5,f15,11+int(st=='TREND_UP'),{'r4_pct':r4*100,'pause_delta':p['delta']})
    if st in ('TREND_DOWN','DOWN_PAUSE','TRANSITION_DOWN') and a5['regime']=='DOWN' and r4<.0010:
        pause=(p['c']>=p['o'] or p['delta']>.05) and (p2['c']>=p2['o'] or p2['delta']>-.05)
        renew=z['c']<z['o'] and z['delta']<-.16 and z['c']<p['l']*1.0003
        if pause and renew and 1.8<=vol<4 and abs(vd)>=.0006:
            return make('RETEST_RENEWAL','SHORT',i,m,f5,f15,12+int(st=='TREND_DOWN'),{'r4_pct':r4*100,'pause_delta':p['delta']})
    return None

def range_rejection(i,m,f5,f15):
    if i<8:return None
    ss=state(i,m,f5,f15)
    if not ss:return None
    st,a5,a15=ss
    if st!='BALANCE' and a15['regime']!='RANGE':return None
    z=m[i];vd=z['c']/z['vwap15']-1;pos=(z['c']-z['l'])/max(z['h']-z['l'],1e-9);vol=z['volr']
    # require meaningful stretch and visible rejection, but not FOMO volume.
    if vd>=.0012 and pos<.45 and z['delta']>.10 and vol<4:
        return make('RANGE_REJECTION','SHORT',i,m,f5,f15,9,{'stretch_pct':vd*100,'close_pos':pos})
    if vd<=-.0012 and pos>.55 and z['delta']<-.10 and vol<4:
        return make('RANGE_REJECTION','LONG',i,m,f5,f15,9,{'stretch_pct':vd*100,'close_pos':pos})
    return None

def displacement_retest(i,m,f5,f15):
    if i<7:return None
    ss=state(i,m,f5,f15)
    if not ss:return None
    st,a5,a15=ss;z=m[i];r3=z['c']/m[i-3]['c']-1;d3=sum(x['delta'] for x in m[i-2:i+1])/3;vol=z['volr'];vd=z['c']/z['vwap15']-1
    if st=='TREND_UP' and .0007<=r3<=.0025 and d3>.08 and .0005<=vd<=.0035 and vol<4:
        return make('DISPLACEMENT_CONT','LONG',i,m,f5,f15,10+int(d3>.25),{'r3_pct':r3*100,'delta3':d3})
    if st=='TREND_DOWN' and -.0025<=r3<=-.0007 and d3<-.12 and -.0035<=vd<=-.0008 and 1.8<=vol<4:
        return make('DISPLACEMENT_CONT','SHORT',i,m,f5,f15,11+int(d3<-.25),{'r3_pct':r3*100,'delta3':d3})
    return None

def state_hypothesis(i,m,f5,f15):
    # Diagnostic-only fallback to create experience, not a claimed production edge.
    if i<5:return None
    ss=state(i,m,f5,f15)
    if not ss:return None
    st,a5,a15=ss;z=m[i];d3=sum(x['delta'] for x in m[i-2:i+1])/3;r3=z['c']/m[i-3]['c']-1;vd=z['c']/z['vwap15']-1
    if st in ('TREND_UP','UP_PAUSE','TRANSITION_UP'):
        side='LONG' if not (d3<-.25 and r3<-.0010) else 'SHORT'
    elif st in ('TREND_DOWN','DOWN_PAUSE','TRANSITION_DOWN'):
        side='SHORT' if not (d3>.25 and r3>.0010) else 'LONG'
    else:
        # balance: bias toward reversion from value stretch; otherwise use price/flow composite
        if vd>.0010: side='SHORT'
        elif vd<-.0010: side='LONG'
        else: side='LONG' if d3+r3*120>=0 else 'SHORT'
    return make('STATE_HYPOTHESIS',side,i,m,f5,f15,4,{'delta3':d3,'r3_pct':r3*100})

def candidates(i,m,f5,f15,allow_fallback=True):
    out=[]
    for fn in (accepted_break,retest_renewal,displacement_retest,range_rejection):
        try:
            x=fn(i,m,f5,f15)
            if x:out.append(x)
        except Exception:pass
    if not out and allow_fallback:
        x=state_hypothesis(i,m,f5,f15)
        if x:out.append(x)
    return sorted(out,key=lambda x:x[4],reverse=True)

def simulate(m,ei,side,stop,tp,equity=100.0):
    entry=m[ei]['o'];sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<=0:return None
    n=min(MAX_LEV*equity,equity*RISK/(sp+COST));target=entry*(1+tp if side=='LONG' else 1-tp);floor=entry*(1+.0033 if side=='LONG' else 1-.0033)
    mfe=mae=0.;activated=False;end=min(len(m)-1,ei+60)
    for j in range(ei,end+1):
        b=m[j];fav=(b['h']/entry-1) if side=='LONG' else (entry/b['l']-1);adv=(entry/b['l']-1) if side=='LONG' else (b['h']/entry-1)
        mfe=max(mfe,fav);mae=max(mae,adv)
        if activated:
            fh=b['l']<=floor if side=='LONG' else b['h']>=floor
            if fh:
                gross=n*.0033;cost=n*COST;return j,floor,'3FEE_FLOOR',gross,cost,gross-cost,n,mfe,mae
        st=b['l']<=stop if side=='LONG' else b['h']>=stop;hit=b['h']>=target if side=='LONG' else b['l']<=target
        if st:
            ret=stop/entry-1 if side=='LONG' else entry/stop-1;gross=n*ret;cost=n*COST;return j,stop,'STOP',gross,cost,gross-cost,n,mfe,mae
        if hit:
            gross=n*tp;cost=n*COST;return j,target,'TP',gross,cost,gross-cost,n,mfe,mae
        if fav>=.0033:activated=True
        px=b['c'];ret=px/entry-1 if side=='LONG' else entry/px-1
        if j>=ei+2 and mfe<.0005 and ret<=-.00035:
            gross=n*ret;cost=n*COST;return j,px,'EARLY_NO_FOLLOW',gross,cost,gross-cost,n,mfe,mae
        if j>=ei+4 and mfe<.0010 and ret<=0:
            gross=n*ret;cost=n*COST;return j,px,'EARLY_WEAK_PROGRESS',gross,cost,gross-cost,n,mfe,mae
        if j>=ei+10 and ret<=0:
            gross=n*ret;cost=n*COST;return j,px,'THESIS_FAIL',gross,cost,gross-cost,n,mfe,mae
    px=m[end]['c'];ret=px/entry-1 if side=='LONG' else entry/px-1;gross=n*ret;cost=n*COST
    return end,px,'DIAG_TIMEOUT',gross,cost,gross-cost,n,mfe,mae

def stat(xs):
    w=[x for x in xs if x['net_pnl']>0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in xs if x['net_pnl']<=0)
    return {'n':len(xs),'wins':len(w),'wr':round(100*len(w)/len(xs),1) if xs else None,'gross':round(sum(x['gross_pnl'] for x in xs),6),'cost':round(sum(x['cost'] for x in xs),6),'net':round(sum(x['net_pnl'] for x in xs),6),'pf':round(gp/gl,2) if gl else (99.0 if gp else None),'mfe':round(sum(x['mfe_pct'] for x in xs)/len(xs),3) if xs else None,'mae':round(sum(x['mae_pct'] for x in xs)/len(xs),3) if xs else None}

def row(x,sim,m,equity=None):
    setup,side,stop,ei,score,diag,tp=x;j,px,why,gross,cost,net,n,mfe,mae=sim
    et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
    sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
    z={'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'stop_pct':round(sp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'mae_pct':round(mae*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'diagnostics':diag}
    if equity is not None:z['equity_after']=round(equity+net,6)
    return z

def main():
    raw=[];dd=datetime(2026,9,4,tzinfo=timezone.utc)
    while dd.date()<=datetime(2026,9,6,tzinfo=timezone.utc).date():raw+=r.b.get_daily(dd);dd+=timedelta(days=1)
    raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw)
    f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
    inds=[i for i,z in enumerate(m) if S<=z['t']<E and i>=12 and i+1<len(m)]

    # 1000 deterministic unique-minute discovery probes. Prefer primary playbooks; fallback is clearly labeled diagnostic.
    chosen=[];used=set()
    for slot in range(NOBS):
        lo=S+int((E-S)*slot/NOBS);hi=S+int((E-S)*(slot+1)/NOBS)
        pool=[i for i in inds if lo<=m[i]['t']<hi]
        if not pool:continue
        best=None
        for i in pool:
            cs=candidates(i,m,f5,f15,True)
            if not cs:continue
            x=cs[0]
            if best is None or x[4]>best[0]:best=(x[4],i,x)
        if best:
            _,i,x=best;chosen.append((slot,i,x));used.add(x[3])
    if len(chosen)<NOBS:
        for i in inds:
            if len(chosen)>=NOBS:break
            cs=candidates(i,m,f5,f15,True)
            if not cs:continue
            x=cs[0]
            if x[3] in used:continue
            used.add(x[3]);chosen.append(('fill',i,x))

    obs=[]
    for slot,i,x in chosen[:NOBS]:
        setup,side,stop,ei,score,diag,tp=x
        if m[ei]['t']>=E:continue
        sim=simulate(m,ei,side,stop,tp,100.0)
        if not sim or m[sim[0]]['t']>=E:continue
        z=row(x,sim,m);z['n']=len(obs)+1;z['source_slot']=slot;obs.append(z)

    # Sequential executable sanity path uses only PRIMARY playbooks, no diagnostic fallback.
    seq=[];eq=100.;i=0
    while i<len(m)-2:
        if m[i]['t']<S:i+=1;continue
        if m[i]['t']>=E:break
        cs=candidates(i,m,f5,f15,False)
        if not cs:i+=1;continue
        x=cs[0];ei=x[3]
        if m[ei]['t']>=E:break
        sim=simulate(m,ei,x[1],x[2],x[6],eq)
        if not sim or m[sim[0]]['t']>=E:break
        z=row(x,sim,m,eq);eq=z['equity_after'];z['n']=len(seq)+1;seq.append(z);i=sim[0]+1

    def group(key,xs):
        d={}
        for x in xs:d.setdefault(x[key],[]).append(x)
        return {str(k):stat(vv) for k,vv in d.items()}
    fail={'NO_FOLLOW':[],'WEAK_PROGRESS':[],'FEE_INSUFFICIENT':[],'NEAR_TARGET_GIVEBACK':[],'OTHER':[]}
    for x in obs:
        if x['net_pnl']>0:continue
        if x['mfe_pct']<.05:fail['NO_FOLLOW'].append(x)
        elif x['mfe_pct']<.12:fail['WEAK_PROGRESS'].append(x)
        elif x['mfe_pct']<.33:fail['FEE_INSUFFICIENT'].append(x)
        elif x['mfe_pct']>=.30:fail['NEAR_TARGET_GIVEBACK'].append(x)
        else:fail['OTHER'].append(x)

    # useful context groups
    for x in obs:
        d=x['diagnostics'];x['state']=d.get('state');x['hour4']=f"{(d.get('hour',0)//4)*4:02d}-{(d.get('hour',0)//4)*4+3:02d}"
        vol=d.get('volr',0);x['vol_bucket']='<.5' if vol<.5 else '.5-1' if vol<1 else '1-2' if vol<2 else '2-4' if vol<4 else '>=4'
        vd=abs(d.get('vwap_dist',0));x['vwap_bucket']='<.05' if vd<.05 else '.05-.12' if vd<.12 else '.12-.25' if vd<.25 else '>=.25'
    out={'version':'DARA-Discovery1000-Sep5-Learned','period_tehran':[START.isoformat(),END.isoformat()],'purpose':'1000 unique-minute discovery futures probes on Sep5 after applying Sep1-4 and first Sep5-pass lessons. Primary-playbook sequential path excludes diagnostic fallback.','rules':{'risk_per_probe':RISK,'modeled_roundtrip_fee':COST,'max_exposure':MAX_LEV,'min_stop_pct':MIN_STOP*100,'profit_floor_pct':.33,'timeframes':'1m/5m/15m only','quarantined':['raw SWEEP_RECLAIM','raw PULLBACK_RELOAD','old FLOW_EFF','raw COMPRESSION_RELEASE without acceptance']},'overall_1000':stat(obs),'observations':len(obs),'primary_vs_fallback':group('setup',obs),'by_side':group('side',obs),'by_state':group('state',obs),'by_4h':group('hour4',obs),'by_volume':group('vol_bucket',obs),'by_vwap':group('vwap_bucket',obs),'failure_taxonomy':{k:stat(vv) for k,vv in fail.items()},'sequential_primary':{'stats':stat(seq),'final_equity':round(eq,6),'net':round(eq-100,6),'trades':len(seq)},'lessons_applied':['Raw compression breakout removed after first Sep5 pass showed 0/5 and almost zero MFE.','Accepted break requires a prior break plus current hold outside the old range.','Retest renewal requires pause/retrace then renewed control.','Range rejection is only allowed in 15m balance/range context.','Short logic remains asymmetric and stricter than long logic.','Early failure exits, minimum 0.16% stop, and 3x-fee profit protection retained.','STATE_HYPOTHESIS is diagnostic-only and excluded from sequential production sanity path.'],'trades':obs,'sequential_trades':seq}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'overall':out['overall_1000'],'sequential':out['sequential_primary'],'by_setup':out['primary_vs_fallback'],'fail':out['failure_taxonomy']},indent=2))
if __name__=='__main__':main()
