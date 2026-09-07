import json, math, time, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timedelta, timezone

OUT=Path('data/research_state.json'); DAYS=60; FEE=26.0; H=8

def get(url):
 req=urllib.request.Request(url,headers={'User-Agent':'QuantBot-research/1.0','Accept':'application/json'})
 for k in range(5):
  try:
   with urllib.request.urlopen(req,timeout=25) as r:return json.loads(r.read())
  except Exception:
   if k==4: raise
   time.sleep(1+k)

def candles(prod):
 end=datetime.now(timezone.utc).replace(second=0,microsecond=0); start=end-timedelta(days=DAYS); d={}; cur=start
 while cur<end:
  ce=min(end,cur+timedelta(minutes=5*290)); q=urllib.parse.urlencode({'granularity':300,'start':cur.isoformat().replace('+00:00','Z'),'end':ce.isoformat().replace('+00:00','Z')})
  for x in get(f'https://api.exchange.coinbase.com/products/{prod}/candles?{q}'):
   if len(x)>=6:d[int(x[0])]=(float(x[4]),float(x[5]),float(x[3]),float(x[2]),float(x[1]))
  cur=ce; time.sleep(.1)
 return d

def agg(d):
 g={}
 for t,v in d.items():g.setdefault(t//900*900,[]).append((t,v))
 o={}
 for t,a in g.items():
  a=sorted(a); vs=[z[1] for z in a]
  if len(vs)>=2:o[t]={'c':vs[-1][0],'v':sum(z[1] for z in vs),'o':vs[0][2],'h':max(z[3] for z in vs),'l':min(z[4] for z in vs)}
 return o

def clamp(x):return max(-1,min(1,x))
def r(a,n):return a[-1]/a[-1-n]-1 if len(a)>n else 0
def z(a,n):
 x=a[-n:]; m=sum(x)/len(x); sd=(sum((q-m)**2 for q in x)/len(x))**.5 or 1; return (x[-1]-m)/sd
def ema(a,n):
 e=a[0]; al=2/(n+1)
 for q in a[1:]:e=al*q+(1-al)*e
 return e

def features(b,e):
 ts=sorted(set(b)&set(e)); bc=[];ec=[];vv=[]; out=[]
 for t in ts:
  x=b[t]; y=e[t];bc.append(x['c']);ec.append(y['c']);vv.append(x['v'])
  if len(bc)<100:continue
  trend=clamp((ema(bc[-80:],8)-ema(bc[-80:],21))/(bc[-1]*.002)); mom=clamp(r(bc,4)/.01); cross=clamp(r(ec,4)/.01); mean=-clamp(z(bc,20)/2)
  vol=abs(r(bc,1)); regime='trend' if abs(r(bc,16))>.012 else ('volatile' if vol>.006 else 'range')
  body=clamp((x['c']-x['o'])/max(x['h']-x['l'],x['c']*1e-8)); flow=body*clamp((x['v']/(sum(vv[-24:])/min(24,len(vv)))-1)/2) if len(vv)>5 else body
  out.append({'t':t,'p':x['c'],'trend':trend,'mom':mom,'cross':cross,'mean':mean,'body':body,'flow':flow,'regime':regime})
 return out

def score(f,stage):
 if stage==1:return .45*f['trend']+.30*f['cross']+.25*f['mom']
 if stage==2:return (.55*f['trend']+.30*f['cross']+.15*f['mom']) if f['regime']=='trend' else (.55*f['mean']+.25*f['cross']+.20*f['body'])
 if stage==3:return .35*f['trend']+.2*f['cross']+.15*f['mom']+.2*f['body']+.1*f['flow']
 if stage==4:return (.4*f['trend']+.25*f['cross']+.15*f['mom']+.15*f['body']+.05*f['flow']) * (1 if f['regime']!='volatile' else .55)
 return .3*f['trend']+.2*f['cross']+.1*f['mom']+.2*f['body']+.1*f['flow']+.1*f['mean']

def sim(fs,stage,thr,a,b):
 tr=[];i=a
 while i<min(b,len(fs)-H):
  s=score(fs[i],stage); side='LONG' if s>thr else ('SHORT' if s<-thr else None)
  if side:
   en=fs[i]['p']; ex=fs[i+H]['p']; gross=(ex/en-1)*1e4 if side=='LONG' else (en/ex-1)*1e4; tr.append(gross-FEE);i+=H
  else:i+=1
 return tr

def stat(x):
 if not x:return {'trades':0,'return_pct':0,'win_rate':None,'pf':None,'avg_net_bps':None,'max_dd_pct':0}
 eq=pk=1;dd=gp=gl=0
 for q in x:
  eq*=1+q/1e4;pk=max(pk,eq);dd=max(dd,(pk-eq)/pk);gp+=max(q,0);gl+=max(-q,0)
 return {'trades':len(x),'return_pct':round((eq-1)*100,3),'win_rate':round(100*sum(q>0 for q in x)/len(x),1),'pf':round(gp/gl,2) if gl else 99,'avg_net_bps':round(sum(x)/len(x),2),'max_dd_pct':round(dd*100,3)}

def main():
 fs=features(agg(candles('BTC-USD')),agg(candles('ETH-USD'))); n=len(fs); stages=[]
 for st in range(1,6):
  train=int(n*.55); val=int(n*.70); best=None
  for th in (.25,.35,.45,.55,.65):
   sv=stat(sim(fs,st,th,train,val)); key=((sv['pf'] or 0),sv['avg_net_bps'] or -999,sv['trades'])
   if sv['trades']>=10 and (best is None or key>best[0]):best=(key,th,sv)
  if not best:best=((0,0,0),.45,stat(sim(fs,st,.45,train,val)))
  th=best[1]; oos=stat(sim(fs,st,th,val,n)); folds=[]
  for aa,bb in ((.55,.65),(.65,.75),(.75,.85)):
   folds.append(stat(sim(fs,st,th,int(n*aa),int(n*bb))))
  pos=sum(x['trades']>=5 and x['return_pct']>0 and (x['pf'] or 0)>1 for x in folds)
  passed=oos['trades']>=20 and oos['return_pct']>0 and (oos['pf'] or 0)>=1.25 and (oos['avg_net_bps'] or -1)>0 and oos['max_dd_pct']<5 and pos>=2
  stages.append({'stage':st,'hypothesis':['cost-aware trend/cross-market','regime-switch trend/mean-reversion','direction plus entry-timing proxy','volatility-regime calibration','diversified ensemble'][st-1],'threshold':th,'validation':best[2],'oos':oos,'walk_forward':folds,'positive_folds':pos,'passed':passed})
 state={'version':'v1.4-rd','updated_at':datetime.now(timezone.utc).isoformat(),'completed_stages':5,'data_rows':n,'friction_bps':FEE,'stages':stages,'robust_candidate':next((x for x in stages if x['passed']),None),'status':'ROBUST_FOUND' if any(x['passed'] for x in stages) else 'CONTINUE','limitations':['public candle/volume data; no historical L2','derivatives context deferred to stages 6-10']}
 OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(state,indent=2));print(json.dumps(state))
if __name__=='__main__':main()
