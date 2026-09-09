import json
from collections import defaultdict
from pathlib import Path
P=Path('data/dara_discovery500_sep3.json')
O=Path('data/dara_discovery500_sep3_analysis.json')
p=json.loads(P.read_text());xs=p['trades']

def stat(a):
    if not a:return {'n':0}
    w=[x for x in a if x['net_pnl']>0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in a if x['net_pnl']<=0)
    return {'n':len(a),'wins':len(w),'wr':round(100*len(w)/len(a),1),'gross':round(sum(x['gross_pnl'] for x in a),6),'cost':round(sum(x['cost'] for x in a),6),'net':round(sum(x['net_pnl'] for x in a),6),'pf':round(gp/gl,2) if gl else (99.0 if gp else None),'mfe':round(sum(x['mfe_pct'] for x in a)/len(a),3),'mae':round(sum(x['mae_pct'] for x in a)/len(a),3)}

def bucket(name,fn):
    d=defaultdict(list)
    for x in xs:d[str(fn(x))].append(x)
    return {k:stat(v) for k,v in d.items()}

def vol(x):
    v=x['diagnostics'].get('volr',0)
    if v<.5:return '<.5'
    if v<1:return '.5-1'
    if v<2:return '1-2'
    if v<4:return '2-4'
    return '>=4'
def vd(x):
    v=abs(x['diagnostics'].get('vwap_dist',0))
    if v<.05:return '<.05'
    if v<.12:return '.05-.12'
    if v<.25:return '.12-.25'
    return '>=.25'
def dlt(x):
    v=abs(x['diagnostics'].get('delta',0))
    if v<.2:return '<.2'
    if v<.4:return '.2-.4'
    if v<.6:return '.4-.6'
    return '>=.6'
def hour(x):
    h=int(x['entry_time'][11:13]);return f'{(h//4)*4:02d}-{(h//4)*4+3:02d}'
def align15(x):
    r=x['diagnostics'].get('reg15');s=x['side'];return (s=='LONG' and r=='UP') or (s=='SHORT' and r=='DOWN')
def align5(x):
    r=x['diagnostics'].get('reg5');s=x['side'];return (s=='LONG' and r=='UP') or (s=='SHORT' and r=='DOWN')
def eff(x):
    v=x['diagnostics'].get('eff')
    if v is None:return 'NA'
    if v<.05:return '<.05'
    if v<.10:return '.05-.10'
    if v<.20:return '.10-.20'
    return '>=.20'

pred={}
checks={
 'micro_long':lambda x:x['setup']=='MICRO_BREAK' and x['side']=='LONG',
 'micro_short':lambda x:x['setup']=='MICRO_BREAK' and x['side']=='SHORT',
 'micro_lowvol':lambda x:x['setup']=='MICRO_BREAK' and x['diagnostics'].get('volr',99)<1,
 'micro_highvol':lambda x:x['setup']=='MICRO_BREAK' and x['diagnostics'].get('volr',0)>=2,
 'flow_long':lambda x:x['setup']=='FLOW_EFF' and x['side']=='LONG',
 'flow_short':lambda x:x['setup']=='FLOW_EFF' and x['side']=='SHORT',
 'flow_eff_ge010':lambda x:x['setup']=='FLOW_EFF' and x['diagnostics'].get('eff',0)>=.10,
 'flow_eff_lt010':lambda x:x['setup']=='FLOW_EFF' and x['diagnostics'].get('eff',9)<.10,
 'flow_long_eff_ge010':lambda x:x['setup']=='FLOW_EFF' and x['side']=='LONG' and x['diagnostics'].get('eff',0)>=.10,
 'state_long':lambda x:x['setup']=='STATE_PROBE' and x['side']=='LONG',
 'state_short':lambda x:x['setup']=='STATE_PROBE' and x['side']=='SHORT',
 'lowvol':lambda x:x['diagnostics'].get('volr',99)<.5,
 'vol_ge2':lambda x:x['diagnostics'].get('volr',0)>=2,
 '15aligned':align15,
 '15notaligned':lambda x:not align15(x),
 '5and15aligned':lambda x:align15(x) and align5(x),
 'long_15aligned':lambda x:x['side']=='LONG' and align15(x),
 'short_15aligned':lambda x:x['side']=='SHORT' and align15(x),
}
for k,f in checks.items():pred[k]=stat([x for x in xs if f(x)])

out={
 'overall':stat(xs),
 'by_setup':bucket('setup',lambda x:x['setup']),
 'by_side':bucket('side',lambda x:x['side']),
 'by_volume':bucket('volume',vol),
 'by_vwap_distance':bucket('vwap',vd),
 'by_abs_delta':bucket('delta',dlt),
 'by_4h':bucket('hour',hour),
 'by_15m_alignment':bucket('a15',align15),
 'by_5m_alignment':bucket('a5',align5),
 'by_flow_efficiency':bucket('eff',eff),
 'predicates':pred,
}
O.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
