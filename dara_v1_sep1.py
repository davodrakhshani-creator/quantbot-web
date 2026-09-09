import csv, io, json, statistics, urllib.request, zipfile, time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

SYMBOL='BTCUSDT'
TEHRAN=timezone(timedelta(hours=3,minutes=30))
START=datetime(2026,9,1,0,0,tzinfo=TEHRAN)
END=datetime(2026,9,2,0,0,tzinfo=TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000)
END_MS=int(END.astimezone(timezone.utc).timestamp()*1000)
START_EQUITY=100.0
COST=0.0011
RISK=0.0025
MAX_LEV=3.0
TP1=0.0033
TP1_FRAC=0.50
RUNNER_FRAC=0.50
MAX_TRADES=5
DAILY_STOP=-0.01
COOLDOWN=20
OUT=Path('data/dara_v1_sep1.json')
UA='DARA-v1-EventFirst/1.0'

def get_daily(day):
    ds=day.strftime('%Y-%m-%d')
    url=f'https://data.binance.vision/data/futures/um/daily/klines/{SYMBOL}/1m/{SYMBOL}-1m-{ds}.zip'
    err=None
    for k in range(5):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA})
            with urllib.request.urlopen(req,timeout=45) as r: b=r.read()
            z=zipfile.ZipFile(io.BytesIO(b)); raw=z.read(z.namelist()[0]).decode('utf-8')
            out=[]
            for x in csv.reader(io.StringIO(raw)):
                if not x or not x[0].isdigit(): continue
                out.append({'t':int(x[0]),'o':float(x[1]),'h':float(x[2]),'l':float(x[3]),'c':float(x[4]),'v':float(x[5]),'tb':float(x[9])})
            return out
        except Exception as e:
            err=e; time.sleep(min(8,0.8*(2**k)))
    raise RuntimeError(err)

def ema(vals,n):
    a=2/(n+1); out=[]; e=None
    for v in vals:
        e=v if e is None else a*v+(1-a)*e; out.append(e)
    return out

def rmean(vals,n):
    q=deque(); s=0.0; out=[]
    for v in vals:
        q.append(v); s+=v
        if len(q)>n:s-=q.popleft()
        out.append(s/len(q))
    return out

def medprev(vals,n):
    q=deque(); out=[]
    for v in vals:
        out.append(statistics.median(q) if q else v); q.append(v)
        if len(q)>n:q.popleft()
    return out

def rsi(vals,n=7):
    out=[50.0]*len(vals); gq=deque(); lq=deque(); sg=sl=0.0
    for i in range(1,len(vals)):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        gq.append(g);lq.append(l);sg+=g;sl+=l
        if len(gq)>n:sg-=gq.popleft();sl-=lq.popleft()
        if len(gq)>=n: out[i]=100 if sl==0 else 100-100/(1+sg/sl)
    return out

def aggregate(rows,mins):
    span=mins*60000; g={}
    for r in rows:g.setdefault((r['t']//span)*span,[]).append(r)
    out=[]
    for k in sorted(g):
        a=g[k]
        if len(a)<mins:continue
        out.append({'t':k,'o':a[0]['o'],'h':max(x['h'] for x in a),'l':min(x['l'] for x in a),'c':a[-1]['c'],'v':sum(x['v'] for x in a),'tb':sum(x['tb'] for x in a)})
    return out

def features(bars,tf):
    closes=[x['c'] for x in bars]; vols=[x['v'] for x in bars]
    e9,e21=ema(closes,9),ema(closes,21); med=medprev(vols,20)
    tr=[]; prev=None
    for b in bars:
        tr.append(b['h']-b['l'] if prev is None else max(b['h']-b['l'],abs(b['h']-prev),abs(b['l']-prev))); prev=b['c']
    atr=rmean(tr,14); rr=rsi(closes,7)
    out=[]
    for i,b in enumerate(bars):
        sell=max(b['v']-b['tb'],1e-12); flow=b['tb']/sell; slope=(e21[i]/e21[i-3]-1) if i>=3 else 0
        th=0.00012 if tf==5 else 0.00018
        reg='UP' if e9[i]>e21[i] and b['c']>e21[i] and slope>th else ('DOWN' if e9[i]<e21[i] and b['c']<e21[i] and slope<-th else 'RANGE')
        out.append({**b,'flow':flow,'ema9':e9[i],'ema21':e21[i],'atr':atr[i],'atrp':atr[i]/b['c'],'rsi':rr[i],'medv':max(med[i],1e-12),'regime':reg,'body':(b['c']-b['o'])/b['o']})
    return out

def minute_features(rows):
    vols=[x['v'] for x in rows]; med=medprev(vols,20); closes=[x['c'] for x in rows]; e20=ema(closes,20)
    out=[]
    for i,b in enumerate(rows):
        sell=max(b['v']-b['tb'],1e-12); flow=b['tb']/sell
        p20=rows[max(0,i-20):i]; p60=rows[max(0,i-60):i]; p240=rows[max(0,i-240):i]
        out.append({**b,'flow':flow,'medv':max(med[i],1e-12),'ema20':e20[i],
                    'prev20h':max((x['h'] for x in p20),default=b['h']),'prev20l':min((x['l'] for x in p20),default=b['l']),
                    'prev60h':max((x['h'] for x in p60),default=b['h']),'prev60l':min((x['l'] for x in p60),default=b['l']),
                    'prev240h':max((x['h'] for x in p240),default=b['h']),'prev240l':min((x['l'] for x in p240),default=b['l']),
                    'body':(b['c']-b['o'])/b['o']})
    return out

def last_closed(t,mins):
    span=mins*60000
    return ((t+60000)//span)*span-span

def room_ok(x,side):
    if side=='LONG':
        if x['c']>=x['prev240h']: return True
        return (x['prev240h']-x['c'])/x['c']>=0.0055
    else:
        if x['c']<=x['prev240l']: return True
        return (x['c']-x['prev240l'])/x['c']>=0.0055

def event_signal(i,m1,f5,f15):
    x=m1[i]; a5=f5.get(last_closed(x['t'],5)); a15=f15.get(last_closed(x['t'],15))
    if i<250 or not a5 or not a15:return None
    align_up=a5['regime']=='UP' and a15['regime']!='DOWN'
    align_dn=a5['regime']=='DOWN' and a15['regime']!='UP'
    vol=x['v']/x['medv']
    # 1) Breakout + acceptance: current close breaks 20m structure; previous minute already attacked the level.
    p=m1[i-1]
    if align_up and x['c']>x['prev20h']*1.0003 and p['h']>=p['prev20h'] and x['flow']>=1.6 and vol>=1.5 and x['c']>x['ema20'] and room_ok(x,'LONG'):
        stop=min(x['l'],x['prev20h'])*0.9997
        return ('BREAK_ACCEPT','LONG',stop)
    if align_dn and x['c']<x['prev20l']*0.9997 and p['l']<=p['prev20l'] and x['flow']<=1/1.6 and vol>=1.5 and x['c']<x['ema20'] and room_ok(x,'SHORT'):
        stop=max(x['h'],x['prev20l'])*1.0003
        return ('BREAK_ACCEPT','SHORT',stop)
    # 2) Sweep + reclaim failed break.
    if a15['regime']!='DOWN' and x['l']<x['prev20l']*0.9995 and x['c']>x['prev20l'] and x['flow']>=1.4 and vol>=1.5 and x['body']>0 and room_ok(x,'LONG'):
        return ('SWEEP_RECLAIM','LONG',x['l']*0.9997)
    if a15['regime']!='UP' and x['h']>x['prev20h']*1.0005 and x['c']<x['prev20h'] and x['flow']<=1/1.4 and vol>=1.5 and x['body']<0 and room_ok(x,'SHORT'):
        return ('SWEEP_RECLAIM','SHORT',x['h']*1.0003)
    # 3) Impulse + retest: completed 5m impulse, then 1m retest into 30-60% zone and renewed flow.
    rng=max(a5['h']-a5['l'],1e-9)
    if a5['body']>=0.0030 and a5['v']>=1.8*a5['medv'] and a5['flow']>=1.6 and a15['regime']!='DOWN':
        retr=(a5['h']-x['l'])/rng
        if 0.30<=retr<=0.60 and x['c']>x['o'] and x['flow']>=1.5 and vol>=1.2 and x['c']>x['ema20'] and room_ok(x,'LONG'):
            return ('IMPULSE_RETEST','LONG',x['l']*0.9997)
    if a5['body']<=-0.0030 and a5['v']>=1.8*a5['medv'] and a5['flow']<=1/1.6 and a15['regime']!='UP':
        retr=(x['h']-a5['l'])/rng
        if 0.30<=retr<=0.60 and x['c']<x['o'] and x['flow']<=1/1.5 and vol>=1.2 and x['c']<x['ema20'] and room_ok(x,'SHORT'):
            return ('IMPULSE_RETEST','SHORT',x['h']*1.0003)
    # 4) BNF extreme mean reversion only if both 5m and 15m are range.
    if a5['regime']=='RANGE' and a15['regime']=='RANGE' and a5['atr']>0:
        dev=(a5['c']-a5['ema21'])/a5['atr']
        if dev<=-2.5 and a5['rsi']<=15 and x['c']>x['prev20l'] and x['flow']>=1.8 and vol>=1.5 and room_ok(x,'LONG'):
            return ('EXTREME_REVERSION','LONG',x['l']*0.9997)
        if dev>=2.5 and a5['rsi']>=85 and x['c']<x['prev20h'] and x['flow']<=1/1.8 and vol>=1.5 and room_ok(x,'SHORT'):
            return ('EXTREME_REVERSION','SHORT',x['h']*1.0003)
    return None

def simulate(m1,entry_i,setup,side,raw_stop,equity):
    entry=m1[entry_i]['o']
    stop_pct=(entry-raw_stop)/entry if side=='LONG' else (raw_stop-entry)/entry
    if stop_pct<0.0012 or stop_pct>0.0035:return None
    # Net reward to TP1 versus full stop must be >=1.5R in price-risk terms.
    if TP1/stop_pct<1.5:return None
    n0=min(MAX_LEV*equity,equity*RISK/(stop_pct+COST))
    if n0<=0:return None
    stop=raw_stop; target=entry*(1+TP1 if side=='LONG' else 1-TP1)
    added=False; add_n=0.0; add_px=None; avg=entry; tp_done=False; realized_gross=0.0; remaining_n=n0
    initial_R=abs(entry-stop)
    end=min(len(m1)-1,entry_i+240)
    for j in range(entry_i,end+1):
        b=m1[j]
        # conservative: stop first
        if not tp_done:
            st=b['l']<=stop if side=='LONG' else b['h']>=stop
            if st:
                r=(stop-entry)/entry if side=='LONG' else (entry-stop)/entry
                gross=n0*r
                if added:
                    r2=(stop-add_px)/add_px if side=='LONG' else (add_px-stop)/add_px
                    gross+=add_n*r2
                cost=(n0+add_n)*COST
                return j,stop,'STOP',gross,cost,gross-cost,added,n0+add_n
            # Add once only after +1R; confirmation by close+flow. Combined exposure <=3x equity and breakeven stop.
            if not added:
                oneR=entry+initial_R if side=='LONG' else entry-initial_R
                reached=b['h']>=oneR if side=='LONG' else b['l']<=oneR
                confirm=(b['c']>b['o'] and b['flow']>=1.5) if side=='LONG' else (b['c']<b['o'] and b['flow']<=1/1.5)
                if reached and confirm:
                    room=max(0.0,MAX_LEV*equity-n0); candidate=min(0.5*n0,room)
                    if candidate>0:
                        add_px=b['c']; add_n=candidate; added=True; stop=entry
                        avg=(n0*entry+add_n*add_px)/(n0+add_n)
                        target=avg*(1+TP1 if side=='LONG' else 1-TP1)
            tp=b['h']>=target if side=='LONG' else b['l']<=target
            if tp:
                total=n0+add_n; close_n=TP1_FRAC*total
                # approximate all units at target relative to weighted average
                realized_gross=close_n*TP1
                remaining_n=RUNNER_FRAC*total; tp_done=True
                # runner stop at weighted avg; first half covers full modeled cost enough to keep trade positive
                stop=avg
        else:
            # runner uses 2-bar structural trail, never below/above weighted avg.
            if j>=entry_i+2:
                if side=='LONG':
                    trail=max(avg,min(m1[j-1]['l'],m1[j-2]['l']))
                    if b['l']<=trail:
                        r=(trail/avg-1); gross=realized_gross+remaining_n*r; cost=(n0+add_n)*COST
                        return j,trail,'TP1+RUNNER_TRAIL',gross,cost,gross-cost,added,n0+add_n
                else:
                    trail=min(avg,max(m1[j-1]['h'],m1[j-2]['h']))
                    if b['h']>=trail:
                        r=(avg/trail-1); gross=realized_gross+remaining_n*r; cost=(n0+add_n)*COST
                        return j,trail,'TP1+RUNNER_TRAIL',gross,cost,gross-cost,added,n0+add_n
    # time exit after 4h
    px=m1[end]['c']; total=n0+add_n
    if tp_done:
        r=(px/avg-1) if side=='LONG' else (avg/px-1); gross=realized_gross+remaining_n*r
    else:
        r=(px/entry-1) if side=='LONG' else (entry/px-1); gross=n0*r
        if added:
            r2=(px/add_px-1) if side=='LONG' else (add_px/px-1); gross+=add_n*r2
    cost=total*COST
    return end,px,'TIME',gross,cost,gross-cost,added,total

def summarize(trades,final_eq,peak,maxdd):
    gp=sum(max(t['net_pnl'],0) for t in trades); gl=-sum(min(t['net_pnl'],0) for t in trades); wins=sum(t['net_pnl']>0 for t in trades)
    return {'start_equity':START_EQUITY,'final_equity':round(final_eq,6),'net_pnl':round(final_eq-START_EQUITY,6),'return_pct':round((final_eq/START_EQUITY-1)*100,3),'trades':len(trades),'wins':wins,'losses':len(trades)-wins,'win_rate':round(100*wins/len(trades),1) if trades else None,'gross_pnl':round(sum(t['gross_pnl'] for t in trades),6),'modeled_costs':round(sum(t['cost'] for t in trades),6),'pf_net':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),'max_dd_pct':round(maxdd*100,3),'add_events':sum(t['added'] for t in trades)}

def main():
    raw=[]
    for d in [datetime(2026,8,30,tzinfo=timezone.utc),datetime(2026,8,31,tzinfo=timezone.utc),datetime(2026,9,1,tzinfo=timezone.utc)]: raw+=get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m1=minute_features(raw); f5={x['t']:x for x in features(aggregate(raw,5),5)}; f15={x['t']:x for x in features(aggregate(raw,15),15)}
    equity=START_EQUITY; peak=equity; maxdd=0; trades=[]; i=0; last_exit=-10**9; consec=0; pause_reg=None
    while i<len(m1)-1:
        x=m1[i]
        if x['t']<START_MS:i+=1;continue
        if x['t']>=END_MS:break
        if len(trades)>=MAX_TRADES or equity<=START_EQUITY*(1+DAILY_STOP):break
        if i-last_exit<COOLDOWN:i+=1;continue
        a15=f15.get(last_closed(x['t'],15))
        if pause_reg is not None:
            if a15 and a15['regime']!=pause_reg: pause_reg=None;consec=0
            else:i+=1;continue
        sig=event_signal(i,m1,f5,f15)
        if not sig:i+=1;continue
        setup,side,raw_stop=sig; entry_i=i+1
        if entry_i>=len(m1) or m1[entry_i]['t']>=END_MS:break
        sim=simulate(m1,entry_i,setup,side,raw_stop,equity)
        if sim is None:i+=1;continue
        j,px,reason,gross,cost,net,added,notional=sim
        before=equity; equity+=net; peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak)
        et=datetime.fromtimestamp(m1[entry_i]['t']/1000,timezone.utc).astimezone(TEHRAN); xt=datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        stop_pct=(m1[entry_i]['o']-raw_stop)/m1[entry_i]['o'] if side=='LONG' else (raw_stop-m1[entry_i]['o'])/m1[entry_i]['o']
        trades.append({'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m1[entry_i]['o'],2),'exit':round(px,2),'structural_stop_pct':round(stop_pct*100,3),'reason':reason,'added':added,'notional':round(notional,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)})
        if net>0:consec=0;pause_reg=None
        else:
            consec+=1
            if consec>=2: pause_reg=a15['regime'] if a15 else 'UNKNOWN'
        last_exit=j; i=j+1
    summary=summarize(trades,equity,peak,maxdd)
    payload={'version':'DARA-v1.0-Event-First-Frozen','period_tehran':[START.isoformat(),END.isoformat()],'symbol':'BTCUSDT USD-M perpetual','source':'Binance official USD-M 1m futures klines; taker-buy volume used as historical aggressor-flow proxy; 5m/15m aggregated from 1m; no historical L2 claimed','rules':{'starting_equity':START_EQUITY,'roundtrip_cost_pct':COST*100,'risk_per_full_stop_pct':RISK*100,'max_effective_exposure_x':MAX_LEV,'event_setups':['BREAK_ACCEPT','SWEEP_RECLAIM','IMPULSE_RETEST','EXTREME_REVERSION'],'quality_gate':'5m/15m regime + flow + relative volume + 0.55% historical room + TP1/stop >=1.5','structural_stop_range_pct':[0.12,0.35],'tp1_gross_pct':TP1*100,'tp1_fraction':TP1_FRAC,'runner_fraction':RUNNER_FRAC,'add_once':'after +1R and renewed flow; max +50% initial size; total exposure <=3x; stop to entry','max_trades_day':MAX_TRADES,'daily_stop_pct':DAILY_STOP*100,'two_losses':'pause until 15m regime changes','max_hold_minutes':240},'methodology_notes':['Rules were frozen before the Sep-1 run and not retuned after seeing its result.','Stop is checked before target when both can occur in the same 1m bar (conservative).','Sep 1 was already inspected during prior strategy development, so this is a development/in-sample test, not clean blind validation.'],'summary':summary,'trades':trades}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps(summary,indent=2));print(json.dumps(trades,indent=2))

if __name__=='__main__':main()
