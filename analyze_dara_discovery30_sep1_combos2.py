import json
from pathlib import Path
p=json.loads(Path('data/dara_discovery30_sep1.json').read_text());ts=p['trades']
def st(xs):
 w=[x for x in xs if x['net_pnl']>0];gp=sum(x['net_pnl'] for x in w);gl=-sum(x['net_pnl'] for x in xs if x['net_pnl']<=0)
 return {'n':len(xs),'w':len(w),'wr':round(100*len(w)/len(xs),1) if xs else None,'gross':round(sum(x['gross_pnl'] for x in xs),6),'cost':round(sum(x['cost'] for x in xs),6),'net':round(sum(x['net_pnl'] for x in xs),6),'pf':round(gp/gl,2) if gl else (99 if gp else None),'mfe':round(sum(x['mfe_pct'] for x in xs)/len(xs),3) if xs else None,'mae':round(sum(x['mae_pct'] for x in xs)/len(xs),3) if xs else None}
def a15(x):
 tag='UP' if x['side']=='LONG' else 'DOWN';return x['diagnostics'].get('reg15')==tag
def a5(x):
 tag='UP' if x['side']=='LONG' else 'DOWN';return x['diagnostics'].get('reg5')==tag
def vs(x):return abs(x['diagnostics'].get('vwap_dist',0))
def vol(x):return x['diagnostics'].get('volr',99)
def hold_same(x):
 h=x['diagnostics'].get('hold_delta',0);return h>=0.05 if x['side']=='LONG' else h<=-0.05
rules={
'15align_vol_lt1':[x for x in ts if a15(x) and vol(x)<1],
'15align_vol_lt1_vwap_ge005':[x for x in ts if a15(x) and vol(x)<1 and vs(x)>=.05],
'15align_vol_lt1_vwap_005_015':[x for x in ts if a15(x) and vol(x)<1 and .05<=vs(x)<.15],
'flow_vol_lt1':[x for x in ts if x['setup']=='FLOW_PULSE' and vol(x)<1],
'flow_15align_vol_lt1':[x for x in ts if x['setup']=='FLOW_PULSE' and a15(x) and vol(x)<1],
'flow_15align_vol_lt1_vwap_ge005':[x for x in ts if x['setup']=='FLOW_PULSE' and a15(x) and vol(x)<1 and vs(x)>=.05],
'flow_holdsame_vol_lt1':[x for x in ts if x['setup']=='FLOW_PULSE' and hold_same(x) and vol(x)<1],
'pullback_vol_lt1_vwap_ge005':[x for x in ts if x['setup']=='PULLBACK_RELOAD' and vol(x)<1 and vs(x)>=.05],
'pullback_vol_lt08':[x for x in ts if x['setup']=='PULLBACK_RELOAD' and vol(x)<.8],
'nonvwap_15align_vol_lt1':[x for x in ts if x['setup']!='VWAP_CROSS' and a15(x) and vol(x)<1],
'nonvwap_15align_vol_lt1_vwap_ge005':[x for x in ts if x['setup']!='VWAP_CROSS' and a15(x) and vol(x)<1 and vs(x)>=.05],
'lowvol_lt05':[x for x in ts if vol(x)<.5],
'lowvol_lt05_15align':[x for x in ts if vol(x)<.5 and a15(x)],
'holdsame':[x for x in ts if hold_same(x)],
'holdsame_15align_vol_lt1':[x for x in ts if hold_same(x) and a15(x) and vol(x)<1],
'5m15m_align_vol_lt1':[x for x in ts if a5(x) and a15(x) and vol(x)<1]
}
out={k:st(v) for k,v in rules.items()};Path('data/dara_discovery30_sep1_combos2.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
