import json
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_v1_sep1 as b

TEHRAN=b.TEHRAN
START=datetime(2026,9,1,0,0,tzinfo=TEHRAN)
END=datetime(2026,9,2,0,0,tzinfo=TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000)
END_MS=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v10_microstructure_sep1.json')

# Frozen before the Sep-1 run.
COST=0.0011          # same conservative all-in round-trip friction used in earlier DARA tests
RISK=0.0025          # max account loss at structural stop including modeled friction
MAX_LEV=3.0
TP=0.0055
FIRST=0.40
RUNNER=0.60
MIN_STOP=0.0014
MAX_STOP=0.0030
MAX_TRADES=4
DAILY_STOP=-0.010
COOLDOWN=30
LOSS_LOCK=90
MAX_HOLD=180
TRAIL_BARS=3


def rolling_vwap(rows,n=15):
    q=deque(); pv=0.0; vv=0.0; out=[]
    for x in rows:
        p=(x['h']+x['l']+x['c'])/3.0; v=x['v']
        q.append((p*v,v)); pv+=p*v; vv+=v
        if len(q)>n:
            a,bv=q.popleft(); pv-=a; vv-=bv
        out.append(pv/max(vv,1e-12))
    return out


def enrich(rows):
    # Only 1m information is added here; no 1h/4h regime is used anywhere in v10.
    m=b.minute_features(rows)
    vw15=rolling_vwap(rows,15)
    for i,x in enumerate(m):
        sell=max(x['v']-x['tb'],1e-12)
        x['delta']=(x['tb']-sell)/max(x['v'],1e-12)
        x['vwap15']=vw15[i]
        p15=m[max(0,i-15):i]
        x['prev15h']=max((z['h'] for z in p15),default=x['h'])
        x['prev15l']=min((z['l'] for z in p15),default=x['l'])
        x['volr']=x['v']/max(x['medv'],1e-12)
    return m


def flow_state(i,m,side):
    if i<3:return None
    w=m[i-2:i+1]
    sign=1 if side=='LONG' else -1
    ds=[sign*z['delta'] for z in w]
    rets=[sign*(w[k]['c']/w[k-1]['c']-1) for k in range(1,len(w))]
    avg_delta=sum(ds)/len(ds)
    persistence=sum(d>0.05 for d in ds)
    signed_progress=sum(rets)
    total_motion=sum(abs(w[k]['c']/w[k-1]['c']-1) for k in range(1,len(w)))
    efficiency=signed_progress/max(total_motion,1e-9)
    return {'avg_delta':avg_delta,'persistence':persistence,'progress':signed_progress,'efficiency':efficiency}


def value_pullback(i,m,f5,f15):
    if i<20:return None
    x=m[i]; a5=f5.get(b.last_closed(x['t'],5)); a15=f15.get(b.last_closed(x['t'],15))
    if not a5 or not a15:return None
    prev=m[i-4:i]
    if len(prev)<4:return None
    minute=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(TEHRAN).minute
    clock_bonus=(minute%5 in (0,1))

    # Continuation engine: 15m regime -> 5m value location -> 1m persistent/informed flow trigger.
    if a15['regime']=='UP' and a5['regime']=='UP':
        fs=flow_state(i,m,'LONG')
        value=(a5['ema21']+x['vwap15'])/2
        touched=min(z['l'] for z in prev)<=value*1.0012
        held=min(z['c'] for z in prev)>=a15['ema21']*0.9975
        trigger=x['c']>max(z['h'] for z in m[i-2:i])*1.00005 and x['c']>x['o']
        extension=(x['c']-value)/x['c']
        room=(x['prev15h']-x['c'])/x['c']
        score=sum([touched,held,trigger,x['delta']>=0.12,x['volr']>=1.10,fs['avg_delta']>=0.08,fs['persistence']>=2,fs['efficiency']>=0.30,extension<=0.0035,room>=0.0038,clock_bonus])
        if score>=9 and touched and trigger and room>=0.0038:
            stop=min(z['l'] for z in prev)*0.9996
            return ('VALUE_PERSISTENT','LONG',stop,i,score,fs)

    if a15['regime']=='DOWN' and a5['regime']=='DOWN':
        fs=flow_state(i,m,'SHORT')
        value=(a5['ema21']+x['vwap15'])/2
        touched=max(z['h'] for z in prev)>=value*0.9988
        held=max(z['c'] for z in prev)<=a15['ema21']*1.0025
        trigger=x['c']<min(z['l'] for z in m[i-2:i])*0.99995 and x['c']<x['o']
        extension=(value-x['c'])/x['c']
        room=(x['c']-x['prev15l'])/x['c']
        score=sum([touched,held,trigger,x['delta']<=-0.12,x['volr']>=1.10,fs['avg_delta']>=0.08,fs['persistence']>=2,fs['efficiency']>=0.30,extension<=0.0035,room>=0.0038,clock_bonus])
        if score>=9 and touched and trigger and room>=0.0038:
            stop=max(z['h'] for z in prev)*1.0004
            return ('VALUE_PERSISTENT','SHORT',stop,i,score,fs)
    return None


def sweep_absorption(i,m,f15):
    if i<20 or i+1>=len(m):return None
    x=m[i]; y=m[i+1]; a15=f15.get(b.last_closed(x['t'],15))
    if not a15:return None
    rng=max(x['h']-x['l'],1e-9); body=abs(x['c']-x['o']); upper=x['h']-max(x['o'],x['c']); lower=min(x['o'],x['c'])-x['l']
    minute=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(TEHRAN).minute
    post_clock=(minute%5 in (0,4))

    # Reversal engine: aggressive flow pushes through a 15m liquidity edge but price refuses to accept it,
    # then the next 1m bar holds the reclaim and delta flips.
    if a15['regime']!='DOWN':
        pen=(x['prev15l']-x['l'])/x['prev15l'] if x['l']<x['prev15l'] else 0
        event=pen>=0.0006 and x['c']>x['prev15l'] and lower>=max(body,0.35*rng) and x['delta']<=-0.10 and x['volr']>=1.30
        hold=y['l']>=x['l']*0.9998 and y['c']>x['prev15l'] and y['delta']>=0.08 and y['c']>=y['o'] and y['volr']>=0.80
        room=(x['prev15h']-y['c'])/y['c']
        score=sum([event,hold,pen>=0.0010,x['volr']>=1.8,lower>=0.50*rng,y['delta']>=0.15,room>=0.0038,post_clock])
        if event and hold and room>=0.0038 and score>=5:
            return ('SWEEP_ABSORPTION','LONG',x['l']*0.9995,i+1,score,{'event_delta':x['delta'],'flip_delta':y['delta']})

    if a15['regime']!='UP':
        pen=(x['h']-x['prev15h'])/x['prev15h'] if x['h']>x['prev15h'] else 0
        event=pen>=0.0006 and x['c']<x['prev15h'] and upper>=max(body,0.35*rng) and x['delta']>=0.10 and x['volr']>=1.30
        hold=y['h']<=x['h']*1.0002 and y['c']<x['prev15h'] and y['delta']<=-0.08 and y['c']<=y['o'] and y['volr']>=0.80
        room=(y['c']-x['prev15l'])/y['c']
        score=sum([event,hold,pen>=0.0010,x['volr']>=1.8,upper>=0.50*rng,y['delta']<=-0.15,room>=0.0038,post_clock])
        if event and hold and room>=0.0038 and score>=5:
            return ('SWEEP_ABSORPTION','SHORT',x['h']*1.0005,i+1,score,{'event_delta':x['delta'],'flip_delta':y['delta']})
    return None


def signal(i,m,f5,f15):
    q=sweep_absorption(i,m,f15)
    if q:return q
    return value_pullback(i,m,f5,f15)


def simulate(m,ei,side,stop,equity):
    entry=m[ei]['o']
    sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<MIN_STOP or sp>MAX_STOP or TP/sp<1.50:return None
    n=min(MAX_LEV*equity,equity*RISK/(sp+COST))
    if n<=0:return None
    target=entry*(1+TP if side=='LONG' else 1-TP)
    tp=False; realized=0.0; remain=n; end=min(len(m)-1,ei+MAX_HOLD)
    for j in range(ei,end+1):
        x=m[j]
        if not tp:
            st=x['l']<=stop if side=='LONG' else x['h']>=stop
            hit=x['h']>=target if side=='LONG' else x['l']<=target
            if st:  # conservative same-bar ordering
                r=stop/entry-1 if side=='LONG' else entry/stop-1
                gross=n*r; cost=n*COST
                return j,stop,'STOP',gross,cost,gross-cost,n
            if hit:
                realized=FIRST*n*TP; remain=RUNNER*n; tp=True; stop=entry
        elif j>=ei+TRAIL_BARS:
            if side=='LONG':
                trail=max(entry,min(m[j-k]['l'] for k in range(1,TRAIL_BARS+1)))
                if x['l']<=trail:
                    gross=realized+remain*(trail/entry-1); cost=n*COST
                    return j,trail,'TP055+RUNNER',gross,cost,gross-cost,n
            else:
                trail=min(entry,max(m[j-k]['h'] for k in range(1,TRAIL_BARS+1)))
                if x['h']>=trail:
                    gross=realized+remain*(entry/trail-1); cost=n*COST
                    return j,trail,'TP055+RUNNER',gross,cost,gross-cost,n
    px=m[end]['c']; r=px/entry-1 if side=='LONG' else entry/px-1
    gross=(realized+remain*r) if tp else n*r; cost=n*COST
    return end,px,'TIME',gross,cost,gross-cost,n


def summarize(ts,start,end,peak,maxdd):
    w=[t for t in ts if t['net_pnl']>0]; l=[t for t in ts if t['net_pnl']<=0]
    gp=sum(t['net_pnl'] for t in w); gl=-sum(t['net_pnl'] for t in l)
    return {'start_equity':round(start,6),'final_equity':round(end,6),'net_pnl':round(end-start,6),'return_pct':round((end/start-1)*100,3),'trades':len(ts),'wins':len(w),'losses':len(l),'win_rate':round(100*len(w)/len(ts),1) if ts else None,'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'modeled_costs':round(sum(t['cost'] for t in ts),6),'pf_net':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),'max_dd_pct':round(maxdd*100,3)}


def main():
    raw=[]
    # Warmup remains 1m data; trading logic itself only references 1m/5m/15m features.
    for d in [datetime(2026,8,31,tzinfo=timezone.utc)+timedelta(days=k) for k in range(2)]:
        raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m=enrich(raw)
    f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)}
    f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)}

    equity=100.0; peak=100.0; maxdd=0.0; trades=[]; last_exit=-10**9; locks={'LONG':-10**9,'SHORT':-10**9}; daynet=0.0; i=0
    while i<len(m)-3:
        x=m[i]
        if x['t']<START_MS:i+=1;continue
        if x['t']>=END_MS:break
        if len(trades)>=MAX_TRADES or daynet<=DAILY_STOP*100 or i-last_exit<COOLDOWN:
            i+=1;continue
        s=signal(i,m,f5,f15)
        if not s:i+=1;continue
        setup,side,stop,ci,score,diag=s
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m) or m[ei]['t']>=END_MS:break
        sim=simulate(m,ei,side,stop,equity)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n=sim
        before=equity; equity+=net; peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak); daynet+=net
        et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN); xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
        tr={'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'diagnostics':diag,'structural_stop_pct':round(sp*100,3),'reason':why,'notional':round(n,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)}
        trades.append(tr)
        if net<0:locks[side]=j+LOSS_LOCK
        last_exit=j; i=j+1

    overall=summarize(trades,100.0,equity,peak,maxdd)
    by_setup={}
    for name in ['VALUE_PERSISTENT','SWEEP_ABSORPTION']:
        ts=[t for t in trades if t['setup']==name]; w=[t for t in ts if t['net_pnl']>0]; l=[t for t in ts if t['net_pnl']<=0]; gp=sum(t['net_pnl'] for t in w); gl=-sum(t['net_pnl'] for t in l)
        by_setup[name]={'trades':len(ts),'wins':len(w),'losses':len(l),'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None)}
    payload={'version':'DARA-v10.0-Microstructure','period_tehran':[START.isoformat(),END.isoformat()],'starting_equity':100.0,'timeframes':['1m trigger/flow','5m location','15m regime'],'design':['No 1h/4h/60m regime or signal logic','Continuation: 15m+5m trend, value pullback, 1m persistent flow/efficiency re-acceleration','Reversal: 15m liquidity sweep, aggressive-flow absorption, next-minute delta flip and reclaim hold','Clock phase is a score feature, not a standalone signal','TP +0.55% on 40%, 60% runner with 3-bar 1m structural trail','Risk 0.25% including conservative 0.11% all-in modeled friction; max 3x notional','Max 4 trades/day, 30m cooldown, 90m same-side lock after a loss'],'methodology_notes':['This is a development test on Sep1. Historical full L2 is unavailable; Binance 1m taker-buy volume is used as the aggressor-flow proxy.','No maker-fill advantage is assumed in the reported PnL; 0.11% all-in friction is retained for comparability and conservatism.'],'overall':overall,'by_setup':by_setup,'trades':trades}
    OUT.parent.mkdir(exist_ok=True); OUT.write_text(json.dumps(payload,indent=2)); print(json.dumps(overall,indent=2)); print(json.dumps(by_setup,indent=2))

if __name__=='__main__':main()
