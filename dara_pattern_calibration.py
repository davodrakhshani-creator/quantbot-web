import json
from datetime import datetime, timezone
from pathlib import Path
import dara_v18_prototype_memory_sep7 as v18
import dara_pattern_memory as pm

TRAIN=v18.TRAIN
OUT=Path('data/dara_pattern_performance_memory_v1.json')


def rows(path):
    q=json.loads(Path(path).read_text())
    for k in ('trades','opportunities','sequential_trades'):
        if isinstance(q.get(k),list) and q[k]: return q[k]
    return []


def valid(x):
    rs=str(x.get('reason','')).upper()
    return float(x.get('net_pnl',0) or 0)>0 and ('TP' in rs or '3FEE' in rs)


def main():
    m,f5,f15,idx=v18.load_market((2026,8,31),(2026,9,7));pm.enrich_indicators(m)
    stats={}; total=win=0
    for day,path in TRAIN:
        for tr in rows(path):
            total+=1; y=1 if valid(tr) else 0;win+=y
            try:t=int(datetime.fromisoformat(tr['entry_time']).astimezone(timezone.utc).timestamp()*1000)
            except Exception:continue
            j=idx.get(t)
            if j is None or j<31:continue
            sig=j-1;side=tr.get('side','LONG')
            ms=[x for x in pm.detect(m,sig) if x['side']==side]
            # A trade can carry several simultaneous pattern labels.
            for x in ms:
                key=x['pattern'];s=stats.setdefault(key,{'n':0,'wins':0,'net':0.0,'gross':0.0,'days':{},'families':set(),'side':side})
                s['n']+=1;s['wins']+=y;s['net']+=float(tr.get('net_pnl',0) or 0);s['gross']+=float(tr.get('gross_pnl',0) or 0);s['days'][day]=s['days'].get(day,{'n':0,'wins':0,'net':0.0})
                s['days'][day]['n']+=1;s['days'][day]['wins']+=y;s['days'][day]['net']+=float(tr.get('net_pnl',0) or 0);s['families'].add(x['family'])
    baseline=win/max(total,1)
    selected=[];allp={}
    for k,s in stats.items():
        wd=sum(1 for d in s['days'].values() if d['wins']>0)
        pd=sum(1 for d in s['days'].values() if d['net']>0)
        wr=s['wins']/max(s['n'],1); lift=wr/max(baseline,1e-9)
        rec={'pattern':k,'side':s['side'],'family':sorted(s['families']),'n':s['n'],'wins':s['wins'],'wr':round(wr,4),'lift':round(lift,2),'winner_days':wd,'positive_net_days':pd,'gross':round(s['gross'],6),'net':round(s['net'],6),'day_stats':{d:{'n':v['n'],'wins':v['wins'],'wr':round(v['wins']/v['n'],3) if v['n'] else None,'net':round(v['net'],6)} for d,v in s['days'].items()}}
        # Selection is deliberately cross-day and after-cost. Famous name alone has no privilege.
        rec['production_eligible']=bool(s['n']>=10 and wd>=2 and pd>=2 and wr>=baseline*1.35 and s['net']>0)
        allp[k]=rec
        if rec['production_eligible']:selected.append(k)
    out={'version':'DARA-Pattern-Performance-Memory-v1','training_days':'Sep1-Sep6','training_trades':total,'valid_positive_trades':win,'baseline_valid_rate':round(baseline,4),'selection_rule':'n>=10, winner_days>=2, positive_net_days>=2, WR>=1.35x baseline, cumulative net>0','selected_patterns':selected,'patterns':allp}
    OUT.write_text(json.dumps(out,indent=2,default=list));print(json.dumps({'baseline':out['baseline_valid_rate'],'selected':selected,'top':sorted([(k,v['n'],v['wr'],v['lift'],v['net'],v['winner_days']) for k,v in allp.items()],key=lambda x:(x[4],x[2]),reverse=True)[:15]},indent=2))

if __name__=='__main__':main()
