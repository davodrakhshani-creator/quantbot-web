import json, math
from collections import defaultdict
from pathlib import Path
SRC=Path('data/dara_discovery100_sep2.json'); OUT=Path('data/dara_discovery100_sep2_analysis.json')
p=json.loads(SRC.read_text()); T=p['trades']

def agg(xs):
    if not xs:return {'n':0}
    w=[x for x in xs if x['net_pnl']>0];gp=sum(max(0,x['net_pnl']) for x in xs);gl=-sum(min(0,x['net_pnl']) for x in xs)
    return {'n':len(xs),'wins':len(w),'wr':round(100*len(w)/len(xs),1),'gross':round(sum(x['gross_pnl'] for x in xs),6),'cost':round(sum(x['cost'] for x in xs),6),'net':round(sum(x['net_pnl'] for x in xs),6),'pf':round(gp/gl,2) if gl else (99.0 if gp else None),'mfe':round(sum(x['mfe_pct'] for x in xs)/len(xs),3),'mae':round(sum(x['mae_pct'] for x in xs)/len(xs),3)}

def group(fn):
    d=defaultdict(list)
    for x in T:d[str(fn(x))].append(x)
    return {k:agg(v) for k,v in d.items()}

def volb(x):
    v=x['diagnostics'].get('volr',0)
    return '<.5' if v<.5 else '.5-1' if v<1 else '1-2' if v<2 else '>=2'

def deltab(x):
    v=abs(x['diagnostics'].get('delta',0));return '<.2' if v<.2 else '.2-.4' if v<.4 else '.4-.6' if v<.6 else '>=.6'

def vwapb(x):
    v=abs(x['diagnostics'].get('vwap_dist',0));return '<.05' if v<.05 else '.05-.12' if v<.12 else '.12-.25' if v<.25 else '>=.25'

def stopb(x):
    v=x['stop_pct'];return '<=.11' if v<=.11 else '.11-.16' if v<=.16 else '.16-.24' if v<=.24 else '>.24'

def hourb(x):
    h=int(x['entry_time'][11:13]);return f'{(h//4)*4:02d}-{(h//4)*4+3:02d}'

def aligned15(x):
    r=x['diagnostics'].get('reg15');return (x['side']=='LONG' and r=='UP') or (x['side']=='SHORT' and r=='DOWN')

def aligned5(x):
    r=x['diagnostics'].get('reg5');return (x['side']=='LONG' and r=='UP') or (x['side']=='SHORT' and r=='DOWN')

def bothalign(x):return aligned15(x) and aligned5(x)

def pred(name,fn):return name,agg([x for x in T if fn(x)])

preds=dict([
 pred('15m_aligned',aligned15),pred('5m15m_aligned',bothalign),
 pred('vol_lt1',lambda x:x['diagnostics'].get('volr',99)<1),
 pred('vol_lt1_15align',lambda x:x['diagnostics'].get('volr',99)<1 and aligned15(x)),
 pred('vwap_005_020',lambda x:.05<=abs(x['diagnostics'].get('vwap_dist',0))<=.20),
 pred('vwap_005_020_15align',lambda x:.05<=abs(x['diagnostics'].get('vwap_dist',0))<=.20 and aligned15(x)),
 pred('flow_15align',lambda x:x['setup']=='FLOW_PULSE' and aligned15(x)),
 pred('flow_15align_vol_lt1',lambda x:x['setup']=='FLOW_PULSE' and aligned15(x) and x['diagnostics'].get('volr',99)<1),
 pred('flow_15align_vwap_005_020',lambda x:x['setup']=='FLOW_PULSE' and aligned15(x) and .05<=abs(x['diagnostics'].get('vwap_dist',0))<=.20),
 pred('micro_15align',lambda x:x['setup']=='MICRO_BREAK' and aligned15(x)),
 pred('micro_vol_lt2',lambda x:x['setup']=='MICRO_BREAK' and x['diagnostics'].get('volr',99)<2),
 pred('sweep_eventdelta_strong',lambda x:x['setup']=='SWEEP_RECLAIM' and abs(x['diagnostics'].get('delta',0))>=.35),
 pred('sweep_vol_lt1',lambda x:x['setup']=='SWEEP_RECLAIM' and x['diagnostics'].get('volr',99)<1),
 pred('pullback_vol_lt1',lambda x:x['setup']=='PULLBACK_RELOAD' and x['diagnostics'].get('volr',99)<1),
 pred('stop_gt16',lambda x:x['stop_pct']>.16),
 pred('tp33',lambda x:abs(x['tp_pct']-.33)<.01),pred('tp44',lambda x:abs(x['tp_pct']-.44)<.01),
])

# Failure taxonomy
failure=[]
for x in T:
    if x['net_pnl']>0:continue
    mfe=x['mfe_pct'];mae=x['mae_pct'];tp=x['tp_pct']
    if mfe<.05:tag='NO_FOLLOW_THROUGH'
    elif mfe<.15:tag='WEAK_PROGRESS'
    elif mfe>=.30 and tp>=.33:tag='NEAR_TARGET_GIVEBACK'
    elif mae>.20:tag='BAD_LOCATION_OR_LATE'
    else:tag='EDGE_TOO_SMALL_FOR_FEE'
    failure.append((tag,x))
fails=defaultdict(list)
for k,x in failure:fails[k].append(x)

out={
 'overall':agg(T),'by_setup':group(lambda x:x['setup']),'by_side':group(lambda x:x['side']),
 'by_exit':group(lambda x:x['reason']),'by_15m_alignment':group(aligned15),'by_5m15m_alignment':group(bothalign),
 'by_volume':group(volb),'by_abs_delta':group(deltab),'by_vwap_distance':group(vwapb),'by_stop':group(stopb),'by_4h_block':group(hourb),
 'predicates':preds,'failure_taxonomy':{k:agg(v) for k,v in fails.items()},
 'best_10_by_mfe':sorted([{'n':x['n'],'setup':x['setup'],'side':x['side'],'time':x['entry_time'],'mfe':x['mfe_pct'],'mae':x['mae_pct'],'net':x['net_pnl'],'diag':x['diagnostics']} for x in T],key=lambda z:z['mfe'],reverse=True)[:10],
 'worst_10_immediate':sorted([{'n':x['n'],'setup':x['setup'],'side':x['side'],'time':x['entry_time'],'mfe':x['mfe_pct'],'mae':x['mae_pct'],'net':x['net_pnl'],'diag':x['diagnostics']} for x in T],key=lambda z:(z['mfe'],-z['mae']))[:10]
}
OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
