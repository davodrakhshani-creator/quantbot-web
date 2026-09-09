import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v3_sep1_8 as v3
import dara_v4_sep1_8 as v4
import dara_v7_confirmed_expansion as v7

OUT=Path('data/dara_v8_sep1_8.json')
TP=0.0055; FIRST=0.40; TRAIL=5
PROGRESS_MIN=0.0022   # 2x modeled round-trip cost in price move
PROGRESS_CHECK_MIN=60


def simulate(m1,ei,side,stop,equity):
    entry=m1[ei]['o']; sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<v3.MIN_STOP or sp>v3.MAX_STOP or 0.0033/sp<1.35:return None
    n=min(v3.MAX_LEV*equity,equity*v3.RISK/(sp+v3.COST));target=entry*(1+TP if side=='LONG' else 1-TP);tp=False;realized=0.;remain=n;end=min(len(m1)-1,ei+300);best=0.0
    for j in range(ei,end+1):
        x=m1[j]
        fav=(x['h']/entry-1) if side=='LONG' else (entry/x['l']-1);best=max(best,fav)
        if not tp:
            st=x['l']<=stop if side=='LONG' else x['h']>=stop;hit=x['h']>=target if side=='LONG' else x['l']<=target
            if st:
                r=stop/entry-1 if side=='LONG' else entry/stop-1;gross=n*r;cost=n*v3.COST;return j,stop,'STOP',gross,cost,gross-cost,n,best
            # Momentum thesis invalidation: after 60m, if never achieved 2x fee progress and current trade is non-positive, cut it.
            if j>=ei+PROGRESS_CHECK_MIN and best<PROGRESS_MIN:
                cur=(x['c']/entry-1) if side=='LONG' else (entry/x['c']-1)
                if cur<=0:
                    gross=n*cur;cost=n*v3.COST;return j,x['c'],'NO_PROGRESS',gross,cost,gross-cost,n,best
            if hit:realized=FIRST*n*TP;remain=(1-FIRST)*n;tp=True;stop=entry
        elif j>=ei+TRAIL:
            if side=='LONG':
                trail=max(entry,min(m1[j-k]['l'] for k in range(1,TRAIL+1)))
                if x['l']<=trail:
                    gross=realized+remain*(trail/entry-1);cost=n*v3.COST;return j,trail,'TP055+RUNNER',gross,cost,gross-cost,n,best
            else:
                trail=min(entry,max(m1[j-k]['h'] for k in range(1,TRAIL+1)))
                if x['h']>=trail:
                    gross=realized+remain*(entry/trail-1);cost=n*v3.COST;return j,trail,'TP055+RUNNER',gross,cost,gross-cost,n,best
    px=m1[end]['c'];r=px/entry-1 if side=='LONG' else entry/px-1;gross=(realized+remain*r) if tp else n*r;cost=n*v3.COST;return end,px,'TIME',gross,cost,gross-cost,n,best


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
        s=v7.confirmed_signal(i,m1,f5,f15,f60)
        if not s:i+=1;continue
        setup,side,stop,ci=s
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m1) or m1[ei]['t']>=end_ms:break
        sim=simulate(m1,ei,side,stop,equity)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n,best=sim;before=equity;equity+=net;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak);daypeak=max(daypeak,equity);daydd=max(daydd,(daypeak-equity)/daypeak);daycount+=1;daynet+=net
        tr={'day':dk,'setup':setup,'side':side,'entry_time':datetime.fromtimestamp(m1[ei]['t']/1000,timezone.utc).astimezone(v4.TEHRAN).isoformat(),'exit_time':datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(v4.TEHRAN).isoformat(),'entry':round(m1[ei]['o'],2),'exit':round(px,2),'reason':why,'mfe_pct':round(best*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)};alltr.append(tr);daytr.append(tr)
        if net<0:locks[side]=j+120
        last=j;i=j+1
    if day is not None:daily.append(v3.summarize(daytr,daystart,equity,daypeak,daydd)|{'day':day})
    overall=v3.summarize(alltr,100.,equity,peak,maxdd);stats={};reasons={}
    for tier in ['VALUE_PULLBACK','CONFIRMED_EXPANSION']:
        ts=[t for t in alltr if t['setup']==tier];w=[t for t in ts if t['net_pnl']>0];l=[t for t in ts if t['net_pnl']<=0];gp=sum(t['net_pnl'] for t in w);gl=-sum(t['net_pnl'] for t in l);stats[tier]={'trades':len(ts),'wins':len(w),'losses':len(l),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None)}
    for t in alltr:reasons[t['reason']]=reasons.get(t['reason'],0)+1
    payload={'version':'DARA-v8.0-ProgressGuard','period_tehran':[start.isoformat(),end.isoformat()],'design':['Entry logic identical to v7','At 60 minutes: if MFE never reached +0.22% (2x fee) and current PnL is non-positive, exit as NO_PROGRESS','TP +0.55% on 40%, 60% runner with 5-bar trail','Risk 0.25% incl cost; max 3/day; 45m cooldown; 120m side loss lock'],'methodology_notes':['v8 was designed after studying v7 performance through Aug20-27 and Jul15-Aug09; those periods and Sep1-8 are development-contaminated for v8.'],'daily':daily,'overall':overall,'by_tier':stats,'exit_reasons':reasons,'trades':alltr};Path(outpath).parent.mkdir(exist_ok=True);Path(outpath).write_text(json.dumps(payload,indent=2));print(json.dumps(overall,indent=2));print(json.dumps(stats,indent=2));print(json.dumps(reasons,indent=2));return payload

def main():run(v4.START,v4.END,OUT)
if __name__=='__main__':main()
