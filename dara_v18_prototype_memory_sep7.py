import json, math
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_discovery1000_sep5_learned as b
import dara_v11_liquidity_transition_sep1_8 as r

TEHRAN=b.TEHRAN
START=datetime(2026,9,7,0,0,tzinfo=TEHRAN);END=datetime(2026,9,8,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000);E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v18_prototype_memory_sep7.json')
COST=.0011;RISK=.0002;MAX_LEV=3.0;MIN_STOP=.0016;MAX_STOP=.0032
b.COST=COST;b.RISK=RISK;b.MAX_LEV=MAX_LEV;b.MIN_STOP=MIN_STOP;b.MAX_STOP=MAX_STOP
TRAIN=[
 ('2026-09-01','data/dara_discovery30_sep1.json'),('2026-09-02','data/dara_discovery100_sep2.json'),
 ('2026-09-03','data/dara_discovery500_sep3.json'),('2026-09-04','data/dara_v14_sep4_opportunity_sweep.json'),
 ('2026-09-05','data/dara_discovery1000_sep5_exact.json'),('2026-09-06','data/dara_v16_1_sep6_capacity_score_discovery.json')]

FEATURES=['side_code','r1','r3','r6','r12','accel','d1','d3','d6','logvol','vwap','atr5','atr15','closepos','reg5','reg15']

def rows(p):
 q=json.loads(Path(p).read_text())
 for k in ('trades','opportunities','sequential_trades'):
  if isinstance(q.get(k),list) and q[k]:return q[k]
 return []

def valid(x):
 rs=str(x.get('reason','')).upper();return float(x.get('net_pnl',0) or 0)>0 and ('TP' in rs or '3FEE' in rs)

def regcode(reg,side):
 if reg=='RANGE':return 0.0
 aligned=(reg=='UP' and side=='LONG') or (reg=='DOWN' and side=='SHORT')
 return 1.0 if aligned else -1.0

def common_vec(i,m,f5,f15,side):
 if i<13:return None
 z=m[i];a5,a15=b.ctx(z,f5,f15)
 if not a5 or not a15:return None
 sg=1 if side=='LONG' else -1
 def rr(k):return sg*(z['c']/m[i-k]['c']-1)*100
 r1=rr(1);r3=rr(3);r6=rr(6);r12=rr(12);prev3=sg*(m[i-3]['c']/m[i-6]['c']-1)*100
 d1=sg*z['delta'];d3=sg*sum(x['delta'] for x in m[i-2:i+1])/3;d6=sg*sum(x['delta'] for x in m[i-5:i+1])/6
 pos=(z['c']-z['l'])/max(z['h']-z['l'],1e-9);pos=pos if side=='LONG' else 1-pos
 vwap=sg*(z['c']/z['vwap15']-1)*100
 return [1.0 if side=='LONG' else -1.0,r1,r3,r6,r12,r3-prev3,d1,d3,d6,math.log1p(min(max(z['volr'],0),8)),vwap,a5['atrp']*100,a15['atrp']*100,pos,regcode(a5['regime'],side),regcode(a15['regime'],side)]

def load_market(startday,endday):
 raw=[];dd=datetime(*startday,tzinfo=timezone.utc)
 while dd.date()<=datetime(*endday,tzinfo=timezone.utc).date():raw+=r.b.get_daily(dd);dd+=timedelta(days=1)
 raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw)
 f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
 return m,f5,f15,{z['t']:i for i,z in enumerate(m)}

def build_training():
 m,f5,f15,idx=load_market((2026,8,31),(2026,9,7))
 ex=[];counts={};wins={}
 for day,p in TRAIN:
  xs=rows(p);counts[day]=len(xs);wins[day]=sum(valid(x) for x in xs)
  for x in xs:
   try:t=int(datetime.fromisoformat(x['entry_time']).astimezone(timezone.utc).timestamp()*1000)
   except Exception:continue
   j=idx.get(t)
   if j is None or j<1:continue
   # Entry is next-minute open in these harnesses; reconstruct signal state one minute before entry.
   vec=common_vec(j-1,m,f5,f15,x.get('side','LONG'))
   if vec is None:continue
   ex.append({'day':day,'vec':vec,'y':1 if valid(x) else 0})
 return ex,counts,wins

def scaling(ex):
 mu=[];sd=[]
 for k in range(len(FEATURES)):
  a=[x['vec'][k] for x in ex];mm=sum(a)/len(a);vv=sum((v-mm)**2 for v in a)/max(1,len(a)-1);mu.append(mm);sd.append(max(math.sqrt(vv),1e-4))
 return mu,sd

def zv(v,mu,sd):return [(v[i]-mu[i])/sd[i] for i in range(len(v))]

def knn_score(vec,exz,exclude_day=None,k=45):
 ds=[]
 for q in exz:
  if exclude_day is not None and q['day']==exclude_day:continue
  d=sum((a-bb)**2 for a,bb in zip(vec,q['z']))
  ds.append((d,q))
 ds.sort(key=lambda x:x[0]);nn=ds[:min(k,len(ds))]
 if not nn:return 0,0,99
 # Distance + day-balance weights prevent Sep5 from owning the memory.
 dayn={}
 for _,q in nn:dayn[q['day']]=dayn.get(q['day'],0)+1
 num=den=0.;wd=set();dw=[]
 for d,q in nn:
  w=math.exp(-min(d,40)/8)/math.sqrt(dayn[q['day']]);num+=w*q['y'];den+=w
  if q['y']:wd.add(q['day']);dw.append(d)
 return num/max(den,1e-12),len(wd),(sum(dw)/len(dw) if dw else 99)

def choose_threshold(ex,exz):
 scored=[]
 for q in exz:
  p,wd,dist=knn_score(q['z'],exz,exclude_day=q['day']);scored.append((p,wd,q['y'],q['day']))
 baseline=sum(q['y'] for q in ex)/len(ex);best=None
 for th in [x/100 for x in range(10,61,2)]:
  s=[z for z in scored if z[0]>=th and z[1]>=2]
  if len(s)<20:continue
  wr=sum(z[2] for z in s)/len(s);days=len(set(z[3] for z in s if z[2]))
  utility=(2*sum(z[2] for z in s)-(len(s)-sum(z[2] for z in s)))
  # Prefer actual positive payoff proxy, then precision, while requiring recurrence.
  obj=utility+10*days+wr*20
  if days>=3 and wr>=baseline*1.35 and (best is None or obj>best[0]):best=(obj,th,len(s),wr,days,utility)
 if best:return best[1],{'cv_n':best[2],'cv_wr':round(best[3],3),'cv_winner_days':best[4],'cv_payoff_proxy':best[5],'baseline':round(baseline,3)}
 return .25,{'cv_n':0,'cv_wr':None,'cv_winner_days':0,'cv_payoff_proxy':None,'baseline':round(baseline,3)}

def candidate(i,m,f5,f15,side,p,wd,threshold):
 if p<threshold or wd<2:return None
 z=m[i];a5,a15=b.ctx(z,f5,f15)
 if not a5 or not a15:return None
 sg=1 if side=='LONG' else -1;r3=sg*(z['c']/m[i-3]['c']-1);r6=sg*(z['c']/m[i-6]['c']-1);d3=sg*sum(x['delta'] for x in m[i-2:i+1])/3
 c5=4*a5['atrp'];c15=2.5*a15['atrp'];cap=.55*c5+.45*c15
 # Keep only dead-market rejection; prototype score handles the rest.
 if cap<.0022 or max(c5,c15)<.0028:return None
 # Exclude pure zero-motion lookalikes even if categorical/numeric neighborhood is close.
 if max(r3,r6)<.00025 and d3<.02:return None
 ei=i+1;entry=m[ei]['o'];look=m[max(0,i-5):i+1];raw=min(x['l'] for x in look)*.9997 if side=='LONG' else max(x['h'] for x in look)*1.0003
 sp=(entry-raw)/entry if side=='LONG' else (raw-entry)/entry;sp=max(MIN_STOP,min(MAX_STOP,sp));stop=entry*(1-sp) if side=='LONG' else entry*(1+sp)
 tp=.0044 if min(1.8*a15['atrp'],3*a5['atrp'])>=.0044 else .0033
 diag={'prototype_p':p,'prototype_winner_days':wd,'threshold':threshold,'reg5':a5['regime'],'reg15':a15['regime'],'volr':z['volr'],'delta':z['delta'],'r3_signed_pct':r3*100,'r6_signed_pct':r6*100,'delta3_signed':d3,'capacity_score_pct':cap*100,'vwap_signed_pct':sg*(z['c']/z['vwap15']-1)*100}
 return ('PROTOTYPE_MEMORY',side,stop,ei,round(p*100+wd*3+cap*100,3),diag,tp)

def stat(xs):
 if not xs:return {'n':0,'valid_positive':0,'negative':0,'valid_wr':None,'gross':0,'cost':0,'net':0,'pf_net':None,'mfe':None,'mae':None}
 vp=[x for x in xs if x['reason'] in ('TP','3FEE_FLOOR')];gp=sum(x['net_pnl'] for x in xs if x['net_pnl']>0);gl=-sum(x['net_pnl'] for x in xs if x['net_pnl']<=0)
 return {'n':len(xs),'valid_positive':len(vp),'negative':len(xs)-len(vp),'valid_wr':round(100*len(vp)/len(xs),1),'gross':round(sum(x['gross_pnl'] for x in xs),6),'cost':round(sum(x['cost'] for x in xs),6),'net':round(sum(x['net_pnl'] for x in xs),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None),'mfe':round(sum(x['mfe_pct'] for x in xs)/len(xs),3),'mae':round(sum(x['mae_pct'] for x in xs)/len(xs),3)}

def row(x,sim,m,equity=None):
 setup,side,stop,ei,score,diag,tp=x;j,px,why,gross,cost,net,n,mfe,mae=sim;et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
 z={'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'mae_pct':round(mae*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'diagnostics':diag}
 if equity is not None:z['equity_after']=round(equity+net,6)
 return z

def main():
 ex,counts,wins=build_training();mu,sd=scaling(ex);exz=[{**q,'z':zv(q['vec'],mu,sd)} for q in ex];threshold,cv=choose_threshold(ex,exz)
 m,f5,f15,idx=load_market((2026,9,6),(2026,9,8))
 opp=[]
 for i,z in enumerate(m):
  if z['t']<S or z['t']>=E or i<13 or i+1>=len(m):continue
  cs=[]
  for side in ('LONG','SHORT'):
   vec=common_vec(i,m,f5,f15,side)
   if vec is None:continue
   p,wd,_=knn_score(zv(vec,mu,sd),exz);x=candidate(i,m,f5,f15,side,p,wd,threshold)
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
   vec=common_vec(i,m,f5,f15,side)
   if vec is None:continue
   p,wd,_=knn_score(zv(vec,mu,sd),exz);x=candidate(i,m,f5,f15,side,p,wd,threshold)
   if x:cs.append(x)
  if not cs:i+=1;continue
  x=sorted(cs,key=lambda q:q[4],reverse=True)[0];sim=b.simulate(m,x[3],x[1],x[2],x[6],eq)
  if not sim or m[sim[0]]['t']>=E:break
  rr=row(x,sim,m,eq);eq=rr['equity_after'];rr['n']=len(seq)+1;seq.append(rr);i=sim[0]+1
 def group(k,xs):
  g={}
  for x in xs:g.setdefault(x[k],[]).append(x)
  return {str(a):stat(v) for a,v in g.items()}
 out={'version':'DARA-v18-PrototypeMemory-Sep7','period_tehran':[START.isoformat(),END.isoformat()],'training_scope':'Only Sep1-Sep6 trade experiences are used to build prototypes; no Sep7 result file is read.','memory':{'examples':len(ex),'valid_positive_examples':sum(q['y'] for q in ex),'day_counts':counts,'day_valid_positives':wins,'features':FEATURES,'knn_k':45,'cross_day_threshold':threshold,'cross_day_validation':cv},'rules':{'risk':RISK,'fee':COST,'profit_floor_pct':.33,'min_stop_pct':MIN_STOP*100,'max_exposure':MAX_LEV,'minimum_winner_days_in_local_memory':2,'only_dead_capacity_is_hard_rejected':True,'no_quarantined_legacy_setup_reintroduced':True},'opportunity_inventory':stat(opp),'sequential':{'stats':stat(seq),'final_equity':round(eq,6),'net':round(eq-100,6),'trades':len(seq)},'by_side':group('side',opp),'opportunities':opp,'sequential_trades':seq}
 OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'memory':out['memory'],'opp':out['opportunity_inventory'],'seq':out['sequential'],'side':out['by_side']},indent=2))
if __name__=='__main__':main()
