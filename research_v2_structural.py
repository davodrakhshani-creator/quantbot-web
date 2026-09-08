"""QuantBot v2 Structural Alpha research harness.
Public-data research only. No exchange credentials, no live orders.
Stages 12-19 reproduce the daily BTC/ETH structural-alpha tests recorded in
data/v2_structural_state.json. Stage 11 is intentionally data-limited until
timestamp-aligned funding+basis+borrow/execution history is available.
"""
import math
import numpy as np
import pandas as pd

COST_PER_UNIT_TURNOVER = 0.0013  # 13 bps; full open+close ~=26 bps
URLS = {
    "BTC": "https://raw.githubusercontent.com/lth-elm/Backtrading-Python-Binance/main/data/BTCUSDT-2017-2020-1d.csv",
    "ETH": "https://raw.githubusercontent.com/lth-elm/Backtrading-Python-Binance/main/data/ETHUSDT-2017-2020-1d.csv",
}
COLS = ["ts","open","high","low","close","volume","close_ts","quote_vol","trades","taker_base","taker_quote","ignore"]

def load():
    out={}
    for k,u in URLS.items():
        d=pd.read_csv(u,header=None,names=COLS)
        d["date"]=pd.to_datetime(d.ts,unit="s",utc=True).dt.floor("D")
        out[k]=d.set_index("date")[["close"]].rename(columns={"close":k})
    x=out["BTC"].join(out["ETH"],how="inner").dropna()
    return x

def metrics(ret):
    ret=pd.Series(ret).fillna(0.0)
    eq=(1+ret).cumprod()
    dd=1-eq/eq.cummax()
    pos=ret[ret>0].sum(); neg=-ret[ret<0].sum()
    years=max(len(ret)/365.25,1/365.25)
    return {
        "n_days":int(len(ret)),
        "net_pct":float((eq.iloc[-1]-1)*100),
        "ann_pct":float((eq.iloc[-1]**(1/years)-1)*100),
        "sharpe":float(ret.mean()/ret.std(ddof=1)*math.sqrt(365.25)) if ret.std(ddof=1)>0 else 0.0,
        "max_dd_pct":float(dd.max()*100),
        "positive_day_pct":float((ret>0).mean()*100),
        "pf":float(pos/max(neg,1e-12)),
        "avg_daily_bps":float(ret.mean()*10000),
    }

def backtest(x, pos):
    r=x.pct_change().fillna(0.0)
    p=pos.shift(1).fillna(0.0)  # causal execution: today's holding uses prior-close signal
    turnover=p.diff().abs().sum(axis=1)
    pnl=(p*r).sum(axis=1)-COST_PER_UNIT_TURNOVER*turnover
    m=metrics(pnl)
    m["turnover_units"]=float(turnover.sum())
    m["cost_pct"]=float((COST_PER_UNIT_TURNOVER*turnover).sum()*100)
    return m

def mom(x,L): return x/x.shift(L)-1
def ann_vol(x,L=20): return x.pct_change().rolling(L).std()*math.sqrt(365.25)

def stage_positions(x):
    sig=sum(np.sign(mom(x,L)) for L in (20,60,120))
    s12=pd.DataFrame({"BTC":np.sign(sig.BTC),"ETH":0.0},index=x.index)
    s13=np.sign(sig)*0.5

    v=ann_vol(x,20).clip(lower=0.05)
    raw=np.sign(sig)*(0.15/v).clip(upper=1.0)
    gross=raw.abs().sum(axis=1).clip(lower=1.0)
    s14=raw.div(gross,axis=0)

    sma100=x.rolling(100).mean()
    s15=((x>sma100)&(mom(x,60)>0)).astype(float)*0.5

    d=mom(x,60).BTC-mom(x,60).ETH
    active=d.abs()>=0.05
    s16=pd.DataFrame(0.0,index=x.index,columns=x.columns)
    s16.loc[active & (d>0),["BTC","ETH"]]=[0.5,-0.5]
    s16.loc[active & (d<0),["BTC","ETH"]]=[-0.5,0.5]

    lr=np.log(x.BTC/x.ETH)
    z=(lr-lr.rolling(30).mean())/lr.rolling(30).std()
    s17=pd.DataFrame(0.0,index=x.index,columns=x.columns)
    s17.loc[z>1.5,["BTC","ETH"]]=[-0.5,0.5]
    s17.loc[z<-1.5,["BTC","ETH"]]=[0.5,-0.5]

    s18=np.sign(mom(x,60))*0.5
    s18=s18.where(ann_vol(x,20)<=1.10,0.0)
    s19=0.7*s15+0.3*s17
    return {12:s12,13:s13,14:s14,15:s15,16:s16,17:s17,18:s18,19:s19}

if __name__=="__main__":
    x=load()
    oos0=int(len(x)*0.80)
    folds=[(int(len(x)*0.55),int(len(x)*0.70)),(int(len(x)*0.70),int(len(x)*0.85)),(int(len(x)*0.85),len(x))]
    for st,p in stage_positions(x).items():
        print("stage",st,"OOS",backtest(x.iloc[oos0:],p.iloc[oos0:]))
        print("WF",[backtest(x.iloc[a:b],p.iloc[a:b]) for a,b in folds])
