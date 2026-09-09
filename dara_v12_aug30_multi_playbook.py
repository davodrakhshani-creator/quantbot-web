import json, statistics
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v11_liquidity_transition_sep1_8 as r

TEHRAN=r.TEHRAN
START=datetime(2026,8,30,0,0,tzinfo=TEHRAN); END=datetime(2026,8,31,0,0,tzinfo=TEHRAN)
START_MS=int(START.astimezone(timezone.utc).timestamp()*1000); END_MS=int(END.astimezone(timezone.utc).timestamp()*1000)
OUT=Path('data/dara_v12_aug30_multi_playbook.json')
MAX_TRADES=15; COOLDOWN=5; LOSS_LOCK=20; DAILY_STOP=-0.015
COST=r.COST

def target_for(a5,a15):
    cap=min(0.0055,1.75*a15['atrp'],3.2*a5['atrp'])
    if cap<0.0033:return None
    return 0.0044 if cap>=0.0044 else cap

def recent_median_range(m,i,n=12):
    xs=[(z['h']-z['l'])/z['c'] for z in m[max(0,i-n):i] if z['c']]
    return statistics.median(xs) if xs else 0

def liq(i,m,f5,f15):
    s=r.liquidity_transition(i,m,f5,f15)
    if not s:return None
    setup,side,stop,ci,score,d,tp=s
    d3=d.get('delta3',0);ed=d.get('event_delta',0);flip=abs(d.get('flip_delta',0));vol=d.get('volr',0)
    prior=d3<=-0.06 if side=='LONG' else d3>=0.06
    exhaustion=abs(ed)<=0.12 or (abs(ed)<=0.16 and flip>=0.18 and vol>=1.20)
    if not(prior and exhaustion):return None
    return ('LIQUIDITY_REVERSAL',side,stop,ci,5+score,{**d,'v12':'smart_exhaustion'},tp)

def failed_pb(i,m,f5,f15):
    s=r.failed_pullback(i,m,f5,f15)
    if not s:return None
    setup,side,stop,ci,score,d,tp=s
    vol=d.get('volr',99);flip=abs(d.get('flip_delta',0))
    if vol>1.55 or flip<0.10:return None
    if vol>1.15 and flip<0.20:return None
    return ('FAILED_PULLBACK',side,stop,ci,3+score,{**d,'v12':'broader_healthy_pullback'},tp)

def vwap_reclaim(i,m,f5,f15):
    if i<20 or i+1>=len(m):return None
    z=m[i];y=m[i+1];a5=f5.get(r.b.last_closed(z['t'],5));a15=f15.get(r.b.last_closed(z['t'],15))
    if not a5 or not a15:return None
    tp=target_for(a5,a15)
    if tp is None:return None
    prev=m[i-3:i]; vwap=z['vwap15']; med=recent_median_range(m,i)
    # Long reclaim of intraday value.
    if a5['regime']!='DOWN' and a15['regime']!='DOWN':
        touched=min(q['l'] for q in prev)<=vwap*1.0007
        reclaim=z['c']>vwap and z['c']>z['o'] and z['delta']>=0.10 and z['volr']>=0.85
        not_chase=(z['c']-min(q['l'] for q in prev))/z['c']<=max(0.0026,1.8*a5['atrp'])
        hold=y['c']>=vwap and y['l']>=min(z['l'],vwap*0.9988) and y['delta']>-0.12
        if touched and reclaim and hold and not_chase:
            stop=min([q['l'] for q in prev]+[z['l'],y['l']])*0.9996
            score=sum([z['delta']>=.18,z['volr']>=1.2,y['delta']>=.05,z['c']>max(q['h'] for q in prev),a15['regime']=='UP'])
            return ('VWAP_RECLAIM','LONG',stop,i+1,4+score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'regime5':a5['regime'],'regime15':a15['regime']},tp)
    if a5['regime']!='UP' and a15['regime']!='UP':
        touched=max(q['h'] for q in prev)>=vwap*0.9993
        reclaim=z['c']<vwap and z['c']<z['o'] and z['delta']<=-0.10 and z['volr']>=0.85
        not_chase=(max(q['h'] for q in prev)-z['c'])/z['c']<=max(0.0026,1.8*a5['atrp'])
        hold=y['c']<=vwap and y['h']<=max(z['h'],vwap*1.0012) and y['delta']<0.12
        if touched and reclaim and hold and not_chase:
            stop=max([q['h'] for q in prev]+[z['h'],y['h']])*1.0004
            score=sum([z['delta']<=-.18,z['volr']>=1.2,y['delta']<=-.05,z['c']<min(q['l'] for q in prev),a15['regime']=='DOWN'])
            return ('VWAP_RECLAIM','SHORT',stop,i+1,4+score,{'delta':z['delta'],'hold_delta':y['delta'],'volr':z['volr'],'regime5':a5['regime'],'regime15':a15['regime']},tp)
    return None

def compression_break(i,m,f5,f15):
    if i<25 or i+1>=len(m):return None
    z=m[i];y=m[i+1];a5=f5.get(r.b.last_closed(z['t'],5));a15=f15.get(r.b.last_closed(z['t'],15))
    if not a5 or not a15:return None
    tp=target_for(a5,a15)
    if tp is None:return None
    pre=m[i-7:i]; mr=recent_median_range(m,i,10); zr=(z['h']-z['l'])/z['c']
    if mr<=0:return None
    compress=mr<=0.75*max(a5['atrp'],1e-6)
    hi=max(q['h'] for q in pre);lo=min(q['l'] for q in pre)
    if compress and zr>=1.35*mr and z['volr']>=1.15:
        if a5['regime']!='DOWN' and a15['regime']!='DOWN' and z['c']>hi and z['delta']>=0.16:
            hold=y['c']>=hi and y['delta']>-0.08
            if hold:
                stop=min(z['l'],min(q['l'] for q in m[i-2:i]))*0.9996
                score=sum([z['volr']>=1.5,z['delta']>=.25,y['delta']>=.06,zr>=1.8*mr,a15['regime']=='UP'])
                return ('COMPRESSION_BREAK','LONG',stop,i+1,4+score,{'delta':z['delta'],'volr':z['volr'],'range_expand':zr/mr,'regime5':a5['regime'],'regime15':a15['regime']},tp)
        if a5['regime']!='UP' and a15['regime']!='UP' and z['c']<lo and z['delta']<=-0.16:
            hold=y['c']<=lo and y['delta']<0.08
            if hold:
                stop=max(z['h'],max(q['h'] for q in m[i-2:i]))*1.0004
                score=sum([z['volr']>=1.5,z['delta']<=-.25,y['delta']<=-.06,zr>=1.8*mr,a15['regime']=='DOWN'])
                return ('COMPRESSION_BREAK','SHORT',stop,i+1,4+score,{'delta':z['delta'],'volr':z['volr'],'range_expand':zr/mr,'regime5':a5['regime'],'regime15':a15['regime']},tp)
    return None

def micro_pullback(i,m,f5,f15):
    if i<20 or i+1>=len(m):return None
    z=m[i];y=m[i+1];a5=f5.get(r.b.last_closed(z['t'],5));a15=f15.get(r.b.last_closed(z['t'],15))
    if not a5 or not a15:return None
    tp=target_for(a5,a15)
    if tp is None:return None
    pre=m[i-6:i]; p2=m[i-2:i]
    if len(pre)<6:return None
    upmove=(max(q['h'] for q in pre)-min(q['l'] for q in pre[:3]))/z['c']
    downmove=(max(q['h'] for q in pre[:3])-min(q['l'] for q in pre))/z['c']
    if a5['regime']=='UP' and a15['regime']!='DOWN' and upmove>=0.0018:
        pull=sum(q['delta'] for q in p2)/2<=-0.06 and min(q['l'] for q in p2)<=z['vwap15']*1.0015
        flip=z['delta']>=0.12 and z['c']>max(q['h'] for q in p2) and z['c']>z['o']
        hold=y['c']>=z['c']*0.9992 and y['delta']>-0.10
        if pull and flip and hold:
            stop=min(q['l'] for q in m[i-3:i+2])*0.9996
            score=sum([z['delta']>=.20,z['volr']>=1.1,y['delta']>=.04,a15['regime']=='UP'])
            return ('MICRO_PULLBACK','LONG',stop,i+1,3+score,{'delta':z['delta'],'volr':z['volr'],'regime5':a5['regime'],'regime15':a15['regime']},tp)
    if a5['regime']=='DOWN' and a15['regime']!='UP' and downmove>=0.0018:
        pull=sum(q['delta'] for q in p2)/2>=0.06 and max(q['h'] for q in p2)>=z['vwap15']*0.9985
        flip=z['delta']<=-0.12 and z['c']<min(q['l'] for q in p2) and z['c']<z['o']
        hold=y['c']<=z['c']*1.0008 and y['delta']<0.10
        if pull and flip and hold:
            stop=max(q['h'] for q in m[i-3:i+2])*1.0004
            score=sum([z['delta']<=-.20,z['volr']>=1.1,y['delta']<=-.04,a15['regime']=='DOWN'])
            return ('MICRO_PULLBACK','SHORT',stop,i+1,3+score,{'delta':z['delta'],'volr':z['volr'],'regime5':a5['regime'],'regime15':a15['regime']},tp)
    return None

def candidates(i,m,f5,f15):
    out=[]
    for fn in (liq,failed_pb,vwap_reclaim,compression_break,micro_pullback):
        try:
            s=fn(i,m,f5,f15)
            if s:out.append(s)
        except Exception:
            pass
    return sorted(out,key=lambda x:x[4],reverse=True)

def main():
    raw=[];d=datetime(2026,8,29,tzinfo=timezone.utc)
    while d.date()<=datetime(2026,8,31,tzinfo=timezone.utc).date():raw+=r.b.get_daily(d);d+=timedelta(days=1)
    raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=r.v.enrich(raw);f5={z['t']:z for z in r.b.features(r.b.aggregate(raw,5),5)};f15={z['t']:z for z in r.b.features(r.b.aggregate(raw,15),15)}
    eq=100.;peak=100.;dd=0.;ts=[];last=-10**9;locks={'LONG':-10**9,'SHORT':-10**9};candidate_count=0;i=0
    while i<len(m)-3:
        if m[i]['t']<START_MS:i+=1;continue
        if m[i]['t']>=END_MS:break
        if len(ts)>=MAX_TRADES or eq-100<=DAILY_STOP*100 or i-last<COOLDOWN:i+=1;continue
        cs=candidates(i,m,f5,f15);candidate_count+=len(cs)
        if not cs:i+=1;continue
        s=cs[0];setup,side,stop,ci,score,diag,tp=s
        if ci<locks[side]:i+=1;continue
        ei=ci+1
        if ei>=len(m) or m[ei]['t']>=END_MS:break
        sim=r.q.simulate(m,ei,side,stop,eq,tp)
        if sim is None:i+=1;continue
        j,px,why,gross,cost,net,n,mfe=sim
        if m[j]['t']>=END_MS:break
        before=eq;eq+=net;peak=max(peak,eq);dd=max(dd,(peak-eq)/peak)
        et=datetime.fromtimestamp(m[ei]['t']/1000,timezone.utc).astimezone(TEHRAN);xt=datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
        sp=(m[ei]['o']-stop)/m[ei]['o'] if side=='LONG' else (stop-m[ei]['o'])/m[ei]['o']
        ts.append({'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':round(m[ei]['o'],2),'exit':round(px,2),'score':score,'tp_pct':round(tp*100,3),'stop_pct':round(sp*100,3),'reason':why,'mfe_pct':round(mfe*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(eq,6),'diagnostics':diag})
        if net<0:locks[side]=j+LOSS_LOCK
        last=j;i=j+1
    w=[x for x in ts if x['net_pnl']>0];l=[x for x in ts if x['net_pnl']<=0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in l)
    by={}
    for name in ['LIQUIDITY_REVERSAL','FAILED_PULLBACK','VWAP_RECLAIM','COMPRESSION_BREAK','MICRO_PULLBACK']:
        qx=[x for x in ts if x['setup']==name];by[name]={'trades':len(qx),'wins':sum(x['net_pnl']>0 for x in qx),'net':round(sum(x['net_pnl'] for x in qx),6)}
    out={'version':'DARA-v12-MultiPlaybook-Aug30-Frozen','period_tehran':[START.isoformat(),END.isoformat()],'starting_equity':100.0,'design':['5 independent 1m/5m/15m playbooks: liquidity reversal, failed pullback, VWAP reclaim, compression breakout, micro pullback','No timeframe above 15m','Max 15 trades/day, 5m cooldown, 20m same-side loss lock','Normal profit target never below 0.33% gross (=3x 0.11% roundtrip fee); target prefers 0.44% (=4x fee) when ATR capacity allows'],'research_basis':['Recent crypto evidence shows short-horizon taker-flow moves can mean-revert, so reversal and continuation are separate playbooks.','Breakout only accepted with participation + next-minute hold; failed pressure/reclaim handled separately.'],'overall':{'final_equity':round(eq,6),'net_pnl':round(eq-100,6),'return_pct':round((eq/100-1)*100,3),'candidate_signals_seen':candidate_count,'trades':len(ts),'wins':len(w),'losses':len(l),'win_rate':round(100*len(w)/len(ts),1) if ts else None,'gross_pnl':round(sum(x['gross_pnl'] for x in ts),6),'modeled_costs':round(sum(x['cost'] for x in ts),6),'pf_net':round(gp/gl,2) if gl else (99.0 if gp else None),'max_dd_pct':round(dd*100,3)},'by_setup':by,'trades':ts,'methodology_notes':['Aug30 was already checked by narrower DARA and produced zero trades, so this is development evidence, not pristine OOS.','v12 playbook definitions were fixed before this run; no parameter changed after seeing v12 Aug30 output.','Historical full L2 unavailable; Binance USD-M 1m taker-buy volume/delta is aggressor-flow proxy.']}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out['overall'],indent=2));print(json.dumps(by,indent=2))
if __name__=='__main__':main()
