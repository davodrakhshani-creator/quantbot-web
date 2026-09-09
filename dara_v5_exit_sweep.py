import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v3_sep1_8 as v3
import dara_v4_sep1_8 as v4

OUT=Path('data/dara_v5_exit_sweep.json')
START=v4.START;END=v4.END;START_MS=v4.START_MS;END_MS=v4.END_MS
POLICIES={
 'BASE_033_50_3':{'tp':0.0033,'first':0.50,'trailbars':3},
 'HOLD_044_50_4':{'tp':0.0044,'first':0.50,'trailbars':4},
 'STRETCH_055_40_5':{'tp':0.0055,'first':0.40,'trailbars':5},
 'ASYM_044_30_5':{'tp':0.0044,'first':0.30,'trailbars':5},
}

def simulate(m1,ei,side,stop,equity,p):
    entry=m1[ei]['o'];sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    # Entry gate intentionally frozen to v4 economics, independent of exit-policy target.
    if sp<v3.MIN_STOP or sp>v3.MAX_STOP or 0.0033/sp<1.35:return None
    n=min(v3.MAX_LEV*equity,equity*v3.RISK/(sp+v3.COST));target=entry*(1+p['tp'] if side=='LONG' else 1-p['tp']);tpdone=False;realized=0.;remain=n;end=min(len(m1)-1,ei+300)
    for j in range(ei,end+1):
        x=m1[j]
        if not tpdone:
            st=x['l']<=stop if side=='LONG' else x['h']>=stop;hit=x['h']>=target if side=='LONG' else x['l']<=target
            if st:
                r=stop/entry-1 if side=='LONG' else entry/stop-1;gross=n*r;cost=n*v3.COST;return j,stop,'STOP',gross,cost,gross-cost,n
            if hit:
                realized=p['first']*n*p['tp'];remain=(1-p['first'])*n;tpdone=True;stop=entry
        else:
            N=p['trailbars']
            if j>=ei+N:
                if side=='LONG':
                    trail=max(entry,min(m1[j-k]['l'] for k in range(1,N+1)))
                    if x['l']<=trail:
                        gross=realized+remain*(trail/entry-1);cost=n*v3.COST;return j,trail,'TP+RUNNER',gross,cost,gross-cost,n
                else:
                    trail=min(entry,max(m1[j-k]['h'] for k in range(1,N+1)))
                    if x['h']>=trail:
                        gross=realized+remain*(entry/trail-1);cost=n*v3.COST;return j,trail,'TP+RUNNER',gross,cost,gross-cost,n
    px=m1[end]['c'];r=px/entry-1 if side=='LONG' else entry/px-1;gross=(realized+remain*r) if tpdone else n*r;cost=n*v3.COST;return end,px,'TIME',gross,cost,gross-cost,n

def run_policy(name,p,m1,f5,f15,f60):
    equity=100.;peak=100.;maxdd=0.;trades=[];last=-10**9;locks={'LONG':-10**9,'SHORT':-10**9};day=None;daycount=0;daynet=0.;daystart=100.;i=0
    while i<len(m1)-5:
        x=m1[i]
        if x['t']<START_MS:i+=1;continue
        if x['t']>=END_MS:break
        dk=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(v4.TEHRAN).strftime('%Y-%m-%d')
        if day!=dk:day=dk;daycount=0;daynet=0.;daystart=equity
        if daycount>=4 or daynet<=-0.0075*daystart or i-last<30:i+=1;continue
        s=v4.fresh_trend(i,m1,f5,f15,f60)
        if not s:i+=1;continue
        setup,side,stop,ci=s
        if ci<locks[side]:i+=1;continue
        ei=ci+1;sim=simulate(m1,ei,side,stop,equity,p)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n=sim;before=equity;equity+=net;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak);daycount+=1;daynet+=net
        tr={'day':dk,'side':side,'entry_time':datetime.fromtimestamp(m1[ei]['t']/1000,timezone.utc).astimezone(v4.TEHRAN).isoformat(),'exit_time':datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(v4.TEHRAN).isoformat(),'entry':round(m1[ei]['o'],2),'exit':round(px,2),'reason':why,'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_after':round(equity,6)};trades.append(tr)
        if net<0:locks[side]=j+90
        last=j;i=j+1
    wins=[t for t in trades if t['net_pnl']>0];loss=[t for t in trades if t['net_pnl']<=0];gp=sum(t['net_pnl'] for t in wins);gl=-sum(t['net_pnl'] for t in loss)
    return {'policy':p,'summary':{'trades':len(trades),'wins':len(wins),'losses':len(loss),'win_rate':round(100*len(wins)/len(trades),1) if trades else None,'gross_pnl':round(sum(t['gross_pnl'] for t in trades),6),'costs':round(sum(t['cost'] for t in trades),6),'net_pnl':round(equity-100,6),'final_equity':round(equity,6),'pf_net':round(gp/gl,2) if gl else (99 if gp else None),'max_dd_pct':round(maxdd*100,3)},'trades':trades}

def main():
    raw=[]
    for d in [datetime(2026,8,30,tzinfo=timezone.utc)+timedelta(days=k) for k in range(10)]:raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t']);m1=v3.enrich_m1(raw);f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)};f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)};f60={x['t']:x for x in b.features(b.aggregate(raw,60),60)}
    res={n:run_policy(n,p,m1,f5,f15,f60) for n,p in POLICIES.items()};OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(res,indent=2));print(json.dumps({n:r['summary'] for n,r in res.items()},indent=2))
if __name__=='__main__':main()
