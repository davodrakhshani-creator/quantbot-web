import json
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_v1_sep1 as b

TEHRAN=b.TEHRAN
START=datetime(2026,9,1,0,0,tzinfo=TEHRAN)
END=datetime(2026,9,9,0,0,tzinfo=TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000)
END_MS=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v3_sep1_8.json')
COST=b.COST
RISK=0.0025
MAX_LEV=3.0
TP1=0.0033
MIN_STOP=0.0014
MAX_STOP=0.0032
MAX_TRADES_DAY=4
DAILY_STOP=-0.0075
COOLDOWN=30
LOSS_LOCK=60


def rolling_vwap(rows,n=240):
    q=deque(); pv=vv=0.0; out=[]
    for x in rows:
        p=(x['h']+x['l']+x['c'])/3; v=x['v']; q.append((p*v,v)); pv+=p*v; vv+=v
        if len(q)>n:
            a,bv=q.popleft(); pv-=a; vv-=bv
        out.append(pv/max(vv,1e-12))
    return out


def enrich_m1(rows):
    m=b.minute_features(rows); vw=rolling_vwap(rows,240)
    for i,x in enumerate(m):
        x['vwap240']=vw[i]
        p5=m[max(0,i-5):i]
        p10=m[max(0,i-10):i]
        x['prev5h']=max((z['h'] for z in p5),default=x['h']); x['prev5l']=min((z['l'] for z in p5),default=x['l'])
        x['prev10h']=max((z['h'] for z in p10),default=x['h']); x['prev10l']=min((z['l'] for z in p10),default=x['l'])
    return m


def room_ok(x,side,min_room=0.0045):
    if side=='LONG':
        if x['c']>=x['prev240h']: return True
        return (x['prev240h']-x['c'])/x['c']>=min_room
    if x['c']<=x['prev240l']: return True
    return (x['c']-x['prev240l'])/x['c']>=min_room


def trend_pullback(i,m1,f5,f15,f60):
    if i<250:return None
    x=m1[i]; a5=f5.get(b.last_closed(x['t'],5)); a15=f15.get(b.last_closed(x['t'],15)); a60=f60.get(b.last_closed(x['t'],60))
    if not a5 or not a15 or not a60:return None
    vol=x['v']/x['medv']; prev=m1[i-5:i]
    if len(prev)<5:return None
    # Context-first: 15m + 1h trend agree, but do not chase if too extended from 15m mean.
    if a15['regime']=='UP' and a60['regime']=='UP':
        extended=(x['c']-a15['ema21'])/max(a15['atr'],1e-9)
        touched=min(z['l'] for z in prev)<=max(a5['ema21'],x['ema20'])*1.0010
        held=min(z['c'] for z in prev)>=a15['ema21']*0.9975
        reac=x['c']>x['prev5h'] and x['c']>x['o'] and x['flow']>=1.45 and vol>=1.15
        if extended<=1.15 and touched and held and reac and x['c']>x['vwap240'] and room_ok(x,'LONG'):
            raw_stop=min(z['l'] for z in prev)*0.9996
            return ('TREND_PULLBACK','LONG',raw_stop)
    if a15['regime']=='DOWN' and a60['regime']=='DOWN':
        extended=(a15['ema21']-x['c'])/max(a15['atr'],1e-9)
        touched=max(z['h'] for z in prev)>=min(a5['ema21'],x['ema20'])*0.9990
        held=max(z['c'] for z in prev)<=a15['ema21']*1.0025
        reac=x['c']<x['prev5l'] and x['c']<x['o'] and x['flow']<=1/1.45 and vol>=1.15
        if extended<=1.15 and touched and held and reac and x['c']<x['vwap240'] and room_ok(x,'SHORT'):
            raw_stop=max(z['h'] for z in prev)*1.0004
            return ('TREND_PULLBACK','SHORT',raw_stop)
    return None


def sweep_reversal(i,m1,f5,f15,f60):
    if i<250:return None
    x=m1[i]; a5=f5.get(b.last_closed(x['t'],5)); a15=f15.get(b.last_closed(x['t'],15)); a60=f60.get(b.last_closed(x['t'],60))
    if not a5 or not a15 or not a60:return None
    vol=x['v']/x['medv']
    # Only fade liquidity at a real 60m extreme, and do not fade a strong aligned HTF trend.
    if a60['regime']!='DOWN' and a15['regime']!='DOWN':
        sweep=x['l']<x['prev60l']*0.9995 and x['c']>x['prev60l']
        reclaim=x['body']>0 and x['flow']>=1.55 and vol>=1.45 and x['c']>x['ema20']*0.9995
        if sweep and reclaim and room_ok(x,'LONG',0.0038):
            # one-minute hold/re-acceleration
            if i+1<len(m1):
                y=m1[i+1]; yvol=y['v']/y['medv']
                if y['l']>=x['l'] and y['c']>x['prev60l'] and y['flow']>=1.15 and yvol>=0.75:
                    return ('LIQUIDITY_SWEEP','LONG',x['l']*0.9996,i+1)
    if a60['regime']!='UP' and a15['regime']!='UP':
        sweep=x['h']>x['prev60h']*1.0005 and x['c']<x['prev60h']
        reclaim=x['body']<0 and x['flow']<=1/1.55 and vol>=1.45 and x['c']<x['ema20']*1.0005
        if sweep and reclaim and room_ok(x,'SHORT',0.0038):
            if i+1<len(m1):
                y=m1[i+1]; yvol=y['v']/y['medv']
                if y['h']<=x['h'] and y['c']<x['prev60h'] and y['flow']<=1/1.15 and yvol>=0.75:
                    return ('LIQUIDITY_SWEEP','SHORT',x['h']*1.0004,i+1)
    return None


def signal(i,m1,f5,f15,f60):
    s=trend_pullback(i,m1,f5,f15,f60)
    if s:return (*s,i)
    q=sweep_reversal(i,m1,f5,f15,f60)
    if q:
        setup,side,stop,confirm_i=q
        return (setup,side,stop,confirm_i)
    return None


def simulate(m1,entry_i,side,raw_stop,equity):
    entry=m1[entry_i]['o']; sp=(entry-raw_stop)/entry if side=='LONG' else (raw_stop-entry)/entry
    if sp<MIN_STOP or sp>MAX_STOP:return None
    if TP1/sp<1.35:return None
    n=min(MAX_LEV*equity,equity*RISK/(sp+COST)); stop=raw_stop; target=entry*(1+TP1 if side=='LONG' else 1-TP1)
    tp=False; realized=0.0; remain=n; end=min(len(m1)-1,entry_i+240)
    for j in range(entry_i,end+1):
        x=m1[j]
        if not tp:
            st=x['l']<=stop if side=='LONG' else x['h']>=stop
            hit=x['h']>=target if side=='LONG' else x['l']<=target
            if st:
                r=(stop/entry-1) if side=='LONG' else (entry/stop-1); gross=n*r; cost=n*COST
                return j,stop,'STOP',gross,cost,gross-cost,n
            if hit:
                realized=.5*n*TP1; remain=.5*n; tp=True; stop=entry
        else:
            if j>=entry_i+3:
                if side=='LONG':
                    trail=max(entry,min(m1[j-1]['l'],m1[j-2]['l'],m1[j-3]['l']))
                    if x['l']<=trail:
                        gross=realized+remain*(trail/entry-1); cost=n*COST
                        return j,trail,'TP1+RUNNER_TRAIL',gross,cost,gross-cost,n
                else:
                    trail=min(entry,max(m1[j-1]['h'],m1[j-2]['h'],m1[j-3]['h']))
                    if x['h']>=trail:
                        gross=realized+remain*(entry/trail-1); cost=n*COST
                        return j,trail,'TP1+RUNNER_TRAIL',gross,cost,gross-cost,n
    px=m1[end]['c']; r=(px/entry-1) if side=='LONG' else (entry/px-1)
    gross=(realized+remain*r) if tp else n*r; cost=n*COST
    return end,px,'TIME',gross,cost,gross-cost,n


def summarize(trades,start,end,peak,maxdd):
    wins=[t for t in trades if t['net_pnl']>0]; losses=[t for t in trades if t['net_pnl']<=0]
    gp=sum(t['net_pnl'] for t in wins); gl=-sum(t['net_pnl'] for t in losses)
    return {'start_equity':round(start,6),'final_equity':round(end,6),'net_pnl':round(end-start,6),'return_pct':round((end/start-1)*100,3) if start else 0,'trades':len(trades),'wins':len(wins),'losses':len(losses),'win_rate':round(100*len(wins)/len(trades),1) if trades else None,'gross_pnl':round(sum(t['gross_pnl'] for t in trades),6),'modeled_costs':round(sum(t['cost'] for t in trades),6),'pf_net':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),'max_dd_pct':round(maxdd*100,3)}


def main():
    raw=[]
    for d in [datetime(2026,8,30,tzinfo=timezone.utc)+timedelta(days=k) for k in range(10)]: raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m1=enrich_m1(raw); f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)}; f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)}; f60={x['t']:x for x in b.features(b.aggregate(raw,60),60)}
    equity=100.0; peak=equity; maxdd=0; alltr=[]; daily=[]; day=None; day_start=equity; day_peak=equity; daydd=0; daytr=[]; daynet=0; daycount=0; last_exit=-10**9; locks={'LONG':-10**9,'SHORT':-10**9}; i=0
    while i<len(m1)-5:
        x=m1[i]
        if x['t']<START_MS:i+=1;continue
        if x['t']>=END_MS:break
        loc=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(TEHRAN); dk=loc.strftime('%Y-%m-%d')
        if day is None:day=dk
        if dk!=day:
            daily.append(summarize(daytr,day_start,equity,day_peak,daydd)|{'day':day}); day=dk; day_start=equity; day_peak=equity; daydd=0; daytr=[]; daynet=0; daycount=0
        if daycount>=MAX_TRADES_DAY or daynet<=DAILY_STOP*day_start or i-last_exit<COOLDOWN:
            i+=1;continue
        s=signal(i,m1,f5,f15,f60)
        if not s:i+=1;continue
        setup,side,stop,ci=s
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m1) or m1[ei]['t']>=END_MS:break
        sim=simulate(m1,ei,side,stop,equity)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n=sim; before=equity; equity+=net; peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak); day_peak=max(day_peak,equity); daydd=max(daydd,(day_peak-equity)/day_peak)
        et=datetime.fromtimestamp(m1[ei]['t']/1000,timezone.utc).astimezone(TEHRAN); xt=datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(TEHRAN); sp=(m1[ei]['o']-stop)/m1[ei]['o'] if side=='LONG' else (stop-m1[ei]['o'])/m1[ei]['o']
        tr={'day':dk,'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m1[ei]['o'],2),'exit':round(px,2),'structural_stop_pct':round(sp*100,3),'reason':why,'notional':round(n,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)}
        alltr.append(tr); daytr.append(tr); daynet+=net; daycount+=1
        if net<0:locks[side]=j+LOSS_LOCK
        last_exit=j; i=j+1
    if day is not None:daily.append(summarize(daytr,day_start,equity,day_peak,daydd)|{'day':day})
    # fill all eight days
    by={x['day']:x for x in daily}; daily2=[]; eq=100.0
    for k in range(8):
        d=(START+timedelta(days=k)).strftime('%Y-%m-%d')
        if d in by:daily2.append(by[d]);eq=by[d]['final_equity']
        else:daily2.append({'day':d,'start_equity':round(eq,6),'final_equity':round(eq,6),'net_pnl':0.0,'return_pct':0.0,'trades':0,'wins':0,'losses':0,'win_rate':None,'gross_pnl':0.0,'modeled_costs':0.0,'pf_net':None,'max_dd_pct':0.0})
    overall=summarize(alltr,100.0,equity,peak,maxdd)
    setup_stats={}
    for name in ['TREND_PULLBACK','LIQUIDITY_SWEEP']:
        ts=[t for t in alltr if t['setup']==name]; w=[t for t in ts if t['net_pnl']>0]; l=[t for t in ts if t['net_pnl']<=0]; gp=sum(t['net_pnl'] for t in w); gl=-sum(t['net_pnl'] for t in l)
        setup_stats[name]={'trades':len(ts),'wins':len(w),'losses':len(l),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl>0 else (99 if gp>0 else None)}
    payload={'version':'DARA-v3.0-ContextFirst-Development','period_tehran':[START.isoformat(),END.isoformat()],'design':['Direct breakout entries removed','Primary setup: 15m+1h aligned Trend Pullback then 1m re-acceleration','Secondary setup: 60m liquidity sweep + reclaim + one-minute hold','Rolling 4h VWAP/room guard','0.14%-0.32% structural stop; risk 0.25% including cost','TP1 +0.33% on 50%, 50% runner','Max 4 trades/day; daily stop -0.75%; 30m cooldown; 60m same-side loss lock','No add-to-winner while proving entry edge'],'methodology_notes':['Sep1-8 are development data because earlier DARA versions were inspected on these dates.','No v3 threshold changed during this run.','Historical L2 unavailable; Binance 1m taker-buy volume is the flow proxy.'],'daily':daily2,'overall':overall,'by_setup':setup_stats,'trades':alltr}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps(overall,indent=2));print(json.dumps(setup_stats,indent=2));print(json.dumps(daily2,indent=2))

if __name__=='__main__':main()
