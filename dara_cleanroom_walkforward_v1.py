import json, math
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
import dara_v18_prototype_memory_sep7 as loader

# CLEAN-ROOM: do not import any learned rules/prototypes/pattern scores from Aug/Sep.
# Only the generic Binance archive loader is reused.
COST=0.0011          # 0.11% round trip friction on notional
TP=0.0044            # 4x fee gross target
STOP=0.0020          # 0.20% structural fixed research stop
HORIZON=45           # max minutes to resolve the event
RISK_EQ=0.0020       # 0.20% equity loss if stop+cost occurs
MAX_LEV=3.0
START_EQ=100.0
OUT=Path('data/dara_cleanroom_walkforward_v1.json')

TRAIN_A=datetime(2026,3,15,tzinfo=timezone.utc)
TRAIN_B=datetime(2026,5,15,tzinfo=timezone.utc)   # exclusive, May15 is embargo
VAL_A=datetime(2026,5,16,tzinfo=timezone.utc)
VAL_B=datetime(2026,5,31,tzinfo=timezone.utc)     # exclusive, May31 is embargo
TEST_A=datetime(2026,6,1,tzinfo=timezone.utc)
TEST_B=datetime(2026,6,15,tzinfo=timezone.utc)    # exclusive

FEATURES=[
 'r1','r3','r5','r10','r15','r30','accel3','accel5',
 'delta1','delta3','delta5','delta15','abs_delta1','volr_log',
 'vwap_dist','range1','body1','closepos','rv5','rv15','rv30',
 'trend5','trend15','trend30','tod_sin','tod_cos'
]

def ts_ms(dt): return int(dt.timestamp()*1000)

def load_df():
    # buffer on both sides for features and labels
    m,_,_,_=loader.load_market((2026,3,14),(2026,6,15))
    df=pd.DataFrame(m)
    df=df.drop_duplicates('t').sort_values('t').reset_index(drop=True)
    # expected generic enriched fields: t,o,h,l,c,delta,volr,vwap15
    for col in ['o','h','l','c','delta','volr','vwap15']:
        df[col]=pd.to_numeric(df[col],errors='coerce')
    c=df['c']
    def ret(k): return c/c.shift(k)-1
    df['r1']=ret(1);df['r3']=ret(3);df['r5']=ret(5);df['r10']=ret(10);df['r15']=ret(15);df['r30']=ret(30)
    df['accel3']=df['r3']-(c.shift(3)/c.shift(6)-1)
    df['accel5']=df['r5']-(c.shift(5)/c.shift(10)-1)
    df['delta1']=df['delta']
    for k in [3,5,15]: df[f'delta{k}']=df['delta'].rolling(k).mean()
    df['abs_delta1']=df['delta'].abs()
    df['volr_log']=np.log1p(df['volr'].clip(lower=0,upper=20))
    df['vwap_dist']=c/df['vwap15']-1
    df['range1']=(df['h']-df['l'])/c
    df['body1']=(df['c']-df['o'])/df['o']
    df['closepos']=((df['c']-df['l'])/(df['h']-df['l']).replace(0,np.nan)).clip(0,1).fillna(.5)
    lr=np.log(c).diff()
    for k in [5,15,30]: df[f'rv{k}']=lr.rolling(k).std()*math.sqrt(k)
    for k in [5,15,30]: df[f'trend{k}']=c/c.ewm(span=k,adjust=False).mean()-1
    dt=pd.to_datetime(df['t'],unit='ms',utc=True)
    mins=dt.dt.hour*60+dt.dt.minute
    df['tod_sin']=np.sin(2*np.pi*mins/1440);df['tod_cos']=np.cos(2*np.pi*mins/1440)
    return df

def first_hit_labels(df):
    n=len(df);yl=np.zeros(n,dtype=np.int8);ys=np.zeros(n,dtype=np.int8)
    # entry is next minute open; stop-first if stop and target both touched on same bar
    o=df['o'].to_numpy();h=df['h'].to_numpy();l=df['l'].to_numpy()
    for i in range(30,n-HORIZON-1):
        e=o[i+1]
        ltp=e*(1+TP);lsl=e*(1-STOP);stp=e*(1-TP);ssl=e*(1+STOP)
        long_done=short_done=False
        for j in range(i+1,min(n,i+1+HORIZON)):
            if not long_done:
                if l[j]<=lsl: long_done=True
                elif h[j]>=ltp: yl[i]=1;long_done=True
            if not short_done:
                if h[j]>=ssl: short_done=True
                elif l[j]<=stp: ys[i]=1;short_done=True
            if long_done and short_done: break
    return yl,ys

def subset(df,a,b):
    t=df['t'].to_numpy();return np.where((t>=ts_ms(a))&(t<ts_ms(b)))[0]

def fit_model(X,y):
    pos=max(1,int(y.sum()));neg=max(1,len(y)-pos)
    w=np.where(y==1,0.5/pos,0.5/neg)*len(y)
    mdl=HistGradientBoostingClassifier(
        learning_rate=.05,max_iter=180,max_leaf_nodes=15,min_samples_leaf=80,
        l2_regularization=2.0,random_state=7,early_stopping=True,validation_fraction=.12
    )
    mdl.fit(X,y,sample_weight=w)
    return mdl

def outcome(df,i,side,equity):
    ei=i+1
    if ei>=len(df): return None
    e=float(df.at[ei,'o'])
    notional=min(MAX_LEV*equity,(RISK_EQ*equity)/(STOP+COST))
    if notional<=0:return None
    if side=='LONG': tgt=e*(1+TP);stp=e*(1-STOP)
    else: tgt=e*(1-TP);stp=e*(1+STOP)
    mfe=mae=0.0
    last=min(len(df)-1,ei+HORIZON-1);px=float(df.at[last,'c']);reason='TIMEOUT'
    for j in range(ei,last+1):
        hi=float(df.at[j,'h']);lo=float(df.at[j,'l'])
        if side=='LONG':
            mfe=max(mfe,hi/e-1);mae=max(mae,1-lo/e)
            if lo<=stp: px=stp;reason='STOP';last=j;break
            if hi>=tgt: px=tgt;reason='TP';last=j;break
        else:
            mfe=max(mfe,1-lo/e);mae=max(mae,hi/e-1)
            if hi>=stp: px=stp;reason='STOP';last=j;break
            if lo<=tgt: px=tgt;reason='TP';last=j;break
    gross_ret=(px/e-1)*(1 if side=='LONG' else -1)
    gross=notional*gross_ret;cost=notional*COST;net=gross-cost
    return {'exit_i':last,'side':side,'entry_i':ei,'entry':e,'exit':px,'reason':reason,
            'gross':gross,'cost':cost,'net':net,'mfe':mfe,'mae':mae,'notional':notional}

def backtest(df,idx,pl,ps,thr,gap=0.03,start_eq=START_EQ):
    allowed=set(int(x) for x in idx);eq=start_eq;i=int(idx[0]);end=int(idx[-1]);tr=[]
    peak=eq;maxdd=0.0
    while i<=end:
        if i not in allowed or i+1> end: i+=1;continue
        a=float(pl[i]);b=float(ps[i]);side=None;prob=0
        if max(a,b)>=thr and abs(a-b)>=gap:
            side='LONG' if a>b else 'SHORT';prob=max(a,b)
        if side is None:i+=1;continue
        z=outcome(df,i,side,eq)
        if z is None:break
        eq+=z['net'];peak=max(peak,eq);maxdd=max(maxdd,(peak-eq)/peak)
        z.update({'signal_i':i,'prob_long':a,'prob_short':b,'prob':prob,'equity_after':eq});tr.append(z)
        i=z['exit_i']+1
    gp=sum(x['net'] for x in tr if x['net']>0);gl=-sum(x['net'] for x in tr if x['net']<=0)
    wins=sum(x['reason']=='TP' for x in tr)
    return {'n':len(tr),'wins':wins,'losses':len(tr)-wins,'wr':wins/len(tr) if tr else None,
            'gross':sum(x['gross'] for x in tr),'cost':sum(x['cost'] for x in tr),'net':eq-start_eq,
            'final_equity':eq,'pf':gp/gl if gl else (99 if gp else None),'maxdd':maxdd,'trades':tr}

def compact(r):
    return {k:(round(v,6) if isinstance(v,float) else v) for k,v in r.items() if k!='trades'}

def daily(df,r):
    g={}
    for x in r['trades']:
        d=pd.to_datetime(int(df.at[x['entry_i'],'t']),unit='ms',utc=True).strftime('%Y-%m-%d')
        g.setdefault(d,[]).append(x)
    out={}
    for d,xs in g.items():
        gp=sum(x['net'] for x in xs if x['net']>0);gl=-sum(x['net'] for x in xs if x['net']<=0)
        out[d]={'n':len(xs),'wins':sum(x['reason']=='TP' for x in xs),'net':round(sum(x['net'] for x in xs),6),'pf':round(gp/gl,2) if gl else (99 if gp else None)}
    return out

def main():
    df=load_df();yl,ys=first_hit_labels(df)
    good=np.isfinite(df[FEATURES]).all(axis=1).to_numpy()
    tri=subset(df,TRAIN_A,TRAIN_B);vai=subset(df,VAL_A,VAL_B);tei=subset(df,TEST_A,TEST_B)
    tri=tri[good[tri]];vai=vai[good[vai]];tei=tei[good[tei]]
    X=df[FEATURES].to_numpy(dtype=np.float64)
    ml=fit_model(X[tri],yl[tri]);ms=fit_model(X[tri],ys[tri])
    pl=np.zeros(len(df));ps=np.zeros(len(df));
    mask=np.concatenate([vai,tei]);pl[mask]=ml.predict_proba(X[mask])[:,1];ps[mask]=ms.predict_proba(X[mask])[:,1]
    # Threshold is selected ONLY on validation, using after-cost sequential equity.
    be=(STOP+COST)/(TP+STOP)  # event-probability break-even approximation
    candidates=sorted(set([round(x,3) for x in np.arange(max(.50,be+.01),.91,.025)]))
    table=[];best=None
    for th in candidates:
        r=backtest(df,vai,pl,ps,th)
        obj=r['net']-START_EQ*0.35*r['maxdd']  # modest drawdown penalty
        row={'threshold':th,**compact(r),'objective':obj};table.append(row)
        if r['n']>=12 and (best is None or obj>best[0]):best=(obj,th,r)
    if best is None:
        best=max((x['objective'],x['threshold'],backtest(df,vai,pl,ps,x['threshold'])) for x in table)
    th=best[1]
    vr=best[2]
    # Freeze here. Test block is touched only after model+threshold are fixed.
    tr=backtest(df,tei,pl,ps,th)
    # A stricter test subperiod split is reported without retuning.
    t1=subset(df,datetime(2026,6,1,tzinfo=timezone.utc),datetime(2026,6,8,tzinfo=timezone.utc));t1=t1[good[t1]]
    t2=subset(df,datetime(2026,6,8,tzinfo=timezone.utc),datetime(2026,6,15,tzinfo=timezone.utc));t2=t2[good[t2]]
    r1=backtest(df,t1,pl,ps,th);r2=backtest(df,t2,pl,ps,th)
    auc={}
    try: auc['val_long']=roc_auc_score(yl[vai],pl[vai]);auc['val_short']=roc_auc_score(ys[vai],ps[vai])
    except Exception: pass
    out={
      'version':'DARA-CLEANROOM-WALKFORWARD-v1',
      'method':'No Aug/Sep hand rules, no prototype/pattern/astro memory. Generic 1m Binance archive only. Two supervised event models; chronological train/validation/test with embargo days; threshold chosen on validation net-after-cost only, then frozen.',
      'periods':{'train_utc':[TRAIN_A.isoformat(),TRAIN_B.isoformat()],'validation_utc':[VAL_A.isoformat(),VAL_B.isoformat()],'test_utc':[TEST_A.isoformat(),TEST_B.isoformat()]},
      'economics':{'cost_roundtrip_pct':COST*100,'tp_pct':TP*100,'stop_pct':STOP*100,'horizon_min':HORIZON,'risk_pct_equity':RISK_EQ*100,'max_leverage':MAX_LEV,'start_equity':START_EQ,'approx_event_breakeven_probability':be},
      'features':FEATURES,'samples':{'train':len(tri),'validation':len(vai),'test':len(tei),'train_long_positive_rate':float(yl[tri].mean()),'train_short_positive_rate':float(ys[tri].mean())},
      'validation_auc':auc,'validation_threshold_search':table,'frozen_threshold':th,'validation_result':compact(vr),
      'test_full':compact(tr),'test_week1':compact(r1),'test_week2':compact(r2),'test_daily':daily(df,tr),
      'test_side':{
        'LONG':{'n':sum(x['side']=='LONG' for x in tr['trades']),'wins':sum(x['side']=='LONG' and x['reason']=='TP' for x in tr['trades']),'net':round(sum(x['net'] for x in tr['trades'] if x['side']=='LONG'),6)},
        'SHORT':{'n':sum(x['side']=='SHORT' for x in tr['trades']),'wins':sum(x['side']=='SHORT' and x['reason']=='TP' for x in tr['trades']),'net':round(sum(x['net'] for x in tr['trades'] if x['side']=='SHORT'),6)}
      }
    }
    OUT.write_text(json.dumps(out,indent=2))
    print(json.dumps({'threshold':th,'samples':out['samples'],'validation':out['validation_result'],'test':out['test_full'],'week1':out['test_week1'],'week2':out['test_week2'],'side':out['test_side']},indent=2))
if __name__=='__main__':main()
