"""Full 11-factor DARA astro calibration on BTCUSDT USD-M daily archives.

Tests all 55 pairwise major aspects plus each factor's zodiac sign and all major
planet retrograde/direct states. Strict chronological splits are used to reduce
(but not eliminate) data-snooping. Output is exploratory research, not causality.
"""
import csv,io,zipfile,urllib.request,time,json,statistics,math
from datetime import datetime,timezone,timedelta
from pathlib import Path
from dara_astro_fullgrid_features import snapshot,BODIES,RETRO_BODIES

START=datetime(2022,1,1,tzinfo=timezone.utc); END=datetime(2026,9,1,tzinfo=timezone.utc)
OUT=Path('data/dara_astro_fullgrid_summary_2022_2026.json')
UA='DARA-Astro-FullGrid/1.0'
METRICS=('return','abs_return','volume_surprise','buy_imbalance')
MIN_EFFECT={'return':0.0005,'abs_return':0.0005,'volume_surprise':0.03,'buy_imbalance':0.005}

def months(start,end):
    y,m=start.year,start.month
    while (y,m)<(end.year,end.month):
        yield y,m
        m+=1
        if m==13:y+=1;m=1

def fetch_daily():
    rows=[]
    for y,m in months(START,END):
        ym=f'{y:04d}-{m:02d}'
        url=f'https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-{ym}.zip'
        err=None
        for k in range(4):
            try:
                req=urllib.request.Request(url,headers={'User-Agent':UA})
                with urllib.request.urlopen(req,timeout=45) as r:data=r.read()
                z=zipfile.ZipFile(io.BytesIO(data));raw=z.read(z.namelist()[0]).decode('utf-8')
                for x in csv.reader(io.StringIO(raw)):
                    if x and x[0].isdigit():rows.append(x)
                err=None;break
            except Exception as e:
                err=e;time.sleep(.5*(2**k))
        if err:raise RuntimeError(f'Archive fetch failed {ym}: {err}')
    uniq={int(x[0]):x for x in rows};s=int(START.timestamp()*1000);e=int(END.timestamp()*1000)
    return [uniq[k] for k in sorted(uniq) if s<=k<e]

def split_name(dt):
    if dt.year<=2024:return 'train_2022_2024'
    if dt.year==2025:return 'validation_2025'
    return 'oos_2026_jan_aug'

def mean(xs):return sum(xs)/len(xs) if xs else None

def med(xs):return statistics.median(xs) if xs else None

def sign(x,eps=1e-12):return 1 if x>eps else(-1 if x<-eps else 0)

def conditions(a):
    out=[]
    for body,p in a['positions'].items():out.append(f'SIGN:{body}:{p["sign"]}')
    for body,z in a['retrograde'].items():out.append(f'{body}_'+('RETROGRADE' if z['retrograde'] else 'DIRECT'))
    for z in a['major_aspects']:out.append(f'ASPECT:{z["pair"]}:{z["aspect"]}')
    return out

def summarize(xs,base):
    if not xs:return {'n':0}
    metrics={}
    for key in METRICS:
        a=[x[key] for x in xs if x.get(key) is not None]
        if not a:continue
        m=mean(a);b=base[key];sd=statistics.stdev(a) if len(a)>1 else 0
        t=(m-b)/(sd/math.sqrt(len(a))) if sd>0 else None
        metrics[key]={'mean':round(m,7),'baseline':round(b,7),'edge':round(m-b,7),'t_vs_baseline':round(t,3) if t is not None else None}
    return {'n':len(xs),'metrics':metrics}

def factor_in_condition(factor,c):
    if c.startswith('SIGN:'):return c.split(':')[1]==factor
    if c.startswith('ASPECT:'):
        pair=c.split(':')[1];return factor in pair.split('-')
    return c.startswith(factor+'_')

def main():
    kl=fetch_daily();market=[];volhist=[]
    for x in kl:
        dt=datetime.fromtimestamp(int(x[0])/1000,timezone.utc)
        o,h,l,c,v=map(float,(x[1],x[2],x[3],x[4],x[5]));tb=float(x[9])
        hv=volhist[-20:];mv=med(hv);vs=v/mv if len(hv)>=10 and mv and mv>0 else None
        volhist.append(v)
        market.append({'date':dt.date().isoformat(),'dt':dt,'return':c/o-1,'abs_return':abs(c/o-1),'volume_surprise':vs,'buy_imbalance':2*(tb/v)-1 if v else 0})
    rows=[]
    for x in market:
        # Noon UTC samples the astronomical state near the middle of the exchange day.
        a=snapshot(x['dt']+timedelta(hours=12),orb=3.0)
        rows.append({**x,'split':split_name(x['dt']),'astro':a,'conds':conditions(a)})
    splits=('train_2022_2024','validation_2025','oos_2026_jan_aug')
    sr={s:[x for x in rows if x['split']==s] for s in splits}
    base={s:{k:mean([x[k] for x in xs if x.get(k) is not None]) for k in METRICS} for s,xs in sr.items()}
    allconds=sorted({c for x in rows for c in x['conds']})
    stats={};candidates=[]
    for c in allconds:
        z={s:summarize([x for x in sr[s] if c in x['conds']],base[s]) for s in splits};stats[c]=z
        ns=[z[s].get('n',0) for s in splits]
        if ns[0]<15 or ns[1]<5 or ns[2]<5:continue
        for metric in METRICS:
            try:edges=[z[s]['metrics'][metric]['edge'] for s in splits]
            except Exception:continue
            signs=[sign(e) for e in edges]
            if 0 in signs or len(set(signs))!=1 or abs(edges[2])<MIN_EFFECT[metric]:continue
            scale=MIN_EFFECT[metric]
            persistence=min(abs(e)/scale for e in edges)
            candidates.append({'condition':c,'metric':metric,'direction':'higher' if signs[0]>0 else 'lower','samples':ns,'edges':edges,'oos_edge':edges[2],'persistence_score':round(persistence,3),
                               't_train':z[splits[0]]['metrics'][metric]['t_vs_baseline'],'t_validation':z[splits[1]]['metrics'][metric]['t_vs_baseline'],'t_oos':z[splits[2]]['metrics'][metric]['t_vs_baseline']})
    top_by_metric={}
    for metric in METRICS:
        zz=[c for c in candidates if c['metric']==metric]
        zz.sort(key=lambda x:(x['persistence_score'],abs(x['oos_edge'])),reverse=True)
        top_by_metric[metric]=zz[:20]
    factor_best={}
    for f in BODIES:
        factor_best[f]={}
        for metric in METRICS:
            zz=[c for c in candidates if c['metric']==metric and factor_in_condition(f,c['condition'])]
            zz.sort(key=lambda x:(x['persistence_score'],abs(x['oos_edge'])),reverse=True)
            factor_best[f][metric]=zz[0] if zz else None
    out={
      'version':'DARA-Astro-FullGrid-v2','period':'2022-01-01 through 2026-08-31 UTC','days':len(rows),
      'factors':list(BODIES.keys()),'factor_count':len(BODIES),'pair_count':55,
      'features':['11 geocentric factor longitudes/signs','55 all-pairs major aspects','Mercury-through-Pluto retrograde/direct','true lunar node axis'],
      'splits':{s:len(sr[s]) for s in splits},'baselines':base,
      'selection':{'minimum_samples':[15,5,5],'requires_same_edge_sign_all_three_splits':True,'minimum_oos_effect':MIN_EFFECT},
      'warning':'Exploratory multi-hypothesis screen. Recurrence across chronological splits reduces overfit but does not prove astrology, causality, or tradability. Slow-body sign effects are especially confounded with calendar regimes.',
      'candidate_count':len(candidates),'top_by_metric':top_by_metric,'best_recurrent_signal_touching_each_factor':factor_best,
    }
    OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(out,indent=2))
    print(json.dumps({'days':out['days'],'candidate_count':len(candidates),'top_by_metric':{k:v[:8] for k,v in top_by_metric.items()},'factor_best':factor_best},indent=2))

if __name__=='__main__':main()
