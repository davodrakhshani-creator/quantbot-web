import csv, io, json, math, statistics, urllib.request, zipfile, time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

SYMBOL='BTCUSDT'
TEHRAN=timezone(timedelta(hours=3,minutes=30))
DATE0=datetime(2026,9,1,0,0,tzinfo=TEHRAN)
START_EQUITY=100.0
ROUNDTRIP_COST=0.0011
TP_PCT=3*ROUNDTRIP_COST   # +0.33% gross move
RISK=0.0025
MAX_LEV=3.0
OUT=Path('data/seven_trader_styles_sep1_8_tp3fee.json')
UA='QuantBot-Seven-Traders-TP3Fee/1.0'

# Public-style reconstructions inherited from the Sep-1 comparison.
# Only common modification in this experiment: no discretionary/profit exit before +3x total fee.
# Native hard stops / negative invalidation remain style-specific.

def getzip(url,tries=5):
    err=None
    for k in range(tries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA})
            with urllib.request.urlopen(req,timeout=90) as r: b=r.read()
            return zipfile.ZipFile(io.BytesIO(b))
        except Exception as e:
            err=e; time.sleep(min(10,1.0*(2**k)))
    raise RuntimeError(f'fetch failed {url}: {err}')

def fetch_agg_utc_date(d):
    ds=d.strftime('%Y-%m-%d')
    url=f'https://data.binance.vision/data/futures/um/daily/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-{ds}.zip'
    z=getzip(url); raw=z.read(z.namelist()[0]).decode('utf-8')
    out=[]
    for x in csv.reader(io.StringIO(raw)):
        if not x or not x[0].isdigit(): continue
        # aggTradeId, price, qty, firstTradeId, lastTradeId, timestamp, buyerMaker
        out.append((int(x[5]),float(x[1]),float(x[2]),str(x[6]).lower()=='true'))
    return out

def agg5s(trades,start_ms,end_ms):
    g={}
    for t,p,q,bm in trades:
        if not (start_ms <= t < end_ms): continue
        k=(t//5000)*5000
        if k not in g: g[k]={'t':k,'o':p,'h':p,'l':p,'c':p,'v':0.0,'buy':0.0,'sell':0.0}
        b=g[k]; b['h']=max(b['h'],p); b['l']=min(b['l'],p); b['c']=p; b['v']+=q
        if bm: b['sell']+=q
        else: b['buy']+=q
    out=[]; last=None
    k=(start_ms//5000)*5000
    while k < end_ms:
        if k in g:
            b=g[k]; last=b['c']
        elif last is not None:
            b={'t':k,'o':last,'h':last,'l':last,'c':last,'v':0.0,'buy':0.0,'sell':0.0}
        else:
            k+=5000; continue
        b['flow']=b['buy']/max(b['sell'],1e-12); out.append(b); k+=5000
    return out

def aggregate(bars,seconds):
    span=seconds*1000; g={}
    for x in bars: g.setdefault((x['t']//span)*span,[]).append(x)
    out=[]
    for k in sorted(g):
        a=g[k]
        b={'t':k,'o':a[0]['o'],'h':max(x['h'] for x in a),'l':min(x['l'] for x in a),'c':a[-1]['c'],'v':sum(x['v'] for x in a),'buy':sum(x['buy'] for x in a),'sell':sum(x['sell'] for x in a)}
        b['flow']=b['buy']/max(b['sell'],1e-12); out.append(b)
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
        if len(q)>n: q.popleft()
    return out

def prepare(bars):
    vols=[x['v'] for x in bars]; med=median_prev(vols,24); closes=[x['c'] for x in bars]; e9=ema(closes,9); e21=ema(closes,21)
    out=[]
    for i,x in enumerate(bars):
        y=dict(x); y['medv']=max(med[i],1e-12); y['ema9']=e9[i]; y['ema21']=e21[i]; y['body']=(x['c']-x['o'])/x['o']; out.append(y)
    return out

def size_for(equity,stop_pct):
    # risk budget includes modeled roundtrip cost, capped by max leverage
    return min(MAX_LEV*equity, equity*RISK/max(stop_pct+ROUNDTRIP_COST,1e-9))

def make_trade(name,side,entry_t,exit_t,entry,exitp,equity,notional,reason):
    r=(exitp/entry-1) if side=='LONG' else (entry/exitp-1)
    gross=notional*r; cost=notional*ROUNDTRIP_COST; net=gross-cost
    return {'trader':name,'side':side,'entry_time':datetime.fromtimestamp(entry_t/1000,timezone.utc).astimezone(TEHRAN).isoformat(),'exit_time':datetime.fromtimestamp(exit_t/1000,timezone.utc).astimezone(TEHRAN).isoformat(),'entry':round(entry,2),'exit':round(exitp,2),'reason':reason,'notional':round(notional,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(equity,6),'equity_after':round(equity+net,6)}

def hold_to_tp_or_stop(name,bars,entry_i,side,equity,stop_pct,day_end_ms,negative_invalidation=None):
    entry=bars[entry_i]['o']; notional=size_for(equity,stop_pct)
    stop=entry*(1-stop_pct if side=='LONG' else 1+stop_pct); target=entry*(1+TP_PCT if side=='LONG' else 1-TP_PCT)
    last=entry_i
    for j in range(entry_i,len(bars)):
        b=bars[j]
        if b['t']>=day_end_ms: break
        last=j
        st=(b['l']<=stop if side=='LONG' else b['h']>=stop); tp=(b['h']>=target if side=='LONG' else b['l']<=target)
        if st: return j,make_trade(name,side,bars[entry_i]['t'],b['t'],entry,stop,equity,notional,'STYLE_STOP')
        if tp: return j,make_trade(name,side,bars[entry_i]['t'],b['t'],entry,target,equity,notional,'TP_3X_FEE')
        # style-specific thesis failure may close only if position is not positive
        if negative_invalidation is not None:
            inval=negative_invalidation(j,b,entry)
            unreal=(b['c']/entry-1) if side=='LONG' else (entry/b['c']-1)
            if inval and unreal<=0:
                return j,make_trade(name,side,bars[entry_i]['t'],b['t'],entry,b['c'],equity,notional,'NEGATIVE_INVALIDATION')
    b=bars[last]
    return last,make_trade(name,side,bars[entry_i]['t'],b['t'],entry,b['c'],equity,notional,'DAY_END')

def run_rotter(bars,day_start_ms,day_end_ms):
    name='Paul Rotter'; equity=START_EQUITY; trades=[]; i=12
    while i<len(bars)-2 and len(trades)<100:
        x=bars[i]
        if x['t']<day_start_ms: i+=1; continue
        if x['t']>=day_end_ms: break
        prev=bars[i-6:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); sig=None
        if x['h']>hi and x['flow']>=2.2 and x['c']<hi: sig='SHORT'
        elif x['l']<lo and x['flow']<=1/2.2 and x['c']>lo: sig='LONG'
        if sig:
            ei=i+1; stop_pct=0.5/max(bars[ei]['o'],1e-12)
            def inval(j,b,entry):
                return (b['flow']>1.25 if sig=='SHORT' else b['flow']<0.8)
            j,t=hold_to_tp_or_stop(name,bars,ei,sig,equity,stop_pct,day_end_ms,inval); equity=t['equity_after']; trades.append(t); i=j+1
        else: i+=1
    return trades

def run_testa(bars,day_start_ms,day_end_ms):
    name='Testa'; equity=START_EQUITY; trades=[]; i=24
    while i<len(bars)-2 and len(trades)<40:
        x=bars[i]
        if x['t']<day_start_ms: i+=1; continue
        if x['t']>=day_end_ms: break
        prev=bars[i-12:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); sig=None; level=None
        if x['c']>hi*1.00003 and x['flow']>=1.8 and x['v']>=1.5*x['medv'] and x['body']>0: sig='LONG'; level=hi
        elif x['c']<lo*0.99997 and x['flow']<=1/1.8 and x['v']>=1.5*x['medv'] and x['body']<0: sig='SHORT'; level=lo
        if sig:
            ei=i+1; weak={'n':0}
            def inval(j,b,entry):
                fail=(b['c']<level if sig=='LONG' else b['c']>level)
                fw=(b['flow']<0.85 if sig=='LONG' else b['flow']>1.18); weak['n']=weak['n']+1 if fw else 0
                return fail or weak['n']>=2
            j,t=hold_to_tp_or_stop(name,bars,ei,sig,equity,0.0008,day_end_ms,inval); equity=t['equity_after']; trades.append(t); i=j+1
        else: i+=1
    return trades

def run_jun(bars,day_start_ms,day_end_ms):
    name='Jun FX'; equity=START_EQUITY; trades=[]; i=200
    while i<len(bars)-2 and len(trades)<40:
        x=bars[i]
        if x['t']<day_start_ms: i+=1; continue
        if x['t']>=day_end_ms: break
        prev=bars[i-180:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); sig=None; kind=None
        if x['c']>hi*1.00003 and x['flow']>=1.7 and x['v']>=1.4*x['medv']: sig='LONG'; kind='BREAK_HIGH'
        elif x['c']<lo*0.99997 and x['flow']<=1/1.7 and x['v']>=1.4*x['medv']: sig='SHORT'; kind='BREAK_LOW'
        elif x['h']>hi*1.00003 and x['c']<hi and x['flow']<=0.8: sig='SHORT'; kind='FAILED_HIGH'
        elif x['l']<lo*0.99997 and x['c']>lo and x['flow']>=1.25: sig='LONG'; kind='FAILED_LOW'
        if sig:
            ei=i+1; weak={'n':0}
            def inval(j,b,entry):
                fw=(b['flow']<0.9 if sig=='LONG' else b['flow']>1.1); weak['n']=weak['n']+1 if fw else 0
                return weak['n']>=2
            j,t=hold_to_tp_or_stop(name,bars,ei,sig,equity,0.0008,day_end_ms,inval); t['reason']+='_'+kind; equity=t['equity_after']; trades.append(t); i=j+1
        else: i+=1
    return trades

def run_hansan(bars,day_start_ms,day_end_ms):
    name='Hansan'; equity=START_EQUITY; trades=[]; i=80
    while i<len(bars)-2 and len(trades)<60:
        x=bars[i]
        if x['t']<day_start_ms: i+=1; continue
        if x['t']>=day_end_ms: break
        prev=bars[i-60:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); mom=x['c']/bars[i-12]['c']-1; sig=None
        if x['c']>hi and mom>0 and x['flow']>=1.5 and x['v']>=1.2*x['medv']: sig='LONG'
        elif x['c']<lo and mom<0 and x['flow']<=1/1.5 and x['v']>=1.2*x['medv']: sig='SHORT'
        if sig:
            ei=i+1
            def inval(j,b,entry):
                return (b['c']<b['o'] and b['flow']<1) if sig=='LONG' else (b['c']>b['o'] and b['flow']>1)
            j,t=hold_to_tp_or_stop(name,bars,ei,sig,equity,0.0005,day_end_ms,inval); equity=t['equity_after']; trades.append(t); i=j+1
        else: i+=1
    return trades

def run_cis(bars5m,day_start_ms,day_end_ms):
    name='cis'; equity=START_EQUITY; trades=[]; i=24
    while i<len(bars5m)-2 and len(trades)<12:
        x=bars5m[i]
        if x['t']<day_start_ms: i+=1; continue
        if x['t']>=day_end_ms: break
        prev=bars5m[i-12:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); sig=None
        if x['c']>hi and x['body']>0 and x['v']>=1.3*x['medv'] and x['ema9']>x['ema21']: sig='LONG'
        elif x['c']<lo and x['body']<0 and x['v']>=1.3*x['medv'] and x['ema9']<x['ema21']: sig='SHORT'
        if sig:
            ei=i+1; entry=bars5m[ei]['o']; structural=(entry-x['l'])/entry if sig=='LONG' else (x['h']-entry)/entry; stop_pct=min(0.005,max(0.0015,structural))
            def inval(j,b,entry):
                # cis-style momentum failure: two-bar structure gives way against position
                if j<2:return False
                return (b['c']<bars5m[j-2]['l']) if sig=='LONG' else (b['c']>bars5m[j-2]['h'])
            j,t=hold_to_tp_or_stop(name,bars5m,ei,sig,equity,stop_pct,day_end_ms,inval); equity=t['equity_after']; trades.append(t); i=j+1
        else: i+=1
    return trades

def run_hougaard(bars5m,day_start_ms,day_end_ms):
    name='Tom Hougaard'; equity=START_EQUITY; trades=[]; i=2
    while i<len(bars5m)-5 and len(trades)<10:
        a,b=bars5m[i-1],bars5m[i]
        if b['t']<day_start_ms: i+=1; continue
        if b['t']>=day_end_ms: break
        bull=(b['c']>b['o'] and a['c']<a['o'] and b['o']<=a['c'] and b['c']>=a['o']); bear=(b['c']<b['o'] and a['c']>a['o'] and b['o']>=a['c'] and b['c']<=a['o']); side='LONG' if bull else ('SHORT' if bear else None)
        if not side: i+=1; continue
        trigger=b['h'] if side=='LONG' else b['l']; stop=b['l'] if side=='LONG' else b['h']; trig_i=None
        for k in range(i+1,min(i+4,len(bars5m))):
            if bars5m[k]['t']>=day_end_ms: break
            if (bars5m[k]['h']>=trigger if side=='LONG' else bars5m[k]['l']<=trigger): trig_i=k; break
        if trig_i is None: i+=1; continue
        entry=trigger; stop_pct=abs(entry-stop)/entry
        if stop_pct<=0 or stop_pct>0.008: i=trig_i+1; continue
        n=size_for(equity,stop_pct); target=entry*(1+TP_PCT if side=='LONG' else 1-TP_PCT); last=trig_i; done=None
        # Common experiment deliberately disables add-to-winner so TP is exactly comparable at +3x fee;
        # Hougaard's native structural stop is preserved.
        for j in range(trig_i,len(bars5m)):
            q=bars5m[j]
            if q['t']>=day_end_ms: break
            last=j; st=(q['l']<=stop if side=='LONG' else q['h']>=stop); tp=(q['h']>=target if side=='LONG' else q['l']<=target)
            if st: done=(j,stop,'STYLE_STOP'); break
            if tp: done=(j,target,'TP_3X_FEE'); break
        if done is None: done=(last,bars5m[last]['c'],'DAY_END')
        j,px,why=done; t=make_trade(name,side,bars5m[trig_i]['t'],bars5m[j]['t'],entry,px,equity,n,why); equity=t['equity_after']; trades.append(t); i=j+1
    return trades

def fetch_aug_daily():
    url=f'https://data.binance.vision/data/futures/um/monthly/klines/{SYMBOL}/1d/{SYMBOL}-1d-2026-08.zip'
    z=getzip(url); raw=z.read(z.namelist()[0]).decode('utf-8'); out=[]
    for x in csv.reader(io.StringIO(raw)):
        if x and x[0].isdigit(): out.append(float(x[4]))
    return out

def run_bnf(bars5m,day_start_ms,day_end_ms,sma25):
    name='BNF'; equity=START_EQUITY; trades=[]
    for i,x in enumerate(bars5m[:-1]):
        if x['t']<day_start_ms: continue
        if x['t']>=day_end_ms: break
        dev=x['c']/sma25-1
        if dev<=-0.15:
            ei=i+1; entry=bars5m[ei]['o']; stop_pct=0.03
            j,t=hold_to_tp_or_stop(name,bars5m,ei,'LONG',equity,stop_pct,day_end_ms,None); trades.append(t); break
    return trades

def summarize(trades):
    eq=START_EQUITY; gp=gl=ggp=ggl=0.0; wins=0; peak=eq; dd=0.0
    for t in trades:
        eq=t['equity_after']; peak=max(peak,eq); dd=max(dd,(peak-eq)/peak)
        if t['net_pnl']>0: wins+=1; gp+=t['net_pnl']
        else: gl-=t['net_pnl']
        if t['gross_pnl']>0: ggp+=t['gross_pnl']
        else: ggl-=t['gross_pnl']
    return {'trades':len(trades),'wins':wins,'losses':len(trades)-wins,'win_rate':round(100*wins/len(trades),1) if trades else None,'gross_pnl':round(sum(t['gross_pnl'] for t in trades),6),'costs':round(sum(t['cost'] for t in trades),6),'net_pnl':round(eq-START_EQUITY,6),'final_equity':round(eq,6),'pf_net':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),'pf_gross':round(ggp/ggl,2) if ggl>0 else (99.0 if ggp>0 else None),'max_dd_pct':round(dd*100,3)}

def main():
    aug=fetch_aug_daily(); sma25=sum(aug[-25:])/25
    all_daily=[]; by_trader={k:[] for k in ['Paul Rotter','Testa','BNF','cis','Tom Hougaard','Jun FX','Hansan']}; all_trades={k:[] for k in by_trader}
    # Cache UTC daily aggTrades because Tehran day spans two UTC dates.
    cache={}
    def utc_trades(d):
        key=d.strftime('%Y-%m-%d')
        if key not in cache: cache[key]=fetch_agg_utc_date(d)
        return cache[key]
    for dayn in range(8):
        local0=DATE0+timedelta(days=dayn); local1=local0+timedelta(days=1)
        start_ms=int(local0.astimezone(timezone.utc).timestamp()*1000); end_ms=int(local1.astimezone(timezone.utc).timestamp()*1000)
        warm_ms=start_ms-3600*1000
        u0=datetime.fromtimestamp(warm_ms/1000,timezone.utc).date(); u1=datetime.fromtimestamp((end_ms-1)/1000,timezone.utc).date()
        trades=[]; d=u0
        while d<=u1:
            trades.extend(utc_trades(datetime(d.year,d.month,d.day,tzinfo=timezone.utc))); d+=timedelta(days=1)
        trades.sort(key=lambda x:x[0])
        b5s=prepare(agg5s(trades,warm_ms,end_ms)); b5m=prepare(aggregate(b5s,300))
        runs={'Paul Rotter':run_rotter(b5s,start_ms,end_ms),'Testa':run_testa(b5s,start_ms,end_ms),'BNF':run_bnf(b5m,start_ms,end_ms,sma25),'cis':run_cis(b5m,start_ms,end_ms),'Tom Hougaard':run_hougaard(b5m,start_ms,end_ms),'Jun FX':run_jun(b5s,start_ms,end_ms),'Hansan':run_hansan(b5s,start_ms,end_ms)}
        row={'day':local0.strftime('%Y-%m-%d'),'results':{}}
        for name,tr in runs.items():
            s=summarize(tr); row['results'][name]=s; by_trader[name].append({'day':row['day'],**s}); all_trades[name].extend([{'day':row['day'],**x} for x in tr])
        all_daily.append(row)
        # release older cache days to reduce memory
        keep={datetime.fromtimestamp((start_ms-3600*1000)/1000,timezone.utc).strftime('%Y-%m-%d'),datetime.fromtimestamp((end_ms-1)/1000,timezone.utc).strftime('%Y-%m-%d')}
        cache={k:v for k,v in cache.items() if k in keep}
    aggregate_results={}
    for name,days in by_trader.items():
        net=sum(d['net_pnl'] for d in days); gross=sum(d['gross_pnl'] for d in days); costs=sum(d['costs'] for d in days); trades=sum(d['trades'] for d in days); wins=sum(d['wins'] for d in days); losses=sum(d['losses'] for d in days)
        # PF from actual trade PnLs across all daily-reset simulations
        tr=all_trades[name]; gp=sum(max(x['net_pnl'],0) for x in tr); gl=-sum(min(x['net_pnl'],0) for x in tr)
        aggregate_results[name]={'trades':trades,'wins':wins,'losses':losses,'win_rate':round(100*wins/trades,1) if trades else None,'gross_pnl_sum':round(gross,6),'costs_sum':round(costs,6),'net_pnl_sum_on_daily_100_accounts':round(net,6),'average_daily_return_pct':round(net/8,3),'positive_days':sum(d['net_pnl']>0 for d in days),'negative_days':sum(d['net_pnl']<0 for d in days),'flat_days':sum(d['net_pnl']==0 for d in days),'pf_net_all_trades':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None)}
    payload={'version':'Seven-Trader-Styles-Sep1-8-TP3Fee-v1','period_tehran':[DATE0.isoformat(),(DATE0+timedelta(days=8)).isoformat()],'symbol':'BTCUSDT USD-M perpetual','starting_equity_each_trader_each_day':START_EQUITY,'common_experiment_change':{'roundtrip_cost_pct':ROUNDTRIP_COST*100,'minimum_normal_profit_exit_gross_pct':TP_PCT*100,'rule':'No normal/discretionary profit exit before +3x total modeled fee. Native hard stops remain; style invalidation may exit only when position is non-positive. Day-end force close.'},'common_comparison_controls':{'risk_budget_per_trade_pct':RISK*100,'max_leverage':MAX_LEV},'data':'Binance official USD-M aggTrades aggregated to 5-second bars; 5m derived. No historical full L2 order book claimed.','methodology_notes':['Each trader resets to $100 at the start of each Tehran day so daily outcomes are directly comparable.','Entry logic is the same public-style reconstruction used in the Sep-1 comparison; only the common profit-exit rule is changed.','This is not a literal replication of proprietary/discretionary execution. Rotter/Testa/Jun/Hansan especially lack historical full order-book context.','If stop and target are touched in the same bar, stop is checked first (conservative).','Hougaard add-to-winner is disabled in this specific experiment because user requested a common +3x-fee exit comparison; his native structural stop is preserved.'],'daily':all_daily,'by_trader_daily':by_trader,'aggregate':aggregate_results,'trades':all_trades}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(aggregate_results,indent=2))
    print('DAILY')
    for row in all_daily:
        print(row['day'], {k:(v['net_pnl'],v['trades'],v['win_rate']) for k,v in row['results'].items()})

if __name__=='__main__': main()
