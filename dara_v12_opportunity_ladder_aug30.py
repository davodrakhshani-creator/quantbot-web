import json, statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v10_microstructure_sep1 as v
import dara_v11_liquidity_transition_sep1_8 as r

TEHRAN=v.TEHRAN
START=datetime(2026,8,30,0,0,tzinfo=TEHRAN)
ENTRY_END=datetime(2026,8,31,0,0,tzinfo=TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000)
ENTRY_END_MS=int(ENTRY_END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v12_opportunity_ladder_aug30.json')

COST=0.0011
RISK=0.0025
MAX_LEV=3.0
MIN_PROFIT_MOVE=3.5*COST  # 0.385%: no normal profitable exit before 3.5x modeled round-trip friction
MAX_TP=0.0060
FIRST=0.50
RUNNER=0.50
MIN_STOP=0.0014
MAX_STOP=0.0030
MAX_TRADES=6
DAILY_STOP=-0.010
COOLDOWN=20
LOSS_LOCK=60
MAX_HOLD=180
TRAIL_BARS=3
rejects={}

def rej(k): rejects[k]=rejects.get(k,0)+1

def adaptive_tp(a5,a15):
    cap=min(MAX_TP,1.85*a15['atrp'],3.25*a5['atrp'])
    return cap if cap>=MIN_PROFIT_MOVE else None

def avg_delta(m,a,bx):
    xs=[z['delta'] for z in m[max(0,a):max(0,bx)]]
    return sum(xs)/len(xs) if xs else 0.0

def strong_regime(a5,a15,side):
    tag='UP' if side=='LONG' else 'DOWN'
    return a5['regime']==tag and a15['regime']==tag

def full_sweep(i,m,f5,f15):
    s=r.liquidity_transition(i,m,f5,f15)
    if not s: return None
    setup,side,stop,ci,score,diag,_=s
    z=m[i]
    a5=f5.get(b.last_closed(z['t'],5)); a15=f15.get(b.last_closed(z['t'],15))
    if not a5 or not a15: return None
    tp=adaptive_tp(a5,a15)
    if tp is None: rej('full_capacity'); return None
    d3=diag.get('delta3',0.0); ed=diag.get('event_delta',0.0)
    prior=(d3<=-0.08) if side=='LONG' else (d3>=0.08)
    exhausted=abs(ed)<=0.13
    if not (prior and exhausted): rej('full_exhaustion'); return None
    diag={**diag,'tier':'FULL_SWEEP','delta_exhausted':True,'tp_pct':tp*100}
    return ('FULL_SWEEP_REVERSAL',side,stop,ci,score+2,diag,tp)

def near_sweep(i,m,f5,f15):
    # Broader opportunity: price attacks a 15m liquidity edge and fails within ~6bp,
    # even if it does not print a textbook penetration. Requires prior aggressive pressure,
    # event-minute delta collapse, wick/close rejection, and next-minute control flip.
    if i<25 or i+1>=len(m): return None
    z=m[i]; y=m[i+1]
    a5=f5.get(b.last_closed(z['t'],5)); a15=f15.get(b.last_closed(z['t'],15))
    if not a5 or not a15: return None
    tp=adaptive_tp(a5,a15)
    if tp is None: rej('near_capacity'); return None
    rng=max(z['h']-z['l'],1e-9); body=abs(z['c']-z['o'])
    lower=min(z['o'],z['c'])-z['l']; upper=z['h']-max(z['o'],z['c'])
    close_pos=(z['c']-z['l'])/rng; d3=avg_delta(m,i-2,i+1)

    dist_low=(z['l']-z['prev15l'])/z['prev15l']
    low_near=(-0.00030<=dist_low<=0.00060)
    sell_pressure=d3<=-0.11
    event_exhaust=abs(z['delta'])<=0.12
    reject_low=lower>=max(0.25*rng,0.65*body) and close_pos>=0.55 and z['volr']>=0.90
    flip=(y['delta']>=0.07 and y['c']>max(z['c'],(z['h']+z['l'])/2) and y['c']>=y['o'] and y['l']>=z['l']*0.9997)
    regime_ok=(not strong_regime(a5,a15,'SHORT')) or (y['c']>z['h'] and y['delta']>=0.14)
    if low_near and sell_pressure and event_exhaust and reject_low and flip and regime_ok:
        stop=z['l']*0.9995
        score=sum([dist_low<=0,z['volr']>=1.25,d3<=-0.16,y['delta']>=0.12,y['c']>z['h']])
        diag={'tier':'NEAR_SWEEP','distance_to_low_pct':dist_low*100,'event_delta':z['delta'],'delta3':d3,'flip_delta':y['delta'],'close_pos':close_pos,'volr':z['volr'],'regime5':a5['regime'],'regime15':a15['regime'],'tp_pct':tp*100}
        return ('NEAR_SWEEP_EXHAUSTION','LONG',stop,i+1,score,diag,tp)

    dist_high=(z['prev15h']-z['h'])/z['prev15h']
    high_near=(-0.00030<=dist_high<=0.00060)
    buy_pressure=d3>=0.11
    reject_high=upper>=max(0.25*rng,0.65*body) and close_pos<=0.45 and z['volr']>=0.90
    flip=(y['delta']<=-0.07 and y['c']<min(z['c'],(z['h']+z['l'])/2) and y['c']<=y['o'] and y['h']<=z['h']*1.0003)
    regime_ok=(not strong_regime(a5,a15,'LONG')) or (y['c']<z['l'] and y['delta']<=-0.14)
    if high_near and buy_pressure and event_exhaust and reject_high and flip and regime_ok:
        stop=z['h']*1.0005
        score=sum([dist_high<=0,z['volr']>=1.25,d3>=0.16,y['delta']<=-0.12,y['c']<z['l']])
        diag={'tier':'NEAR_SWEEP','distance_to_high_pct':dist_high*100,'event_delta':z['delta'],'delta3':d3,'flip_delta':y['delta'],'close_pos':close_pos,'volr':z['volr'],'regime5':a5['regime'],'regime15':a15['regime'],'tp_pct':tp*100}
        return ('NEAR_SWEEP_EXHAUSTION','SHORT',stop,i+1,score,diag,tp)
    rej('near_core'); return None

def failed_pullback(i,m,f5,f15):
    s=r.failed_pullback(i,m,f5,f15)
    if not s: return None
    setup,side,stop,ci,score,diag,_=s
    z=m[i]
    a5=f5.get(b.last_closed(z['t'],5)); a15=f15.get(b.last_closed(z['t'],15))
    if not a5 or not a15: return None
    tp=adaptive_tp(a5,a15)
    if tp is None: rej('pb_capacity'); return None
    # Slightly less strict than v11.1, but still reject expanding counter-flow.
    if diag.get('volr',99)>1.20: rej('pb_high_volume'); return None
    # Require a real control flip so relaxed participation does not become noise.
    if abs(diag.get('flip_delta',0))<0.12: rej('pb_weak_flip'); return None
    diag={**diag,'tier':'LOW_VOLUME_PULLBACK','tp_pct':tp*100}
    return ('FAILED_PULLBACK',side,stop,ci,score,diag,tp)

def signal(i,m,f5,f15):
    for fn in (full_sweep,near_sweep,failed_pullback):
        s=fn(i,m,f5,f15)
        if s:return s
    return None

def simulate(m,ei,side,stop,equity,tp):
    entry=m[ei]['o']
    sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<MIN_STOP or sp>MAX_STOP: rej('invalid_stop'); return None
    if tp<MIN_PROFIT_MOVE: rej('profit_floor'); return None
    if tp/sp<1.25: rej('poor_reward_stop'); return None
    n=min(MAX_LEV*equity,equity*RISK/(sp+COST))
    if n<=0:return None
    target=entry*(1+tp if side=='LONG' else 1-tp)
    tp_done=False; realized=0.; remain=n; mfe=0.; end=min(len(m)-1,ei+MAX_HOLD)
    for j in range(ei,end+1):
        bar=m[j]
        favorable=(bar['h']/entry-1) if side=='LONG' else (entry/bar['l']-1)
        mfe=max(mfe,favorable)
        if not tp_done:
            st=bar['l']<=stop if side=='LONG' else bar['h']>=stop
            hit=bar['h']>=target if side=='LONG' else bar['l']<=target
            if st:
                rr=stop/entry-1 if side=='LONG' else entry/stop-1
                gross=n*rr; cost=n*COST
                return j,stop,'STOP',gross,cost,gross-cost,n,mfe
            # Losing/non-positive thesis can be cut early. A positive trade below 3.5x fee-move is NOT harvested.
            if j>=ei+10 and mfe<0.0011:
                cur=(bar['c']/entry-1) if side=='LONG' else (entry/bar['c']-1)
                if cur<=0:
                    gross=n*cur; cost=n*COST
                    return j,bar['c'],'VELOCITY_FAIL',gross,cost,gross-cost,n,mfe
            if hit:
                realized=FIRST*n*tp; remain=RUNNER*n; tp_done=True; stop=entry
        elif j>=ei+TRAIL_BARS:
            if side=='LONG':
                trail=max(entry,min(m[j-k]['l'] for k in range(1,TRAIL_BARS+1)))
                if bar['l']<=trail:
                    gross=realized+remain*(trail/entry-1); cost=n*COST
                    return j,trail,'TP3.5FEE+RUNNER',gross,cost,gross-cost,n,mfe
            else:
                trail=min(entry,max(m[j-k]['h'] for k in range(1,TRAIL_BARS+1)))
                if bar['h']>=trail:
                    gross=realized+remain*(entry/trail-1); cost=n*COST
                    return j,trail,'TP3.5FEE+RUNNER',gross,cost,gross-cost,n,mfe
    px=m[end]['c']; rr=px/entry-1 if side=='LONG' else entry/px-1
    gross=(realized+remain*rr) if tp_done else n*rr; cost=n*COST
    return end,px,'TIME',gross,cost,gross-cost,n,mfe

def summarize(ts,start,end,peak,maxdd):
    w=[t for t in ts if t['net_pnl']>0]; l=[t for t in ts if t['net_pnl']<=0]
    gp=sum(t['net_pnl'] for t in w); gl=-sum(t['net_pnl'] for t in l)
    return {'start_equity':round(start,6),'final_equity':round(end,6),'net_pnl':round(end-start,6),'return_pct':round((end/start-1)*100,3),'trades':len(ts),'wins':len(w),'losses':len(l),'win_rate':round(100*len(w)/len(ts),1) if ts else None,'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'modeled_costs':round(sum(t['cost'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99.0 if gp else None),'max_dd_pct':round(maxdd*100,3)}

def main():
    raw=[]
    # UTC Aug29 provides warmup; Aug30 contains the complete Tehran entry window; Aug31 permits late-entry exits.
    d=datetime(2026,8,29,tzinfo=timezone.utc)
    while d.date()<=datetime(2026,8,31,tzinfo=timezone.utc).date():
        raw+=b.get_daily(d); d+=timedelta(days=1)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m=v.enrich(raw)
    f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)}
    f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)}
    equity=100.; peak=100.; maxdd=0.; trades=[]; last=-10**9; locks={'LONG':-10**9,'SHORT':-10**9}; daynet=0.; i=0
    while i<len(m)-3:
        z=m[i]
        if z['t']<START_MS: i+=1; continue
        if z['t']>=ENTRY_END_MS: break
        if len(trades)>=MAX_TRADES or daynet<=DAILY_STOP*100 or i-last<COOLDOWN: i+=1; continue
        s=signal(i,m,f5,f15)
        if not s: i+=1; continue
        setup,side,stop,ci,score,diag,tp=s
        if ci<locks[side]: i+=1; continue
        ei=ci+1
        if ei>=len(m) or m[ei]['t']>=ENTRY_END_MS: break
        sim=simulate(m,ei,side,stop,equity,tp)
        if sim is None: i+=1; continue
        j,px,why,gross,cost,net,n,mfe=sim
        before=equity; equity+=net; peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak); daynet+=net
        et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN); xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
        tr={'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'profit_floor_pct':round(MIN_PROFIT_MOVE*100,3),'structural_stop_pct':round(sp*100,3),'mfe_pct':round(mfe*100,3),'reason':why,'diagnostics':diag,'notional':round(n,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)}
        trades.append(tr)
        if net<0: locks[side]=j+LOSS_LOCK
        last=j; i=j+1
    overall=summarize(trades,100.,equity,peak,maxdd)
    by={}
    for name in ['FULL_SWEEP_REVERSAL','NEAR_SWEEP_EXHAUSTION','FAILED_PULLBACK']:
        ts=[t for t in trades if t['setup']==name]; w=[t for t in ts if t['net_pnl']>0]; l=[t for t in ts if t['net_pnl']<=0]; gp=sum(t['net_pnl'] for t in w); gl=-sum(t['net_pnl'] for t in l)
        by[name]={'trades':len(ts),'wins':len(w),'losses':len(l),'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None)}
    payload={'version':'DARA-v12-Opportunity-Ladder-Blind-Aug30','period_tehran':[START.isoformat(),ENTRY_END.isoformat()],'starting_equity':100.0,'rules':['1m trigger/order-flow proxy, 5m location/capacity, 15m regime/capacity only','Opportunity ladder: full sweep reversal > near-sweep exhaustion > low-volume failed pullback','Max 6 trades/day, 20m cooldown, 60m same-side lock after loss','0.25% full-stop risk incl modeled friction, max 3x notional, -1% daily stop','0.11% modeled all-in round-trip friction','No normal profitable exit before 3.5x friction move = +0.385% gross price move','50% first take-profit then 50% runner; losing thesis may exit early only when non-positive'],'methodology_notes':['Aug30 was not inspected to tune v12; rules were fixed from Sep development plus public microstructure research before this run.','Historical full L2 unavailable; 1m taker-buy volume/delta is an aggressor-flow proxy.','This is one-day blind evidence only, not proof of a durable edge.'],'overall':overall,'by_setup':by,'reject_counts':rejects,'trades':trades}
    OUT.parent.mkdir(exist_ok=True); OUT.write_text(json.dumps(payload,indent=2)); print(json.dumps(overall,indent=2)); print(json.dumps(by,indent=2)); print(json.dumps(rejects,indent=2))

if __name__=='__main__': main()
