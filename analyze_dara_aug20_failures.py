import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import dara_v18_prototype_memory_sep7 as v18

SRC=Path('data/dara_aug20_current_brain_replay.json')
OUT=Path('data/dara_aug20_failure_analysis.json')
TEHRAN=v18.TEHRAN


def stat(xs):
    if not xs:return {'n':0,'wins':0,'wr':None,'gross':0,'cost':0,'net':0,'pf':None,'mfe':None,'mae':None}
    w=[x for x in xs if x['net_pnl']>0]
    gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in xs if x['net_pnl']<=0)
    return {'n':len(xs),'wins':len(w),'wr':round(100*len(w)/len(xs),1),'gross':round(sum(x['gross_pnl'] for x in xs),6),'cost':round(sum(x['cost'] for x in xs),6),'net':round(sum(x['net_pnl'] for x in xs),6),'pf':round(gp/gl,2) if gl else (99 if gp else None),'mfe':round(sum(x['mfe_pct'] for x in xs)/len(xs),3),'mae':round(sum(x['mae_pct'] for x in xs)/len(xs),3)}

def group(xs,key):
    d=defaultdict(list)
    for x in xs:d[str(key(x))].append(x)
    return {k:stat(v) for k,v in sorted(d.items())}

def bucket(v,cuts,labels):
    for c,l in zip(cuts,labels):
        if v<c:return l
    return labels[-1]

def main():
    o=json.loads(SRC.read_text())
    seq=o['sequential_trades']
    m,f5,f15,idx=v18.load_market((2026,8,19),(2026,8,21))
    # index bars by local entry minute and enrich DTD / recent momentum context.
    by_iso={datetime.fromtimestamp(z['t']/1000,timezone.utc).astimezone(TEHRAN).isoformat():i for i,z in enumerate(m)}
    for x in seq:
        i=by_iso.get(x['entry_time'])
        if i is None:continue
        z=m[i];d=x['diagnostics']
        local=datetime.fromisoformat(x['entry_time'])
        day_start=next((j for j in range(i,-1,-1) if datetime.fromtimestamp(m[j]['t']/1000,timezone.utc).astimezone(TEHRAN).date()!=local.date()),None)
        ds=(day_start+1) if day_start is not None else max(0,i-1440)
        x['hour']=local.hour
        x['r5_pct']=round((z['c']/m[max(ds,i-5)]['c']-1)*100,4)
        x['r15_pct']=round((z['c']/m[max(ds,i-15)]['c']-1)*100,4)
        x['r30_pct']=round((z['c']/m[max(ds,i-30)]['c']-1)*100,4)
        x['dtd_pct']=round((z['c']/m[ds]['o']-1)*100,4)
        x['regime_alignment']=('ALIGNED' if ((x['side']=='LONG' and d.get('reg5')=='UP' and d.get('reg15')=='UP') or (x['side']=='SHORT' and d.get('reg5')=='DOWN' and d.get('reg15')=='DOWN')) else 'MIXED_OR_OPPOSED')
        x['flow_sign']=('ALIGNED' if d.get('instant_flow_aligned') else 'OPPOSED')
        x['proto_bucket']=bucket(float(d.get('prototype_p',0)),[.25,.35,.45,.60],['<.25','.25-.35','.35-.45','>=.45'])
        x['pattern_bucket']=bucket(float(d.get('pattern_score',0)),[1.8,2.5,3.5,99],['<1.8','1.8-2.5','2.5-3.5','>=3.5'])
        x['vol_bucket']=bucket(float(d.get('volr',0)),[.5,1,2,4,999],['<.5','.5-1','1-2','2-4','>=4'])
        x['delta_bucket']=bucket(abs(float(d.get('delta',0))),[.2,.4,.6,99],['<.2','.2-.4','.4-.6','>=.6'])
        x['dtd_state']='UP_STRONG' if x['dtd_pct']>=.5 else 'UP' if x['dtd_pct']>=.15 else 'DOWN_STRONG' if x['dtd_pct']<=-.5 else 'DOWN' if x['dtd_pct']<=-.15 else 'NEUTRAL'
        x['side_vs_dtd']='WITH' if ((x['dtd_state'].startswith('UP') and x['side']=='LONG') or (x['dtd_state'].startswith('DOWN') and x['side']=='SHORT')) else 'AGAINST' if x['dtd_state']!='NEUTRAL' else 'NEUTRAL'
    losses=[x for x in seq if x['net_pnl']<=0];wins=[x for x in seq if x['net_pnl']>0]
    # failure families based on realized path, not only exit label.
    ff=defaultdict(list)
    for x in losses:
        if x['mfe_pct']<.05: k='NO_FOLLOW'
        elif x['mfe_pct']<.12:k='WEAK_PROGRESS'
        elif x['mfe_pct']<.33:k='FEE_INSUFFICIENT'
        else:k='GIVEBACK_AFTER_EDGE'
        ff[k].append(x)
    out={
      'baseline':o['sequential'],
      'all':stat(seq),'losses':stat(losses),'wins':stat(wins),
      'by_exit_reason':group(seq,lambda x:x['reason']),
      'by_side':group(seq,lambda x:x['side']),
      'by_hour4':group(seq,lambda x:f"{(x['hour']//4)*4:02d}-{(x['hour']//4)*4+3:02d}"),
      'by_regime_pair':group(seq,lambda x:f"{x['diagnostics'].get('reg15')}/{x['diagnostics'].get('reg5')}"),
      'by_regime_alignment':group(seq,lambda x:x['regime_alignment']),
      'by_flow_sign':group(seq,lambda x:x['flow_sign']),
      'by_proto_bucket':group(seq,lambda x:x['proto_bucket']),
      'by_pattern_bucket':group(seq,lambda x:x['pattern_bucket']),
      'by_vol_bucket':group(seq,lambda x:x['vol_bucket']),
      'by_delta_bucket':group(seq,lambda x:x['delta_bucket']),
      'by_dtd_state':group(seq,lambda x:x['dtd_state']),
      'by_side_vs_dtd':group(seq,lambda x:x['side_vs_dtd']),
      'failure_families':{k:stat(v) for k,v in ff.items()},
      'trade_digest':[{'n':x['n'],'side':x['side'],'entry':x['entry_time'],'reason':x['reason'],'net':x['net_pnl'],'mfe':x['mfe_pct'],'mae':x['mae_pct'],'p':x['diagnostics'].get('prototype_p'),'wd':x['diagnostics'].get('prototype_winner_days'),'ps':x['diagnostics'].get('pattern_score'),'reg15':x['diagnostics'].get('reg15'),'reg5':x['diagnostics'].get('reg5'),'flow':x['flow_sign'],'volr':x['diagnostics'].get('volr'),'delta':x['diagnostics'].get('delta'),'dtd':x['dtd_pct'],'side_vs_dtd':x['side_vs_dtd'],'patterns':x['diagnostics'].get('patterns',[])} for x in seq]
    }
    OUT.write_text(json.dumps(out,indent=2))
    print(json.dumps({k:out[k] for k in ['all','by_exit_reason','by_side','by_hour4','by_regime_pair','by_flow_sign','by_proto_bucket','by_pattern_bucket','by_dtd_state','by_side_vs_dtd','failure_families']},indent=2))
if __name__=='__main__':main()
