import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v10_microstructure_sep1 as v

OUT=Path('data/dara_v10_1_microstructure_sep1.json')

# v10.1 changes ONE structural mistake from v10.0:
# recent-15m "room" is not a valid hard gate for continuation because persistent flow may cross that edge.
# Replace it with volatility capacity from the allowed 5m/15m timeframes. All risk/cost/exit rules stay frozen.

def value_pullback(i,m,f5,f15):
    if i<20:return None
    x=m[i]; a5=f5.get(b.last_closed(x['t'],5)); a15=f15.get(b.last_closed(x['t'],15))
    if not a5 or not a15:return None
    prev=m[i-4:i]
    if len(prev)<4:return None
    minute=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(v.TEHRAN).minute
    clock_bonus=(minute%5 in (0,1))
    capacity=(a15['atrp']>=0.0022 and a5['atrp']>=0.0008)

    if a15['regime']=='UP' and a5['regime']=='UP':
        fs=v.flow_state(i,m,'LONG'); value=(a5['ema21']+x['vwap15'])/2
        touched=min(z['l'] for z in prev)<=value*1.0012
        held=min(z['c'] for z in prev)>=a15['ema21']*0.9975
        trigger=x['c']>max(z['h'] for z in m[i-2:i])*1.00005 and x['c']>x['o']
        extension=(x['c']-value)/x['c']
        score=sum([touched,held,trigger,x['delta']>=0.12,x['volr']>=1.10,fs['avg_delta']>=0.08,fs['persistence']>=2,fs['efficiency']>=0.30,extension<=0.0035,capacity,clock_bonus])
        if score>=9 and touched and trigger and capacity:
            return ('VALUE_PERSISTENT','LONG',min(z['l'] for z in prev)*0.9996,i,score,{**fs,'capacity':capacity,'atr5_pct':round(a5['atrp']*100,3),'atr15_pct':round(a15['atrp']*100,3)})

    if a15['regime']=='DOWN' and a5['regime']=='DOWN':
        fs=v.flow_state(i,m,'SHORT'); value=(a5['ema21']+x['vwap15'])/2
        touched=max(z['h'] for z in prev)>=value*0.9988
        held=max(z['c'] for z in prev)<=a15['ema21']*1.0025
        trigger=x['c']<min(z['l'] for z in m[i-2:i])*0.99995 and x['c']<x['o']
        extension=(value-x['c'])/x['c']
        score=sum([touched,held,trigger,x['delta']<=-0.12,x['volr']>=1.10,fs['avg_delta']>=0.08,fs['persistence']>=2,fs['efficiency']>=0.30,extension<=0.0035,capacity,clock_bonus])
        if score>=9 and touched and trigger and capacity:
            return ('VALUE_PERSISTENT','SHORT',max(z['h'] for z in prev)*1.0004,i,score,{**fs,'capacity':capacity,'atr5_pct':round(a5['atrp']*100,3),'atr15_pct':round(a15['atrp']*100,3)})
    return None


def signal(i,m,f5,f15):
    q=v.sweep_absorption(i,m,f15)
    if q:return q
    return value_pullback(i,m,f5,f15)


def summarize(ts,start,end,peak,maxdd):return v.summarize(ts,start,end,peak,maxdd)


def main():
    raw=[]
    for d in [datetime(2026,8,31,tzinfo=timezone.utc)+timedelta(days=k) for k in range(2)]:raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t']);m=v.enrich(raw);f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)};f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)}
    equity=100.;peak=100.;maxdd=0.;trades=[];last=-10**9;locks={'LONG':-10**9,'SHORT':-10**9};daynet=0.;i=0
    while i<len(m)-3:
        x=m[i]
        if x['t']<v.START_MS:i+=1;continue
        if x['t']>=v.END_MS:break
        if len(trades)>=v.MAX_TRADES or daynet<=v.DAILY_STOP*100 or i-last<v.COOLDOWN:i+=1;continue
        s=signal(i,m,f5,f15)
        if not s:i+=1;continue
        setup,side,stop,ci,score,diag=s
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m) or m[ei]['t']>=v.END_MS:break
        sim=v.simulate(m,ei,side,stop,equity)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n=sim;before=equity;equity+=net;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak);daynet+=net
        et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(v.TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(v.TEHRAN);sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
        tr={'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'diagnostics':diag,'structural_stop_pct':round(sp*100,3),'reason':why,'notional':round(n,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)};trades.append(tr)
        if net<0:locks[side]=j+v.LOSS_LOCK
        last=j;i=j+1
    overall=summarize(trades,100.,equity,peak,maxdd);by={}
    for name in ['VALUE_PERSISTENT','SWEEP_ABSORPTION']:
        ts=[t for t in trades if t['setup']==name];w=[t for t in ts if t['net_pnl']>0];l=[t for t in ts if t['net_pnl']<=0];gp=sum(t['net_pnl'] for t in w);gl=-sum(t['net_pnl'] for t in l);by[name]={'trades':len(ts),'wins':len(w),'losses':len(l),'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None)}
    payload={'version':'DARA-v10.1-Microstructure-CapacityFix','period_tehran':[v.START.isoformat(),v.END.isoformat()],'starting_equity':100.0,'change_from_v10':'Replace contradictory prior-15m room hard gate with 5m/15m ATR volatility-capacity gate; no other conceptual change.','timeframes':['1m trigger/flow','5m location/capacity','15m regime/capacity'],'risk_cost_exit':['0.25% max stop loss incl 0.11% friction','max 3x notional','TP +0.55% on 40%, 60% runner','max 4 trades/day'],'methodology_notes':['Sep1 is development data. Historical full L2 unavailable; 1m taker-buy volume is aggressor-flow proxy.','No maker-fee advantage is credited.'],'overall':overall,'by_setup':by,'trades':trades};OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(payload,indent=2));print(json.dumps(overall,indent=2));print(json.dumps(by,indent=2))
if __name__=='__main__':main()
