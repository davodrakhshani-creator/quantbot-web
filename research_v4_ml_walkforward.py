"""Independent walk-forward cross-sectional ML lane for QuantBot v4.

Transparent, fixed models only: Ridge and shallow HistGradientBoosting plus their rank
ensemble. Features use public daily OHLCV. Every prediction is trained only on samples
whose forward target is already observable at prediction time. No live orders.
"""
from __future__ import annotations
import json, math, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import research_v3_independent as v3
import research_v3_audit as audit
import research_v3_snapshot_validation as snap

OUT=Path('data/v4_ml_walkforward_state.json')
BASE=.0013; C40=.0040
START='2019-01-01'


def get_ohlcv(sym):
    start=int(pd.Timestamp(START,tz='UTC').timestamp()*1000); end=int(datetime.now(timezone.utc).timestamp()*1000)
    rows=[]; sess=requests.Session()
    urls=v3.ENDPOINTS
    while start<end:
        batch=None
        for url in urls:
            try:
                r=sess.get(url,params=dict(symbol=sym,interval='1d',startTime=start,endTime=end,limit=1000),timeout=20)
                r.raise_for_status(); x=r.json()
                if isinstance(x,list): batch=x; break
            except Exception: pass
        if not batch: break
        rows.extend(batch); nxt=int(batch[-1][0])+86400000
        if nxt<=start: break
        start=nxt
        if len(batch)<1000: break
        time.sleep(.03)
    if not rows: raise RuntimeError('no ohlcv')
    d=pd.DataFrame(rows)
    idx=pd.to_datetime(d[0].astype('int64'),unit='ms',utc=True).dt.floor('D')
    return pd.DataFrame({'close':pd.to_numeric(d[4],errors='coerce').to_numpy(),
                         'quote_vol':pd.to_numeric(d[7],errors='coerce').to_numpy()},index=pd.DatetimeIndex(idx)).loc[lambda x:~x.index.duplicated(keep='last')]


def load_universe(symbols):
    closes=[]; vols=[]; errors={}
    for s in symbols:
        try:
            d=get_ohlcv(s)
            if d.close.notna().sum()>=500:
                closes.append(d.close.rename(s)); vols.append(d.quote_vol.rename(s))
            else: errors[s]='insufficient'
        except Exception as e: errors[s]=repr(e)
    px=pd.concat(closes,axis=1).sort_index(); vol=pd.concat(vols,axis=1).reindex(px.index)
    px=px.loc[px['BTCUSDT'].notna()]
    return px,vol,errors


def feature_frames(px,vol):
    ret=px.pct_change(fill_method=None)
    feats={
      'mom7':px/px.shift(7)-1,'mom30':px/px.shift(30)-1,'mom90':px/px.shift(90)-1,'mom180':px/px.shift(180)-1,
      'vol30':ret.rolling(30).std()*math.sqrt(365.25),
      'dd90':px/px.rolling(90,min_periods=60).max()-1,
      'qv30':np.log1p(vol.rolling(30,min_periods=15).median()),
      'qv_accel':np.log1p(vol.rolling(7,min_periods=5).median())-np.log1p(vol.rolling(30,min_periods=15).median()),
    }
    target=px.shift(-7)/px-1
    return feats,target


def rows_at(feats,target,dates):
    rec=[]
    for dt in dates:
        for sym in target.columns:
            vals=[feats[k].at[dt,sym] if dt in feats[k].index and sym in feats[k].columns else np.nan for k in feats]
            y=target.at[dt,sym] if dt in target.index else np.nan
            if np.all(np.isfinite(vals)) and np.isfinite(y): rec.append((dt,sym,*vals,y))
    cols=['dt','sym',*feats.keys(),'y']
    return pd.DataFrame(rec,columns=cols)


def weekly_dates(idx):
    m=v3.rebalance_mask(idx,'W')
    return list(idx[m.to_numpy()])


def make_positions(px,vol,model_kind):
    feats,target=feature_frames(px,vol); dates=weekly_dates(px.index)
    pos=pd.DataFrame(0.0,index=px.index,columns=px.columns)
    last_fit=None; models=None
    for dt in dates:
        if dt<pd.Timestamp('2021-01-01',tz='UTC'): continue
        # Target is 7d forward, so training rows stop at t-8. Use rolling ~3 years to adapt regimes.
        train_end=dt-pd.Timedelta(days=8); train_start=train_end-pd.Timedelta(days=1095)
        hist_dates=[d for d in dates if train_start<=d<=train_end]
        if len(hist_dates)<40: continue
        if models is None or last_fit is None or (dt-last_fit).days>=28:
            tr=rows_at(feats,target,hist_dates)
            if len(tr)<300: continue
            X=tr[list(feats)].to_numpy(); y=tr.y.to_numpy()
            ridge=make_pipeline(StandardScaler(),Ridge(alpha=10.0))
            hgb=HistGradientBoostingRegressor(max_depth=2,learning_rate=.05,max_iter=120,l2_regularization=10.0,random_state=17)
            ridge.fit(X,y); hgb.fit(X,y); models=(ridge,hgb); last_fit=dt
        test=[]; names=[]
        for sym in px.columns:
            vals=np.array([feats[k].at[dt,sym] for k in feats],dtype=float)
            if np.all(np.isfinite(vals)): test.append(vals); names.append(sym)
        if len(test)<5: continue
        X=np.asarray(test); pr=models[0].predict(X); pg=models[1].predict(X)
        if model_kind=='ridge': score=pd.Series(pr,index=names)
        elif model_kind=='hgb': score=pd.Series(pg,index=names)
        else:
            rr=pd.Series(pr,index=names).rank(pct=True); rg=pd.Series(pg,index=names).rank(pct=True)
            score=(rr+rg)/2
        gate=px['BTCUSDT'].at[dt]>px['BTCUSDT'].rolling(200).mean().at[dt]
        if not gate: continue
        if model_kind!='ensemble': eligible=score[score>0]
        else: eligible=score[score>=.60]
        chosen=list(eligible.sort_values(ascending=False).index[:3])
        if not chosen: continue
        av=px[chosen].pct_change(fill_method=None).rolling(30).std().loc[dt]*math.sqrt(365.25)
        av=av.replace(0,np.nan).dropna()
        chosen=[x for x in chosen if x in av.index]
        if not chosen: continue
        inv=1/av[chosen].clip(lower=.10); w=inv/inv.sum(); scalar=min(1.0,.12/max(float((w*av[chosen]).sum()),1e-9))
        pos.loc[dt,chosen]=w*scalar
    rb=v3.rebalance_mask(px.index,'W')
    return pos.where(rb,np.nan).ffill().fillna(0.0)


def evaluate(px,pos):
    h=audit.diag(px,pos,'2022-01-01','2026-04-24',BASE); s=audit.diag(px,pos,'2022-01-01','2026-04-24',C40)
    folds=[]
    for y in range(2022,2026):
        d=audit.diag(px,pos,f'{y}-01-01',f'{y}-12-31',BASE); d['year']=y; folds.append(d)
    roll=audit.rolling_windows(px.loc[px.index<=v3.LOCK_DATE],pos.loc[pos.index<=v3.LOCK_DATE],start='2022-01-01',window=180,step=30,cost=BASE)
    post=audit.diag(px,pos,'2026-04-25',None,BASE); post40=audit.diag(px,pos,'2026-04-25',None,C40)
    posfold=sum(x['net_pct']>0 and x['active_day_pct']>=10 for x in folds)
    gate=(h['net_pct']>0 and h['sharpe']>=.5 and h['pf']>=1.08 and h['max_dd_pct']<=20 and s['net_pct']>0 and
          posfold>=3 and roll['positive_fraction_meaningful']>=.60 and h['active_day_pct']>=15)
    return {'history':h,'stress40':s,'folds':folds,'rolling180':{k:v for k,v in roll.items() if k!='windows'},
            'postlock_diagnostic':post,'postlock40_diagnostic':post40,'history_gate':bool(gate)}


def main():
    cut=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC')
    current_syms=v3.UNIVERSE; historical_syms=snap.SYMS
    universes={}
    for name,syms in [('current',current_syms),('historical_snapshot',historical_syms)]:
        px,vol,err=load_universe(syms); px=px.loc[px.index<cut]; vol=vol.reindex(px.index)
        universes[name]=(px,vol,err)
    results={}
    for kind in ['ridge','hgb','ensemble']:
        per={}
        for uname,(px,vol,err) in universes.items():
            p=make_positions(px,vol,kind); per[uname]=evaluate(px,p)
        results[kind]=per
    both={k:all(results[k][u]['history_gate'] for u in results[k]) for k in results}
    # Winner uses prelock evidence only; reused postlock remains diagnostic.
    def sc(k):
        vals=[]
        for u in results[k]:
            h=results[k][u]['history']; r=results[k][u]['rolling180']
            vals.append(2*h['sharpe']+math.log(max(h['pf'],.01))+2*r['positive_fraction_meaningful']-.05*h['max_dd_pct'])
        return min(vals)
    winner=max(results,key=lambda k:(both[k],sc(k)))
    post_ok=all(results[winner][u]['postlock40_diagnostic']['net_pct']>0 for u in results[winner])
    state={'version':'quantbot-v4-walkforward-ml','updated':datetime.now(timezone.utc).isoformat(),
           'models':['Ridge(alpha=10)','HistGradientBoosting(depth=2,l2=10)','cross-sectional rank ensemble'],
           'features':['mom7','mom30','mom90','mom180','vol30','drawdown90','quote-volume30','quote-volume-acceleration'],
           'protocol':'3-year rolling training; 7-day forward target; train data embargoed 8 days; refit every 28d; weekly top3; BTC>200d gate; inverse-vol 12% target; no hyperparameter tuning on postlock.',
           'results':results,'selected':winner,'both_universe_history_gate':both[winner],
           'reused_postlock40_positive_both':bool(post_ok),
           'status':'ML_SUPPORTS_PAPER_CANDIDATE' if both[winner] and post_ok else 'ML_NO_ROBUST_EDGE',
           'live_money_authorized':False,
           'limitations':['Current and fixed-snapshot universes are not a fully dynamic point-in-time universe.','Daily OHLCV omits order-book and intraday microstructure.','2026-04-25+ is already-viewed diagnostic evidence; fresh proof is forward-only.']}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'selected':winner,'both_gate':both[winner],'post40_both':post_ok,'status':state['status']},indent=2))
if __name__=='__main__': main()
