import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v11_liquidity_transition_sep1_8 as r
START=datetime(2026,8,30,0,0,tzinfo=r.TEHRAN);END=datetime(2026,8,31,0,0,tzinfo=r.TEHRAN)
S=int(START.astimezone(timezone.utc).timestamp()*1000);E=int(END.astimezone(timezone.utc).timestamp()*1000)
raw=[];d=datetime(2026,8,29,tzinfo=timezone.utc)
while d.date()<=datetime(2026,8,31,tzinfo=timezone.utc).date():raw+=r.b.get_daily(d);d+=timedelta(days=1)
raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw);f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
xs=[z for z in m if S<=z['t']<E]
reg={}; rows=[]
for i,z in enumerate(m):
 if not(S<=z['t']<E):continue
 a5=f5.get(r.b.last_closed(z['t'],5));a15=f15.get(r.b.last_closed(z['t'],15))
 if not a5 or not a15:continue
 k=f"{a5['regime']}/{a15['regime']}";reg[k]=reg.get(k,0)+1
 if i+15<len(m):
  fut=m[i+1:i+16]; up=max(q['h'] for q in fut)/z['c']-1;dn=1-min(q['l'] for q in fut)/z['c']
  if max(up,dn)>=.0033:
   rows.append({'time':datetime.fromtimestamp(z['t']/1000,timezone.utc).astimezone(r.TEHRAN).isoformat(),'price':z['c'],'delta':round(z['delta'],3),'volr':round(z['volr'],2),'reg5':a5['regime'],'reg15':a15['regime'],'up15m_pct':round(up*100,3),'down15m_pct':round(dn*100,3),'vwap_dist_pct':round((z['c']/z['vwap15']-1)*100,3)})
rows=sorted(rows,key=lambda x:max(x['up15m_pct'],x['down15m_pct']),reverse=True)[:40]
out={'open':xs[0]['o'],'close':xs[-1]['c'],'high':max(z['h'] for z in xs),'low':min(z['l'] for z in xs),'return_pct':round((xs[-1]['c']/xs[0]['o']-1)*100,3),'range_pct':round((max(z['h'] for z in xs)/min(z['l'] for z in xs)-1)*100,3),'regime_minutes':reg,'top_future_15m_moves':rows}
Path('data/diag_aug30_market_state.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
