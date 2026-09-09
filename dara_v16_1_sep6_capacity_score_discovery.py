import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_discovery1000_sep5_learned as b
import dara_v11_liquidity_transition_sep1_8 as r

TEHRAN=b.TEHRAN
START=datetime(2026,9,6,0,0,tzinfo=TEHRAN);END=datetime(2026,9,7,0,0,tzinfo=TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000);E=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v16_1_sep6_capacity_score_discovery.json')
b.RISK=.0002;b.COST=.0011;b.MAX_LEV=3.0;b.MIN_STOP=.0016;b.MAX_STOP=.0032

# Development pass after v16 exposed over-filtering. Old failed engines remain quarantined.

def ctx(i,m,f5,f15):
 z=m[i];a5,a15=b.ctx(z,f5,f15)
 if not a5 or not a15:return None
 st=b.state(i,m,f5,f15)[0];vd=z['c']/z['vwap15']-1
 c5=4*a5['atrp'];c15=2.5*a15['atrp'];cs=.55*c5+.45*c15
 return z,a5,a15,st,vd,c5,c15,cs

def tradable(c5,c15,cs):
 # Soften the binary gate: weighted capacity must be near the 3x-fee floor,
 # and at least one timeframe must independently support 0.33% movement.
 return cs>=.0028 and max(c5,c15)>=.0033

def make(setup,side,i,m,f5,f15,score,diag,raw=None):
 c=ctx(i,m,f5,f15)
 if not c:return None
 z,a5,a15,st,vd,c5,c15,cs=c
 if not tradable(c5,c15,cs):return None
 return b.make(setup,side,i,m,f5,f15,score,{**diag,'capacity_score_pct':cs*100,'cap5_pct':c5*100,'cap15_pct':c15*100},raw)

def impulse_follow(i,m,f5,f15):
 if i<6:return None
 c=ctx(i,m,f5,f15)
 if not c:return None
 z,a5,a15,st,vd,c5,c15,cs=c;p=m[i-1]
 r3=z['c']/m[i-3]['c']-1;d3=sum(x['delta'] for x in m[i-2:i+1])/3;vol=z['volr'];pos=(z['c']-z['l'])/max(z['h']-z['l'],1e-9)
 if st=='TREND_UP' and .00055<=r3<=.0032 and d3>.06 and .00035<=vd<=.0038 and vol<4.5 and pos>.52 and z['c']>=p['c']*.9996:
  return make('IMPULSE_FOLLOW','LONG',i,m,f5,f15,10+int(d3>.20)+int(cs>=.0033),{'r3_pct':r3*100,'delta3':d3,'close_pos':pos})
 if st=='TREND_DOWN' and -.0035<=r3<=-.0008 and d3<-.12 and -.004<=vd<=-.00055 and 1.2<=vol<4.2 and pos<.48 and z['c']<=p['c']*1.0003:
  return make('IMPULSE_FOLLOW','SHORT',i,m,f5,f15,12+int(d3<-.25)+int(cs>=.0033),{'r3_pct':r3*100,'delta3':d3,'close_pos':pos})
 return None

def staged_break(i,m,f5,f15):
 if i<8:return None
 c=ctx(i,m,f5,f15)
 if not c:return None
 z,a5,a15,st,vd,c5,c15,cs=c;p=m[i-1];old=m[i-7:i-1];hi=max(x['h'] for x in old);lo=min(x['l'] for x in old);vol=z['volr']
 # Previous minute stages at the edge; current minute breaks and closes with the move. This is not raw one-bar break.
 if st in ('TREND_UP','TRANSITION_UP') and p['c']>=hi*.9995 and z['c']>hi and z['delta']>.06 and .7<=vol<4 and vd>=.0004:
  ext=z['c']/hi-1
  if .0002<=ext<=.0025:return make('STAGED_BREAK','LONG',i,m,f5,f15,11+int(st=='TREND_UP')+int(cs>=.0033),{'extension_pct':ext*100,'edge':hi},lo)
 if st in ('TREND_DOWN','TRANSITION_DOWN') and p['c']<=lo*1.0005 and z['c']<lo and z['delta']<-.12 and 1.2<=vol<4 and vd<=-.0006:
  ext=lo/z['c']-1
  if .00025<=ext<=.0028:return make('STAGED_BREAK','SHORT',i,m,f5,f15,13+int(st=='TREND_DOWN')+int(cs>=.0033),{'extension_pct':ext*100,'edge':lo},hi)
 return None

def pause_reaccel(i,m,f5,f15):
 if i<7:return None
 c=ctx(i,m,f5,f15)
 if not c:return None
 z,a5,a15,st,vd,c5,c15,cs=c;p=m[i-1];p2=m[i-2];vol=z['volr']
 # Two-step pause then renewed directional close; stricter short asymmetry retained.
 if st in ('TREND_UP','UP_PAUSE') and p['c']<=p2['c']*1.0003 and p['delta']<.08 and z['c']>p['h'] and z['delta']>.10 and .0004<=vd<=.0035 and vol<4:
  return make('PAUSE_REACCEL','LONG',i,m,f5,f15,12+int(st=='TREND_UP')+int(cs>=.0033),{'pause_delta':p['delta']},min(p['l'],p2['l'])*.9997)
 if st in ('TREND_DOWN','DOWN_PAUSE') and p['c']>=p2['c']*.9997 and p['delta']>-.06 and z['c']<p['l'] and z['delta']<-.16 and -.0038<=vd<=-.0007 and 1.3<=vol<4:
  return make('PAUSE_REACCEL','SHORT',i,m,f5,f15,14+int(st=='TREND_DOWN')+int(cs>=.0033),{'pause_delta':p['delta']},max(p['h'],p2['h'])*1.0003)
 return None

def transition_extension(i,m,f5,f15):
 if i<9:return None
 c=ctx(i,m,f5,f15)
 if not c:return None
 z,a5,a15,st,vd,c5,c15,cs=c;p=m[i-1];p2=m[i-2];d2=(p['delta']+z['delta'])/2;vol=z['volr']
 if st=='TRANSITION_UP' and p['c']>p2['h'] and z['c']>p['c'] and d2>.08 and .8<=vol<4 and vd>=.0004:
  ext=z['c']/p['c']-1
  if ext>=.00018:return make('TRANSITION_EXTENSION','LONG',i,m,f5,f15,12+int(cs>=.0033),{'extension_pct':ext*100,'delta2':d2})
 if st=='TRANSITION_DOWN' and p['c']<p2['l'] and z['c']<p['c'] and d2<-.14 and 1.2<=vol<4 and vd<=-.0006:
  ext=p['c']/z['c']-1
  if ext>=.00022:return make('TRANSITION_EXTENSION','SHORT',i,m,f5,f15,14+int(cs>=.0033),{'extension_pct':ext*100,'delta2':d2})
 return None

def candidates(i,m,f5,f15):
 out=[]
 for fn in (transition_extension,pause_reaccel,staged_break,impulse_follow):
  try:
   x=fn(i,m,f5,f15)
   if x:out.append(x)
  except Exception:pass
 uniq={}
 for x in out:
  k=(x[1],x[3]);
  if k not in uniq or x[4]>uniq[k][4]:uniq[k]=x
 return sorted(uniq.values(),key=lambda x:x[4],reverse=True)

def stat(xs):
 if not xs:return {'n':0,'valid_positive':0,'negative_or_invalid':0,'valid_wr':None,'net_positive':0,'gross':0,'cost':0,'net':0,'pf_net':None,'mfe':None,'mae':None}
 val=[x for x in xs if x['reason'] in ('TP','3FEE_FLOOR')];np=[x for x in xs if x['net_pnl']>0];gp=sum(x['net_pnl'] for x in np);gl=-sum(x['net_pnl'] for x in xs if x['net_pnl']<=0)
 return {'n':len(xs),'valid_positive':len(val),'negative_or_invalid':len(xs)-len(val),'valid_wr':round(100*len(val)/len(xs),1),'net_positive':len(np),'gross':round(sum(x['gross_pnl'] for x in xs),6),'cost':round(sum(x['cost'] for x in xs),6),'net':round(sum(x['net_pnl'] for x in xs),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None),'mfe':round(sum(x['mfe_pct'] for x in xs)/len(xs),3),'mae':round(sum(x['mae_pct'] for x in xs)/len(xs),3)}

def main():
 raw=[];dd=datetime(2026,9,5,tzinfo=timezone.utc)
 while dd.date()<=datetime(2026,9,7,tzinfo=timezone.utc).date():raw+=r.b.get_daily(dd);dd+=timedelta(days=1)
 raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw);f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
 opp=[]
 for i,z in enumerate(m):
  if z['t']<S or z['t']>=E or i<12 or i+1>=len(m):continue
  cs=candidates(i,m,f5,f15)
  if not cs:continue
  x=cs[0];sim=b.simulate(m,x[3],x[1],x[2],x[6],100.)
  if not sim or m[sim[0]]['t']>=E:continue
  rr=b.row(x,sim,m);rr['state']=rr['diagnostics'].get('state');h=rr['diagnostics'].get('hour',0);rr['hour4']=f'{(h//4)*4:02d}-{(h//4)*4+3:02d}';opp.append(rr)
 seq=[];eq=100.;i=0
 while i<len(m)-2:
  if m[i]['t']<S:i+=1;continue
  if m[i]['t']>=E:break
  cs=candidates(i,m,f5,f15)
  if not cs:i+=1;continue
  x=cs[0];sim=b.simulate(m,x[3],x[1],x[2],x[6],eq)
  if not sim or m[sim[0]]['t']>=E:break
  rr=b.row(x,sim,m,eq);eq=rr['equity_after'];rr['n']=len(seq)+1;seq.append(rr);i=sim[0]+1
 def group(k,xs):
  g={}
  for x in xs:g.setdefault(x[k],[]).append(x)
  return {str(a):stat(v) for a,v in g.items()}
 fail={'NO_FOLLOW':[],'WEAK_PROGRESS':[],'FEE_INSUFFICIENT':[],'VALID_POSITIVE':[],'OTHER':[]}
 for x in opp:
  if x['reason'] in ('TP','3FEE_FLOOR'):fail['VALID_POSITIVE'].append(x)
  elif x['mfe_pct']<.05:fail['NO_FOLLOW'].append(x)
  elif x['mfe_pct']<.12:fail['WEAK_PROGRESS'].append(x)
  elif x['mfe_pct']<.33:fail['FEE_INSUFFICIENT'].append(x)
  else:fail['OTHER'].append(x)
 out={'version':'DARA-v16.1-Sep6-CapacityScore-Development','period_tehran':[START.isoformat(),END.isoformat()],'note':'Development replay after frozen v16 exposed over-filtering; do not treat as out-of-sample evidence. No diagnostic fallback or previously quarantined setup was reintroduced.','rules':{'risk':b.RISK,'fee':b.COST,'profit_floor_pct':.33,'capacity_score':'0.55*(4x ATR5)+0.45*(2.5x ATR15) >= 0.28%, with at least one leg >=0.33%','quarantined':['STATE_HYPOTHESIS','raw sweep','raw pullback','old flow-eff','raw compression','accepted-break v15','retest-renewal v15']},'opportunity_inventory':stat(opp),'sequential':{'stats':stat(seq),'final_equity':round(eq,6),'net':round(eq-100,6),'trades':len(seq)},'by_setup':group('setup',opp),'by_side':group('side',opp),'by_state':group('state',opp),'by_4h':group('hour4',opp),'failure_taxonomy':{k:stat(v) for k,v in fail.items()},'opportunities':opp,'sequential_trades':seq}
 OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'opp':out['opportunity_inventory'],'seq':out['sequential'],'setup':out['by_setup'],'fail':out['failure_taxonomy']},indent=2))
if __name__=='__main__':main()
