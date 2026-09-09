import json
from dataclasses import asdict
from datetime import date,datetime,timezone
from pathlib import Path
import dara_aug20_current_brain_replay as b0
import dara_astro_day_bias_policy as astro
import dara_v18_prototype_memory_sep7 as v18
import dara_v19_pattern_fusion_sep7 as v19
import dara_pattern_memory as pm

TEHRAN=v18.TEHRAN;DAY=date(2026,8,22)
START=datetime(2026,8,22,0,0,tzinfo=TEHRAN);END=datetime(2026,8,23,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000);E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_aug22_transfer_v5.json')

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
 if not(p>=th and wd>=2):stage['reject_base_memory']+=1;return None
 ps,primary,weak,matches=v19.pattern_side(m,i,side)
 z=m[i];a5,a15=v18.b.ctx(z,f5,f15)
 if not a5 or not a15:return None
 # v5 lesson 1: pattern count cannot override weak memory. Require p>=0.32 always.
 if p<.32:stage['reject_memory_floor']+=1;return None
 patterns=[x.get('name') for x in matches]
 families=set(x.get('family') for x in matches)
 delta=float(z.get('delta',0))
 if not(delta>0 if side=='LONG' else delta<0):stage['reject_flow_sign']+=1;return None
 if abs(delta)<.05:stage['reject_weak_flow']+=1;return None
 vol=float(z.get('volr',0))
 if 1<=vol<2:stage['reject_vol_1_2']+=1;return None
 ds,dtd=day_state(i,m,a15,day_open)
 if dtd>=.015 and side=='SHORT' and not(a15['regime']=='DOWN' and p>=.32):stage['reject_day_lock']+=1;return None
 if dtd<=-.015 and side=='LONG' and not(a15['regime']=='UP' and p>=.32):stage['reject_day_lock']+=1;return None
 if (ds=='STRONG_UP' and side=='SHORT') or (ds=='STRONG_DOWN' and side=='LONG'):stage['reject_counter_day']+=1;return None
 sg=1 if side=='LONG' else -1
 r3=sg*(z['c']/m[i-3]['c']-1);r6=sg*(z['c']/m[i-6]['c']-1)
 if max(r3,r6)<.00025:stage['reject_no_price_response']+=1;return None
 # v5 lesson 2: after >=2% day extension, no ordinary chase. Require p>=0.40 AND both 3m/6m signed response >=0.25%.
 if abs(dtd)>=.02:
  if p<.40 or min(r3,r6)<.0025:
   stage['reject_late_extension']+=1;return None
 # v5 lesson 3: extreme volume is only accepted with true pullback/continuation context, not a lone candle reversal.
 if vol>=8:
  if not(('trend_pullback' in families) or ('continuation' in families) or ('price_action_core' in families)):
   stage['reject_highvol_no_structure']+=1;return None
 # keep v4 exhaustion guard for large extension + extreme volume
 if abs(dtd)>=.02 and vol>=8:stage['reject_exhaustion_chase']+=1;return None
 x=v19.fused_candidate(i,m,f5,f15,side,p,wd,th)
 if not x:return None
 setup,s,stop,ei,score,diag,tp=x;diag=dict(diag)
 diag.update({'transfer_v5':True,'memory_floor':p>=.32,'day_state_live':ds,'dtd_pct':round(dtd*100,4),'r3_signed_pct':round(r3*100,4),'r6_signed_pct':round(r6*100,4),'material_flow':abs(delta)>=.05,'risk_pct_equity':pol['risk_pct_equity'],'risk_fraction_equity':pol['risk_fraction_equity'],'astro_day_bias':ab.bias,'astro_score':ab.score,'astro_aligned':None if ab.bias=='NEUTRAL' else side==ab.bias,'patterns_v5':patterns,'families_v5':sorted(f for f in families if f)})
 stage['final']+=1
 return (setup,s,stop,ei,round(score+2,3),diag,tp),pol

def main():
 # Freeze astro first, before Aug22 market bars are loaded.
 ab=astro.day_bias(DAY)
 ex,counts,wins=v18.build_training();mu,sd=v18.scaling(ex);exz=[{**q,'z':v18.zv(q['vec'],mu,sd)} for q in ex];th,cv=v18.choose_threshold(ex,exz)
 m,f5,f15,idx=v18.load_market((2026,8,21),(2026,8,23));pm.enrich_indicators(m)
 inds=[i for i,z in enumerate(m) if S<=z['t']<E];day_open=m[inds[0]]['o']
 keys=['seen','reject_base_memory','reject_memory_floor','reject_flow_sign','reject_weak_flow','reject_vol_1_2','reject_day_lock','reject_counter_day','reject_no_price_response','reject_late_extension','reject_highvol_no_structure','reject_exhaustion_chase','final'];stage={k:0 for k in keys}
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
  rr=b0.row_from(x,sim,m,eq);eq=rr['equity_after'];rr['n']=len(seq)+1;seq.append(rr)
  cooldown=sim[0]+6 if rr['net_pnl']<=0 else sim[0]+1;i=sim[0]+1
 first=m[inds[0]];last=m[inds[-1]]
 market={'open':first['o'],'close':last['c'],'return_pct':round((last['c']/first['o']-1)*100,4),'high':max(m[j]['h'] for j in inds),'low':min(m[j]['l'] for j in inds)}
 out={'version':'DARA-Aug22-Transfer-v5-FROZEN','warning':'Aug22 was not used to tune v5 in this run. v5 was derived from Aug20-21 diagnostics; prototype memory still contains earlier project history, so this is diagnostic transfer rather than pristine OOS evidence.','period_tehran':[START.isoformat(),END.isoformat()],'astro_first':asdict(ab),'market_after_freeze':market,'rules_frozen_before_aug22':['prototype p>=0.32 mandatory; pattern count cannot override weak memory','flow sign aligned and abs delta>=0.05','volr 1-2 quarantined','live day-direction lock','real 3m/6m price response','after abs DTD>=2%, require p>=0.40 and both r3/r6 >=0.25% in trade direction','volr>=8 requires pullback/continuation structure; lone candlestick reversal rejected','after abs DTD>=2%, volr>=8 rejected as exhaustion','5-minute cooldown after loss','fee/risk/TP unchanged'],'stage':stage,'opportunity':b0.stats(opp),'opportunity_by_side':b0.group_side(opp),'sequential':{'stats':b0.stats(seq),'by_side':b0.group_side(seq),'start_equity':100.,'final_equity':round(eq,6),'return_pct':round(eq-100,4),'max_drawdown_pct':b0.max_drawdown(seq,100.),'trades':len(seq)},'trades':seq}
 OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'astro':out['astro_first'],'market':market,'stage':stage,'opp':out['opportunity'],'opp_side':out['opportunity_by_side'],'seq':out['sequential']},indent=2))
if __name__=='__main__':main()
