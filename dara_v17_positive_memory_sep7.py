import json, math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_discovery1000_sep5_learned as b
import dara_v11_liquidity_transition_sep1_8 as r

TEHRAN=b.TEHRAN
START=datetime(2026,9,7,0,0,tzinfo=TEHRAN); END=datetime(2026,9,8,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000); E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v17_positive_memory_sep7.json')
COST=.0011; RISK=.0002; MAX_LEV=3.0; MIN_STOP=.0016; MAX_STOP=.0032
b.COST=COST; b.RISK=RISK; b.MAX_LEV=MAX_LEV; b.MIN_STOP=MIN_STOP; b.MAX_STOP=MAX_STOP

# Training memory is explicitly limited to Sep1-Sep6 artifacts. No Sep7 artifact is read.
TRAIN=[
 ('2026-09-01','data/dara_discovery30_sep1.json'),
 ('2026-09-02','data/dara_discovery100_sep2.json'),
 ('2026-09-03','data/dara_discovery500_sep3.json'),
 ('2026-09-04','data/dara_v14_sep4_opportunity_sweep.json'),
 ('2026-09-05','data/dara_discovery1000_sep5_exact.json'),
 ('2026-09-06','data/dara_v16_1_sep6_capacity_score_discovery.json'),
]
TEMPLATES=[
 ('side','state'),('side','hour4'),('side','state','hour4'),
 ('side','state','vol_bucket'),('side','state','vwap_bucket'),
 ('side','hour4','vol_bucket'),('side','state','delta_bucket'),
 ('side','reg15','vol_bucket'),
]

def trade_rows(p):
    q=json.loads(Path(p).read_text())
    for k in ('trades','opportunities','sequential_trades'):
        if isinstance(q.get(k),list) and q[k]: return q[k]
    return []

def valid_positive(x):
    reason=str(x.get('reason','')).upper()
    return float(x.get('net_pnl',0) or 0)>0 and ('TP' in reason or '3FEE' in reason)

def state_from(di):
    st=di.get('state') or di.get('market_state')
    if st:return str(st)
    a5=di.get('reg5');a15=di.get('reg15')
    if a15=='UP' and a5=='UP':return 'TREND_UP'
    if a15=='DOWN' and a5=='DOWN':return 'TREND_DOWN'
    if a15=='RANGE' and a5=='UP':return 'TRANSITION_UP'
    if a15=='RANGE' and a5=='DOWN':return 'TRANSITION_DOWN'
    if a15=='UP' and a5=='RANGE':return 'UP_PAUSE'
    if a15=='DOWN' and a5=='RANGE':return 'DOWN_PAUSE'
    return 'BALANCE'

def bucket_vol(v):
    if v is None:return 'NA'
    v=float(v)
    return '<.5' if v<.5 else '.5-1' if v<1 else '1-2' if v<2 else '2-4' if v<4 else '>=4'

def bucket_vwap(v):
    if v is None:return 'NA'
    v=abs(float(v))
    return '<.05' if v<.05 else '.05-.12' if v<.12 else '.12-.25' if v<.25 else '>=.25'

def bucket_delta(side,v):
    if v is None:return 'NA'
    s=float(v) if side=='LONG' else -float(v)
    return '<=0' if s<=0 else '0-.15' if s<.15 else '.15-.4' if s<.4 else '>=.4'

def context_from_trade(x):
    di=x.get('diagnostics') or {}
    side=x.get('side','LONG')
    try:h=datetime.fromisoformat(x.get('entry_time')).hour
    except Exception:h=int(di.get('hour',0) or 0)
    return {
      'side':side,'state':state_from(di),'hour4':f'{(h//4)*4:02d}-{(h//4)*4+3:02d}',
      'vol_bucket':bucket_vol(di.get('volr')),'vwap_bucket':bucket_vwap(di.get('vwap_dist')),
      'delta_bucket':bucket_delta(side,di.get('delta')),'reg15':str(di.get('reg15','NA')),
    }

def mine_memory():
    rows=[]; day_counts={}; day_wins={}
    for day,p in TRAIN:
        xs=trade_rows(p); day_counts[day]=len(xs);day_wins[day]=sum(valid_positive(x) for x in xs)
        for x in xs:
            rows.append((day,context_from_trade(x),valid_positive(x)))
    baseline=sum(1 for _,_,y in rows if y)/max(1,len(rows))
    stats={tpl:defaultdict(lambda:{'n':0,'w':0,'days':set(),'wdays':set()}) for tpl in TEMPLATES}
    for day,c,y in rows:
        for tpl in TEMPLATES:
            key=tuple(c[k] for k in tpl);s=stats[tpl][key];s['n']+=1;s['w']+=int(y);s['days'].add(day)
            if y:s['wdays'].add(day)
    rules=[]
    for tpl,tab in stats.items():
        for key,s in tab.items():
            wr=s['w']/s['n'];lift=wr/max(baseline,1e-9)
            if s['n']>=6 and s['w']>=2 and len(s['wdays'])>=2 and lift>=1.20:
                weight=min(3.0,lift)*(1+.18*(len(s['wdays'])-2))*math.log1p(s['w'])
                rules.append({'tpl':tpl,'key':key,'n':s['n'],'wins':s['w'],'wr':wr,'lift':lift,'win_days':len(s['wdays']),'weight':weight})
    rules=sorted(rules,key=lambda z:(z['win_days'],z['weight'],z['wins']),reverse=True)[:40]
    def score(c):
        return sum(q['weight'] for q in rules if tuple(c[k] for k in q['tpl'])==q['key'])
    scored=[(score(c),y) for _,c,y in rows]
    vals=sorted(set(s for s,_ in scored if s>0))
    best=None
    for th in vals:
        sel=[y for s,y in scored if s>=th]
        if len(sel)<20:continue
        wr=sum(sel)/len(sel)
        # Prefer precision/lift while retaining enough training coverage.
        objective=(wr/max(baseline,1e-9))*math.log1p(len(sel))
        if wr>=baseline*1.35 and (best is None or objective>best[0]):best=(objective,th,len(sel),wr)
    threshold=best[1] if best else (vals[len(vals)//2] if vals else 999)
    return rules,threshold,baseline,day_counts,day_wins,rows,score

def ctx_market(i,m,f5,f15,side):
    z=m[i];a5,a15=b.ctx(z,f5,f15)
    if not a5 or not a15:return None
    st=b.state(i,m,f5,f15)[0];h=datetime.fromtimestamp(z['t']/1000,timezone.utc).astimezone(TEHRAN).hour
    return {
      'side':side,'state':st,'hour4':f'{(h//4)*4:02d}-{(h//4)*4+3:02d}',
      'vol_bucket':bucket_vol(z['volr']),'vwap_bucket':bucket_vwap((z['c']/z['vwap15']-1)*100),
      'delta_bucket':bucket_delta(side,z['delta']),'reg15':str(a15['regime'])
    },a5,a15,st

def make_candidate(i,m,f5,f15,side,memscore,threshold):
    if i<8 or i+1>=len(m):return None
    pack=ctx_market(i,m,f5,f15,side)
    if not pack:return None
    c,a5,a15,st=pack;z=m[i]
    sgn=1 if side=='LONG' else -1
    r3=sgn*(z['c']/m[i-3]['c']-1);r6=sgn*(z['c']/m[i-6]['c']-1)
    d3=sgn*sum(x['delta'] for x in m[i-2:i+1])/3;d1=sgn*z['delta']
    pos=(z['c']-z['l'])/max(z['h']-z['l'],1e-9);dpos=pos if side=='LONG' else 1-pos
    vd=sgn*(z['c']/z['vwap15']-1)
    c5=4*a5['atrp'];c15=2.5*a15['atrp'];cap=.55*c5+.45*c15
    # Learned from Sep5/6: capacity is a score, not a binary wall, but dead capacity is not tradeable.
    if cap<.00245 or max(c5,c15)<.0030:return None
    # Positive-memory entries require fresh directional displacement or re-acceleration, never state label alone.
    fresh=(.00035<=r3<=.0035 and d3>=.035 and dpos>=.50)
    reacc=(r6>=.00065 and d1>=.08 and d3>=.02 and dpos>=.55)
    if not (fresh or reacc):return None
    if memscore<threshold:return None
    # Avoid extreme late chase; allow larger displacement only with stronger capacity.
    if r3>.0025 and cap<.0036:return None
    ei=i+1;entry=m[ei]['o'];look=m[max(0,i-5):i+1]
    raw=min(x['l'] for x in look)*.9997 if side=='LONG' else max(x['h'] for x in look)*1.0003
    sp=(entry-raw)/entry if side=='LONG' else (raw-entry)/entry
    sp=max(MIN_STOP,min(MAX_STOP,sp));stop=entry*(1-sp) if side=='LONG' else entry*(1+sp)
    tp=.0044 if min(1.8*a15['atrp'],3.0*a5['atrp'])>=.0044 else .0033
    score=round(memscore + 2*min(cap/.0033,1.5) + min(d3/.15,2),3)
    diag={'memory_score':memscore,'memory_threshold':threshold,'state':st,'reg5':a5['regime'],'reg15':a15['regime'],'volr':z['volr'],'delta':z['delta'],'delta3_signed':d3,'r3_signed_pct':r3*100,'r6_signed_pct':r6*100,'vwap_signed_pct':vd*100,'capacity_score_pct':cap*100,'cap5_pct':c5*100,'cap15_pct':c15*100,'close_pos_directional':dpos}
    return ('POSITIVE_MEMORY',side,stop,ei,score,diag,tp)

def stat(xs):
    if not xs:return {'n':0,'valid_positive':0,'negative':0,'valid_wr':None,'gross':0,'cost':0,'net':0,'pf_net':None,'mfe':None,'mae':None}
    val=[x for x in xs if x['reason'] in ('TP','3FEE_FLOOR')];gp=sum(x['net_pnl'] for x in xs if x['net_pnl']>0);gl=-sum(x['net_pnl'] for x in xs if x['net_pnl']<=0)
    return {'n':len(xs),'valid_positive':len(val),'negative':len(xs)-len(val),'valid_wr':round(100*len(val)/len(xs),1),'gross':round(sum(x['gross_pnl'] for x in xs),6),'cost':round(sum(x['cost'] for x in xs),6),'net':round(sum(x['net_pnl'] for x in xs),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None),'mfe':round(sum(x['mfe_pct'] for x in xs)/len(xs),3),'mae':round(sum(x['mae_pct'] for x in xs)/len(xs),3)}

def row(x,sim,m,equity=None):
    setup,side,stop,ei,score,diag,tp=x;j,px,why,gross,cost,net,n,mfe,mae=sim
    et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
    z={'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'mae_pct':round(mae*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'diagnostics':diag}
    if equity is not None:z['equity_after']=round(equity+net,6)
    return z

def main():
    rules,threshold,baseline,day_counts,day_wins,trainrows,scorefn=mine_memory()
    raw=[];dd=datetime(2026,9,6,tzinfo=timezone.utc)
    while dd.date()<=datetime(2026,9,8,tzinfo=timezone.utc).date():raw+=r.b.get_daily(dd);dd+=timedelta(days=1)
    raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw)
    f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
    opp=[]
    for i,z in enumerate(m):
        if z['t']<S or z['t']>=E or i<12 or i+1>=len(m):continue
        cs=[]
        for side in ('LONG','SHORT'):
            p=ctx_market(i,m,f5,f15,side)
            if not p:continue
            c=p[0];ms=scorefn(c);x=make_candidate(i,m,f5,f15,side,ms,threshold)
            if x:cs.append(x)
        if not cs:continue
        x=sorted(cs,key=lambda q:q[4],reverse=True)[0];sim=b.simulate(m,x[3],x[1],x[2],x[6],100.)
        if not sim or m[sim[0]]['t']>=E:continue
        opp.append(row(x,sim,m))
    seq=[];eq=100.;i=0
    while i<len(m)-2:
        if m[i]['t']<S:i+=1;continue
        if m[i]['t']>=E:break
        cs=[]
        for side in ('LONG','SHORT'):
            p=ctx_market(i,m,f5,f15,side)
            if not p:continue
            ms=scorefn(p[0]);x=make_candidate(i,m,f5,f15,side,ms,threshold)
            if x:cs.append(x)
        if not cs:i+=1;continue
        x=sorted(cs,key=lambda q:q[4],reverse=True)[0];sim=b.simulate(m,x[3],x[1],x[2],x[6],eq)
        if not sim or m[sim[0]]['t']>=E:break
        rr=row(x,sim,m,eq);eq=rr['equity_after'];rr['n']=len(seq)+1;seq.append(rr);i=sim[0]+1
    def group(k,xs):
        g={}
        for x in xs:g.setdefault(x[k],[]).append(x)
        return {str(a):stat(v) for a,v in g.items()}
    for x in opp:
        h=datetime.fromisoformat(x['entry_time']).hour;x['hour4']=f'{(h//4)*4:02d}-{(h//4)*4+3:02d}'
    out={'version':'DARA-v17-PositiveMemory-Sep7-Frozen','period_tehran':[START.isoformat(),END.isoformat()],'training_scope':'Sep1-Sep6 result artifacts only; no Sep7 result artifact read by this script','positive_memory':{'training_rows':len(trainrows),'valid_positive_rows':sum(y for _,_,y in trainrows),'baseline_valid_rate':round(baseline,4),'day_counts':day_counts,'day_valid_positives':day_wins,'selected_rules':[{**q,'tpl':list(q['tpl']),'key':list(q['key']),'wr':round(q['wr'],4),'lift':round(q['lift'],2),'weight':round(q['weight'],3)} for q in rules],'entry_memory_threshold':round(threshold,4)},'rules':{'risk':RISK,'fee':COST,'profit_floor_pct':.33,'max_exposure':MAX_LEV,'min_stop_pct':MIN_STOP*100,'memory_rule_requires_winner_days':2,'capacity':'score >=0.245% and one timeframe >=0.30%','trigger':'fresh displacement or re-acceleration + positive-memory context','quarantined':['STATE_HYPOTHESIS','raw sweep','raw pullback','raw compression','accepted-break','retest-renewal','pause-reaccel','transition-extension']},'opportunity_inventory':stat(opp),'sequential':{'stats':stat(seq),'final_equity':round(eq,6),'net':round(eq-100,6),'trades':len(seq)},'by_side':group('side',opp),'by_4h':group('hour4',opp),'opportunities':opp,'sequential_trades':seq}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'memory':out['positive_memory'],'opp':out['opportunity_inventory'],'seq':out['sequential'],'side':out['by_side']},indent=2))
if __name__=='__main__':main()
