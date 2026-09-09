import csv, io, json, statistics
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
import seven_trader_styles_sep1 as base

TEHRAN=base.TEHRAN
START=datetime(2026,9,1,0,0,tzinfo=TEHRAN)
END=datetime(2026,9,9,0,0,tzinfo=TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000)
END_MS=int(END.astimezone(timezone.utc).timestamp()*1000)
TARGET_PCT=3*base.ROUNDTRIP_COST  # 0.33% gross move
OUT=Path('data/seven_trader_styles_sep1_8_fee3.json')


def fetch_day_5s(ds):
    url=f'https://data.binance.vision/data/futures/um/daily/aggTrades/{base.SYMBOL}/{base.SYMBOL}-aggTrades-{ds}.zip'
    z=base.getzip(url)
    out={}
    with z.open(z.namelist()[0]) as fh:
        text=io.TextIOWrapper(fh,encoding='utf-8')
        for x in csv.reader(text):
            if not x or not x[0].isdigit():
                continue
            t=int(x[5]); p=float(x[1]); q=float(x[2]); bm=str(x[6]).lower()=='true'
            k=(t//5000)*5000
            b=out.get(k)
            if b is None:
                b={'t':k,'o':p,'h':p,'l':p,'c':p,'v':0.0,'buy':0.0,'sell':0.0}; out[k]=b
            b['h']=max(b['h'],p); b['l']=min(b['l'],p); b['c']=p; b['v']+=q
            if bm: b['sell']+=q
            else: b['buy']+=q
    return out


def build_5s():
    d=datetime(2026,8,31,tzinfo=timezone.utc)
    end=datetime(2026,9,9,tzinfo=timezone.utc)
    g={}
    while d<end:
        g.update(fetch_day_5s(d.strftime('%Y-%m-%d')))
        d+=timedelta(days=1)
    lo=START_MS-3600_000; hi=END_MS
    out=[]; last=None
    first=(lo//5000)*5000; final=((hi-1)//5000)*5000
    for k in range(first,final+1,5000):
        if k in g:
            b=g[k]; last=b['c']
        elif last is not None:
            b={'t':k,'o':last,'h':last,'l':last,'c':last,'v':0.0,'buy':0.0,'sell':0.0}
        else:
            continue
        b['flow']=b['buy']/max(b['sell'],1e-12)
        out.append(b)
    return base.prepare(out)


def ret(side,entry,px):
    return px/entry-1 if side=='LONG' else entry/px-1


def target_px(side,entry):
    return entry*(1+TARGET_PCT if side=='LONG' else 1-TARGET_PCT)


def day_bounds(day,bars):
    s=int(day.astimezone(timezone.utc).timestamp()*1000)
    e=int((day+timedelta(days=1)).astimezone(timezone.utc).timestamp()*1000)
    idx=[i for i,b in enumerate(bars) if s<=b['t']<e]
    return (idx[0],idx[-1]) if idx else (None,None)


def mk_trade(name,side,bars,ei,j,entry,px,equity,notional,reason,gross_override=None,cost_mult=1.0):
    return base.record_trade(name,side,bars[ei]['t'],bars[j]['t'],entry,px,equity,notional,reason,gross_override,cost_mult)


def manage_simple(name,bars,ei,side,equity,stop_pct,day_end,native_horizon,invalidator=None):
    entry=bars[ei]['o']; stop=entry*(1-stop_pct if side=='LONG' else 1+stop_pct); target=target_px(side,entry); notional=base.size_for(equity,stop_pct)
    for j in range(ei,day_end+1):
        b=bars[j]
        st=b['l']<=stop if side=='LONG' else b['h']>=stop
        tp=b['h']>=target if side=='LONG' else b['l']<=target
        if st:
            return j,mk_trade(name,side,bars,ei,j,entry,stop,equity,notional,'STOP')
        if tp:
            return j,mk_trade(name,side,bars,ei,j,entry,target,equity,notional,'TARGET_3X_FEE')
        if invalidator is not None:
            px,why=invalidator(j)
            if px is not None and ret(side,entry,px)<=0:
                return j,mk_trade(name,side,bars,ei,j,entry,px,equity,notional,why)
        if j>=ei+native_horizon and ret(side,entry,b['c'])<=0:
            return j,mk_trade(name,side,bars,ei,j,entry,b['c'],equity,notional,'TIME_NEGATIVE')
    b=bars[day_end]
    return day_end,mk_trade(name,side,bars,ei,day_end,entry,b['c'],equity,notional,'DAY_END_FORCE')


def run_rotter(daybars,equity,maxtr=100):
    name='Paul Rotter'; bars=daybars; trades=[]; i=12; end=len(bars)-1
    while i<end-1 and len(trades)<maxtr:
        prev=bars[i-6:i]; hi=max(x['h'] for x in prev); lo=min(x['l'] for x in prev); x=bars[i]; side=None
        if x['h']>hi and x['flow']>=2.2 and x['c']<hi: side='SHORT'
        elif x['l']<lo and x['flow']<=1/2.2 and x['c']>lo: side='LONG'
        if side:
            ei=i+1; tick5=0.5/bars[ei]['o']; j,t=manage_simple(name,bars,ei,side,equity,tick5,end,6); equity=t['equity_after']; trades.append(t); i=j+1
        else: i+=1
    return equity,trades


def run_testa(daybars,equity,maxtr=40):
    name='Testa'; bars=daybars; trades=[]; i=24; end=len(bars)-1
    while i<end-20 and len(trades)<maxtr:
        x=bars[i]; prev=bars[i-12:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); side=None; level=None
        if x['c']>hi*1.00003 and x['flow']>=1.8 and x['v']>=1.5*x['medv'] and x['body']>0: side='LONG'; level=hi
        elif x['c']<lo*0.99997 and x['flow']<=1/1.8 and x['v']>=1.5*x['medv'] and x['body']<0: side='SHORT'; level=lo
        if not side: i+=1; continue
        weak=[0]
        def inv(j):
            b=bars[j]; fail=(b['c']<level if side=='LONG' else b['c']>level); flowweak=(b['flow']<0.85 if side=='LONG' else b['flow']>1.18); weak[0]=weak[0]+1 if flowweak else 0
            return (b['c'],'MOMENTUM_STALL') if (fail or weak[0]>=2) else (None,None)
        ei=i+1; j,t=manage_simple(name,bars,ei,side,equity,0.0008,end,18,inv); equity=t['equity_after']; trades.append(t); i=j+1
    return equity,trades


def run_jun(daybars,equity,maxtr=40):
    name='Jun FX'; bars=daybars; trades=[]; i=200; end=len(bars)-1
    while i<end-15 and len(trades)<maxtr:
        x=bars[i]; prev=bars[i-180:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); side=None; kind=None
        if x['c']>hi*1.00003 and x['flow']>=1.7 and x['v']>=1.4*x['medv']: side='LONG'; kind='BREAK_HIGH'
        elif x['c']<lo*0.99997 and x['flow']<=1/1.7 and x['v']>=1.4*x['medv']: side='SHORT'; kind='BREAK_LOW'
        elif x['h']>hi*1.00003 and x['c']<hi and x['flow']<=0.8: side='SHORT'; kind='FAILED_HIGH'
        elif x['l']<lo*0.99997 and x['c']>lo and x['flow']>=1.25: side='LONG'; kind='FAILED_LOW'
        if not side: i+=1; continue
        weak=[0]
        def inv(j):
            b=bars[j]; fw=(b['flow']<0.9 if side=='LONG' else b['flow']>1.1); weak[0]=weak[0]+1 if fw else 0
            return (b['c'],'FLOW_EXIT_'+kind) if weak[0]>=2 else (None,None)
        ei=i+1; j,t=manage_simple(name,bars,ei,side,equity,0.0008,end,12,inv); equity=t['equity_after']; trades.append(t); i=j+1
    return equity,trades


def run_hansan(daybars,equity,maxtr=60):
    name='Hansan'; bars=daybars; trades=[]; i=80; end=len(bars)-1
    while i<end-10 and len(trades)<maxtr:
        x=bars[i]; prev=bars[i-60:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); mom=x['c']/bars[i-12]['c']-1; side=None
        if x['c']>hi and mom>0 and x['flow']>=1.5 and x['v']>=1.2*x['medv']: side='LONG'
        elif x['c']<lo and mom<0 and x['flow']<=1/1.5 and x['v']>=1.2*x['medv']: side='SHORT'
        if not side: i+=1; continue
        def inv(j):
            b=bars[j]; adverse=(b['c']<b['o'] and b['flow']<1 if side=='LONG' else b['c']>b['o'] and b['flow']>1)
            return (b['c'],'IMMEDIATE_INVALIDATION') if adverse else (None,None)
        ei=i+1; j,t=manage_simple(name,bars,ei,side,equity,0.0005,end,6,inv); equity=t['equity_after']; trades.append(t); i=j+1
    return equity,trades


def run_cis(daybars,equity,maxtr=12):
    name='cis'; bars=daybars; trades=[]; i=24; end=len(bars)-1
    while i<end-2 and len(trades)<maxtr:
        x=bars[i]; prev=bars[i-12:i]; hi=max(z['h'] for z in prev); lo=min(z['l'] for z in prev); side=None
        if x['c']>hi and x['body']>0 and x['v']>=1.3*x['medv'] and x['ema9']>x['ema21']: side='LONG'
        elif x['c']<lo and x['body']<0 and x['v']>=1.3*x['medv'] and x['ema9']<x['ema21']: side='SHORT'
        if not side: i+=1; continue
        ei=i+1; entry=bars[ei]['o']; structural=(entry-x['l'])/entry if side=='LONG' else (x['h']-entry)/entry; stop_pct=min(0.005,max(0.0015,structural))
        def inv(j):
            if j<ei+2: return (None,None)
            b=bars[j]
            trail=min(bars[j-1]['l'],bars[j-2]['l']) if side=='LONG' else max(bars[j-1]['h'],bars[j-2]['h'])
            hit=b['l']<=trail if side=='LONG' else b['h']>=trail
            return (trail,'TREND_TRAIL') if hit else (None,None)
        j,t=manage_simple(name,bars,ei,side,equity,stop_pct,end,48,inv); equity=t['equity_after']; trades.append(t); i=j+1
    return equity,trades


def run_hougaard(daybars,equity,maxtr=10):
    name='Tom Hougaard'; bars=daybars; trades=[]; i=2; end=len(bars)-1
    while i<end-5 and len(trades)<maxtr:
        a,b=bars[i-1],bars[i]; bull=(b['c']>b['o'] and a['c']<a['o'] and b['o']<=a['c'] and b['c']>=a['o']); bear=(b['c']<b['o'] and a['c']>a['o'] and b['o']>=a['c'] and b['c']<=a['o']); side='LONG' if bull else ('SHORT' if bear else None)
        if not side: i+=1; continue
        trigger=b['h'] if side=='LONG' else b['l']; hard_stop=b['l'] if side=='LONG' else b['h']; trig=None
        for k in range(i+1,min(i+4,end+1)):
            if (bars[k]['h']>=trigger if side=='LONG' else bars[k]['l']<=trigger): trig=k; break
        if trig is None: i+=1; continue
        stop_pct=abs(trigger-hard_stop)/trigger
        if stop_pct<=0 or stop_pct>0.008: i=trig+1; continue
        n1=base.size_for(equity,stop_pct); R=abs(trigger-hard_stop); add_px=trigger+R if side=='LONG' else trigger-R; target=target_px(side,trigger); added=False; n2=0.0; add_entry=None; cur_stop=hard_stop; done=None
        for j in range(trig,end+1):
            q=bars[j]
            if (q['l']<=cur_stop if side=='LONG' else q['h']>=cur_stop):
                rr=ret(side,trigger,cur_stop)
                if rr<=0: done=(j,cur_stop,'STOP_OR_TRAIL'); break
            if (q['h']>=target if side=='LONG' else q['l']<=target): done=(j,target,'TARGET_3X_FEE'); break
            if not added and (q['h']>=add_px if side=='LONG' else q['l']<=add_px):
                added=True; add_entry=add_px; n2=max(0.0,min(n1,base.MAX_LEV*equity-n1)); cur_stop=trigger
            if added and j>=trig+2:
                tr=min(bars[j-1]['l'],bars[j-2]['l']) if side=='LONG' else max(bars[j-1]['h'],bars[j-2]['h'])
                if side=='LONG': cur_stop=max(cur_stop,tr)
                else: cur_stop=min(cur_stop,tr)
        if done is None: done=(end,bars[end]['c'],'DAY_END_FORCE')
        j,px,why=done; r1=ret(side,trigger,px); gross=n1*r1
        if added and n2>0: gross+=n2*ret(side,add_entry,px)
        t=mk_trade(name,side,bars,trig,j,trigger,px,equity,n1+n2,why,gross_override=gross); equity=t['equity_after']; trades.append(t); i=j+1
    return equity,trades


def run_bnf(daybars,equity,sma25):
    name='BNF'; bars=daybars; trades=[]
    for i,x in enumerate(bars[:-1]):
        if x['c']/sma25-1<=-0.15:
            ei=i+1; j,t=manage_simple(name,bars,ei,'LONG',equity,0.03,len(bars)-1,48,None); equity=t['equity_after']; trades.append(t); break
    return equity,trades


def summarize(trades,start_eq,end_eq):
    gp=sum(max(t['net_pnl'],0) for t in trades); gl=-sum(min(t['net_pnl'],0) for t in trades); w=sum(t['net_pnl']>0 for t in trades)
    return {'trades':len(trades),'wins':w,'losses':len(trades)-w,'win_rate':round(100*w/len(trades),1) if trades else None,'net_pnl':round(end_eq-start_eq,6),'end_equity':round(end_eq,6),'pf':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),'status':'POSITIVE' if end_eq>start_eq else ('NEGATIVE' if end_eq<start_eq else 'FLAT')}


def main():
    b5s=build_5s(); b5m=base.prepare(base.aggregate(b5s,300)); aug=base.fetch_aug_daily(); sma25=sum(aug[-25:])/25
    traders=['Paul Rotter','Testa','BNF','cis','Tom Hougaard','Jun FX','Hansan']; eq={n:100.0 for n in traders}; alltr={n:[] for n in traders}; daily=[]
    for d in range(8):
        day=START+timedelta(days=d); ds=int(day.astimezone(timezone.utc).timestamp()*1000); de=int((day+timedelta(days=1)).astimezone(timezone.utc).timestamp()*1000)
        day5s=[x for x in b5s if ds-3600_000<=x['t']<de]
        day5m=[x for x in b5m if ds-6*3600_000<=x['t']<de]
        row={'day':day.strftime('%Y-%m-%d'),'traders':{}}
        funcs={'Paul Rotter':(run_rotter,day5s),'Testa':(run_testa,day5s),'cis':(run_cis,day5m),'Tom Hougaard':(run_hougaard,day5m),'Jun FX':(run_jun,day5s),'Hansan':(run_hansan,day5s)}
        for n,(fn,bars) in funcs.items():
            # entry signals must belong to this Tehran day; warmup bars are included but filtered after run
            start_eq=eq[n]; neweq,tr=fn(bars,eq[n]); tr=[t for t in tr if ds<=int(datetime.fromisoformat(t['entry_time']).astimezone(timezone.utc).timestamp()*1000)<de]
            if tr:
                neweq=tr[-1]['equity_after']
            else:
                neweq=start_eq
            eq[n]=neweq; alltr[n].extend(tr); row['traders'][n]=summarize(tr,start_eq,neweq)
        start_eq=eq['BNF']; neweq,tr=run_bnf(day5m,eq['BNF'],sma25); tr=[t for t in tr if ds<=int(datetime.fromisoformat(t['entry_time']).astimezone(timezone.utc).timestamp()*1000)<de]; neweq=tr[-1]['equity_after'] if tr else start_eq; eq['BNF']=neweq; alltr['BNF'].extend(tr); row['traders']['BNF']=summarize(tr,start_eq,neweq)
        daily.append(row)
    overall={}
    for n in traders:
        tr=alltr[n]; gp=sum(max(t['net_pnl'],0) for t in tr); gl=-sum(min(t['net_pnl'],0) for t in tr); w=sum(t['net_pnl']>0 for t in tr)
        overall[n]={'trades':len(tr),'wins':w,'losses':len(tr)-w,'win_rate':round(100*w/len(tr),1) if tr else None,'net_pnl':round(eq[n]-100,6),'final_equity':round(eq[n],6),'pf':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),'positive_days':sum(r['traders'][n]['status']=='POSITIVE' for r in daily),'negative_days':sum(r['traders'][n]['status']=='NEGATIVE' for r in daily),'flat_days':sum(r['traders'][n]['status']=='FLAT' for r in daily),'gross_pnl':round(sum(t['gross_pnl'] for t in tr),6),'costs':round(sum(t['cost'] for t in tr),6)}
    payload={'version':'Seven-Trader-Styles-Sep1-8-3xFeeGate-v1','period_tehran':[START.isoformat(),END.isoformat()],'symbol':'BTCUSDT USD-M perpetual','change_from_prior_test':'Entry logic and trader-specific stops unchanged. Profitable exits below +0.33% gross price move are blocked; hard stop/negative invalidation remains allowed. Day-end force-close is the only positive exit below target.','target_gross_pct':TARGET_PCT*100,'roundtrip_cost_pct':base.ROUNDTRIP_COST*100,'risk_budget_per_trade_pct':base.RISK*100,'max_leverage':base.MAX_LEV,'data':'Binance official USD-M aggTrades aggregated to 5-second bars; 5m derived. No historical full L2 order book claimed.','daily':daily,'overall':overall,'trades':alltr}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8'); print(json.dumps(overall,indent=2)); print(json.dumps(daily,indent=2))

if __name__=='__main__': main()
