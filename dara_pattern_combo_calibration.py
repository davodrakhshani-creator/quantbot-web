import json,itertools
from datetime import datetime,timezone
from pathlib import Path
import dara_v18_prototype_memory_sep7 as v18
import dara_pattern_memory as pm

OUT=Path('data/dara_pattern_combo_memory_v1.json')


def rows(path):
    q=json.loads(Path(path).read_text())
    for k in ('trades','opportunities','sequential_trades'):
        if isinstance(q.get(k),list) and q[k]:return q[k]
    return []

def valid(x):
    rs=str(x.get('reason','')).upper();return float(x.get('net_pnl',0) or 0)>0 and ('TP' in rs or '3FEE' in rs)

def cap_bucket(a5,a15):
    c=.55*(4*a5['atrp'])+.45*(2.5*a15['atrp'])
    if c<.0028:return '<.28'
    if c<.0033:return '.28-.33'
    if c<.0044:return '.33-.44'
    return '>=.44'

def main():
    m,f5,f15,idx=v18.load_market((2026,8,31),(2026,9,7));pm.enrich_indicators(m)
    total=win=0;st={}
    for day,path in v18.TRAIN:
        for tr in rows(path):
            total+=1;y=1 if valid(tr) else 0;win+=y
            try:t=int(datetime.fromisoformat(tr['entry_time']).astimezone(timezone.utc).timestamp()*1000)
            except Exception:continue
            j=idx.get(t)
            if j is None or j<31:continue
            i=j-1;side=tr.get('side','LONG');a5,a15=v18.b.ctx(m[i],f5,f15)
            if not a5 or not a15:continue
            pats=sorted(set(x['pattern'] for x in pm.detect(m,i) if x['side']==side))
            if len(pats)<2:continue
            # pair patterns; context tags are included only as coarse structure, never exact hour/price.
            tags=[f'REG5:{a5["regime"]}',f'REG15:{a15["regime"]}',f'CAP:{cap_bucket(a5,a15)}']
            combos=[]
            combos += [('PAIR',)+c for c in itertools.combinations(pats,2)]
            # Pattern + coarse regime/capacity combinations improve conditionality without memorizing timestamps.
            for p in pats:
                combos += [('PCTX',p,tg) for tg in tags]
            for key in combos:
                s=st.setdefault('|'.join(key),{'n':0,'wins':0,'net':0.0,'gross':0.0,'days':{}})
                s['n']+=1;s['wins']+=y;s['net']+=float(tr.get('net_pnl',0) or 0);s['gross']+=float(tr.get('gross_pnl',0) or 0)
                d=s['days'].setdefault(day,{'n':0,'wins':0,'net':0.0});d['n']+=1;d['wins']+=y;d['net']+=float(tr.get('net_pnl',0) or 0)
    baseline=win/max(total,1);selected=[];allc={}
    for k,s in st.items():
        wr=s['wins']/s['n'];wd=sum(v['wins']>0 for v in s['days'].values());pd=sum(v['net']>0 for v in s['days'].values());lift=wr/max(baseline,1e-9)
        rec={'key':k,'n':s['n'],'wins':s['wins'],'wr':round(wr,4),'lift':round(lift,2),'winner_days':wd,'positive_net_days':pd,'gross':round(s['gross'],6),'net':round(s['net'],6),'day_stats':{d:{'n':v['n'],'wins':v['wins'],'wr':round(v['wins']/v['n'],3),'net':round(v['net'],6)} for d,v in s['days'].items()}}
        rec['production_eligible']=bool(s['n']>=8 and wd>=2 and pd>=2 and wr>=baseline*1.6 and s['net']>0)
        allc[k]=rec
        if rec['production_eligible']:selected.append(k)
    selected=sorted(selected,key=lambda k:(allc[k]['net'],allc[k]['wr'],allc[k]['n']),reverse=True)
    out={'version':'DARA-Pattern-Combo-Memory-v1','training_days':'Sep1-Sep6','training_trades':total,'valid_positive_trades':win,'baseline_valid_rate':round(baseline,4),'selection_rule':'n>=8, winner_days>=2, positive_net_days>=2, WR>=1.6x baseline, cumulative net>0','selected_combos':selected,'selected_stats':{k:allc[k] for k in selected},'all_combos':allc}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps({'baseline':out['baseline_valid_rate'],'selected_count':len(selected),'selected':[(k,allc[k]['n'],allc[k]['wr'],allc[k]['net'],allc[k]['winner_days']) for k in selected[:20]]},indent=2))
if __name__=='__main__':main()
