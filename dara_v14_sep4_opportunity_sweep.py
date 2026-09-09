import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_discovery500_sep3 as s
import dara_v11_liquidity_transition_sep1_8 as r

TEHRAN=r.TEHRAN
START=datetime(2026,9,4,0,0,tzinfo=TEHRAN); END=datetime(2026,9,5,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000); E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v14_sep4_opportunity_sweep.json')
COST=0.0011; RISK=0.0002; MAX_LEV=3.0; MIN_STOP=0.0016; MAX_STOP=0.0032

# Lessons locked BEFORE seeing Sep4:
# - raw sweep/reclaim and raw pullback remain quarantined
# - old FLOW_EFF rejected after Sep3
# - micro-break is primary surviving family
# - direction is asymmetric: shorts require stronger 5m+15m agreement
# - avoid entries hugging VWAP unless a true expansion break exists
# - 1x-2x relative volume remains a danger zone
# - early no-follow / weak-progress exits and 3x-fee floor remain active

def ctx(z,f5,f15):
    return f5.get(r.b.last_closed(z['t'],5)), f15.get(r.b.last_closed(z['t'],15))

def mkstop(entry,raw,side):
    sp=(entry-raw)/entry if side=='LONG' else (raw-entry)/entry
    sp=max(MIN_STOP,min(MAX_STOP,sp))
    return entry*(1-sp) if side=='LONG' else entry*(1+sp)

def tp_for(a5,a15):
    cap=min(0.0044,1.8*a15['atrp'],3.0*a5['atrp'])
    return 0.0044 if cap>=0.0044 else 0.0033

def make(setup,side,i,m,f5,f15,score,diag,rawstop=None):
    if i+1>=len(m): return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    ei=i+1;entry=m[ei]['o']
    if rawstop is None:
        look=m[max(0,i-5):i+1]
        rawstop=min(x['l'] for x in look)*0.9997 if side=='LONG' else max(x['h'] for x in look)*1.0003
    stop=mkstop(entry,rawstop,side)
    vd=(z['c']/z['vwap15']-1)*100
    hour=datetime.fromtimestamp(z['t']/1000,timezone.utc).astimezone(TEHRAN).hour
    return (setup,side,stop,ei,score,{'delta':z['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':vd,'hour':hour,**diag},tp_for(a5,a15))

def micro_break(i,m,f5,f15):
    if i<8:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    pre=m[i-8:i];hi=max(x['h'] for x in pre);lo=min(x['l'] for x in pre);vol=z['volr'];vd=abs(z['c']/z['vwap15']-1)
    # Sep2/3: 1-2x volume bucket repeatedly weak; true break must avoid VWAP chop.
    if 1.0<=vol<2.0:return None
    if z['c']>hi and z['delta']>.08 and a15['regime']=='UP' and vd>=.0005:
        return make('MICRO_BREAK','LONG',i,m,f5,f15,10+int(a5['regime']=='UP')+int(vol>=2),{'range8_pct':(hi-lo)/z['c']*100},lo)
    # Shorts need stronger agreement after Sep3 4% short WR.
    if z['c']<lo and z['delta']<-.12 and a15['regime']=='DOWN' and a5['regime']=='DOWN' and vd>=.0008:
        return make('MICRO_BREAK','SHORT',i,m,f5,f15,11+int(vol>=2),{'range8_pct':(hi-lo)/z['c']*100},hi)
    return None

def compression_release(i,m,f5,f15):
    if i<12:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    pre=m[i-10:i];hi=max(x['h'] for x in pre);lo=min(x['l'] for x in pre);rng=(hi-lo)/z['c'];vol=z['volr'];vd=abs(z['c']/z['vwap15']-1)
    if rng>.0022 or 1.0<=vol<2.0:return None
    if z['c']>hi and z['delta']>.15 and a15['regime']!='DOWN' and vd>=.0005:
        return make('COMPRESSION_RELEASE','LONG',i,m,f5,f15,9+int(a15['regime']=='UP')+int(vol>=2),{'range10_pct':rng*100},lo)
    if z['c']<lo and z['delta']<-.18 and a15['regime']=='DOWN' and a5['regime']=='DOWN' and vd>=.0008:
        return make('COMPRESSION_RELEASE','SHORT',i,m,f5,f15,10+int(vol>=2),{'range10_pct':rng*100},hi)
    return None

def impulse_retest(i,m,f5,f15):
    # Replaces the failed raw pullback: requires a fresh impulse, shallow retrace, and renewed control.
    if i<6:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    imp=m[i-4]; r4=z['c']/m[i-4]['c']-1; vol=z['volr'];vd=abs(z['c']/z['vwap15']-1)
    if 1.0<=vol<2.0 or vd<.0005:return None
    if a15['regime']=='UP' and a5['regime']=='UP' and r4>.0004 and z['delta']>.10 and z['c']>z['o']:
        # require prior minute to be a pause/retrace, not chase
        p=m[i-1]
        if p['c']<=p['o'] and p['delta']<.08:
            return make('IMPULSE_RETEST','LONG',i,m,f5,f15,9+int(vol<1),{'r4_pct':r4*100,'pause_delta':p['delta']})
    if a15['regime']=='DOWN' and a5['regime']=='DOWN' and r4<-.0004 and z['delta']<-.14 and z['c']<z['o']:
        p=m[i-1]
        if p['c']>=p['o'] and p['delta']>-.08:
            return make('IMPULSE_RETEST','SHORT',i,m,f5,f15,10+int(vol<1),{'r4_pct':r4*100,'pause_delta':p['delta']})
    return None

def value_escape(i,m,f5,f15):
    # New: exploit the Sep3 observation that trades farther from VWAP behaved better.
    if i<5:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    vd=(z['c']/z['vwap15']-1);d3=sum(x['delta'] for x in m[i-2:i+1])/3
    if abs(vd)<.0020:return None
    if vd>.0020 and a15['regime']=='UP' and d3>.10 and z['delta']>.05:
        return make('VALUE_ESCAPE','LONG',i,m,f5,f15,8+int(abs(vd)>=.0025),{'delta3':d3,'stretch_pct':vd*100})
    if vd<-.0020 and a15['regime']=='DOWN' and a5['regime']=='DOWN' and d3<-.15 and z['delta']<-.08:
        return make('VALUE_ESCAPE','SHORT',i,m,f5,f15,9+int(abs(vd)>=.0025),{'delta3':d3,'stretch_pct':vd*100})
    return None

def candidates(i,m,f5,f15):
    out=[]
    for fn in (micro_break,compression_release,impulse_retest,value_escape):
        try:
            x=fn(i,m,f5,f15)
            if x: out.append(x)
        except Exception: pass
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
            floor_hit=b['l']<=floor if side=='LONG' else b['h']>=floor
            if floor_hit:
                gross=n*.0033;cost=n*COST;return j,floor,'3FEE_FLOOR',gross,cost,gross-cost,n,mfe,mae
        st=b['l']<=stop if side=='LONG' else b['h']>=stop; hit=b['h']>=target if side=='LONG' else b['l']<=target
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

def rec(setup,side,stop,ei,score,diag,tp,sim,m,eq=None):
    j,px,why,gross,cost,net,n,mfe,mae=sim;et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
    sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
    z={'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'stop_pct':round(sp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'mae_pct':round(mae*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'diagnostics':diag}
    if eq is not None:z['equity_after']=round(eq+net,6)
    return z

def main():
    raw=[];dd=datetime(2026,9,3,tzinfo=timezone.utc)
    while dd.date()<=datetime(2026,9,5,tzinfo=timezone.utc).date():raw+=r.b.get_daily(dd);dd+=timedelta(days=1)
    raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw)
    f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
    # Opportunity inventory: every learned candidate minute, one highest-ranked playbook per minute.
    opp=[]
    for i,z in enumerate(m):
        if z['t']<S or z['t']>=E or i<12 or i+1>=len(m):continue
        cs=candidates(i,m,f5,f15)
        if not cs:continue
        setup,side,stop,ei,score,diag,tp=cs[0]
        if m[ei]['t']>=E:continue
        sim=simulate(m,ei,side,stop,tp,100.0)
        if not sim or m[sim[0]]['t']>=E:continue
        opp.append(rec(setup,side,stop,ei,score,diag,tp,sim,m))
    # Sequential executable subset: only next signal after previous trade exit.
    seq=[];eq=100.;i=0
    while i<len(m)-2:
        if m[i]['t']<S:i+=1;continue
        if m[i]['t']>=E:break
        cs=candidates(i,m,f5,f15)
        if not cs:i+=1;continue
        setup,side,stop,ei,score,diag,tp=cs[0]
        if m[ei]['t']>=E:break
        sim=simulate(m,ei,side,stop,tp,eq)
        if not sim or m[sim[0]]['t']>=E:break
        row=rec(setup,side,stop,ei,score,diag,tp,sim,m,eq);eq=row['equity_after'];row['n']=len(seq)+1;seq.append(row);i=sim[0]+1
    def group(key,xs):
        vals={}
        for x in xs:vals.setdefault(x[key],[]).append(x)
        return {k:stat(v) for k,v in vals.items()}
    # Failure taxonomy from learned exits.
    fail={'NO_FOLLOW':[],'WEAK_PROGRESS':[],'FEE_INSUFFICIENT':[],'NEAR_TARGET_GIVEBACK':[]}
    for x in opp:
        if x['net_pnl']>0:continue
        if x['mfe_pct']<.05:fail['NO_FOLLOW'].append(x)
        elif x['mfe_pct']<.12:fail['WEAK_PROGRESS'].append(x)
        elif x['mfe_pct']<.33:fail['FEE_INSUFFICIENT'].append(x)
        else:fail['NEAR_TARGET_GIVEBACK'].append(x)
    out={'version':'DARA-v14-Sep4-OpportunitySweep','period_tehran':[START.isoformat(),END.isoformat()],'purpose':'Development/experience day. Applies lessons from Sep1-3, tests every qualifying opportunity on Sep4 and reports a separate non-overlapping sequential account path.','rules':{'risk_per_trade':RISK,'modeled_roundtrip_fee':COST,'max_exposure':MAX_LEV,'min_stop_pct':MIN_STOP*100,'profit_floor_pct':.33,'timeframes':'1m/5m/15m only','quarantined':['raw SWEEP_RECLAIM','raw PULLBACK_RELOAD','old FLOW_EFF']},'opportunity_inventory':stat(opp),'sequential':{'stats':stat(seq),'final_equity':round(eq,6),'return_pct':round(eq-100,3)},'by_setup':group('setup',opp),'by_side':group('side',opp),'by_exit':group('reason',opp),'failure_taxonomy':{k:stat(v) for k,v in fail.items()},'opportunities':opp,'sequential_trades':seq,'notes':['Sep4 is a development/experience test, not clean out-of-sample validation.','Historical full L2 unavailable; taker-buy delta from Binance USD-M 1m klines remains an aggressor-flow proxy.','No forced trade count: every qualifying learned opportunity is tested.']}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps({k:out[k] for k in ['opportunity_inventory','sequential','by_setup','by_side','failure_taxonomy']},indent=2))
if __name__=='__main__':main()
