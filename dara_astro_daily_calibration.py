"""Exploratory BTC/astronomy calibration with strict chronological splits.

Astronomical data are objective; financial associations are hypotheses.
This script does NOT let astro open trades. It estimates whether any timing feature
recurs across train (2022-24), validation (2025), and OOS (2026 Jan-Aug).
"""
import json, math, statistics, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from dara_astro_features import snapshot

OUT=Path('data/dara_astro_calibration_2022_2026.json')
START=datetime(2022,1,1,tzinfo=timezone.utc)
END=datetime(2026,9,1,tzinfo=timezone.utc)

def http_json(url,params):
    q=url+'?'+urlencode(params)
    req=Request(q,headers={'User-Agent':'DARA-Astro-Calibration/1.0'})
    with urlopen(req,timeout=30) as r:return json.loads(r.read().decode())

def fetch_daily():
    rows=[]; start=int(START.timestamp()*1000); end=int(END.timestamp()*1000)-1
    while start<end:
        data=http_json('https://fapi.binance.com/fapi/v1/klines',{'symbol':'BTCUSDT','interval':'1d','startTime':start,'endTime':end,'limit':1500})
        if not data:break
        rows.extend(data); nxt=int(data[-1][0])+86400000
        if nxt<=start:break
        start=nxt; time.sleep(.08)
    uniq={int(x[0]):x for x in rows}
    return [uniq[k] for k in sorted(uniq) if int(START.timestamp()*1000)<=k<int(END.timestamp()*1000)]

def split_name(dt):
    if dt.year<=2024:return 'train_2022_2024'
    if dt.year==2025:return 'validation_2025'
    return 'oos_2026_jan_aug'

def mean(xs):return sum(xs)/len(xs) if xs else None

def med(xs):return statistics.median(xs) if xs else None

def sign(x,eps=1e-12):return 1 if x>eps else (-1 if x<-eps else 0)

def summarize(xs,base):
    if not xs:return {'n':0}
    metrics={}
    for key in ('return','abs_return','volume_surprise','buy_imbalance'):
        a=[x[key] for x in xs if x.get(key) is not None]
        if not a:continue
        m=mean(a); b=base.get(key,0); sd=statistics.stdev(a) if len(a)>1 else 0
        t=(m-b)/(sd/math.sqrt(len(a))) if sd>0 else None
        metrics[key]={'mean':round(m,7),'baseline':round(b,7),'edge':round(m-b,7),'t_vs_baseline':round(t,3) if t is not None else None}
    return {'n':len(xs),'metrics':metrics}

def conditions(row):
    a=row['astro']; out=[]
    out.append('MOON_PHASE:'+a['moon_phase_octant'])
    if a['eclipse_geometry_proxy']>=0.5:out.append('ECLIPSE_GEOMETRY_PROXY_HIGH')
    for body,z in a['retrograde'].items():
        out.append(f'{body}_'+('RETROGRADE' if z['retrograde'] else 'DIRECT'))
    for z in a['major_aspects']:
        out.append(f"ASPECT:{z['pair']}:{z['aspect']}")
    return out

def main():
    kl=fetch_daily()
    market=[]; vols=[]
    for x in kl:
        dt=datetime.fromtimestamp(int(x[0])/1000,timezone.utc)
        o,h,l,c,v=map(float,(x[1],x[2],x[3],x[4],x[5]));tb=float(x[9])
        hist=vols[-20:]; vs=v/med(hist) if len(hist)>=10 and med(hist)>0 else None
        vols.append(v)
        market.append({'date':dt.date().isoformat(),'dt':dt,'open':o,'close':c,'return':c/o-1,'abs_return':abs(c/o-1),'volume':v,'volume_surprise':vs,'buy_imbalance':2*(tb/v)-1 if v else 0})
    # Daily noon UTC avoids sampling exactly at exchange day boundary.
    rows=[]
    for j,x in enumerate(market):
        a=snapshot(x['dt']+timedelta(hours=12))
        rows.append({**x,'split':split_name(x['dt']),'astro':a})
    split_rows={s:[x for x in rows if x['split']==s] for s in ('train_2022_2024','validation_2025','oos_2026_jan_aug')}
    bases={}
    for s,xs in split_rows.items():
        bases[s]={k:mean([x[k] for x in xs if x.get(k) is not None]) for k in ('return','abs_return','volume_surprise','buy_imbalance')}
    allconds=sorted({c for x in rows for c in conditions(x)})
    stats={}; selected=[]
    for c in allconds:
        z={}
        for s,xs in split_rows.items():
            hit=[x for x in xs if c in conditions(x)]
            z[s]=summarize(hit,bases[s])
        stats[c]=z
        ns=[z[s].get('n',0) for s in ('train_2022_2024','validation_2025','oos_2026_jan_aug')]
        if ns[0]<15 or ns[1]<5 or ns[2]<5:continue
        reasons=[]
        for metric,min_oos in [('return',0.0005),('buy_imbalance',0.005),('volume_surprise',0.03),('abs_return',0.0005)]:
            edges=[]
            ok=True
            for s in ('train_2022_2024','validation_2025','oos_2026_jan_aug'):
                try:edges.append(z[s]['metrics'][metric]['edge'])
                except Exception:ok=False
            if not ok:continue
            sg=[sign(e) for e in edges]
            if 0 not in sg and len(set(sg))==1 and abs(edges[2])>=min_oos:
                reasons.append({'metric':metric,'direction':'higher' if sg[0]>0 else 'lower','edges':edges})
        if reasons:selected.append({'condition':c,'samples':ns,'persistent_edges':reasons})
    # Explicit new-vs-full comparison by split.
    moon_compare={}
    for s,xs in split_rows.items():
        new=[x for x in xs if x['astro']['moon_phase_octant']=='NEW'];full=[x for x in xs if x['astro']['moon_phase_octant']=='FULL']
        moon_compare[s]={
          'new_n':len(new),'full_n':len(full),
          'new_mean_return':round(mean([x['return'] for x in new]),7) if new else None,
          'full_mean_return':round(mean([x['return'] for x in full]),7) if full else None,
          'new_minus_full_return':round(mean([x['return'] for x in new])-mean([x['return'] for x in full]),7) if new and full else None,
          'new_buy_imbalance':round(mean([x['buy_imbalance'] for x in new]),7) if new else None,
          'full_buy_imbalance':round(mean([x['buy_imbalance'] for x in full]),7) if full else None
        }
    out={
      'version':'DARA-Astro-Cal-v1',
      'period':'2022-01-01 through 2026-08-31 UTC',
      'methodology':{
        'market':'Binance BTCUSDT USD-M perpetual daily klines',
        'astro_sample_time':'12:00 UTC each exchange day',
        'splits':['train 2022-2024','validation 2025','OOS Jan-Aug 2026'],
        'selection':'exploratory recurrence only; requires n>=15/5/5 and same edge sign across all 3 splits with minimum OOS effect',
        'warning':'Multiple hypotheses are tested. Selected items are research candidates, not causal proof and not production trade signals.'
      },
      'days':len(rows),'split_counts':{k:len(v) for k,v in split_rows.items()},'baselines':bases,
      'moon_new_vs_full':moon_compare,
      'selected_recurrent_conditions':selected,
      'condition_stats':stats
    }
    OUT.write_text(json.dumps(out,indent=2))
    print(json.dumps({'days':out['days'],'split_counts':out['split_counts'],'moon':moon_compare,'selected':selected},indent=2))
if __name__=='__main__':main()
