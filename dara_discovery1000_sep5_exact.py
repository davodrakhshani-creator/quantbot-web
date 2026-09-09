import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_discovery1000_sep5_learned as d
import dara_v11_liquidity_transition_sep1_8 as r

OUT=Path('data/dara_discovery1000_sep5_exact.json')

def main():
    raw=[];dd=datetime(2026,9,4,tzinfo=timezone.utc)
    while dd.date()<=datetime(2026,9,6,tzinfo=timezone.utc).date():
        raw+=r.b.get_daily(dd);dd+=timedelta(days=1)
    raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw)
    f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)}
    f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
    valid=[]
    for i,z in enumerate(m):
        if z['t']<d.S or z['t']>=d.E or i<12 or i+1>=len(m):continue
        cs=d.candidates(i,m,f5,f15,True)
        if not cs:continue
        x=cs[0];ei=x[3]
        if m[ei]['t']>=d.E:continue
        sim=d.simulate(m,ei,x[1],x[2],x[6],100.0)
        if not sim or m[sim[0]]['t']>=d.E:continue
        valid.append((i,x,sim))
    # Deterministically span the full valid intraday population with exactly 1000 distinct probes.
    if len(valid)<1000:raise RuntimeError(f'Only {len(valid)} valid probes available')
    idxs=[]
    for k in range(1000):
        j=round(k*(len(valid)-1)/999)
        if j not in idxs:idxs.append(j)
    # rounding can theoretically duplicate; fill any gap from unused valid indices
    if len(idxs)<1000:
        used=set(idxs)
        for j in range(len(valid)):
            if j not in used:
                idxs.append(j);used.add(j)
                if len(idxs)==1000:break
    idxs=sorted(idxs[:1000])
    obs=[]
    for n,j in enumerate(idxs,1):
        i,x,sim=valid[j];z=d.row(x,sim,m);z['n']=n;z['source_index']=j;obs.append(z)
    # sequential path: primary playbooks only, no diagnostic fallback
    seq=[];eq=100.;i=0
    while i<len(m)-2:
        if m[i]['t']<d.S:i+=1;continue
        if m[i]['t']>=d.E:break
        cs=d.candidates(i,m,f5,f15,False)
        if not cs:i+=1;continue
        x=cs[0];sim=d.simulate(m,x[3],x[1],x[2],x[6],eq)
        if not sim or m[sim[0]]['t']>=d.E:break
        z=d.row(x,sim,m,eq);eq=z['equity_after'];z['n']=len(seq)+1;seq.append(z);i=sim[0]+1
    def group(key,xs):
        g={}
        for x in xs:g.setdefault(x[key],[]).append(x)
        return {str(k):d.stat(v) for k,v in g.items()}
    fail={'NO_FOLLOW':[],'WEAK_PROGRESS':[],'FEE_INSUFFICIENT':[],'NEAR_TARGET_GIVEBACK':[],'OTHER':[]}
    for x in obs:
        if x['net_pnl']>0:continue
        if x['mfe_pct']<.05:fail['NO_FOLLOW'].append(x)
        elif x['mfe_pct']<.12:fail['WEAK_PROGRESS'].append(x)
        elif x['mfe_pct']<.33:fail['FEE_INSUFFICIENT'].append(x)
        elif x['mfe_pct']>=.30:fail['NEAR_TARGET_GIVEBACK'].append(x)
        else:fail['OTHER'].append(x)
    for x in obs:
        di=x['diagnostics'];x['state']=di.get('state');h=di.get('hour',0);x['hour4']=f'{(h//4)*4:02d}-{(h//4)*4+3:02d}'
        vol=di.get('volr',0);x['vol_bucket']='<.5' if vol<.5 else '.5-1' if vol<1 else '1-2' if vol<2 else '2-4' if vol<4 else '>=4'
        vd=abs(di.get('vwap_dist',0));x['vwap_bucket']='<.05' if vd<.05 else '.05-.12' if vd<.12 else '.12-.25' if vd<.25 else '>=.25'
    out={'version':'DARA-Discovery1000-Sep5-Exact','period_tehran':[d.START.isoformat(),d.END.isoformat()],'purpose':'Exactly 1000 unique-minute discovery futures probes spanning valid Sep5 intraday entries, using the same frozen learned rules. Sequential path excludes diagnostic fallback.','rules':{'risk_per_probe':d.RISK,'modeled_roundtrip_fee':d.COST,'max_exposure':d.MAX_LEV,'min_stop_pct':d.MIN_STOP*100,'profit_floor_pct':.33,'timeframes':'1m/5m/15m only','quarantined':['raw SWEEP_RECLAIM','raw PULLBACK_RELOAD','old FLOW_EFF','raw COMPRESSION_RELEASE without acceptance']},'overall_1000':d.stat(obs),'observations':len(obs),'by_setup':group('setup',obs),'by_side':group('side',obs),'by_state':group('state',obs),'by_4h':group('hour4',obs),'by_volume':group('vol_bucket',obs),'by_vwap':group('vwap_bucket',obs),'failure_taxonomy':{k:d.stat(v) for k,v in fail.items()},'sequential_primary':{'stats':d.stat(seq),'final_equity':round(eq,6),'net':round(eq-100,6),'trades':len(seq)},'notes':['Exactly 1000 valid intraday probes; no exits spill beyond Sep5 Tehran day.','STATE_HYPOTHESIS is diagnostic-only and excluded from sequential primary path.','Rules are identical to the prior learned run; only deterministic sampling was corrected from 995 to exactly 1000 valid probes.'],'trades':obs,'sequential_trades':seq}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'overall':out['overall_1000'],'by_setup':out['by_setup'],'failure':out['failure_taxonomy'],'seq':out['sequential_primary']},indent=2))
if __name__=='__main__':main()
