import json, math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_v11_liquidity_transition_sep1_8 as r

TEHRAN=r.TEHRAN
START=datetime(2026,9,2,0,0,tzinfo=TEHRAN); END=datetime(2026,9,3,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000); E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_discovery100_sep2.json')
COST=0.0011; RISK=0.0005; MAX_LEV=3.0; MAX_TRADES=100
MIN_STOP=0.0010; MAX_STOP=0.0032
BASE_HOLD=7; EXTEND_HOLD=25


def ctx(z,f5,f15):
    return f5.get(r.b.last_closed(z['t'],5)), f15.get(r.b.last_closed(z['t'],15))

def tp_for(a5,a15):
    cap=min(0.0044,1.8*a15['atrp'],3.0*a5['atrp'])
    return 0.0044 if cap>=0.0044 else 0.0033

def mkstop(entry,raw,side):
    sp=(entry-raw)/entry if side=='LONG' else (raw-entry)/entry
    sp=max(MIN_STOP,min(MAX_STOP,sp))
    return entry*(1-sp) if side=='LONG' else entry*(1+sp)

def signed_ret(z0,z1): return z1['c']/z0['c']-1

def make(setup,side,i,m,f5,f15,score,diag,rawstop=None):
    if i+1>=len(m): return None
    z=m[i]; a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    ei=i+1; entry=m[ei]['o']; tp=tp_for(a5,a15)
    if rawstop is None:
        look=m[max(0,i-3):i+1]
        rawstop=min(q['l'] for q in look)*0.9997 if side=='LONG' else max(q['h'] for q in look)*1.0003
    stop=mkstop(entry,rawstop,side)
    vd=(z['c']/z['vwap15']-1)*100
    d={'delta':z['delta'],'volr':z['volr'],'reg5':a5['regime'],'reg15':a15['regime'],'vwap_dist':vd,**diag}
    return (setup,side,stop,ei,score,d,tp)

def sweep_reclaim(i,m,f5,f15):
    if i<4:return None
    z=m[i]; pos=(z['c']-z['l'])/max(z['h']-z['l'],1e-9)
    if z['l']<z['prev15l'] and z['c']>z['prev15l'] and pos>.42:
        return make('SWEEP_RECLAIM','LONG',i,m,f5,f15,9,{'event':'low_sweep'},z['l']*0.9995)
    if z['h']>z['prev15h'] and z['c']<z['prev15h'] and pos<.58:
        return make('SWEEP_RECLAIM','SHORT',i,m,f5,f15,9,{'event':'high_sweep'},z['h']*1.0005)
    return None

def pullback_reload(i,m,f5,f15):
    if i<3:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    if a5['regime']=='UP' and a15['regime']=='UP' and z['delta']<-.10 and z['c']<=z['o']:
        if i+1<len(m) and m[i+1]['delta']>-.05 and m[i+1]['c']>=z['c']:
            return make('PULLBACK_RELOAD','LONG',i+1,m,f5,f15,8,{'attack_delta':z['delta'],'flip_delta':m[i+1]['delta']})
    if a5['regime']=='DOWN' and a15['regime']=='DOWN' and z['delta']>.10 and z['c']>=z['o']:
        if i+1<len(m) and m[i+1]['delta']<.05 and m[i+1]['c']<=z['c']:
            return make('PULLBACK_RELOAD','SHORT',i+1,m,f5,f15,8,{'attack_delta':z['delta'],'flip_delta':m[i+1]['delta']})
    return None

def flow_pulse(i,m,f5,f15):
    if i<2:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    d2=(m[i-1]['delta']+z['delta'])/2
    r2=z['c']/m[i-2]['c']-1
    if d2>.12 and r2>0 and a15['regime']!='DOWN':
        return make('FLOW_PULSE','LONG',i,m,f5,f15,6+int(d2>.3)+int(a5['regime']=='UP'),{'delta2':d2,'ret2_pct':r2*100})
    if d2<-.12 and r2<0 and a15['regime']!='UP':
        return make('FLOW_PULSE','SHORT',i,m,f5,f15,6+int(d2<-.3)+int(a5['regime']=='DOWN'),{'delta2':d2,'ret2_pct':r2*100})
    return None

def micro_break(i,m,f5,f15):
    if i<6:return None
    z=m[i];pre=m[i-6:i];hi=max(q['h'] for q in pre);lo=min(q['l'] for q in pre);a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    if z['c']>hi and z['delta']>.05 and a15['regime']!='DOWN':
        return make('MICRO_BREAK','LONG',i,m,f5,f15,7+int(z['volr']>.8),{'range6_pct':(hi-lo)/z['c']*100},lo)
    if z['c']<lo and z['delta']<-.05 and a15['regime']!='UP':
        return make('MICRO_BREAK','SHORT',i,m,f5,f15,7+int(z['volr']>.8),{'range6_pct':(hi-lo)/z['c']*100},hi)
    return None

def value_fade(i,m,f5,f15):
    if i<3:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    vd=(z['c']/z['vwap15']-1)
    if a15['regime']=='RANGE' and vd>.0012 and z['delta']>.18 and z['c']<z['h']-(z['h']-z['l'])*.2:
        return make('VALUE_FADE','SHORT',i,m,f5,f15,7,{'stretch_pct':vd*100})
    if a15['regime']=='RANGE' and vd<-.0012 and z['delta']<-.18 and z['c']>z['l']+(z['h']-z['l'])*.2:
        return make('VALUE_FADE','LONG',i,m,f5,f15,7,{'stretch_pct':vd*100})
    return None

def momentum_retest(i,m,f5,f15):
    if i<5:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    r3=z['c']/m[i-3]['c']-1
    vd=(z['c']/z['vwap15']-1)
    if r3>.0010 and a15['regime']=='UP' and z['delta']>.05 and vd<.0025:
        return make('MOMENTUM_CONT','LONG',i,m,f5,f15,6+int(a5['regime']=='UP'),{'ret3_pct':r3*100})
    if r3<-.0010 and a15['regime']=='DOWN' and z['delta']<-.05 and vd>-.0025:
        return make('MOMENTUM_CONT','SHORT',i,m,f5,f15,6+int(a5['regime']=='DOWN'),{'ret3_pct':r3*100})
    return None

def exploratory_state(i,m,f5,f15):
    # Last-resort discovery playbook: broad directional hypothesis, still based on 15m regime + 3m flow/price state.
    if i<4:return None
    z=m[i];a5,a15=ctx(z,f5,f15)
    if not a5 or not a15:return None
    d3=sum(q['delta'] for q in m[i-2:i+1])/3
    r3=z['c']/m[i-3]['c']-1
    if a15['regime']=='UP':
        side='LONG' if d3>=-.05 else ('SHORT' if r3<-.0012 else 'LONG')
    elif a15['regime']=='DOWN':
        side='SHORT' if d3<=.05 else ('LONG' if r3>.0012 else 'SHORT')
    else:
        side='LONG' if d3+r3*120>=0 else 'SHORT'
    return make('STATE_PROBE',side,i,m,f5,f15,4,{'delta3':d3,'ret3_pct':r3*100})

def candidates(i,m,f5,f15,allow_probe=False):
    out=[]
    for fn in (sweep_reclaim,pullback_reload,micro_break,value_fade,momentum_retest,flow_pulse):
        try:
            x=fn(i,m,f5,f15)
            if x:out.append(x)
        except Exception:pass
    if not out and allow_probe:
        x=exploratory_state(i,m,f5,f15)
        if x:out.append(x)
    return sorted(out,key=lambda x:x[4],reverse=True)

def simulate(m,ei,side,stop,equity,tp):
    entry=m[ei]['o']; sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<=0:return None
    n=min(MAX_LEV*equity,equity*RISK/(sp+COST)); target=entry*(1+tp if side=='LONG' else 1-tp)
    mfe=mae=0.; base_end=min(len(m)-1,ei+BASE_HOLD); final_end=min(len(m)-1,ei+EXTEND_HOLD)
    for j in range(ei,final_end+1):
        b=m[j]; fav=(b['h']/entry-1) if side=='LONG' else (entry/b['l']-1); adv=(entry/b['l']-1) if side=='LONG' else (b['h']/entry-1)
        mfe=max(mfe,fav);mae=max(mae,adv)
        st=b['l']<=stop if side=='LONG' else b['h']>=stop; hit=b['h']>=target if side=='LONG' else b['l']<=target
        if st:
            ret=stop/entry-1 if side=='LONG' else entry/stop-1; gross=n*ret;cost=n*COST;return j,stop,'STOP',gross,cost,gross-cost,n,mfe,mae
        if hit:
            gross=n*tp;cost=n*COST;return j,target,'TP',gross,cost,gross-cost,n,mfe,mae
        if j>=base_end:
            px=b['c']; ret=px/entry-1 if side=='LONG' else entry/px-1
            # Do not intentionally take a small positive exit below 3x fee. Thesis-fail can close when non-positive.
            if ret<=0:
                gross=n*ret;cost=n*COST;return j,px,'THESIS_FAIL',gross,cost,gross-cost,n,mfe,mae
    # Hard diagnostic timeout: if still positive but below TP, mark unresolved and close for accounting only.
    px=m[final_end]['c'];ret=px/entry-1 if side=='LONG' else entry/px-1;gross=n*ret;cost=n*COST
    return final_end,px,'DIAG_TIMEOUT',gross,cost,gross-cost,n,mfe,mae

def main():
    raw=[];d=datetime(2026,9,1,tzinfo=timezone.utc)
    while d.date()<=datetime(2026,9,3,tzinfo=timezone.utc).date():raw+=r.b.get_daily(d);d+=timedelta(days=1)
    raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw)
    f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
    eq=100.;peak=100.;dd=0.;ts=[];counts={};i=0
    while i<len(m)-3 and len(ts)<MAX_TRADES:
        if m[i]['t']<S:i+=1;continue
        if m[i]['t']>=E:break
        elapsed=max(0,(m[i]['t']-S)/60000); expected=elapsed/(1440/MAX_TRADES)
        allow_probe=len(ts)+3<expected
        cs=candidates(i,m,f5,f15,allow_probe)
        for c in cs:counts[c[0]]=counts.get(c[0],0)+1
        if not cs:i+=1;continue
        setup,side,stop,ei,score,diag,tp=cs[0]
        if ei>=len(m) or m[ei]['t']>=E:break
        sim=simulate(m,ei,side,stop,eq,tp)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n,mfe,mae=sim
        if m[j]['t']>=E:break
        before=eq;eq+=net;peak=max(peak,eq);dd=max(dd,(peak-eq)/peak)
        et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
        ts.append({'n':len(ts)+1,'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'stop_pct':round(sp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'mae_pct':round(mae*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(eq,6),'diagnostics':diag})
        i=j+1
    # If under target, fill remaining observations using independent low-confidence probes spaced through unused late minutes. Mark them as diagnostic-only.
    if len(ts)<MAX_TRADES:
        used={x['entry_time'] for x in ts}
        for k,z in enumerate(m):
            if len(ts)>=MAX_TRADES:break
            if z['t']<S or z['t']>=E or k<5 or k+1>=len(m):continue
            et=datetime.fromtimestamp(m[k+1]['t']/1000,timezone.utc).astimezone(TEHRAN).isoformat()
            if et in used:continue
            x=exploratory_state(k,m,f5,f15)
            if not x:continue
            setup,side,stop,ei,score,diag,tp=x
            sim=simulate(m,ei,side,stop,100.0,tp)
            if not sim:continue
            j,px,why,gross,cost,net,n,mfe,mae=sim
            if m[j]['t']>=E:continue
            xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
            sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
            ts.append({'n':len(ts)+1,'setup':'STATE_PROBE_INDEPENDENT','side':side,'entry_time':et,'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'stop_pct':round(sp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'mae_pct':round(mae*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':100.0,'equity_after':round(100+net,6),'diagnostics':{**diag,'independent_probe':True}});used.add(et)
    seq=[x for x in ts if not x['diagnostics'].get('independent_probe')]
    w=[x for x in ts if x['net_pnl']>0];l=[x for x in ts if x['net_pnl']<=0]
    by={}
    for name in sorted(set(x['setup'] for x in ts)):
        q=[x for x in ts if x['setup']==name];by[name]={'trades':len(q),'wins':sum(x['net_pnl']>0 for x in q),'wr':round(100*sum(x['net_pnl']>0 for x in q)/len(q),1),'gross':round(sum(x['gross_pnl'] for x in q),6),'cost':round(sum(x['cost'] for x in q),6),'net':round(sum(x['net_pnl'] for x in q),6),'avg_mfe':round(sum(x['mfe_pct'] for x in q)/len(q),3),'avg_mae':round(sum(x['mae_pct'] for x in q)/len(q),3)}
    out={'version':'DARA-Discovery100-Sep2','period_tehran':[START.isoformat(),END.isoformat()],'purpose':'100-trade high-frequency discovery experiment; broad playbooks and low risk to learn failure modes, not deployable performance.','rules':{'risk_per_sequential_trade':RISK,'modeled_roundtrip_fee':COST,'max_exposure':MAX_LEV,'profit_target':'0.33% or 0.44% gross; normal positive exit below 3x fee is avoided','timeframes':'1m/5m/15m only'},'overall':{'observations':len(ts),'sequential_trades':len(seq),'independent_probe_observations':len(ts)-len(seq),'sequential_final_equity':round(eq,6),'sequential_net':round(eq-100,6),'all_observation_wins':len(w),'all_observation_losses':len(l),'all_observation_wr':round(100*len(w)/len(ts),1) if ts else None,'all_observation_gross':round(sum(x['gross_pnl'] for x in ts),6),'all_observation_cost':round(sum(x['cost'] for x in ts),6),'max_dd_sequential_pct':round(dd*100,3),'candidate_counts':counts},'by_setup':by,'trades':ts,'notes':['Sequential trades use one evolving $100 account. If sequential timing cannot naturally reach 100, remaining items are explicitly marked independent diagnostic probes and are NOT included in sequential final equity.','Historical full L2 unavailable; Binance 1m taker-buy delta is an aggressor-flow proxy.','This is discovery, not validation.']}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out['overall'],indent=2));print(json.dumps(by,indent=2))
if __name__=='__main__':main()
