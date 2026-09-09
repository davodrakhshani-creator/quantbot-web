import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v10_microstructure_sep1 as v

OUT=Path('data/dara_v10_2_impact_absorption_sep1.json')

# DARA v10.2: distinguish informed/persistent flow from absorption/exhaustion.
# Timeframes remain strictly 1m/5m/15m. Risk/cost/exit remain frozen.

def impact_state(i,m,side):
    fs=v.flow_state(i,m,side)
    if not fs:return None
    avg=max(fs['avg_delta'],0.0)
    impact_ratio=fs['progress']/max(avg,0.05)
    informed=(fs['avg_delta']>=0.12 and fs['persistence']>=2 and fs['progress']>=0.0010 and fs['efficiency']>=0.45 and impact_ratio>=0.0025)
    absorbed=(fs['avg_delta']>=0.16 and fs['persistence']>=2 and fs['progress']<=0.00075 and impact_ratio<0.0025)
    return {**fs,'impact_ratio':impact_ratio,'informed':informed,'absorbed':absorbed}


def capacity_ok(a5,a15):
    # Expected movement capacity using only 5m/15m realized ATR.
    return a15['atrp']>=0.0022 and a5['atrp']>=0.0008


def value_continuation(i,m,f5,f15):
    if i<20:return None
    x=m[i]; a5=f5.get(b.last_closed(x['t'],5)); a15=f15.get(b.last_closed(x['t'],15))
    if not a5 or not a15 or not capacity_ok(a5,a15):return None
    prev=m[i-4:i]
    if len(prev)<4:return None
    minute=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(v.TEHRAN).minute
    clock_bonus=(minute%5 in (0,1))

    if a15['regime']=='UP' and a5['regime']=='UP':
        st=impact_state(i,m,'LONG'); value=(a5['ema21']+x['vwap15'])/2
        touched=min(z['l'] for z in prev)<=value*1.0012
        held=min(z['c'] for z in prev)>=a15['ema21']*0.9975
        trigger=x['c']>max(z['h'] for z in m[i-2:i])*1.00005 and x['c']>x['o'] and x['delta']>=0.10
        extension=(x['c']-value)/x['c']
        score=sum([touched,held,trigger,x['volr']>=1.0,st['informed'],extension<=0.0035,clock_bonus])
        if touched and held and trigger and st['informed'] and extension<=0.0035 and score>=5:
            stop=min(z['l'] for z in prev)*0.9996
            return ('VALUE_INFORMED','LONG',stop,i,score,{**st,'atr5_pct':round(a5['atrp']*100,3),'atr15_pct':round(a15['atrp']*100,3)})

    if a15['regime']=='DOWN' and a5['regime']=='DOWN':
        st=impact_state(i,m,'SHORT'); value=(a5['ema21']+x['vwap15'])/2
        touched=max(z['h'] for z in prev)>=value*0.9988
        held=max(z['c'] for z in prev)<=a15['ema21']*1.0025
        trigger=x['c']<min(z['l'] for z in m[i-2:i])*0.99995 and x['c']<x['o'] and x['delta']<=-0.10
        extension=(value-x['c'])/x['c']
        score=sum([touched,held,trigger,x['volr']>=1.0,st['informed'],extension<=0.0035,clock_bonus])
        if touched and held and trigger and st['informed'] and extension<=0.0035 and score>=5:
            stop=max(z['h'] for z in prev)*1.0004
            return ('VALUE_INFORMED','SHORT',stop,i,score,{**st,'atr5_pct':round(a5['atrp']*100,3),'atr15_pct':round(a15['atrp']*100,3)})
    return None


def sweep_absorption(i,m,f5,f15):
    if i<20 or i+1>=len(m):return None
    x=m[i]; y=m[i+1]; a5=f5.get(b.last_closed(x['t'],5)); a15=f15.get(b.last_closed(x['t'],15))
    if not a5 or not a15 or not capacity_ok(a5,a15):return None
    rng=max(x['h']-x['l'],1e-9); body=abs(x['c']-x['o']); upper=x['h']-max(x['o'],x['c']); lower=min(x['o'],x['c'])-x['l']
    minute=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(v.TEHRAN).minute
    post_clock=(minute%5 in (0,4))

    # Sweep low with aggressive selling that fails to move price efficiently, then delta flips positive.
    if a15['regime']!='DOWN':
        pen=(x['prev15l']-x['l'])/x['prev15l'] if x['l']<x['prev15l'] else 0
        st=impact_state(i,m,'SHORT')
        event=pen>=0.0005 and x['c']>x['prev15l'] and lower>=max(body,0.30*rng) and x['delta']<=-0.10 and x['volr']>=1.20 and st['absorbed']
        hold=y['l']>=x['l']*0.9998 and y['c']>x['prev15l'] and y['delta']>=0.08 and y['c']>=y['o'] and y['volr']>=0.70
        score=sum([event,hold,pen>=0.0008,x['volr']>=1.5,lower>=0.45*rng,y['delta']>=0.15,post_clock])
        if event and hold and score>=4:
            return ('SWEEP_ABSORPTION','LONG',x['l']*0.9995,i+1,score,{'event_delta':x['delta'],'flip_delta':y['delta'],**st})

    # Sweep high with aggressive buying that fails to move price efficiently, then delta flips negative.
    if a15['regime']!='UP':
        pen=(x['h']-x['prev15h'])/x['prev15h'] if x['h']>x['prev15h'] else 0
        st=impact_state(i,m,'LONG')
        event=pen>=0.0005 and x['c']<x['prev15h'] and upper>=max(body,0.30*rng) and x['delta']>=0.10 and x['volr']>=1.20 and st['absorbed']
        hold=y['h']<=x['h']*1.0002 and y['c']<x['prev15h'] and y['delta']<=-0.08 and y['c']<=y['o'] and y['volr']>=0.70
        score=sum([event,hold,pen>=0.0008,x['volr']>=1.5,upper>=0.45*rng,y['delta']<=-0.15,post_clock])
        if event and hold and score>=4:
            return ('SWEEP_ABSORPTION','SHORT',x['h']*1.0005,i+1,score,{'event_delta':x['delta'],'flip_delta':y['delta'],**st})
    return None


def signal(i,m,f5,f15):
    q=sweep_absorption(i,m,f5,f15)
    if q:return q
    return value_continuation(i,m,f5,f15)


def main():
    raw=[]
    for d in [datetime(2026,8,31,tzinfo=timezone.utc)+timedelta(days=k) for k in range(2)]:raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m=v.enrich(raw); f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)}; f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)}
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
    overall=v.summarize(trades,100.,equity,peak,maxdd);by={}
    for name in ['VALUE_INFORMED','SWEEP_ABSORPTION']:
        ts=[t for t in trades if t['setup']==name];w=[t for t in ts if t['net_pnl']>0];l=[t for t in ts if t['net_pnl']<=0];gp=sum(t['net_pnl'] for t in w);gl=-sum(t['net_pnl'] for t in l);by[name]={'trades':len(ts),'wins':len(w),'losses':len(l),'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None)}
    payload={'version':'DARA-v10.2-ImpactAbsorption','period_tehran':[v.START.isoformat(),v.END.isoformat()],'starting_equity':100.0,'change_from_v10_1':'Persistent delta is no longer sufficient. Continuation requires measurable 3m price impact; high aggressive flow with weak price impact is classified as absorption and continuation is blocked. Sweep reversals require absorption plus next-minute delta flip.','timeframes':['1m flow/impact trigger','5m value/capacity','15m regime/capacity'],'risk_cost_exit':['0.25% max stop loss incl 0.11% friction','max 3x notional','TP +0.55% on 40%, 60% runner','max 4 trades/day'],'methodology_notes':['Sep1 is development data and v10.2 was designed after inspecting v10.1 Sep1 losses; this is not out-of-sample proof.','Historical full L2 unavailable; 1m taker-buy volume is aggressor-flow proxy.','No maker-fee advantage credited.'],'overall':overall,'by_setup':by,'trades':trades};OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(payload,indent=2));print(json.dumps(overall,indent=2));print(json.dumps(by,indent=2))

if __name__=='__main__':main()
