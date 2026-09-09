import json
from pathlib import Path
import dara_discovery30_sep1 as d

OUT=Path('data/dara_discovery30_learned_sep1.json')
# Lessons from Discovery30: raw VWAP cross is removed; continuation only when 15m agrees,
# relative participation is quiet (<1.0), and entry is not in the exact VWAP chop zone.

def learned_flow(i,m,f5,f15):
    s=d.flow_pulse(i,m,f5,f15)
    if not s:return None
    setup,side,stop,ci,score,diag,tp=s
    tag='UP' if side=='LONG' else 'DOWN'
    if diag.get('reg15')!=tag:return None
    if diag.get('volr',99)>=1.0:return None
    if abs(diag.get('vwap_dist',0))<0.05:return None
    # fee-aware: prefer 4x fee; if market capacity only supported 3x in discovery, skip here.
    if tp<0.0044:return None
    diag={**diag,'lesson_filter':'15m aligned + volr<1 + |VWAP dist|>=0.05 + 4x fee capacity'}
    return ('FLOW_PULSE_LEARNED',side,stop,ci,score,diag,0.0044)

def learned_candidates(i,m,f5,f15):
    out=[]
    # Keep only proven core. Other playbooks are quarantined pending fresh data.
    s=learned_flow(i,m,f5,f15)
    if s:out.append(s)
    return out

def simulate_strict(m,ei,side,stop,equity,tp):
    entry=m[ei]['o'];sp=(entry-stop)/entry if side=='LONG' else (stop-entry)/entry
    if sp<=0:return None
    n=min(d.MAX_LEV*equity,equity*d.RISK/(sp+d.COST));target=entry*(1+tp if side=='LONG' else 1-tp)
    # 90m diagnostic hold. A profitable exit is only TP=4x fee; otherwise exit only on stop or thesis failure while non-positive.
    end=min(len(m)-1,ei+90);mfe=0.;mae=0.
    for j in range(ei,end+1):
        b=m[j];fav=(b['h']/entry-1) if side=='LONG' else (entry/b['l']-1);adv=(entry/b['l']-1) if side=='LONG' else (b['h']/entry-1);mfe=max(mfe,fav);mae=max(mae,adv)
        st=b['l']<=stop if side=='LONG' else b['h']>=stop;hit=b['h']>=target if side=='LONG' else b['l']<=target
        if st:
            ret=stop/entry-1 if side=='LONG' else entry/stop-1;gross=n*ret;cost=n*d.COST;return j,stop,'STOP',gross,cost,gross-cost,n,mfe,mae
        if hit:
            gross=n*tp;cost=n*d.COST;return j,target,'TP4FEE',gross,cost,gross-cost,n,mfe,mae
        if j>=ei+25:
            cur=(b['c']/entry-1) if side=='LONG' else (entry/b['c']-1)
            if cur<=0 and mfe<0.0033:
                gross=n*cur;cost=n*d.COST;return j,b['c'],'THESIS_FAIL',gross,cost,gross-cost,n,mfe,mae
    # If still small positive after 90m, do not book a sub-3x-fee profit: classify as unresolved and close only if non-positive.
    b=m[end];cur=(b['c']/entry-1) if side=='LONG' else (entry/b['c']-1)
    if cur>0 and cur<0.0033:
        gross=0;cost=n*d.COST;return end,entry,'UNRESOLVED_TO_FLAT',gross,cost,-cost,n,mfe,mae
    gross=n*cur;cost=n*d.COST;return end,b['c'],'TIME',gross,cost,gross-cost,n,mfe,mae

def main():
    raw=[];x=d.datetime(2026,8,31,tzinfo=d.timezone.utc)
    while x.date()<=d.datetime(2026,9,2,tzinfo=d.timezone.utc).date():raw+=d.r.b.get_daily(x);x+=d.timedelta(days=1)
    raw=sorted({z['t']:z for z in raw}.values(),key=lambda z:z['t']);m=d.r.v.enrich(raw);f5={z['t']:z for z in d.r.b.features(d.r.b.aggregate(raw,5),5)};f15={z['t']:z for z in d.r.b.features(d.r.b.aggregate(raw,15),15)}
    eq=100.;peak=100.;dd=0.;ts=[];i=0;last=-10**9
    while i<len(m)-3:
        if m[i]['t']<d.S:i+=1;continue
        if m[i]['t']>=d.E:break
        if i-last<2:i+=1;continue
        cs=learned_candidates(i,m,f5,f15)
        if not cs:i+=1;continue
        setup,side,stop,ci,score,diag,tp=cs[0];ei=ci+1
        if ei>=len(m) or m[ei]['t']>=d.E:break
        sim=simulate_strict(m,ei,side,stop,eq,tp)
        if not sim:i+=1;continue
        j,px,why,gross,cost,net,n,mfe,mae=sim
        if m[j]['t']>=d.E:break
        before=eq;eq+=net;peak=max(peak,eq);dd=max(dd,(peak-eq)/peak)
        et=d.datetime.fromtimestamp(m[ei]['t']/1000,d.timezone.utc).astimezone(d.TEHRAN);xt=d.datetime.fromtimestamp(m[j]['t']/1000,d.timezone.utc).astimezone(d.TEHRAN)
        ts.append({'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),'entry':m[ei]['o'],'exit':round(px,2),'reason':why,'tp_pct':0.44,'mfe_pct':round(mfe*100,3),'mae_pct':round(mae*100,3),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),'equity_before':round(before,6),'equity_after':round(eq,6),'diagnostics':diag})
        last=j;i=j+1
    w=[x for x in ts if x['net_pnl']>0];l=[x for x in ts if x['net_pnl']<=0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in l)
    out={'version':'DARA-Discovery30-Learned-Sep1-InSample','purpose':'Posthoc pruning of Discovery30 lessons; not validation.','changes':['Removed raw VWAP_CROSS','Quarantined Pullback/MicroBreak until fresh evidence','FLOW continuation requires 15m alignment, volr<1, |VWAP distance|>=0.05%','Require volatility capacity for 4x fee target = 0.44%','No normal profitable exit below 3x fee; primary TP fixed at 4x fee'],'overall':{'start':100,'final':round(eq,6),'net':round(eq-100,6),'return_pct':round((eq/100-1)*100,3),'trades':len(ts),'wins':len(w),'losses':len(l),'wr':round(100*len(w)/len(ts),1) if ts else None,'gross':round(sum(x['gross_pnl'] for x in ts),6),'cost':round(sum(x['cost'] for x in ts),6),'pf':round(gp/gl,2) if gl else (99 if gp else None),'max_dd_pct':round(dd*100,3)},'trades':ts,'warning':'Highly in-sample: rules were chosen after inspecting the 30 Sep1 discovery trades.'}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
