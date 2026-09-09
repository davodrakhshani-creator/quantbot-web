import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v3_sep1_8 as v3
import dara_v4_sep1_8 as v4

TP=0.0055;FIRST=0.40;TRAIL=5;OUT=Path('data/dara_v6_sep1_8.json')

def context_gate(i,m1,f15,f60,side):
    x=m1[i];a15=f15.get(b.last_closed(x['t'],15));a60=f60.get(b.last_closed(x['t'],60))
    if not a15 or not a60:return None
    rng=max(x['prev240h']-x['prev240l'],1e-9);pos=(x['c']-x['prev240l'])/rng;dirpos=pos if side=='LONG' else 1-pos
    vwd=(x['c']/x['vwap240']-1) if side=='LONG' else (x['vwap240']/x['c']-1)
    room=((x['prev240h']-x['c'])/x['c']) if side=='LONG' else ((x['c']-x['prev240l'])/x['c'])
    dist=((a15['c']-a15['ema21'])/max(a15['atr'],1e-9)) if side=='LONG' else ((a15['ema21']-a15['c'])/max(a15['atr'],1e-9))
    dir_rsi=a60['rsi'] if side=='LONG' else 100-a60['rsi'];vol=x['v']/x['medv'];body=abs(x['body'])
    if dirpos<0.98 and abs(vwd)<=0.0015 and room>=0.0050 and 25<=dir_rsi<=75:
        return 'VALUE_PULLBACK'
    if dirpos>=0.98 and vol>=3.0 and body>=0.00025 and 0.70<=dist<=1.60:
        return 'VOLUME_EXPANSION'
    return None

def simulate(m1,ei,side,stop,equity):
    entry=m1[ei]['o'];sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<v3.MIN_STOP or sp>v3.MAX_STOP or 0.0033/sp<1.35:return None
    n=min(v3.MAX_LEV*equity,equity*v3.RISK/(sp+v3.COST));target=entry*(1+TP if side=='LONG' else 1-TP);tp=False;realized=0.;remain=n;end=min(len(m1)-1,ei+300)
    for j in range(ei,end+1):
        x=m1[j]
        if not tp:
            st=x['l']<=stop if side=='LONG' else x['h']>=stop;hit=x['h']>=target if side=='LONG' else x['l']<=target
            if st:
                r=stop/entry-1 if side=='LONG' else entry/stop-1;gross=n*r;cost=n*v3.COST;return j,stop,'STOP',gross,cost,gross-cost,n
            if hit:realized=FIRST*n*TP;remain=(1-FIRST)*n;tp=True;stop=entry
        elif j>=ei+TRAIL:
            if side=='LONG':
                trail=max(entry,min(m1[j-k]['l'] for k in range(1,TRAIL+1)))
                if x['l']<=trail:
                    gross=realized+remain*(trail/entry-1);cost=n*v3.COST;return j,trail,'TP055+RUNNER',gross,cost,gross-cost,n
            else:
                trail=min(entry,max(m1[j-k]['h'] for k in range(1,TRAIL+1)))
                if x['h']>=trail:
                    gross=realized+remain*(entry/trail-1);cost=n*v3.COST;return j,trail,'TP055+RUNNER',gross,cost,gross-cost,n
    px=m1[end]['c'];r=px/entry-1 if side=='LONG' else entry/px-1;gross=(realized+remain*r) if tp else n*r;cost=n*v3.COST;return end,px,'TIME',gross,cost,gross-cost,n

def run(start,end,outpath):
    start_ms=int(start.astimezone(timezone.utc).timestamp()*1000);end_ms=int(end.astimezone(timezone.utc).timestamp()*1000)
    d0=(start.astimezone(timezone.utc)-timedelta(days=2)).date();d1=end.astimezone(timezone.utc).date();raw=[];d=d0
    while d<=d1:
        raw+=b.get_daily(datetime(d.year,d.month,d.day,tzinfo=timezone.utc));d+=timedelta(days=1)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t']);m1=v3.enrich_m1(raw);f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)};f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)};f60={x['t']:x for x in b.features(b.aggregate(raw,60),60)}
    equity=100.;peak=100.;maxdd=0.;alltr=[];daily=[];day=None;daystart=100.;daypeak=100.;daydd=0;daytr=[];daynet=0;daycount=0;last=-10**9;locks={'LONG':-10**9,'SHORT':-10**9};i=0
    while i<len(m1)-5:
        x=m1[i]
        if x['t']<start_ms:i+=1;continue
        if x['t']>=end_ms:break
        dk=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(v4.TEHRAN).strftime('%Y-%m-%d')
        if day is None:day=dk
        if dk!=day:
            daily.append(v3.summarize(daytr,daystart,equity,daypeak,daydd)|{'day':day});day=dk;daystart=equity;daypeak=equity;daydd=0;daytr=[];daynet=0;daycount=0
        if daycount>=3 or daynet<=-0.0075*daystart or i-last<45:i+=1;continue
        s=v4.fresh_trend(i,m1,f5,f15,f60)
        if not s:i+=1;continue
        _,side,stop,ci=s;tier=context_gate(i,m1,f15,f60,side)
        if not tier or ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m1) or m1[ei]['t']>=end_ms:break
        sim=simulate(m1,ei,side,stop,equity)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n=sim;before=equity;equity+=net;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak);daypeak=max(daypeak,equity);daydd=max(daydd,(daypeak-equity)/daypeak);daycount+=1;daynet+=net
        tr={'day':dk,'setup':tier,'side':side,'entry_time':datetime.fromtimestamp(m1[ei]['t']/1000,timezone.utc).astimezone(v4.TEHRAN).isoformat(),'exit_time':datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(v4.TEHRAN).isoformat(),'entry':round(m1[ei]['o'],2),'exit':round(px,2),'reason':why,'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)};alltr.append(tr);daytr.append(tr)
        if net<0:locks[side]=j+120
        last=j;i=j+1
    if day is not None:daily.append(v3.summarize(daytr,daystart,equity,daypeak,daydd)|{'day':day})
    overall=v3.summarize(alltr,100.,equity,peak,maxdd);stats={}
    for tier in ['VALUE_PULLBACK','VOLUME_EXPANSION']:
        ts=[t for t in alltr if t['setup']==tier];w=[t for t in ts if t['net_pnl']>0];l=[t for t in ts if t['net_pnl']<=0];gp=sum(t['net_pnl'] for t in w);gl=-sum(t['net_pnl'] for t in l);stats[tier]={'trades':len(ts),'wins':len(w),'losses':len(l),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None)}
    payload={'version':'DARA-v6.0-ContextTiered-Development','period_tehran':[start.isoformat(),end.isoformat()],'design':['FreshTrend only; Shock and Sweep removed until separately proven','Tier A VALUE_PULLBACK: inside 4h range, near rolling VWAP, >=0.5% room, non-exhausted 1h RSI','Tier B VOLUME_EXPANSION: beyond 4h extreme, >=3x relative volume, 0.7-1.6 ATR extension, meaningful signal body','TP first at +0.55% on 40%; 60% runner with 5-bar trail','Risk 0.25% including modeled cost; max 3 trades/day; 45m cooldown; 120m side lock after loss'],'methodology_notes':['Sep1-8 thresholds are derived from prior diagnostics on the same dates; this is intentionally in-sample development and not proof.'],'daily':daily,'overall':overall,'by_tier':stats,'trades':alltr};Path(outpath).parent.mkdir(exist_ok=True);Path(outpath).write_text(json.dumps(payload,indent=2));print(json.dumps(overall,indent=2));print(json.dumps(stats,indent=2));return payload

def main():run(v4.START,v4.END,OUT)
if __name__=='__main__':main()
