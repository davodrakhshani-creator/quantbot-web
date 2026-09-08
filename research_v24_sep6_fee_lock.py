from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_v19_japan_tick_clone as v19
import research_v20_japan_event_clone as v20

DAY='2026-09-06'
STATE=Path('data/v24_sep6_fee_lock_state.json')
ROUNDTRIP_COST_BPS=7.5
PROFIT_UNLOCK_BPS=9.0   # fee hurdle + safety margin
EMERGENCY_STOP_BPS=12.0
MAX_HOLD_SEC=1800
MIN_ENGINE_GAP=60
MAX_TAKE_BPS=60.0
TRAIL_MIN_BPS=2.0
TRAIL_FRAC=0.35


def simulate(y):
    sig=v20.events(y)
    rows=[]; i=0; n=len(y); idx=y.index
    last={'jun':-10**9,'hansan':-10**9}
    while i<n-2:
        side=int(sig.side.iloc[i]); eng=sig.engine.iloc[i]
        if side==0 or eng is None or i-last[eng]<MIN_ENGINE_GAP:
            i+=1; continue
        last[eng]=i
        ei=i+1
        entry=float(y.open.iloc[ei])
        best=-1e9; unlocked=False
        xi=min(ei+MAX_HOLD_SEC,n-1); reason='max_hold'
        gross=side*(float(y.close.iloc[xi])/entry-1)*1e4
        peak=best
        for j in range(ei, min(ei+MAX_HOLD_SEC,n-1)+1):
            hi=float(y.high.iloc[j]); lo=float(y.low.iloc[j]); cl=float(y.close.iloc[j])
            # favorable excursion uses tradable side of OHLC approximation conservatively by close for exit logic
            cur=side*(cl/entry-1)*1e4
            if side==1:
                fav=(hi/entry-1)*1e4
                adverse=(lo/entry-1)*1e4
            else:
                fav=(entry/lo-1)*1e4
                adverse=(entry/hi-1)*1e4
            best=max(best,fav)
            if adverse<=-EMERGENCY_STOP_BPS:
                gross=-EMERGENCY_STOP_BPS; xi=j; reason='emergency_stop'; break
            if best>=PROFIT_UNLOCK_BPS:
                unlocked=True
            if unlocked:
                trail=max(TRAIL_MIN_BPS, TRAIL_FRAC*max(best,0))
                # profitable exit only after fee gate, and only if close remains above fee hurdle
                if cur>=ROUNDTRIP_COST_BPS and (best-cur)>=trail:
                    gross=cur; xi=j; reason='fee_unlocked_trail'; break
                if best>=MAX_TAKE_BPS and cur>=ROUNDTRIP_COST_BPS:
                    gross=cur; xi=j; reason='max_take'; break
        if xi>=n-1 and reason=='max_hold':
            reason='day_end_or_max_hold'
        net=gross-ROUNDTRIP_COST_BPS
        rows.append({
            'signal_ts':str(idx[i]),'entry_ts':str(idx[ei]),'exit_ts':str(idx[xi]),
            'engine':eng,'side':side,'gross_bps':float(gross),'net_bps':float(net),
            'best_favorable_bps':float(best),'profit_gate_reached':bool(unlocked),'reason':reason,
            'hold_sec':int(xi-ei)
        })
        i=xi+2
    return pd.DataFrame(rows)


def equity_path(t, leverage):
    eq=100.0; peak=eq; maxdd=0.0
    wins=losses=0
    for _,r in t.iterrows():
        ret=leverage*float(r.net_bps)/1e4
        eq=max(0.0,eq*(1.0+ret))
        peak=max(peak,eq)
        if peak>0: maxdd=max(maxdd,(peak-eq)/peak*100)
        if r.net_bps>0: wins+=1
        elif r.net_bps<0: losses+=1
    return {'start_usd':100.0,'end_usd':eq,'pnl_usd':eq-100.0,'return_pct':eq-100.0,
            'wins':wins,'losses':losses,'max_drawdown_pct':maxdd}


def main():
    y=v19.features(v19.fetch_day(DAY))
    t=simulate(y)
    if t.empty:
        met={'trades':0,'wins':0,'losses':0,'win_pct':None,'avg_net_bps':None,'pf':None,'gate_reached_pct':None}
    else:
        a=t.net_bps.to_numpy(); w=a[a>0]; l=a[a<0]
        pf=float(w.sum()/abs(l.sum())) if len(l) else (999.0 if len(w) else None)
        met={'trades':int(len(t)),'wins':int((a>0).sum()),'losses':int((a<0).sum()),
             'win_pct':float((a>0).mean()*100),'avg_net_bps':float(a.mean()),'median_net_bps':float(np.median(a)),
             'pf':pf,'gate_reached_pct':float(t.profit_gate_reached.mean()*100),
             'avg_best_favorable_bps':float(t.best_favorable_bps.mean()),
             'avg_hold_sec':float(t.hold_sec.mean())}
    out={'version':'quantbot-v24-sep6-fee-lock','purpose':'SINGLE_DAY_DIAGNOSTIC_NOT_LIVE_AUTHORIZATION',
         'date':DAY,'symbol':'BTCUSDT','source':'Binance Vision USD-M aggTrades -> 1s',
         'method':'Jun distortion/snapback + Hansan breakout/acceleration + Testa tape-quality proxy',
         'rules':{'roundtrip_cost_bps':ROUNDTRIP_COST_BPS,'profit_unlock_bps':PROFIT_UNLOCK_BPS,
                  'emergency_stop_bps':EMERGENCY_STOP_BPS,'max_hold_sec':MAX_HOLD_SEC,
                  'trail_min_bps':TRAIL_MIN_BPS,'trail_frac':TRAIL_FRAC},
         'metrics':met,'equity':{str(x)+'x':equity_path(t,x) for x in [1,2,3,5]},
         'trades':t.to_dict('records') if not t.empty else [],'live_orders':False}
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))

if __name__=='__main__': main()
