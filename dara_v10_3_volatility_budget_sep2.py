import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v10_microstructure_sep1 as v
import dara_v10_2_impact_absorption_sep1 as x

START=datetime(2026,9,2,0,0,tzinfo=v.TEHRAN)
END=datetime(2026,9,3,0,0,tzinfo=v.TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000)
END_MS=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v10_3_volatility_budget_sep2.json')

# DARA v10.3 is frozen from Sep1. No strategy parameter is changed here.
def volatility_budget(a5,a15):
    return (2.0*a15['atrp'] >= v.TP) and (3.5*a5['atrp'] >= v.TP)

x.capacity_ok = volatility_budget


def main():
    raw=[]
    # Warmup only; trading window is exactly Sep2 Tehran.
    for d in [datetime(2026,8,31,tzinfo=timezone.utc)+timedelta(days=k) for k in range(3)]:
        raw+=b.get_daily(d)
    raw=sorted({r['t']:r for r in raw}.values(),key=lambda r:r['t'])
    m=v.enrich(raw)
    f5={r['t']:r for r in b.features(b.aggregate(raw,5),5)}
    f15={r['t']:r for r in b.features(b.aggregate(raw,15),15)}
    equity=100.;peak=100.;maxdd=0.;trades=[];last=-10**9;locks={'LONG':-10**9,'SHORT':-10**9};daynet=0.;i=0
    while i<len(m)-3:
        bar=m[i]
        if bar['t']<START_MS:i+=1;continue
        if bar['t']>=END_MS:break
        if len(trades)>=v.MAX_TRADES or daynet<=v.DAILY_STOP*100 or i-last<v.COOLDOWN:i+=1;continue
        s=x.signal(i,m,f5,f15)
        if not s:i+=1;continue
        setup,side,stop,ci,score,diag=s
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m) or m[ei]['t']>=END_MS:break
        sim=v.simulate(m,ei,side,stop,equity)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n=sim
        # Do not let a trade extend beyond the requested Sep2 Tehran test window.
        if m[j]['t']>=END_MS:
            j=max(ei,min(j,next((k-1 for k in range(ei,len(m)) if m[k]['t']>=END_MS),j)))
        before=equity;equity+=net;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak);daynet+=net
        a5=f5.get(b.last_closed(m[ci]['t'],5));a15=f15.get(b.last_closed(m[ci]['t'],15))
        et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(v.TEHRAN)
        xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(v.TEHRAN)
        sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
        diag={**diag,'atr5_pct':round(a5['atrp']*100,3),'atr15_pct':round(a15['atrp']*100,3),'target_to_atr15':round(v.TP/a15['atrp'],2),'target_to_atr5':round(v.TP/a5['atrp'],2)}
        tr={'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'diagnostics':diag,'structural_stop_pct':round(sp*100,3),'reason':why,'notional':round(n,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)}
        trades.append(tr)
        if net<0:locks[side]=j+v.LOSS_LOCK
        last=j;i=j+1
    overall=v.summarize(trades,100.,equity,peak,maxdd);by={}
    for name in ['VALUE_INFORMED','SWEEP_ABSORPTION']:
        ts=[t for t in trades if t['setup']==name];w=[t for t in ts if t['net_pnl']>0];l=[t for t in ts if t['net_pnl']<=0];gp=sum(t['net_pnl'] for t in w);gl=-sum(t['net_pnl'] for t in l)
        by[name]={'trades':len(ts),'wins':len(w),'losses':len(l),'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None)}
    payload={'version':'DARA-v10.3-FROZEN','period_tehran':[START.isoformat(),END.isoformat()],'starting_equity':100.0,'frozen_from':'Sep1 v10.3; zero strategy changes for Sep2','timeframes':['1m flow/impact','5m value + ATR budget','15m regime + ATR budget'],'risk_cost_exit':['0.25% max stop loss incl 0.11% friction','max 3x notional','TP +0.55% on 40%, 60% runner','max 4 trades/day'],'methodology_notes':['Sep2 is the first forward day after freezing v10.3 from Sep1.','Historical full L2 unavailable; 1m taker-buy volume is aggressor-flow proxy.','No maker-fee advantage credited.'],'overall':overall,'by_setup':by,'trades':trades}
    OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(payload,indent=2));print(json.dumps(overall,indent=2));print(json.dumps(by,indent=2))

if __name__=='__main__':main()
