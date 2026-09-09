import json, statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v10_microstructure_sep1 as v
import dara_v10_4_state_transition_sep3_8 as q

TEHRAN=v.TEHRAN
START=datetime(2026,9,1,0,0,tzinfo=TEHRAN)
END=datetime(2026,9,9,0,0,tzinfo=TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000)
END_MS=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v11_liquidity_transition_sep1_8.json')

COST=q.COST; RISK=q.RISK; MAX_LEV=q.MAX_LEV
MAX_TRADES_DAY=4; DAILY_STOP=-0.010; COOLDOWN=30; LOSS_LOCK=90
rejects={}
def rej(k): rejects[k]=rejects.get(k,0)+1

def avg_delta(m,a,bx):
    xs=[z['delta'] for z in m[max(0,a):max(0,bx)]]
    return sum(xs)/len(xs) if xs else 0.0

def adaptive_tp(a5,a15):
    return q.adaptive_tp(a5,a15)

def enough_capacity(a5,a15):
    return adaptive_tp(a5,a15) is not None

def strong_regime(a5,a15,side):
    tag='UP' if side=='LONG' else 'DOWN'
    return a5['regime']==tag and a15['regime']==tag

def liquidity_transition(i,m,f5,f15):
    # Event minute z sweeps visible 15m liquidity; y must prove control actually changed.
    if i<25 or i+1>=len(m): return None
    z=m[i]; y=m[i+1]
    a5=f5.get(b.last_closed(z['t'],5)); a15=f15.get(b.last_closed(z['t'],15))
    if not a5 or not a15 or not enough_capacity(a5,a15):
        rej('liq_capacity'); return None
    tp=adaptive_tp(a5,a15)
    rng=max(z['h']-z['l'],1e-9); body=abs(z['c']-z['o'])
    lower=min(z['o'],z['c'])-z['l']; upper=z['h']-max(z['o'],z['c'])
    close_pos=(z['c']-z['l'])/rng
    d3=avg_delta(m,i-2,i+1)

    # SELL pressure fails below prior 15m low -> reclaim -> buyer control.
    pen=(z['prev15l']-z['l'])/z['prev15l'] if z['l']<z['prev15l'] else 0.0
    event=(pen>=0.00030 and pen<=0.0030 and z['c']>z['prev15l'] and
           lower>=max(0.28*rng,0.75*body) and close_pos>=0.52 and
           z['volr']>=1.00 and (z['delta']<=-0.08 or d3<=-0.10))
    flip=(y['c']>max(z['c'],z['prev15l']) and y['c']>=y['o'] and
          y['delta']>=0.05 and y['l']>=z['l']*0.9997)
    # Against a still-strong down regime demand a true break of the event high.
    regime_ok=(not strong_regime(a5,a15,'SHORT')) or (y['c']>z['h'] and y['delta']>=0.14)
    if event and flip and regime_ok:
        stop=z['l']*0.9995
        score=sum([pen>=0.0006,lower>=0.40*rng,z['volr']>=1.35,z['delta']<=-0.14,y['delta']>=0.12,y['c']>z['h']])
        diag={'penetration_pct':pen*100,'event_delta':z['delta'],'delta3':d3,'flip_delta':y['delta'],'close_pos':close_pos,'volr':z['volr'],'regime5':a5['regime'],'regime15':a15['regime'],'tp_pct':tp*100}
        return ('LIQUIDITY_REVERSAL','LONG',stop,i+1,score,diag,tp)

    # BUY pressure fails above prior 15m high -> reclaim -> seller control.
    pen=(z['h']-z['prev15h'])/z['prev15h'] if z['h']>z['prev15h'] else 0.0
    event=(pen>=0.00030 and pen<=0.0030 and z['c']<z['prev15h'] and
           upper>=max(0.28*rng,0.75*body) and close_pos<=0.48 and
           z['volr']>=1.00 and (z['delta']>=0.08 or d3>=0.10))
    flip=(y['c']<min(z['c'],z['prev15h']) and y['c']<=y['o'] and
          y['delta']<=-0.05 and y['h']<=z['h']*1.0003)
    regime_ok=(not strong_regime(a5,a15,'LONG')) or (y['c']<z['l'] and y['delta']<=-0.14)
    if event and flip and regime_ok:
        stop=z['h']*1.0005
        score=sum([pen>=0.0006,upper>=0.40*rng,z['volr']>=1.35,z['delta']>=0.14,y['delta']<=-0.12,y['c']<z['l']])
        diag={'penetration_pct':pen*100,'event_delta':z['delta'],'delta3':d3,'flip_delta':y['delta'],'close_pos':close_pos,'volr':z['volr'],'regime5':a5['regime'],'regime15':a15['regime'],'tp_pct':tp*100}
        return ('LIQUIDITY_REVERSAL','SHORT',stop,i+1,score,diag,tp)

    rej('liq_core'); return None

def failed_pullback(i,m,f5,f15):
    # Do not buy/sell an already-extended breakout. Wait for counter-flow to fail, then enter on control flip.
    if i<30 or i+1>=len(m): return None
    z=m[i]; y=m[i+1]
    a5=f5.get(b.last_closed(z['t'],5)); a15=f15.get(b.last_closed(z['t'],15))
    if not a5 or not a15 or not enough_capacity(a5,a15):
        rej('pb_capacity'); return None
    tp=adaptive_tp(a5,a15)
    pre=m[i-10:i]
    if len(pre)<10: return None
    rng=max(z['h']-z['l'],1e-9); mid=(z['h']+z['l'])/2

    # UP impulse existed first; then aggressive sellers fail to break value, buyers retake control.
    if a15['regime']=='UP' and a5['regime']!='DOWN':
        base=min(w['l'] for w in pre[:5]); peak=max(w['h'] for w in pre[5:])
        impulse=(peak-base)/max(base,1e-9)
        directional=pre[-2]['c']>pre[1]['c']
        pressure=(z['delta']<=-0.10 and z['volr']>=0.90 and z['c']<=z['o'])
        held=(z['l']>=a15['ema21']*0.9975 and z['c']>=a15['ema21']*0.9985)
        weak_sellers=((z['c']-z['l'])/rng>=0.35 or z['delta']<=-0.18 and abs(z['c']/z['o']-1)<=0.0015)
        flip=(y['delta']>=0.08 and y['c']>max(z['c'],mid) and y['c']>=y['o'] and y['l']>=z['l']*0.9997)
        no_chase=(y['c']-z['l'])/y['c']<=max(0.0028,1.8*a5['atrp'])
        if impulse>=0.0022 and directional and pressure and held and weak_sellers and flip and no_chase:
            stop=min(z['l'],min(w['l'] for w in m[i-2:i]))*0.9996
            score=sum([impulse>=0.0032,z['delta']<=-0.16,y['delta']>=0.14,y['c']>z['h'],z['volr']>=1.20])
            diag={'impulse_pct':impulse*100,'counter_delta':z['delta'],'flip_delta':y['delta'],'volr':z['volr'],'regime5':a5['regime'],'regime15':a15['regime'],'tp_pct':tp*100}
            return ('FAILED_PULLBACK','LONG',stop,i+1,score,diag,tp)

    # DOWN impulse existed first; aggressive buyers fail to break value, sellers retake control.
    if a15['regime']=='DOWN' and a5['regime']!='UP':
        peak=max(w['h'] for w in pre[:5]); trough=min(w['l'] for w in pre[5:])
        impulse=(peak-trough)/max(peak,1e-9)
        directional=pre[-2]['c']<pre[1]['c']
        pressure=(z['delta']>=0.10 and z['volr']>=0.90 and z['c']>=z['o'])
        held=(z['h']<=a15['ema21']*1.0025 and z['c']<=a15['ema21']*1.0015)
        weak_buyers=((z['h']-z['c'])/rng>=0.35 or z['delta']>=0.18 and abs(z['c']/z['o']-1)<=0.0015)
        flip=(y['delta']<=-0.08 and y['c']<min(z['c'],mid) and y['c']<=y['o'] and y['h']<=z['h']*1.0003)
        no_chase=(z['h']-y['c'])/y['c']<=max(0.0028,1.8*a5['atrp'])
        if impulse>=0.0022 and directional and pressure and held and weak_buyers and flip and no_chase:
            stop=max(z['h'],max(w['h'] for w in m[i-2:i]))*1.0004
            score=sum([impulse>=0.0032,z['delta']>=0.16,y['delta']<=-0.14,y['c']<z['l'],z['volr']>=1.20])
            diag={'impulse_pct':impulse*100,'counter_delta':z['delta'],'flip_delta':y['delta'],'volr':z['volr'],'regime5':a5['regime'],'regime15':a15['regime'],'tp_pct':tp*100}
            return ('FAILED_PULLBACK','SHORT',stop,i+1,score,diag,tp)

    rej('pb_core'); return None

def signal(i,m,f5,f15):
    s=liquidity_transition(i,m,f5,f15)
    if s: return s
    return failed_pullback(i,m,f5,f15)

def summary(ts,start,end,maxdd):
    w=[t for t in ts if t['net_pnl']>0]; l=[t for t in ts if t['net_pnl']<=0]
    gp=sum(t['net_pnl'] for t in w); gl=-sum(t['net_pnl'] for t in l)
    return {'start_equity':round(start,6),'final_equity':round(end,6),'net_pnl':round(end-start,6),'return_pct':round((end/start-1)*100,3),'trades':len(ts),'wins':len(w),'losses':len(l),'win_rate':round(100*len(w)/len(ts),1) if ts else None,'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'modeled_costs':round(sum(t['cost'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99.0 if gp else None),'max_dd_pct':round(maxdd*100,3)}

def main():
    raw=[]
    d=datetime(2026,8,31,tzinfo=timezone.utc)
    while d.date()<=datetime(2026,9,8,tzinfo=timezone.utc).date():
        raw+=b.get_daily(d); d+=timedelta(days=1)
    raw=sorted({r['t']:r for r in raw}.values(),key=lambda r:r['t'])
    m=v.enrich(raw)
    f5={r['t']:r for r in b.features(b.aggregate(raw,5),5)}
    f15={r['t']:r for r in b.features(b.aggregate(raw,15),15)}

    equity=100.0; peak=100.0; maxdd=0.0; alltr=[]; daily=[]
    day=None; daystart=100.0; daypeak=100.0; daydd=0.0; daytr=[]; daynet=0.0; daycount=0
    last=-10**9; locks={'LONG':-10**9,'SHORT':-10**9}; i=0
    while i<len(m)-3:
        bar=m[i]
        if bar['t']<START_MS: i+=1; continue
        if bar['t']>=END_MS: break
        dk=datetime.fromtimestamp(bar['t']/1000,timezone.utc).astimezone(TEHRAN).strftime('%Y-%m-%d')
        if day is None: day=dk
        if dk!=day:
            daily.append(summary(daytr,daystart,equity,daydd)|{'day':day})
            day=dk; daystart=equity; daypeak=equity; daydd=0.0; daytr=[]; daynet=0.0; daycount=0
        if daycount>=MAX_TRADES_DAY or daynet<=DAILY_STOP*daystart or i-last<COOLDOWN:
            i+=1; continue
        s=signal(i,m,f5,f15)
        if not s: i+=1; continue
        setup,side,stop,ci,score,diag,tp=s
        if ci<locks[side]: i+=1; continue
        ei=ci+1
        if ei>=len(m) or m[ei]['t']>=END_MS: break
        sim=q.simulate(m,ei,side,stop,equity,tp)
        if sim is None: i+=1; continue
        j,px,why,gross,cost,net,n,mfe=sim
        before=equity; equity+=net; peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak)
        daypeak=max(daypeak,equity); daydd=max(daydd,(daypeak-equity)/daypeak); daynet+=net; daycount+=1
        et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN)
        xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
        tr={'day':dk,'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'structural_stop_pct':round(sp*100,3),'mfe_pct':round(mfe*100,3),'reason':why,'diagnostics':diag,'notional':round(n,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)}
        alltr.append(tr); daytr.append(tr)
        if net<0: locks[side]=j+LOSS_LOCK
        last=j; i=j+1
    if day is not None: daily.append(summary(daytr,daystart,equity,daydd)|{'day':day})

    # Ensure all eight Tehran dates appear, including zero-trade days.
    byday={z['day']:z for z in daily}; daily2=[]; eq=100.0
    for k in range(8):
        ds=(START+timedelta(days=k)).strftime('%Y-%m-%d')
        if ds in byday:
            row=byday[ds]; eq=row['final_equity']; daily2.append(row)
        else:
            daily2.append({'day':ds,'start_equity':eq,'final_equity':eq,'net_pnl':0.0,'return_pct':0.0,'trades':0,'wins':0,'losses':0,'win_rate':None,'gross_pnl':0.0,'modeled_costs':0.0,'pf_net':None,'max_dd_pct':0.0})

    overall=summary(alltr,100.0,equity,maxdd)
    bysetup={}
    for name in ['LIQUIDITY_REVERSAL','FAILED_PULLBACK']:
        ts=[t for t in alltr if t['setup']==name]; w=[t for t in ts if t['net_pnl']>0]; l=[t for t in ts if t['net_pnl']<=0]
        gp=sum(t['net_pnl'] for t in w); gl=-sum(t['net_pnl'] for t in l)
        bysetup[name]={'trades':len(ts),'wins':len(w),'losses':len(l),'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None)}

    merged_rejects=dict(rejects)
    for k,val in q.rejects.items(): merged_rejects['exit_'+k]=val
    payload={
        'version':'DARA-v11-Liquidity-State-Transition-Frozen',
        'period_tehran':[START.isoformat(),END.isoformat()],
        'starting_equity':100.0,
        'architecture':[
            'Primary: liquidity sweep + failed aggressive pressure + reclaim + opposite-side control confirmation',
            'Secondary: trend impulse + counter-flow pullback that fails + control flip; no late breakout chasing',
            'Persistent same-direction flow alone is never an entry reason'
        ],
        'preserved_strengths':[
            '0.25% risk budget per full stop including modeled friction','0.11% all-in modeled roundtrip friction','max 3x notional','adaptive TP never below +0.33% gross','velocity thesis-fail exit','40% first take-profit plus 60% runner','max 4 trades/day and -1% daily stop'
        ],
        'methodology_notes':[
            'DARA v11 was designed after reviewing Sep1-8 behavior, therefore this Sep1-8 rerun is in-sample/development evidence, not clean out-of-sample proof.',
            'Historical full L2 is unavailable; Binance 1m taker-buy volume/delta remains an aggressor-flow proxy.',
            'Rules in this file were frozen before this workflow run; no parameter is changed during the Sep1-8 run.'
        ],
        'daily':daily2,'overall':overall,'by_setup':bysetup,'reject_counts':merged_rejects,'trades':alltr
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2))
    print(json.dumps(overall,indent=2)); print(json.dumps(bysetup,indent=2)); print(json.dumps(merged_rejects,indent=2))

if __name__=='__main__': main()
