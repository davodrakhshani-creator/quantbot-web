import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

import dara_aug20_current_brain_replay as b0
import dara_astro_day_bias_policy as astro
import dara_v18_prototype_memory_sep7 as v18
import dara_v19_pattern_fusion_sep7 as v19
import dara_pattern_memory as pm

TEHRAN=v18.TEHRAN; DAY=date(2026,8,20)
START=datetime(2026,8,20,0,0,tzinfo=TEHRAN);END=datetime(2026,8,21,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000);E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_aug20_learned_replay_v2.json')
START_EQUITY=100.; DAILY_STOP=-.01

# Learned from Aug20 v1 failure analysis (development/in-sample):
# 1) pattern-only bypass with prototype p<.25 caused most damage -> prototype memory mandatory.
# 2) instantaneous flow-opposed trades were weak -> require directional taker-flow sign.
# 3) volr 1-2 was toxic on Aug20 and had also been toxic on Sep2 -> quarantine this bucket.
# 4) once day-to-date move >=0.50% and 15m regime agrees, suppress counter-day trades.
# Exit/risk logic unchanged so improvement must come from better entries.


def day_state(i,m,a15,day_open):
    dtd=m[i]['c']/day_open-1
    if dtd>=.005 and a15['regime']=='UP':return 'STRONG_UP',dtd
    if dtd<=-.005 and a15['regime']=='DOWN':return 'STRONG_DOWN',dtd
    return 'NEUTRAL',dtd

def build_candidate(i,m,f5,f15,side,exz,mu,sd,base_threshold,ab,day_open,stage):
    stage['seen']+=1
    vec=v18.common_vec(i,m,f5,f15,side)
    if vec is None:return None
    p,wd,_=v18.knn_score(v18.zv(vec,mu,sd),exz)
    pol=b0.astro_policy(ab,side);th=b0.effective_threshold(base_threshold,pol)
    # repair #1: prototype is mandatory; strong pattern cannot bypass it.
    if not (p>=th and wd>=2):
        stage['reject_prototype']+=1;return None
    stage['prototype_pass']+=1
    z=m[i];a5,a15=v18.b.ctx(z,f5,f15)
    if not a5 or not a15:return None
    # repair #2: flow sign must support side.
    flow=(z.get('delta',0)>0 if side=='LONG' else z.get('delta',0)<0)
    if not flow:
        stage['reject_flow']+=1;return None
    stage['flow_pass']+=1
    # repair #3: quarantine repeated toxic 1x-2x relative-volume state.
    vol=float(z.get('volr',0))
    if 1.0<=vol<2.0:
        stage['reject_vol_1_2']+=1;return None
    # repair #4: live day-state uses only information available at this minute.
    ds,dtd=day_state(i,m,a15,day_open)
    if (ds=='STRONG_UP' and side=='SHORT') or (ds=='STRONG_DOWN' and side=='LONG'):
        stage['reject_counter_day']+=1;return None
    x=v19.fused_candidate(i,m,f5,f15,side,p,wd,th)
    if not x:return None
    setup,s,stop,ei,score,diag,tp=x
    diag=dict(diag);diag.update({'learned_v2':True,'day_state_live':ds,'dtd_pct':round(dtd*100,4),'instant_flow_aligned':True,'risk_pct_equity':pol['risk_pct_equity'],'risk_fraction_equity':pol['risk_fraction_equity'],'astro_day_bias':ab.bias,'astro_score':ab.score,'effective_prototype_threshold':th})
    # state ranking: aligned live intraday direction receives a small preference only.
    if (ds=='STRONG_UP' and side=='LONG') or (ds=='STRONG_DOWN' and side=='SHORT'):score+=2
    stage['final']+=1
    return (setup,s,stop,ei,round(score,3),diag,tp),pol

def simulate(m,x,eq,pol):return b0.simulate_with_policy(m,x,eq,pol)
def row(x,sim,m,eq=None):return b0.row_from(x,sim,m,eq)

def main():
    ab=astro.day_bias(DAY)
    ex,counts,wins=v18.build_training();mu,sd=v18.scaling(ex);exz=[{**q,'z':v18.zv(q['vec'],mu,sd)} for q in ex]
    base_threshold,cv=v18.choose_threshold(ex,exz)
    m,f5,f15,idx=v18.load_market((2026,8,19),(2026,8,21));pm.enrich_indicators(m)
    day_indices=[i for i,z in enumerate(m) if S<=z['t']<E];day_open=m[day_indices[0]]['o']
    stage={k:0 for k in ['seen','reject_prototype','prototype_pass','reject_flow','flow_pass','reject_vol_1_2','reject_counter_day','final']}
    # opportunity inventory
    opp=[]
    for i in day_indices:
        if i<35 or i+1>=len(m):continue
        cs=[]
        for side in ('LONG','SHORT'):
            q=build_candidate(i,m,f5,f15,side,exz,mu,sd,base_threshold,ab,day_open,stage)
            if q:cs.append(q)
        if not cs:continue
        x,pol=max(cs,key=lambda q:q[0][4]);sim=simulate(m,x,100.,pol)
        if sim and m[sim[0]]['t']<E:opp.append(row(x,sim,m))
    # sequential one-position account
    seq=[];eq=START_EQUITY;i=day_indices[0];daily_stop_hit=False
    while i<len(m)-2 and m[i]['t']<E:
        if eq<=START_EQUITY*(1+DAILY_STOP):daily_stop_hit=True;break
        if i<35:i+=1;continue
        cs=[];dummy={k:0 for k in stage}
        for side in ('LONG','SHORT'):
            q=build_candidate(i,m,f5,f15,side,exz,mu,sd,base_threshold,ab,day_open,dummy)
            if q:cs.append(q)
        if not cs:i+=1;continue
        x,pol=max(cs,key=lambda q:q[0][4]);sim=simulate(m,x,eq,pol)
        if not sim or m[sim[0]]['t']>=E:break
        rr=row(x,sim,m,eq);eq=rr['equity_after'];rr['n']=len(seq)+1;seq.append(rr);i=sim[0]+1
    out={'version':'DARA-Aug20-LearnedReplay-v2','warning':'INTENTIONALLY IN-SAMPLE learning replay. Rules were derived from Aug20 v1 failure analysis; this is not OOS evidence.',
         'period_tehran':[START.isoformat(),END.isoformat()],'astro':asdict(ab),'learning_rules':['prototype p>=threshold and winner_days>=2 mandatory; no pattern-only bypass','instantaneous taker delta must align with side','quarantine volr 1.0-2.0','after live DTD >=0.50% plus agreeing 15m regime, suppress counter-day trades','risk/fee/TP/stop/exit logic unchanged'],
         'stage':stage,'opportunity':b0.stats(opp),'opportunity_by_side':b0.group_side(opp),'sequential':{'stats':b0.stats(seq),'by_side':b0.group_side(seq),'start_equity':100.,'final_equity':round(eq,6),'return_pct':round((eq/100-1)*100,4),'max_drawdown_pct':b0.max_drawdown(seq,100.),'daily_stop_hit':daily_stop_hit,'trades':len(seq)},'trades':seq}
    OUT.write_text(json.dumps(out,indent=2))
    print(json.dumps({'stage':stage,'opp':out['opportunity'],'seq':out['sequential']},indent=2))
if __name__=='__main__':main()
