import json
from datetime import datetime, timezone
from pathlib import Path
import dara_v18_prototype_memory_sep7 as v18
import dara_pattern_memory as pm

TEHRAN=v18.TEHRAN
START=datetime(2026,9,7,0,0,tzinfo=TEHRAN); END=datetime(2026,9,8,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000); E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v19_pattern_fusion_sep7.json')

PRIMARY={'price_action_core','continuation','volatility_expansion','trend_pullback','candlestick_reversal','three_candle_reversal','wick_reversal','intraday_value'}
WEAK={'momentum_divergence','momentum_continuation','range_reversal'}


def pattern_side(rows,i,side):
    ms=pm.detect(rows,i)
    xs=[x for x in ms if x['side']==side]
    fam={}
    for x in xs: fam[x['family']]=max(fam.get(x['family'],0),x['quality'])
    score=sum(fam.values())
    primary=sum(1 for f in fam if f in PRIMARY)
    weak=sum(1 for f in fam if f in WEAK)
    return score,primary,weak,xs


def fused_candidate(i,m,f5,f15,side,p,wd,threshold):
    z=m[i]; a5,a15=v18.b.ctx(z,f5,f15)
    if not a5 or not a15:return None
    ps,primary,weak,matches=pattern_side(m,i,side)
    if ps<=0:return None
    c5=4*a5['atrp']; c15=2.5*a15['atrp']; cap=.55*c5+.45*c15
    if cap<.0022 or max(c5,c15)<.0028:return None
    proto_ok=(p>=threshold and wd>=2)
    strong_pattern=(ps>=1.8 and primary>=2 and max(c5,c15)>=.0033)
    # weak oscillator/indicator clusters never create an entry by themselves.
    if not proto_ok and not strong_pattern:return None
    if primary==0:return None
    # Avoid pure indicator contradiction: require a directional price-action/structure family.
    directional=[x for x in matches if x['family'] in PRIMARY]
    if not directional:return None
    ei=i+1
    if ei>=len(m):return None
    entry=m[ei]['o']; look=m[max(0,i-6):i+1]
    raw=min(x['l'] for x in look)*.9997 if side=='LONG' else max(x['h'] for x in look)*1.0003
    sp=(entry-raw)/entry if side=='LONG' else (raw-entry)/entry
    sp=max(v18.MIN_STOP,min(v18.MAX_STOP,sp)); stop=entry*(1-sp) if side=='LONG' else entry*(1+sp)
    expected=min(1.8*a15['atrp'],3*a5['atrp'])
    tp=.0044 if expected>=.0044 else .0033
    diag={'prototype_p':round(p,4),'prototype_winner_days':wd,'prototype_threshold':threshold,'pattern_score':round(ps,3),'pattern_primary_families':primary,'patterns':[x['pattern'] for x in matches],'families':sorted(set(x['family'] for x in matches)),'capacity_score_pct':round(cap*100,4),'expected_move_pct':round(expected*100,4),'reg5':a5['regime'],'reg15':a15['regime'],'volr':z.get('volr'),'delta':z.get('delta')}
    composite=round(100*p + 5*wd + 8*ps + 100*cap,3)
    return ('PATTERN_PROTOTYPE_FUSION',side,stop,ei,composite,diag,tp)


def stat(xs):return v18.stat(xs)

def row(x,sim,m,equity=None):return v18.row(x,sim,m,equity)


def main():
    ex,counts,wins=v18.build_training(); mu,sd=v18.scaling(ex); exz=[{**q,'z':v18.zv(q['vec'],mu,sd)} for q in ex]
    threshold,cv=v18.choose_threshold(ex,exz)
    m,f5,f15,idx=v18.load_market((2026,9,6),(2026,9,8)); pm.enrich_indicators(m)
    opp=[]
    for i,z in enumerate(m):
        if z['t']<S or z['t']>=E or i<35 or i+1>=len(m):continue
        cs=[]
        for side in ('LONG','SHORT'):
            vec=v18.common_vec(i,m,f5,f15,side)
            if vec is None:continue
            p,wd,_=v18.knn_score(v18.zv(vec,mu,sd),exz)
            x=fused_candidate(i,m,f5,f15,side,p,wd,threshold)
            if x:cs.append(x)
        if not cs:continue
        x=max(cs,key=lambda q:q[4]); sim=v18.b.simulate(m,x[3],x[1],x[2],x[6],100.)
        if not sim or m[sim[0]]['t']>=E:continue
        opp.append(row(x,sim,m))
    seq=[];eq=100.;i=0
    while i<len(m)-2:
        if m[i]['t']<S:i+=1;continue
        if m[i]['t']>=E:break
        cs=[]
        if i>=35:
            for side in ('LONG','SHORT'):
                vec=v18.common_vec(i,m,f5,f15,side)
                if vec is None:continue
                p,wd,_=v18.knn_score(v18.zv(vec,mu,sd),exz); x=fused_candidate(i,m,f5,f15,side,p,wd,threshold)
                if x:cs.append(x)
        if not cs:i+=1;continue
        x=max(cs,key=lambda q:q[4]); sim=v18.b.simulate(m,x[3],x[1],x[2],x[6],eq)
        if not sim or m[sim[0]]['t']>=E:break
        rr=row(x,sim,m,eq); eq=rr['equity_after'];rr['n']=len(seq)+1;seq.append(rr);i=sim[0]+1
    bypat={}
    for x in opp:
        for p in x['diagnostics']['patterns']:bypat.setdefault(p,[]).append(x)
    out={'version':'DARA-v19-PatternPrototypeFusion-Sep7-Development','period_tehran':[START.isoformat(),END.isoformat()],'note':'Development replay: Sep7 was already seen by v18 result review. Pattern definitions are sourced externally and fixed before this replay; do not treat as OOS proof.','training_memory':{'examples':len(ex),'valid_positive':sum(q['y'] for q in ex),'days':'Sep1-Sep6','prototype_threshold':threshold,'cv':cv},'pattern_library':'data/dara_pattern_library_v1.json','rules':{'fee':v18.COST,'risk':v18.RISK,'normal_profit_floor_pct':.33,'preferred_profit_pct':.44,'weak_indicator_alone_cannot_enter':True,'entry':'prototype memory + recognized technical pattern, OR >=2 strong primary pattern families; capacity required'},'opportunity_inventory':stat(opp),'sequential':{'stats':stat(seq),'final_equity':round(eq,6),'net':round(eq-100,6),'trades':len(seq)},'by_pattern':{k:stat(v) for k,v in bypat.items()},'opportunities':opp,'sequential_trades':seq}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'opp':out['opportunity_inventory'],'seq':out['sequential'],'top_patterns':sorted([(k,v['n'],v['valid_wr'],v['net']) for k,v in out['by_pattern'].items()],key=lambda x:(x[2] or 0),reverse=True)[:12]},indent=2))

if __name__=='__main__':main()
