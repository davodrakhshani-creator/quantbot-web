import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v3_sep1_8 as v3
import dara_v4_sep1_8 as v4
import dara_v6_context_tiered as v6

OUT=Path('data/dara_v9_sep1_8.json')
TP=0.0055;FIRST=0.40;TRAIL=5


def value_signal(i,m1,f5,f15,f60):
    s=v4.fresh_trend(i,m1,f5,f15,f60)
    if not s:return None
    _,side,stop,ci=s
    if v6.context_gate(i,m1,f15,f60,side)!='VALUE_PULLBACK':return None
    return ('VALUE_PULLBACK',side,stop,ci)


def sweep_signal(i,m1,f15,f60):
    if i<250 or i+1>=len(m1):return None
    x=m1[i];y=m1[i+1];a15=f15.get(b.last_closed(x['t'],15));a60=f60.get(b.last_closed(x['t'],60))
    if not a15 or not a60:return None
    vol=x['v']/x['medv'];rng=max(x['h']-x['l'],1e-9);body=abs(x['c']-x['o']);upper=x['h']-max(x['o'],x['c']);lower=min(x['o'],x['c'])-x['l']
    # Sweep of a real prior 60m extreme, close back through the level, rejection wick, strong reversed aggressor flow.
    # Do not fade when both higher timeframes strongly trend in the direction of the sweep.
    if not (a15['regime']=='DOWN' and a60['regime']=='DOWN'):
        penetration=(x['prev60l']-x['l'])/x['prev60l'] if x['l']<x['prev60l'] else 0
        event=penetration>=0.0008 and x['c']>x['prev60l'] and lower>=max(body,0.20*rng) and x['flow']>=1.8 and vol>=1.5
        hold=y['l']>=x['l'] and y['c']>x['prev60l'] and y['flow']>=1.20 and y['c']>=y['o']*0.9995
        room=(x['prev60h']-y['c'])/y['c']
        if event and hold and room>=0.0040:
            return ('SWEEP_RECLAIM','LONG',x['l']*0.9995,i+1)
    if not (a15['regime']=='UP' and a60['regime']=='UP'):
        penetration=(x['h']-x['prev60h'])/x['prev60h'] if x['h']>x['prev60h'] else 0
        event=penetration>=0.0008 and x['c']<x['prev60h'] and upper>=max(body,0.20*rng) and x['flow']<=1/1.8 and vol>=1.5
        hold=y['h']<=x['h'] and y['c']<x['prev60h'] and y['flow']<=1/1.20 and y['c']<=y['o']*1.0005
        room=(y['c']-x['prev60l'])/y['c']
        if event and hold and room>=0.0040:
            return ('SWEEP_RECLAIM','SHORT',x['h']*1.0005,i+1)
    return None


def signal(i,m1,f5,f15,f60):
    q=sweep_signal(i,m1,f15,f60)
    if q:return q
    return value_signal(i,m1,f5,f15,f60)


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
    equity=100.;peak=100.;maxdd=0.;alltr=[];daily=[];day=None;daystart=100.;daypeak=100.;daydd=0;daytr=[];daynet=0.;daycount=0;last=-10**9;locks={'LONG':-10**9,'SHORT':-10**9};i=0
    while i<len(m1)-5:
        x=m1[i]
        if x['t']<start_ms:i+=1;continue
        if x['t']>=end_ms:break
        dk=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(v4.TEHRAN).strftime('%Y-%m-%d')
        if day is None:day=dk
        if dk!=day:
            daily.append(v3.summarize(daytr,daystart,equity,daypeak,daydd)|{'day':day});day=dk;daystart=equity;daypeak=equity;daydd=0;daytr=[];daynet=0;daycount=0
        if daycount>=3 or daynet<=-0.0075*daystart or i-last<45:i+=1;continue
        s=signal(i,m1,f5,f15,f60)
        if not s:i+=1;continue
        setup,side,stop,ci=s
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m1) or m1[ei]['t']>=end_ms:break
        sim=simulate(m1,ei,side,stop,equity)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n=sim;before=equity;equity+=net;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak);daypeak=max(daypeak,equity);daydd=max(daydd,(daypeak-equity)/daypeak);daycount+=1;daynet+=net
        tr={'day':dk,'setup':setup,'side':side,'entry_time':datetime.fromtimestamp(m1[ei]['t']/1000,timezone.utc).astimezone(v4.TEHRAN).isoformat(),'exit_time':datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(v4.TEHRAN).isoformat(),'entry':round(m1[ei]['o'],2),'exit':round(px,2),'reason':why,'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)};alltr.append(tr);daytr.append(tr)
        if net<0:locks[side]=j+120
        last=j;i=j+1
    if day is not None:daily.append(v3.summarize(daytr,daystart,equity,daypeak,daydd)|{'day':day})
    overall=v3.summarize(alltr,100.,equity,peak,maxdd);stats={}
    for tier in ['VALUE_PULLBACK','SWEEP_RECLAIM']:
        ts=[t for t in alltr if t['setup']==tier];w=[t for t in ts if t['net_pnl']>0];l=[t for t in ts if t['net_pnl']<=0];gp=sum(t['net_pnl'] for t in w);gl=-sum(t['net_pnl'] for t in l);stats[tier]={'trades':len(ts),'wins':len(w),'losses':len(l),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'gross_pnl':round(sum(t['gross_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None)}
    payload={'version':'DARA-v9.0-ValuePlusSweep','period_tehran':[start.isoformat(),end.isoformat()],'design':['Confirmed Expansion removed after repeated OOS weakness','VALUE_PULLBACK unchanged','New independent SWEEP_RECLAIM: prior 60m liquidity sweep >=0.08%, close back through level, rejection wick, reverse aggressor flow, next-minute hold, >=0.4% room','TP +0.55% on 40%; 60% runner, 5-bar trail','Risk 0.25% incl modeled cost; max 3/day; 45m cooldown; 120m same-side loss lock'],'methodology_notes':['Sep1-8 and all prior tested blocks are development-contaminated for v9; a new untouched block is required after development.','Historical full L2 unavailable; 1m taker-buy volume is the aggressor-flow proxy.'],'daily':daily,'overall':overall,'by_setup':stats,'trades':alltr};Path(outpath).parent.mkdir(exist_ok=True);Path(outpath).write_text(json.dumps(payload,indent=2));print(json.dumps(overall,indent=2));print(json.dumps(stats,indent=2));return payload

def main():run(v4.START,v4.END,OUT)
if __name__=='__main__':main()
