import json, statistics
from pathlib import Path
src=Path('data/dara_discovery30_sep1.json')
outp=Path('data/dara_discovery30_sep1_analysis.json')
p=json.loads(src.read_text());ts=p['trades']

def stats(xs):
    if not xs:return {'trades':0}
    w=[x for x in xs if x['net_pnl']>0]
    return {'trades':len(xs),'wins':len(w),'win_rate':round(100*len(w)/len(xs),1),'gross':round(sum(x['gross_pnl'] for x in xs),6),'cost':round(sum(x['cost'] for x in xs),6),'net':round(sum(x['net_pnl'] for x in xs),6),'avg_mfe':round(sum(x['mfe_pct'] for x in xs)/len(xs),3),'avg_mae':round(sum(x['mae_pct'] for x in xs)/len(xs),3)}

def group(fn):
    d={}
    for x in ts:
        k=str(fn(x));d.setdefault(k,[]).append(x)
    return {k:stats(v) for k,v in d.items()}

def aligned(x):
    d=x['diagnostics'];tag='UP' if x['side']=='LONG' else 'DOWN'
    return d.get('reg5')==tag and d.get('reg15')==tag

def reg15ok(x):
    d=x['diagnostics'];tag='UP' if x['side']=='LONG' else 'DOWN'
    return d.get('reg15')==tag

def volbucket(x):
    v=x['diagnostics'].get('volr',0)
    return '<0.5' if v<.5 else ('0.5-1.0' if v<1 else ('1.0-2.0' if v<2 else '>=2'))

def deltabucket(x):
    v=abs(x['diagnostics'].get('delta',0))
    return '<.3' if v<.3 else ('.3-.5' if v<.5 else '>=.5')

def vwapbucket(x):
    v=abs(x['diagnostics'].get('vwap_dist',0))
    return '<.05%' if v<.05 else ('.05-.12%' if v<.12 else '>=.12%')

def stopbucket(x):
    v=x['stop_pct']
    return '<=.13%' if v<=.13 else ('.13-.20%' if v<=.20 else '>.20%')

def scorebucket(x):
    s=x['score'];return '<7' if s<7 else ('7-8' if s<9 else '>=9')

def hourbucket(x):
    h=int(x['entry_time'][11:13]);return f'{(h//4)*4:02d}-{(h//4)*4+3:02d}'

analysis={
 'overall':stats(ts),
 'by_setup':group(lambda x:x['setup']),
 'by_side':group(lambda x:x['side']),
 'by_regime_alignment':group(aligned),
 'by_15m_alignment':group(reg15ok),
 'by_volume_bucket':group(volbucket),
 'by_abs_delta_bucket':group(deltabucket),
 'by_vwap_distance':group(vwapbucket),
 'by_stop_bucket':group(stopbucket),
 'by_score_bucket':group(scorebucket),
 'by_exit_reason':group(lambda x:x['reason']),
 'by_4h_block':group(hourbucket),
}
# Rank simple interpretable predicates, min 3 trades, by net PF-like quality and gross-after-cost.
preds={
 'aligned_5m15m':aligned,
 '15m_aligned':reg15ok,
 'score_ge_8':lambda x:x['score']>=8,
 'score_ge_9':lambda x:x['score']>=9,
 'abs_delta_ge_45':lambda x:abs(x['diagnostics'].get('delta',0))>=.45,
 'volr_lt_1':lambda x:x['diagnostics'].get('volr',99)<1,
 'volr_05_to_2':lambda x:.5<=x['diagnostics'].get('volr',0)<2,
 'vwap_abs_lt_012':lambda x:abs(x['diagnostics'].get('vwap_dist',99))<.12,
 'stop_gt_013':lambda x:x['stop_pct']>.13,
 'flow_pulse_only':lambda x:x['setup']=='FLOW_PULSE',
 'no_vwap_cross':lambda x:x['setup']!='VWAP_CROSS',
 'no_pullback':lambda x:x['setup']!='PULLBACK_RELOAD',
}
analysis['predicate_stats']={k:stats([x for x in ts if f(x)]) for k,f in preds.items()}
# paired combinations useful for next version
combos={
 'flow_score8_15aligned':lambda x:x['setup']=='FLOW_PULSE' and x['score']>=8 and reg15ok(x),
 'flow_aligned':lambda x:x['setup']=='FLOW_PULSE' and aligned(x),
 'pullback_score9':lambda x:x['setup']=='PULLBACK_RELOAD' and x['score']>=9,
 'pullback_vol_lt1':lambda x:x['setup']=='PULLBACK_RELOAD' and x['diagnostics'].get('volr',99)<1,
 'nonvwap_score8':lambda x:x['setup']!='VWAP_CROSS' and x['score']>=8,
 'aligned_score8':lambda x:aligned(x) and x['score']>=8,
 '15aligned_delta45':lambda x:reg15ok(x) and abs(x['diagnostics'].get('delta',0))>=.45,
}
analysis['combo_stats']={k:stats([x for x in ts if f(x)]) for k,f in combos.items()}
analysis['winners']=[{'n':x['n'],'setup':x['setup'],'side':x['side'],'time':x['entry_time'],'score':x['score'],'net':x['net_pnl'],'mfe':x['mfe_pct'],'mae':x['mae_pct'],'diag':x['diagnostics']} for x in ts if x['net_pnl']>0]
analysis['losers']=[{'n':x['n'],'setup':x['setup'],'side':x['side'],'time':x['entry_time'],'score':x['score'],'net':x['net_pnl'],'mfe':x['mfe_pct'],'mae':x['mae_pct'],'diag':x['diagnostics']} for x in ts if x['net_pnl']<=0]
outp.write_text(json.dumps(analysis,indent=2));print(json.dumps({k:analysis[k] for k in ['overall','by_setup','by_regime_alignment','by_volume_bucket','by_abs_delta_bucket','by_vwap_distance','by_score_bucket','combo_stats']},indent=2))
