import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v11_liquidity_transition_sep1_8 as r

START=datetime(2026,8,30,0,0,tzinfo=r.TEHRAN); END=datetime(2026,8,31,0,0,tzinfo=r.TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000); END_MS=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v11_2_aug30_smart_frequency_fixed.json')
MAX_TRADES=6; COOLDOWN=18; LOSS_LOCK=45
orig_liq=r.liquidity_transition; orig_pb=r.failed_pullback

def liq(i,m,f5,f15):
 s=orig_liq(i,m,f5,f15)
 if not s:return None
 setup,side,stop,ci,score,d,tp=s; d3=d.get('delta3',0);ed=d.get('event_delta',0);flip=abs(d.get('flip_delta',0));vol=d.get('volr',0)
 prior=d3<=-.08 if side=='LONG' else d3>=.08
 good=abs(ed)<=.10 or (abs(ed)<=.14 and flip>=.20 and vol>=1.25)
 if not(prior and good):return None
 return setup,side,stop,ci,score,d,tp

def pb(i,m,f5,f15):
 s=orig_pb(i,m,f5,f15)
 if not s:return None
 setup,side,stop,ci,score,d,tp=s;vol=d.get('volr',99);flip=abs(d.get('flip_delta',0))
 if vol>1.35 or (vol>1.10 and flip<.22):return None
 return setup,side,stop,ci,score,d,tp

def signal(i,m,f5,f15):return liq(i,m,f5,f15) or pb(i,m,f5,f15)

def main():
 raw=[];d=datetime(2026,8,29,tzinfo=timezone.utc)
 while d.date()<=datetime(2026,8,31,tzinfo=timezone.utc).date():raw+=r.b.get_daily(d);d+=timedelta(days=1)
 raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw);f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
 eq=100.;peak=100.;dd=0.;ts=[];last=-10**9;locks={'LONG':-10**9,'SHORT':-10**9};i=0
 while i<len(m)-3:
  if m[i]['t']<START_MS:i+=1;continue
  if m[i]['t']>=END_MS:break
  if len(ts)>=MAX_TRADES or i-last<COOLDOWN:i+=1;continue
  s=signal(i,m,f5,f15)
  if not s:i+=1;continue
  setup,side,stop,ci,score,diag,tp=s
  if ci<locks[side]:i+=1;continue
  ei=ci+1
  if ei>=len(m) or m[ei]['t']>=END_MS:break
  sim=r.q.simulate(m,ei,side,stop,eq,tp)
  if sim is None:i+=1;continue
  j,px,why,gross,cost,net,n,mfe=sim
  # reject any simulated exit spilling past Aug30 boundary rather than contaminating day test
  if m[j]['t']>=END_MS:break
  before=eq;eq+=net;peak=max(peak,eq);dd=max(dd,(peak-eq)/peak)
  et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(r.TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(r.TEHRAN)
  sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
  ts.append({'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'tp_pct':round(tp*100,3),'stop_pct':round(sp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_after':round(eq,6)})
  if net<0:locks[side]=j+LOSS_LOCK
  last=j;i=j+1
 w=[x for x in ts if x['net_pnl']>0];l=[x for x in ts if x['net_pnl']<=0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in l)
 out={'version':'DARA-v11.2-Smart-Frequency-Aug30-Fixed-Frozen','period_tehran':[START.isoformat(),END.isoformat()],'starting_equity':100.0,'rules':{'risk_per_stop':r.RISK,'modeled_roundtrip_cost':r.COST,'max_leverage':r.MAX_LEV,'max_trades':MAX_TRADES,'cooldown_minutes':COOLDOWN,'loss_lock_minutes':LOSS_LOCK,'profit_floor':'0.33% gross = 3x full modeled 0.11% roundtrip fee; adaptive target up to 0.55%'},'overall':{'final_equity':round(eq,6),'net_pnl':round(eq-100,6),'return_pct':round(eq-100,3),'trades':len(ts),'wins':len(w),'losses':len(l),'win_rate':round(100*len(w)/len(ts),1) if ts else None,'gross_pnl':round(sum(x['gross_pnl'] for x in ts),6),'modeled_costs':round(sum(x['cost'] for x in ts),6),'pf_net':round(gp/gl,2) if gl else (99.0 if gp else None),'max_dd_pct':round(dd*100,3)},'trades':ts,'notes':['Corrected harness fetches Aug29-Aug31 raw Binance data; previous wrapper inherited Sep-only loader and produced a false zero-trade result.','No Aug30 result was inspected before these v11.2 thresholds were frozen.','Historical full L2 unavailable; 1m taker-buy delta is aggressor-flow proxy.']}
 OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
