import json
from dataclasses import asdict
from datetime import date,datetime,timezone
from pathlib import Path
import dara_aug20_current_brain_replay as b0
import dara_astro_day_bias_policy as astro
import dara_v18_prototype_memory_sep7 as v18
import dara_v19_pattern_fusion_sep7 as v19
import dara_pattern_memory as pm

TEHRAN=v18.TEHRAN;DAY=date(2026,8,20)
START=datetime(2026,8,20,0,0,tzinfo=TEHRAN);END=datetime(2026,8,21,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000);E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_aug20_learned_replay_v4.json')

def day_state(i,m,a15,day_open):
 dtd=m[i]['c']/day_open-1
 if dtd>=.005 and a15['regime']=='UP':return 'STRONG_UP',dtd
 if dtd<=-.005 and a15['regime']=='DOWN':return 'STRONG_DOWN',dtd
 return 'NEUTRAL',dtd

def build_candidate(i,m,f5,f15,side,exz,mu,sd,base_threshold,ab,day_open,stage):
 stage['seen']+=1;vec=v18.common_vec(i,m,f5,f15,side)
 if vec is None:return None
 p,wd,_=v18.knn_score(v18.zv(vec,mu,sd),exz);pol=b0.astro_policy(ab,side);th=b0.effective_threshold(base_threshold,pol)
 if not(p>=th and wd>=2):stage['reject_base_memory']+=1;return None
 ps,primary,weak,matches=v19.pattern_side(m,i,side);memory_strong=p>=.32;confluence=primary>=3
 if not(memory_strong or confluence):stage['reject_quality']+=1;return None
 z=m[i];a5,a15=v18.b.ctx(z,f5,f15)
 if not a5 or not a15:return None
 delta=float(z.get('delta',0));flow=(delta>0 if side=='LONG' else delta<0)
 if not flow:stage['reject_flow_sign']+=1;return None
 # v4 lesson A: weak signed flow was a false confirmation; require material aggressor participation.
 if abs(delta)<.05:stage['reject_weak_flow']+=1;return None
 vol=float(z.get('volr',0))
 if 1<=vol<2:stage['reject_vol_1_2']+=1;return None
 ds,dtd=day_state(i,m,a15,day_open)
 # v4 lesson B: once day move is decisive, countertrend entries need real 15m reversal + strong memory.
 if dtd>=.015 and side=='SHORT' and not(a15['regime']=='DOWN' and p>=.32):stage['reject_day_lock']+=1;return None
 if dtd<=-.015 and side=='LONG' and not(a15['regime']=='UP' and p>=.32):stage['reject_day_lock']+=1;return None
 if (ds=='STRONG_UP' and side=='SHORT') or (ds=='STRONG_DOWN' and side=='LONG'):stage['reject_counter_day']+=1;return None
 sg=1 if side=='LONG' else -1;r3=sg*(z['c']/m[i-3]['c']-1);r6=sg*(z['c']/m[i-6]['c']-1)
 if max(r3,r6)<.00025:stage['reject_no_price_response']+=1;return None
 # v4 lesson C: after a large day extension, extreme volume is treated as exhaustion/chase, not continuation.
 if abs(dtd)>=.02 and vol>=8:stage['reject_exhaustion_chase']+=1;return None
 # ordinary late chase remains blocked unless there is broad confluence.
 if abs(dtd)>=.02 and not confluence:stage['reject_late_chase']+=1;return None
 x=v19.fused_candidate(i,m,f5,f15,side,p,wd,th)
 if not x:return None
 setup,s,stop,ei,score,diag,tp=x;diag=dict(diag)
 diag.update({'learned_v4':True,'memory_strong':memory_strong,'triple_primary_confluence':confluence,'day_state_live':ds,'dtd_pct':round(dtd*100,4),'r3_signed_pct':round(r3*100,4),'r6_signed_pct':round(r6*100,4),'instant_flow_aligned':True,'material_flow':abs(delta)>=.05,'risk_pct_equity':pol['risk_pct_equity'],'risk_fraction_equity':pol['risk_fraction_equity'],'astro_day_bias':ab.bias,'astro_score':ab.score,'astro_aligned':None if ab.bias=='NEUTRAL' else side==ab.bias,'effective_prototype_threshold':th})
 score+=4*int(confluence)+2*int(memory_strong);stage['final']+=1
 return (setup,s,stop,ei,round(score,3),diag,tp),pol

def main():
 ab=astro.day_bias(DAY);ex,counts,wins=v18.build_training();mu,sd=v18.scaling(ex);exz=[{**q,'z':v18.zv(q['vec'],mu,sd)} for q in ex];th,cv=v18.choose_threshold(ex,exz)
 m,f5,f15,idx=v18.load_market((2026,8,19),(2026,8,21));pm.enrich_indicators(m);inds=[i for i,z in enumerate(m) if S<=z['t']<E];day_open=m[inds[0]]['o']
 keys=['seen','reject_base_memory','reject_quality','reject_flow_sign','reject_weak_flow','reject_vol_1_2','reject_day_lock','reject_counter_day','reject_no_price_response','reject_exhaustion_chase','reject_late_chase','final'];stage={k:0 for k in keys}
 opp=[]
 for i in inds:
  if i<35 or i+1>=len(m):continue
  cs=[]
  for side in ('LONG','SHORT'):
   q=build_candidate(i,m,f5,f15,side,exz,mu,sd,th,ab,day_open,stage)
   if q:cs.append(q)
  if not cs:continue
  x,pol=max(cs,key=lambda q:q[0][4]);sim=b0.simulate_with_policy(m,x,100.,pol)
  if sim and m[sim[0]]['t']<E:opp.append(b0.row_from(x,sim,m))
 seq=[];eq=100.;i=inds[0];cooldown=-1
 while i<len(m)-2 and m[i]['t']<E:
  if i<cooldown:i+=1;continue
  cs=[];dummy={k:0 for k in keys}
  for side in ('LONG','SHORT'):
   q=build_candidate(i,m,f5,f15,side,exz,mu,sd,th,ab,day_open,dummy)
   if q:cs.append(q)
  if not cs:i+=1;continue
  x,pol=max(cs,key=lambda q:q[0][4]);sim=b0.simulate_with_policy(m,x,eq,pol)
  if not sim or m[sim[0]]['t']>=E:break
  rr=b0.row_from(x,sim,m,eq);eq=rr['equity_after'];rr['n']=len(seq)+1;seq.append(rr);cooldown=sim[0]+6 if rr['net_pnl']<=0 else sim[0]+1;i=sim[0]+1
 out={'version':'DARA-Aug20-LearnedReplay-v4','warning':'INTENTIONALLY IN-SAMPLE. v4 rules were derived from Aug20 v1-v3 losses; NOT OOS evidence.','learning_rules':['v3 gates retained','abs instantaneous delta >=0.05','after DTD >=1.5%, shorts require 15m DOWN and prototype p>=0.32; mirror for strong down days','after abs(DTD)>=2%, volr>=8 rejected as exhaustion/chase','risk/fee/TP unchanged'],'astro':asdict(ab),'stage':stage,'opportunity':b0.stats(opp),'opportunity_by_side':b0.group_side(opp),'sequential':{'stats':b0.stats(seq),'by_side':b0.group_side(seq),'start_equity':100.,'final_equity':round(eq,6),'return_pct':round(eq-100,4),'max_drawdown_pct':b0.max_drawdown(seq,100.),'trades':len(seq)},'trades':seq}
 OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'stage':stage,'opp':out['opportunity'],'seq':out['sequential']},indent=2))
if __name__=='__main__':main()
