import csv, io, json, math, statistics, urllib.request, zipfile, time
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
ROUNDTRIP_COST=0.0011
RISK=0.0025
MAX_LEV=3.0
OUT=Path('data/seven_trader_styles_sep1.json')
UA='QuantBot-Seven-Traders/1.0'

def getzip(url,tries=5):
    err=None
    for k in range(tries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA})
            with urllib.request.urlopen(req,timeout=60) as r: b=r.read()
            return zipfile.ZipFile(io.BytesIO(b))
        except Exception as e:
            err=e; time.sleep(min(8,0.8*(2**k)))
    raise RuntimeError(f'fetch failed {url}: {err}')

def fetch_agg(ds):
    url=f'https://data.binance.vision/data/futures/um/daily/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-{ds}.zip'
    z=getzip(url); raw=z.read(z.namelist()[0]).decode('utf-8')
    out=[]
    for x in csv.reader(io.StringIO(raw)):
        if not x or not x[0].isdigit(): continue
        # aggTradeId, price, qty, firstId, lastId, time, buyerMaker
        t=int(x[5]); p=float(x[1]); q=float(x[2]); bm=str(x[6]).lower()=='true'
        if START_MS-600000 <= t < END_MS+600000:
            out.append((t,p,q,bm))
    return out

def agg5s(trades):
    g={}
    for t,p,q,bm in trades:
        k=(t//5000)*5000
        if k not in g:
            g[k]={'t':k,'o':p,'h':p,'l':p,'c':p,'v':0.0,'buy':0.0,'sell':0.0}
        b=g[k]; b['h']=max(b['h'],p); b['l']=min(b['l'],p); b['c']=p; b['v']+=q
        if bm: b['sell']+=q
        else: b['buy']+=q
    out=[]
    last=None
    for k in range((START_MS//5000)*5000, ((END_MS-1)//5000)*5000+1,5000):
        if k in g:
            b=g[k]; last=b['c']
        elif last is not None:
            b={'t':k,'o':last,'h':last,'l':last,'c':last,'v':0.0,'buy':0.0,'sell':0.0}
        else: continue
        b['flow']=b['buy']/max(b['sell'],1e-12)
        out.append(b)
    return out

def aggregate(bars,seconds):
    span=seconds*1000; g={}
    for x in bars:
        k=(x['t']//span)*span
        g.setdefault(k,[]).append(x)
    out=[]
    for k in sorted(g):
        a=g[k]
        b={'t':k,'o':a[0]['o'],'h':max(x['h'] for x in a),'l':min(x['l'] for x in a),'c':a[-1]['c'],
           'v':sum(x['v'] for x in a),'buy':sum(x['buy'] for x in a),'sell':sum(x['sell'] for x in a)}
        b['flow']=b['buy']/max(b['sell'],1e-12)
        out.append(b)
    return out

def ema(vals,n):
    a=2/(n+1); out=[]; e=None
    for v in vals:
        e=v if e is None else a*v+(1-a)*e; out.append(e)
    return out

def median_prev(vals,n):
    out=[]; q=deque()
    for v in vals:
        out.append(statistics.median(q) if q else v); q.append(v)
        if len(q)>n:q.popleft()
    return out

def prepare(bars):
    vols=[x['v'] for x in bars]; med=median_prev(vols,24); closes=[x['c'] for x in bars]; e9=ema(closes,9); e21=ema(closes,21)
    out=[]
    for i,x in enumerate(bars):
        y=dict(x); y['medv']=max(med[i],1e-12); y['ema9']=e9[i]; y['ema21']=e21[i]; y['body']=(x['c']-x['o'])/x['o']; out.append(y)
    return out

def size_for(equity,stop_pct):
    return min(MAX_LEV*equity, equity*RISK/max(stop_pct+ROUNDTRIP_COST,1e-9))

def record_trade(name,side,entry_t,exit_t,entry,exitp,equity,notional,reason,gross_override=None,cost_mult=1.0):
    r=(exitp/entry-1) if side=='LONG' else (entry/exitp-1)
    gross=notional*r if gross_override is None else gross_override
    cost=notional*ROUNDTRIP_COST*cost_mult
    net=gross-cost
    return {'trader':name,'side':side,'entry_time':datetime.fromtimestamp(entry_t/1000,timezone.utc).astimezone(TEHRAN).isoformat(),
            'exit_time':datetime.fromtimestamp(exit_t/1000,timezone.utc).astimezone(TEHRAN).isoformat(),'entry':round(entry,2),'exit':round(exitp,2),
            'reason':reason,'notional':round(notional,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),
            'equity_before':round(equity,6),'equity_after':round(equity+net,6)}

def fixed_trade(name,bars,entry_i,side,equity,stop_pct,target_pct,max_bars):
    entry=bars[entry_i]['o']; notional=size_for(equity,stop_pct); stop=entry*(1-stop_pct if side=='LONG' else 1+stop_pct); target=entry*(1+target_pct if side=='LONG' else 1-target_pct)
    end=min(len(bars)-1,entry_i+max_bars)
    for j in range(entry_i,end+1):
        b=bars[j]; st=(b['l']<=stop if side=='LONG' else b['h']>=stop); tp=(b['h']>=target if side=='LONG' else b['l']<=target)
        if st: return j,record_trade(name,side,bars[entry_i]['t'],b['t'],entry,stop,equity,notional,'STOP')
        if tp: return j,record_trade(name,side,bars[entry_i]['t'],b['t'],entry,target,equity,notional,'TARGET')
    b=bars[end]; return end,record_trade(name,side,bars[entry_i]['t'],b['t'],entry,b['c'],equity,notional,'TIME')

def run_rotter(bars):
    name='Paul Rotter'; equity=START_EQUITY; trades=[]; i=12
    while i<len(bars)-2 and len(trades)<100:
        prev=bars[i-6:i]; hi=max(x['h'] for x in prev); lo=min(x['l'] for x in prev); x=bars[i]; sig=None
        if x['h']>hi and x['flow']>=2.2 and x['c']<hi: sig='SHORT'
        elif x['l']<lo and x['flow']<=1/2.2 and x['c']>lo: sig='LONG'
        if sig:
            ei=i+1; entry=bars[ei]['o']; tick5=0.5/entry
            j,t=fixed_trade(name,bars,ei,sig,equity,tick5,tick5,6); equity=t['equity_after']; trades.append(t); i=j+1
        else:i+=1
    return trades

def run_testa(bars):
    name='Testa'; equity=START_EQUITY; trades=[]; i=24
    while i<len(bars)-20 and len(trades)<40:
        x=bars[i]; prev=bars[i-12:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); sig=None; level=None
        if x['c']>hi*1.00003 and x['flow']>=1.8 and x['v']>=1.5*x['medv'] and x['body']>0: sig='LONG'; level=hi
        elif x['c']<lo*0.99997 and x['flow']<=1/1.8 and x['v']>=1.5*x['medv'] and x['body']<0: sig='SHORT'; level=lo
        if not sig: i+=1; continue
        ei=i+1; entry=bars[ei]['o']; stop_pct=0.0008; notional=size_for(equity,stop_pct); stop=entry*(1-stop_pct if sig=='LONG' else 1+stop_pct); end=min(len(bars)-1,ei+18); weak=0; done=None
        for j in range(ei,end+1):
            b=bars[j]
            if (b['l']<=stop if sig=='LONG' else b['h']>=stop): done=(j,stop,'STOP'); break
            fail=(b['c']<level if sig=='LONG' else b['c']>level)
            flowweak=(b['flow']<0.85 if sig=='LONG' else b['flow']>1.18); weak=weak+1 if flowweak else 0
            if fail or weak>=2: done=(j,b['c'],'MOMENTUM_STALL'); break
        if done is None: done=(end,bars[end]['c'],'TIME')
        j,px,why=done; t=record_trade(name,sig,bars[ei]['t'],bars[j]['t'],entry,px,equity,notional,why); equity=t['equity_after']; trades.append(t); i=j+1
    return trades

def run_jun(bars):
    name='Jun FX'; equity=START_EQUITY; trades=[]; i=200
    while i<len(bars)-15 and len(trades)<40:
        x=bars[i]; prev=bars[i-180:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); sig=None; kind=None
        if x['c']>hi*1.00003 and x['flow']>=1.7 and x['v']>=1.4*x['medv']: sig='LONG'; kind='BREAK_HIGH'
        elif x['c']<lo*0.99997 and x['flow']<=1/1.7 and x['v']>=1.4*x['medv']: sig='SHORT'; kind='BREAK_LOW'
        elif x['h']>hi*1.00003 and x['c']<hi and x['flow']<=0.8: sig='SHORT'; kind='FAILED_HIGH'
        elif x['l']<lo*0.99997 and x['c']>lo and x['flow']>=1.25: sig='LONG'; kind='FAILED_LOW'
        if not sig: i+=1; continue
        ei=i+1; entry=bars[ei]['o']; stop_pct=0.0008; notional=size_for(equity,stop_pct); stop=entry*(1-stop_pct if sig=='LONG' else 1+stop_pct); end=min(len(bars)-1,ei+12); done=None; weak=0
        for j in range(ei,end+1):
            b=bars[j]
            if (b['l']<=stop if sig=='LONG' else b['h']>=stop): done=(j,stop,'STOP_'+kind); break
            fw=(b['flow']<0.9 if sig=='LONG' else b['flow']>1.1); weak=weak+1 if fw else 0
            if weak>=2: done=(j,b['c'],'FLOW_EXIT_'+kind); break
        if done is None: done=(end,bars[end]['c'],'TIME_'+kind)
        j,px,why=done; t=record_trade(name,sig,bars[ei]['t'],bars[j]['t'],entry,px,equity,notional,why); equity=t['equity_after']; trades.append(t); i=j+1
    return trades

def run_hansan(bars):
    name='Hansan'; equity=START_EQUITY; trades=[]; i=80
    while i<len(bars)-10 and len(trades)<60:
        x=bars[i]; prev=bars[i-60:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); mom=x['c']/bars[i-12]['c']-1; sig=None
        if x['c']>hi and mom>0 and x['flow']>=1.5 and x['v']>=1.2*x['medv']: sig='LONG'
        elif x['c']<lo and mom<0 and x['flow']<=1/1.5 and x['v']>=1.2*x['medv']: sig='SHORT'
        if not sig: i+=1; continue
        ei=i+1; entry=bars[ei]['o']; stop_pct=0.0005; notional=size_for(equity,stop_pct); stop=entry*(1-stop_pct if sig=='LONG' else 1+stop_pct); end=min(len(bars)-1,ei+6); done=None
        for j in range(ei,end+1):
            b=bars[j]
            if (b['l']<=stop if sig=='LONG' else b['h']>=stop): done=(j,stop,'STOP'); break
            adverse=(b['c']<bars[j]['o'] and b['flow']<1 if sig=='LONG' else b['c']>bars[j]['o'] and b['flow']>1)
            if adverse: done=(j,b['c'],'IMMEDIATE_INVALIDATION'); break
        if done is None: done=(end,bars[end]['c'],'SECONDS_EXIT')
        j,px,why=done; t=record_trade(name,sig,bars[ei]['t'],bars[j]['t'],entry,px,equity,notional,why); equity=t['equity_after']; trades.append(t); i=j+1
    return trades

def run_cis(bars5m):
    name='cis'; equity=START_EQUITY; trades=[]; i=24; last_exit=-1
    while i<len(bars5m)-2 and len(trades)<12:
        x=bars5m[i]; prev=bars5m[i-12:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); sig=None
        if x['c']>hi and x['body']>0 and x['v']>=1.3*x['medv'] and x['ema9']>x['ema21']: sig='LONG'
        elif x['c']<lo and x['body']<0 and x['v']>=1.3*x['medv'] and x['ema9']<x['ema21']: sig='SHORT'
        if not sig: i+=1; continue
        ei=i+1; entry=bars5m[ei]['o']; structural=(entry-x['l'])/entry if sig=='LONG' else (x['h']-entry)/entry; stop_pct=min(0.005,max(0.0015,structural)); notional=size_for(equity,stop_pct); stop=entry*(1-stop_pct if sig=='LONG' else 1+stop_pct); end=min(len(bars5m)-1,ei+48); done=None
        for j in range(ei,end+1):
            b=bars5m[j]
            if (b['l']<=stop if sig=='LONG' else b['h']>=stop): done=(j,stop,'STOP'); break
            if j>=ei+2:
                if sig=='LONG':
                    trail=min(bars5m[j-1]['l'],bars5m[j-2]['l'])
                    if b['l']<=trail: done=(j,trail,'TREND_TRAIL'); break
                else:
                    trail=max(bars5m[j-1]['h'],bars5m[j-2]['h'])
                    if b['h']>=trail: done=(j,trail,'TREND_TRAIL'); break
        if done is None: done=(end,bars5m[end]['c'],'TIME')
        j,px,why=done; t=record_trade(name,sig,bars5m[ei]['t'],bars5m[j]['t'],entry,px,equity,notional,why); equity=t['equity_after']; trades.append(t); i=j+1
    return trades

def run_hougaard(bars5m):
    name='Tom Hougaard'; equity=START_EQUITY; trades=[]; i=2
    while i<len(bars5m)-5 and len(trades)<10:
        a,b=bars5m[i-1],bars5m[i]; bull=(b['c']>b['o'] and a['c']<a['o'] and b['o']<=a['c'] and b['c']>=a['o']); bear=(b['c']<b['o'] and a['c']>a['o'] and b['o']>=a['c'] and b['c']<=a['o']); side='LONG' if bull else ('SHORT' if bear else None)
        if not side: i+=1; continue
        trigger=b['h'] if side=='LONG' else b['l']; stop=b['l'] if side=='LONG' else b['h']; trig_i=None
        for k in range(i+1,min(i+4,len(bars5m))):
            if (bars5m[k]['h']>=trigger if side=='LONG' else bars5m[k]['l']<=trigger): trig_i=k; break
        if trig_i is None: i+=1; continue
        entry=trigger; stop_pct=abs(entry-stop)/entry
        if stop_pct<=0 or stop_pct>0.008: i=trig_i+1; continue
        n1=size_for(equity,stop_pct); R=abs(entry-stop); end=min(len(bars5m)-1,trig_i+36); add_px=entry+R if side=='LONG' else entry-R; added=False; n2=0.0; add_entry=None; cur_stop=stop; done=None
        for j in range(trig_i,end+1):
            q=bars5m[j]
            if (q['l']<=cur_stop if side=='LONG' else q['h']>=cur_stop): done=(j,cur_stop,'STOP_OR_TRAIL'); break
            if not added and (q['h']>=add_px if side=='LONG' else q['l']<=add_px):
                added=True; add_entry=add_px; n2=max(0.0,min(n1,MAX_LEV*equity-n1)); cur_stop=entry
            if added and j>=trig_i+2:
                if side=='LONG': cur_stop=max(cur_stop,min(bars5m[j-1]['l'],bars5m[j-2]['l']))
                else: cur_stop=min(cur_stop,max(bars5m[j-1]['h'],bars5m[j-2]['h']))
        if done is None: done=(end,bars5m[end]['c'],'TIME')
        j,exitp,why=done; r1=(exitp/entry-1) if side=='LONG' else (entry/exitp-1); gross=n1*r1
        if added and n2>0:
            r2=(exitp/add_entry-1) if side=='LONG' else (add_entry/exitp-1); gross+=n2*r2
        cost=(n1+n2)*ROUNDTRIP_COST; net=gross-cost
        t={'trader':name,'side':side,'entry_time':datetime.fromtimestamp(bars5m[trig_i]['t']/1000,timezone.utc).astimezone(TEHRAN).isoformat(),'exit_time':datetime.fromtimestamp(bars5m[j]['t']/1000,timezone.utc).astimezone(TEHRAN).isoformat(),'entry':round(entry,2),'exit':round(exitp,2),'reason':why+('_ADDED' if added else ''),'notional':round(n1+n2,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(equity,6),'equity_after':round(equity+net,6)}
        equity+=net; trades.append(t); i=j+1
    return trades

def fetch_aug_daily():
    url=f'https://data.binance.vision/data/futures/um/monthly/klines/{SYMBOL}/1d/{SYMBOL}-1d-2026-08.zip'
    z=getzip(url); raw=z.read(z.namelist()[0]).decode('utf-8'); out=[]
    for x in csv.reader(io.StringIO(raw)):
        if x and x[0].isdigit(): out.append(float(x[4]))
    return out

def run_bnf(bars5m):
    name='BNF'; closes=fetch_aug_daily(); sma25=sum(closes[-25:])/25; equity=START_EQUITY; trades=[]
    # Public classic BNF reversal idea: unusually deep negative deviation from 25-day MA. Literal conservative threshold = -15%.
    for i,x in enumerate(bars5m[:-1]):
        dev=x['c']/sma25-1
        if dev<=-0.15:
            ei=i+1; entry=bars5m[ei]['o']; stop_pct=0.03; notional=size_for(equity,stop_pct); target=sma25*0.90; stop=entry*(1-stop_pct); end=min(len(bars5m)-1,ei+48); done=None
            for j in range(ei,end+1):
                b=bars5m[j]
                if b['l']<=stop: done=(j,stop,'STOP'); break
                if b['h']>=target: done=(j,target,'MEAN_REVERSION'); break
            if done is None: done=(end,bars5m[end]['c'],'TIME')
            j,px,why=done; t=record_trade(name,'LONG',bars5m[ei]['t'],bars5m[j]['t'],entry,px,equity,notional,why); trades.append(t); break
    return trades

def summarize(trades):
    eq=START_EQUITY; gp=gl=ggp=ggl=0.0; wins=0; peak=eq; dd=0
    for t in trades:
        eq=t['equity_after']; peak=max(peak,eq); dd=max(dd,(peak-eq)/peak)
        if t['net_pnl']>0: wins+=1; gp+=t['net_pnl']
        else: gl-=t['net_pnl']
        if t['gross_pnl']>0: ggp+=t['gross_pnl']
        else: ggl-=t['gross_pnl']
    return {'trades':len(trades),'wins':wins,'losses':len(trades)-wins,'win_rate':round(100*wins/len(trades),1) if trades else None,'gross_pnl':round(sum(t['gross_pnl'] for t in trades),6),'costs':round(sum(t['cost'] for t in trades),6),'net_pnl':round(eq-START_EQUITY,6),'final_equity':round(eq,6),'pf_net':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),'pf_gross':round(ggp/ggl,2) if ggl>0 else (99.0 if ggp>0 else None),'max_dd_pct':round(dd*100,3)}

def main():
    raw=fetch_agg('2026-08-31')+fetch_agg('2026-09-01')
    raw.sort(key=lambda x:x[0]); b5s=prepare(agg5s(raw)); b5m=prepare(aggregate(b5s,300))
    runs={'Paul Rotter':run_rotter(b5s),'Testa':run_testa(b5s),'BNF':run_bnf(b5m),'cis':run_cis(b5m),'Tom Hougaard':run_hougaard(b5m),'Jun FX':run_jun(b5s),'Hansan':run_hansan(b5s)}
    fidelity={'Paul Rotter':'LOW: historical L2/order book unavailable; 5-second aggressor-flow failed-break proxy only','Testa':'MEDIUM-LOW: public supply/demand+tape philosophy mapped to 5-second Binance aggressor flow; no historical order book','BNF':'MEDIUM: classic public 25-day MA-deviation reversal rule used literally; single-asset BTC cannot reproduce stock-selection breadth','cis':'MEDIUM: public momentum/trend-follow + quick-loss/let-winner-run philosophy mapped to 5m BTC','Tom Hougaard':'MEDIUM-HIGH: public 5m price-action/engulfing entry, structural stop, add-to-winner and trailing logic reconstructed from his own material','Jun FX':'MEDIUM: public high/low, breakout/failed-break and market-psychology seconds-scalp logic mapped to 5-second BTC','Hansan':'MEDIUM: public trend-follow breakout and immediate-invalidation seconds-scalp logic mapped to 5-second BTC'}
    sources_note={'Paul Rotter':'order-book scalper; 3-5 ticks; close quickly when trade goes against','Testa':'short-horizon scalping; board/tape supply-demand; cut when thesis fails','BNF':'classic negative deviation from 25-day MA and rebound','cis':'buy strength / sell weakness; fast loss cutting; focus on total P&L','Tom Hougaard':'price action/mechanical entries; chart/ATR stops; add to winners; no fixed target','Jun FX':'seconds scalping near significant highs/lows; breakout or failed-break depending on market psychology','Hansan':'trend-follow breakout; enter before/at/just after line; very fast invalidation, avg FX stop about 1 pip'}
    result={k:summarize(v) for k,v in runs.items()}
    payload={'version':'Seven-Trader-Style-Separate-Test-v1','period_tehran':[START.isoformat(),END.isoformat()],'symbol':'BTCUSDT USD-M perpetual','starting_equity_each':START_EQUITY,'common_comparison_controls':{'roundtrip_cost_pct':ROUNDTRIP_COST*100,'risk_budget_per_trade_pct':RISK*100,'max_leverage':MAX_LEV,'note':'Signal/exit logic follows each public style reconstruction; position-risk budget is normalized so results compare style edge rather than wealth/leverage.'},'data':'Binance official USD-M aggTrades, aggregated to 5-second bars; 5m derived; taker direction from buyer-maker flag. No historical full L2 order book claimed.','fidelity':fidelity,'style_basis':sources_note,'results':result,'trades':runs}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
