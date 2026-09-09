import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v3_sep1_8 as v3
import dara_v4_sep1_8 as v4
import dara_v6_context_tiered as v6

OUT=Path('data/dara_v7_sep1_8.json')

def confirmed_signal(i,m1,f5,f15,f60):
    s=v4.fresh_trend(i,m1,f5,f15,f60)
    if not s:return None
    _,side,stop,ci=s
    tier=v6.context_gate(i,m1,f15,f60,side)
    if not tier:return None
    if tier=='VALUE_PULLBACK':
        return (tier,side,stop,ci)
    # Volume expansion needs one closed minute of acceptance before entry.
    if ci+1>=len(m1):return None
    x=m1[ci];y=m1[ci+1];mid=(x['o']+x['c'])/2
    if side=='LONG':
        ok=(y['c']>=mid and y['l']>=x['l'] and y['flow']>=1.10 and y['c']>=y['o']*0.9997)
    else:
        ok=(y['c']<=mid and y['h']<=x['h'] and y['flow']<=1/1.10 and y['c']<=y['o']*1.0003)
    if not ok:return None
    return ('CONFIRMED_EXPANSION',side,stop,ci+1)

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
        s=confirmed_signal(i,m1,f5,f15,f60)
        if not s:i+=1;continue
        setup,side,stop,ci=s
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m1) or m1[ei]['t']>=end_ms:break
        sim=v6.simulate(m1,ei,side,stop,equity)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n=sim;before=equity;equity+=net;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak);daypeak=max(daypeak,equity);daydd=max(daydd,(daypeak-equity)/daypeak);daycount+=1;daynet+=net
        tr={'day':dk,'setup':setup,'side':side,'entry_time':datetime.fromtimestamp(m1[ei]['t']/1000,timezone.utc).astimezone(v4.TEHRAN).isoformat(),'exit_time':datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(v4.TEHRAN).isoformat(),'entry':round(m1[ei]['o'],2),'exit':round(px,2),'reason':why,'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(equity,6)};alltr.append(tr);daytr.append(tr)
        if net<0:locks[side]=j+120
        last=j;i=j+1
    if day is not None:daily.append(v3.summarize(daytr,daystart,equity,daypeak,daydd)|{'day':day})
    overall=v3.summarize(alltr,100.,equity,peak,maxdd);stats={}
    for tier in ['VALUE_PULLBACK','CONFIRMED_EXPANSION']:
        ts=[t for t in alltr if t['setup']==tier];w=[t for t in ts if t['net_pnl']>0];l=[t for t in ts if t['net_pnl']<=0];gp=sum(t['net_pnl'] for t in w);gl=-sum(t['net_pnl'] for t in l);stats[tier]={'trades':len(ts),'wins':len(w),'losses':len(l),'net_pnl':round(sum(t['net_pnl'] for t in ts),6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None)}
    payload={'version':'DARA-v7.0-ConfirmedExpansion','period_tehran':[start.isoformat(),end.isoformat()],'design':['VALUE_PULLBACK unchanged from v6','VOLUME_EXPANSION requires one additional closed-minute acceptance: no full signal-candle reversal + directional flow','TP first +0.55% on 40%; 60% runner, 5-bar trail','Risk 0.25% incl cost; max 3/day; 45m cooldown; 120m same-side loss lock'],'methodology_notes':['v7 was designed after v6 Sep development and Aug20-27 blind results; those periods are development-contaminated for v7.'],'daily':daily,'overall':overall,'by_tier':stats,'trades':alltr};Path(outpath).parent.mkdir(exist_ok=True);Path(outpath).write_text(json.dumps(payload,indent=2));print(json.dumps(overall,indent=2));print(json.dumps(stats,indent=2));return payload

def main():run(v4.START,v4.END,OUT)
if __name__=='__main__':main()
