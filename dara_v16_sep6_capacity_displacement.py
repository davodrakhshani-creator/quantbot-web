import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_discovery1000_sep5_learned as b
import dara_v11_liquidity_transition_sep1_8 as r

TEHRAN=b.TEHRAN
START=datetime(2026,9,6,0,0,tzinfo=TEHRAN); END=datetime(2026,9,7,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000); E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v16_sep6_capacity_displacement.json')
# Learning/production-sanity risk remains small. Friction and exposure are unchanged.
b.RISK=0.0002; b.COST=0.0011; b.MAX_LEV=3.0; b.MIN_STOP=0.0016; b.MAX_STOP=0.0032

# Frozen before looking at Sep6:
# 1) raw sweep/reclaim, pullback, flow-eff, raw compression, accepted-break and retest-renewal stay quarantined.
# 2) Capacity first: market must plausibly cover the 0.33% = 3x fee floor.
# 3) Balance/Pause states are not production states unless a true range->expansion transition completes.
# 4) Shorts are stricter: only downside expansion, never mirror-image trend labels.
# 5) Multi-stage confirmation: impulse/break -> hold -> extension/re-acceleration -> next-open entry.
# 6) Early failure exits + minimum 0.16% stop + 3x-fee protection retained.

def ctx(i,m,f5,f15):
    z=m[i]; a5,a15=b.ctx(z,f5,f15)
    if not a5 or not a15:return None
    st=b.state(i,m,f5,f15)[0]
    vd=(z['c']/z['vwap15']-1)
    return z,a5,a15,st,vd

def capacity(a5,a15):
    # Must have enough local volatility for the minimum +0.33% gross target.
    cap5=4.0*a5['atrp']; cap15=2.5*a15['atrp']; cap=min(cap5,cap15)
    return cap, cap>=0.0033

def make(setup,side,i,m,f5,f15,score,diag,rawstop=None):
    c=ctx(i,m,f5,f15)
    if not c:return None
    z,a5,a15,st,vd=c; cap,ok=capacity(a5,a15)
    if not ok:return None
    x=b.make(setup,side,i,m,f5,f15,score,{**diag,'capacity_pct':cap*100,'atr5_pct':a5['atrp']*100,'atr15_pct':a15['atrp']*100},rawstop)
    return x

def trend_displacement(i,m,f5,f15):
    if i<8:return None
    c=ctx(i,m,f5,f15)
    if not c:return None
    z,a5,a15,st,vd=c; p=m[i-1]; p2=m[i-2]
    d3=sum(x['delta'] for x in m[i-2:i+1])/3
    r3=z['c']/m[i-3]['c']-1; r6=z['c']/m[i-6]['c']-1
    vol=z['volr']; pos=(z['c']-z['l'])/max(z['h']-z['l'],1e-9)
    # Long: real displacement, not a one-bar FOMO spike; current bar confirms control.
    if st=='TREND_UP' and .0008<=r3<=.0028 and r6<=.0045 and d3>.10 and .00045<=vd<=.0035 and vol<4.0:
        confirm=(z['c']>=p['c']*.9997 and pos>=.55 and z['delta']>-.05)
        prior_progress=(p['c']/p2['c']-1)>-.0005
        if confirm and prior_progress:
            return make('TREND_DISPLACEMENT','LONG',i,m,f5,f15,12+int(d3>.25),{'r3_pct':r3*100,'r6_pct':r6*100,'delta3':d3,'close_pos':pos})
    # Short: much stricter after repeated short failures.
    if st=='TREND_DOWN' and -.0032<=r3<=-.0012 and r6>=-.0050 and d3<-.20 and -.0038<=vd<=-.0008 and 1.5<=vol<4.0:
        confirm=(z['c']<=p['c']*1.0002 and pos<=.45 and z['delta']<-.10)
        prior_progress=(p['c']/p2['c']-1)<.0004
        if confirm and prior_progress:
            return make('DOWNSIDE_DISPLACEMENT','SHORT',i,m,f5,f15,14+int(d3<-.35),{'r3_pct':r3*100,'r6_pct':r6*100,'delta3':d3,'close_pos':pos})
    return None

def reacceleration(i,m,f5,f15):
    if i<9:return None
    c=ctx(i,m,f5,f15)
    if not c:return None
    z,a5,a15,st,vd=c
    # Stronger than prior retest-renewal: an earlier impulse must exist, followed by an actual 2-bar pause,
    # then current bar must break the pause and re-accelerate.
    pre=m[i-7:i-2]; pause=m[i-2:i]
    imp_hi=max(x['h'] for x in pre); imp_lo=min(x['l'] for x in pre)
    imp_move=(imp_hi-imp_lo)/z['c']
    ph=max(x['h'] for x in pause); pl=min(x['l'] for x in pause)
    d2=(m[i-1]['delta']+z['delta'])/2; vol=z['volr']; pos=(z['c']-z['l'])/max(z['h']-z['l'],1e-9)
    if st=='TREND_UP' and imp_move>=.0015 and z['c']>ph and d2>.10 and z['delta']>.08 and .0005<=vd<=.0035 and vol<4 and pos>.58:
        pullback=(m[i-1]['c']<=m[i-2]['c'] or m[i-1]['delta']<.02)
        if pullback:
            return make('REACCELERATION','LONG',i,m,f5,f15,13+int(d2>.25),{'impulse_range_pct':imp_move*100,'delta2':d2,'pause_range_pct':(ph-pl)/z['c']*100},pl)
    if st=='TREND_DOWN' and imp_move>=.0018 and z['c']<pl and d2<-.18 and z['delta']<-.12 and -.0038<=vd<=-.0008 and 1.5<=vol<4 and pos<.42:
        pullback=(m[i-1]['c']>=m[i-2]['c'] or m[i-1]['delta']>-.02)
        if pullback:
            return make('REACCELERATION','SHORT',i,m,f5,f15,15+int(d2<-.30),{'impulse_range_pct':imp_move*100,'delta2':d2,'pause_range_pct':(ph-pl)/z['c']*100},ph)
    return None

def range_to_expansion(i,m,f5,f15):
    if i<13:return None
    c=ctx(i,m,f5,f15)
    if not c:return None
    z,a5,a15,st,vd=c; p=m[i-1]; p2=m[i-2]
    # Old range is deliberately ended before the two confirmation bars.
    old=m[i-12:i-2]; hi=max(x['h'] for x in old); lo=min(x['l'] for x in old); width=(hi-lo)/z['c']
    if width>.0024:return None
    vol=z['volr']; d2=(p['delta']+z['delta'])/2
    # Break -> hold -> extension. Current close must extend beyond previous close, not merely sit outside the range.
    if a15['regime']=='RANGE' and a5['regime']=='UP' and p2['c']>hi and p['c']>hi and z['c']>p['c'] and z['l']>hi*.9995 and d2>.10 and 1.2<=vol<4:
        ext=z['c']/p['c']-1
        if ext>=.00025 and vd>=.0005:
            return make('RANGE_EXPANSION','LONG',i,m,f5,f15,14+int(d2>.25),{'range_pct':width*100,'extension_pct':ext*100,'delta2':d2,'edge':hi},lo)
    if a15['regime']=='RANGE' and a5['regime']=='DOWN' and p2['c']<lo and p['c']<lo and z['c']<p['c'] and z['h']<lo*1.0005 and d2<-.18 and 1.5<=vol<4:
        ext=p['c']/z['c']-1
        if ext>=.00030 and vd<=-.0007:
            return make('RANGE_EXPANSION','SHORT',i,m,f5,f15,16+int(d2<-.30),{'range_pct':width*100,'extension_pct':ext*100,'delta2':d2,'edge':lo},hi)
    return None

def candidates(i,m,f5,f15):
    out=[]
    for fn in (range_to_expansion,reacceleration,trend_displacement):
        try:
            x=fn(i,m,f5,f15)
            if x:out.append(x)
        except Exception:pass
    # de-dup same entry/side; favor higher score
    uniq={}
    for x in out:
        k=(x[1],x[3]);
        if k not in uniq or x[4]>uniq[k][4]:uniq[k]=x
    return sorted(uniq.values(),key=lambda x:x[4],reverse=True)

def stat(xs):
    if not xs:return {'n':0,'valid_positive':0,'negative_or_invalid':0,'valid_wr':None,'net_positive':0,'gross':0,'cost':0,'net':0,'pf_net':None,'mfe':None,'mae':None}
    valid=[x for x in xs if x['reason'] in ('TP','3FEE_FLOOR')]
    netpos=[x for x in xs if x['net_pnl']>0]
    gp=sum(x['net_pnl'] for x in netpos); gl=-sum(x['net_pnl'] for x in xs if x['net_pnl']<=0)
    return {'n':len(xs),'valid_positive':len(valid),'negative_or_invalid':len(xs)-len(valid),'valid_wr':round(100*len(valid)/len(xs),1),'net_positive':len(netpos),'gross':round(sum(x['gross_pnl'] for x in xs),6),'cost':round(sum(x['cost'] for x in xs),6),'net':round(sum(x['net_pnl'] for x in xs),6),'pf_net':round(gp/gl,2) if gl else (99.0 if gp else None),'mfe':round(sum(x['mfe_pct'] for x in xs)/len(xs),3),'mae':round(sum(x['mae_pct'] for x in xs)/len(xs),3)}

def row(x,sim,m,equity=None):
    z=b.row(x,sim,m,equity)
    return z

def main():
    raw=[];dd=datetime(2026,9,5,tzinfo=timezone.utc)
    while dd.date()<=datetime(2026,9,7,tzinfo=timezone.utc).date():raw+=r.b.get_daily(dd);dd+=timedelta(days=1)
    raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw)
    f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
    # Every qualifying primary opportunity, highest-ranked per minute.
    opp=[]
    for i,z in enumerate(m):
        if z['t']<S or z['t']>=E or i<15 or i+1>=len(m):continue
        cs=candidates(i,m,f5,f15)
        if not cs:continue
        x=cs[0];sim=b.simulate(m,x[3],x[1],x[2],x[6],100.0)
        if not sim or m[sim[0]]['t']>=E:continue
        rr=row(x,sim,m);rr['source_i']=i;opp.append(rr)
    # Sequential account path, no overlap.
    seq=[];eq=100.;i=0
    while i<len(m)-2:
        if m[i]['t']<S:i+=1;continue
        if m[i]['t']>=E:break
        cs=candidates(i,m,f5,f15)
        if not cs:i+=1;continue
        x=cs[0];sim=b.simulate(m,x[3],x[1],x[2],x[6],eq)
        if not sim or m[sim[0]]['t']>=E:break
        rr=row(x,sim,m,eq);eq=rr['equity_after'];rr['n']=len(seq)+1;seq.append(rr);i=sim[0]+1
    def group(key,xs):
        g={}
        for x in xs:g.setdefault(x[key],[]).append(x)
        return {str(k):stat(v) for k,v in g.items()}
    for x in opp:
        d=x['diagnostics'];x['state']=d.get('state');x['capacity_bucket']='.33-.44' if d.get('capacity_pct',0)<.44 else '>=.44';x['hour4']=f"{(d.get('hour',0)//4)*4:02d}-{(d.get('hour',0)//4)*4+3:02d}"
    fail={'NO_FOLLOW':[],'WEAK_PROGRESS':[],'FEE_INSUFFICIENT':[],'VALID_POSITIVE':[],'OTHER':[]}
    for x in opp:
        if x['reason'] in ('TP','3FEE_FLOOR'):fail['VALID_POSITIVE'].append(x)
        elif x['mfe_pct']<.05:fail['NO_FOLLOW'].append(x)
        elif x['mfe_pct']<.12:fail['WEAK_PROGRESS'].append(x)
        elif x['mfe_pct']<.33:fail['FEE_INSUFFICIENT'].append(x)
        else:fail['OTHER'].append(x)
    out={'version':'DARA-v16-Sep6-CapacityDisplacement-Frozen','period_tehran':[START.isoformat(),END.isoformat()],'starting_equity':100.0,'purpose':'Sep6 transfer test after Sep1-5 learning; every capacity-qualified primary opportunity is tested, plus a separate non-overlapping sequential account path.','rules':{'risk_per_trade':b.RISK,'modeled_roundtrip_fee':b.COST,'max_exposure':b.MAX_LEV,'min_stop_pct':b.MIN_STOP*100,'profit_floor_pct':.33,'timeframes':'1m/5m/15m only','capacity_gate':'min(4x ATR5, 2.5x ATR15) >= 0.33%','quarantined':['raw SWEEP_RECLAIM','raw PULLBACK_RELOAD','old FLOW_EFF','raw COMPRESSION_RELEASE','ACCEPTED_BREAK v15','RETEST_RENEWAL v15','diagnostic STATE_HYPOTHESIS']},'opportunity_inventory':stat(opp),'sequential':{'stats':stat(seq),'final_equity':round(eq,6),'net':round(eq-100,6),'trades':len(seq)},'by_setup':group('setup',opp),'by_side':group('side',opp),'by_state':group('state',opp),'by_capacity':group('capacity_bucket',opp),'by_4h':group('hour4',opp),'failure_taxonomy':{k:stat(v) for k,v in fail.items()},'lessons_applied':['Capacity gate precedes every entry.','Pause and balance labels alone cannot create production trades.','Trend entries require multi-bar displacement confirmation rather than trend label + flow.','Range expansion requires break, hold and additional extension before entry.','Shorts require materially stronger downside displacement than longs.','Old Sep5 accepted-break and retest-renewal engines remain quarantined.','Early no-follow/weak-progress exits, >=0.16% stop and 3x-fee protection remain active.'],'opportunities':opp,'sequential_trades':seq}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'opp':out['opportunity_inventory'],'seq':out['sequential'],'setups':out['by_setup'],'failure':out['failure_taxonomy']},indent=2))
if __name__=='__main__':main()
