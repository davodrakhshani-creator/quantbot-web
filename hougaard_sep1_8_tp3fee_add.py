import csv,io,json,urllib.request,zipfile,time
from datetime import datetime,timedelta,timezone
from pathlib import Path

SYMBOL='BTCUSDT'; TEHRAN=timezone(timedelta(hours=3,minutes=30)); DATE0=datetime(2026,9,1,0,0,tzinfo=TEHRAN)
START_EQUITY=100.0; COST=0.0011; TP=0.0033; RISK=0.0025; MAX_LEV=3.0
OUT=Path('data/hougaard_sep1_8_tp3fee_add.json'); UA='QuantBot-Hougaard-TP3Fee-Add/1.0'

def getzip(url):
    err=None
    for k in range(5):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA})
            with urllib.request.urlopen(req,timeout=60) as r:b=r.read()
            return zipfile.ZipFile(io.BytesIO(b))
        except Exception as e:
            err=e;time.sleep(min(8,2**k))
    raise RuntimeError(err)

def fetch_day(d):
    ds=d.strftime('%Y-%m-%d');url=f'https://data.binance.vision/data/futures/um/daily/klines/{SYMBOL}/1m/{SYMBOL}-1m-{ds}.zip'
    z=getzip(url);raw=z.read(z.namelist()[0]).decode();out=[]
    for x in csv.reader(io.StringIO(raw)):
        if x and x[0].isdigit():out.append({'t':int(x[0]),'o':float(x[1]),'h':float(x[2]),'l':float(x[3]),'c':float(x[4])})
    return out

def agg5m(rows):
    g={}
    for x in rows:g.setdefault((x['t']//300000)*300000,[]).append(x)
    out=[]
    for k in sorted(g):
        a=g[k];out.append({'t':k,'o':a[0]['o'],'h':max(x['h'] for x in a),'l':min(x['l'] for x in a),'c':a[-1]['c']})
    return out

def size_for(eq,stop_pct):return min(MAX_LEV*eq,eq*RISK/max(stop_pct+COST,1e-9))

def target_price(side,n1,e1,n2=0.0,e2=None):
    N=n1+n2;den=n1/e1+(n2/e2 if n2 and e2 else 0)
    return ((1+TP) if side=='LONG' else (1-TP))*N/den

def run_day(bars,start_ms,end_ms):
    eq=START_EQUITY;tr=[];i=2
    while i<len(bars)-5 and len(tr)<10:
        a,b=bars[i-1],bars[i]
        if b['t']<start_ms:i+=1;continue
        if b['t']>=end_ms:break
        bull=b['c']>b['o'] and a['c']<a['o'] and b['o']<=a['c'] and b['c']>=a['o']
        bear=b['c']<b['o'] and a['c']>a['o'] and b['o']>=a['c'] and b['c']<=a['o']
        side='LONG' if bull else ('SHORT' if bear else None)
        if not side:i+=1;continue
        trigger=b['h'] if side=='LONG' else b['l'];stop=b['l'] if side=='LONG' else b['h'];ti=None
        for k in range(i+1,min(i+4,len(bars))):
            if bars[k]['t']>=end_ms:break
            if (bars[k]['h']>=trigger if side=='LONG' else bars[k]['l']<=trigger):ti=k;break
        if ti is None:i+=1;continue
        e1=trigger;sp=abs(e1-stop)/e1
        if sp<=0 or sp>0.008:i=ti+1;continue
        n1=size_for(eq,sp);R=abs(e1-stop);add_px=e1+R if side=='LONG' else e1-R
        n2=0.0;e2=None;added=False;cur_stop=stop;last=ti;done=None
        for j in range(ti,len(bars)):
            q=bars[j]
            if q['t']>=end_ms:break
            last=j
            tgt=target_price(side,n1,e1,n2,e2)
            st=(q['l']<=cur_stop if side=='LONG' else q['h']>=cur_stop)
            tp=(q['h']>=tgt if side=='LONG' else q['l']<=tgt)
            if st:done=(j,cur_stop,'STYLE_STOP'+('_AFTER_ADD' if added else ''));break
            if tp:done=(j,tgt,'TP_3X_FEE'+('_AFTER_ADD' if added else ''));break
            if not added and (q['h']>=add_px if side=='LONG' else q['l']<=add_px):
                room=max(0.0,MAX_LEV*eq-n1);n2=min(n1,room)
                if n2>0:
                    added=True;e2=add_px;cur_stop=e1
                    tgt=target_price(side,n1,e1,n2,e2)
                    # same-bar conservative handling: after add, stop-at-entry before post-add target
                    if (q['l']<=cur_stop if side=='LONG' else q['h']>=cur_stop):done=(j,cur_stop,'BREAKEVEN_AFTER_ADD');break
                    if (q['h']>=tgt if side=='LONG' else q['l']<=tgt):done=(j,tgt,'TP_3X_FEE_AFTER_ADD');break
        if done is None:done=(last,bars[last]['c'],'DAY_END')
        j,px,why=done;gross=n1*((px/e1-1) if side=='LONG' else (e1/px-1))
        if n2>0:gross+=n2*((px/e2-1) if side=='LONG' else (e2/px-1))
        cost=(n1+n2)*COST;net=gross-cost
        tr.append({'side':side,'entry_time':datetime.fromtimestamp(bars[ti]['t']/1000,timezone.utc).astimezone(TEHRAN).isoformat(),'exit_time':datetime.fromtimestamp(bars[j]['t']/1000,timezone.utc).astimezone(TEHRAN).isoformat(),'entry':round(e1,2),'exit':round(px,2),'added':added,'notional':round(n1+n2,6),'reason':why,'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(eq,6),'equity_after':round(eq+net,6)})
        eq+=net;i=j+1
    wins=sum(x['net_pnl']>0 for x in tr);gp=sum(max(x['net_pnl'],0) for x in tr);gl=-sum(min(x['net_pnl'],0) for x in tr)
    return tr,{'trades':len(tr),'wins':wins,'losses':len(tr)-wins,'win_rate':round(100*wins/len(tr),1) if tr else None,'gross_pnl':round(sum(x['gross_pnl'] for x in tr),6),'costs':round(sum(x['cost'] for x in tr),6),'net_pnl':round(eq-START_EQUITY,6),'final_equity':round(eq,6),'pf_net':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),'adds':sum(x['added'] for x in tr)}

def main():
    cache={};daily=[];alltr=[]
    for n in range(8):
        l0=DATE0+timedelta(days=n);l1=l0+timedelta(days=1);s=int(l0.astimezone(timezone.utc).timestamp()*1000);e=int(l1.astimezone(timezone.utc).timestamp()*1000);warm=s-3600000
        d0=datetime.fromtimestamp(warm/1000,timezone.utc).date();d1=datetime.fromtimestamp((e-1)/1000,timezone.utc).date();rows=[];d=d0
        while d<=d1:
            k=str(d)
            if k not in cache:cache[k]=fetch_day(datetime(d.year,d.month,d.day,tzinfo=timezone.utc))
            rows+=cache[k];d+=timedelta(days=1)
        rows=[x for x in rows if warm<=x['t']<e];bars=agg5m(rows);tr,st=run_day(bars,s,e);daily.append({'day':l0.strftime('%Y-%m-%d'),**st});alltr += [{'day':l0.strftime('%Y-%m-%d'),**x} for x in tr]
    net=sum(x['net_pnl'] for x in daily);gross=sum(x['gross_pnl'] for x in daily);costs=sum(x['costs'] for x in daily);T=sum(x['trades'] for x in daily);W=sum(x['wins'] for x in daily);gp=sum(max(x['net_pnl'],0) for x in alltr);gl=-sum(min(x['net_pnl'],0) for x in alltr)
    agg={'trades':T,'wins':W,'losses':T-W,'win_rate':round(100*W/T,1) if T else None,'gross_pnl_sum':round(gross,6),'costs_sum':round(costs,6),'net_pnl_sum_on_daily_100_accounts':round(net,6),'average_daily_return_pct':round(net/8,3),'positive_days':sum(x['net_pnl']>0 for x in daily),'negative_days':sum(x['net_pnl']<0 for x in daily),'flat_days':sum(x['net_pnl']==0 for x in daily),'pf_net_all_trades':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),'total_add_events':sum(x['adds'] for x in daily)}
    p={'version':'Hougaard-Sep1-8-TP3Fee-AddPreserved-v1','rule':'Original engulfing/trigger/structural stop/add-at-1R preserved. Only positive exit changed to +0.33% gross return on combined notional; stop checked first conservatively; day-end force close.','daily':daily,'aggregate':agg,'trades':alltr};OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(p,indent=2));print(json.dumps(agg,indent=2));print(daily)
if __name__=='__main__':main()
