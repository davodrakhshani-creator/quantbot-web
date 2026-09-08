"""QuantBot v5 precision-layer tournament.

Goal: reduce false/weak momentum entries without retuning the frozen forward T2 rule.
This is a separate research lane. Candidate selection uses only data <= 2026-04-24.
The already-viewed later period and Coinbase are diagnostics only. No live orders.
"""
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import research_v3_independent as v3
import research_v3_audit as audit
import research_v3_snapshot_validation as snap
import research_v4_dynamic_universe as dyn
import research_v5_coinbase_validation as cb

OUT=Path('data/v5_precision_state.json')
LOCK=v3.LOCK_DATE
BASE=.0013; C40=.0040; C75=.0075


def _positions(px:pd.DataFrame, qv:pd.DataFrame|None=None):
    m20,m60,m120,m180=[v3.momentum(px,n) for n in (20,60,120,180)]
    raw=.25*m20+.35*m60+.25*m120+.15*m180
    vol=v3.ann_vol(px,30).replace(0,np.nan)
    btc=px['BTCUSDT']
    market=(btc>btc.rolling(200).mean())&(m60['BTCUSDT']>0)&(m120['BTCUSDT']>0)
    disp=m120.std(axis=1,skipna=True)
    disp80=disp.rolling(252,min_periods=126).quantile(.80).shift(1)
    disp_ok=disp<disp80
    breadth60=(m60>0).sum(axis=1)/m60.notna().sum(axis=1).replace(0,np.nan)
    breadth120=(m120>0).sum(axis=1)/m120.notna().sum(axis=1).replace(0,np.nan)
    b120=breadth120>=.50
    bdual=(breadth60>=.50)&(breadth120>=.50)
    own200=px>px.rolling(200,min_periods=180).mean()
    dual=(m60>0)&(m120>0)
    triple=dual&(m180>0)
    liq=qv.rolling(30,min_periods=20).median() if qv is not None else None

    specs={
      'P0_T2_baseline':dict(gate=market, af=None,k=3),
      'P1_disp80':dict(gate=market&disp_ok, af=None,k=3),
      'P2_breadth120':dict(gate=market&b120, af=None,k=3),
      'P3_breadth_dual':dict(gate=market&bdual, af=None,k=3),
      'P4_asset_dual':dict(gate=market, af=dual,k=3),
      'P5_asset_sma200':dict(gate=market, af=own200,k=3),
      'P6_asset_dual_sma200':dict(gate=market, af=(dual&own200),k=3),
      'P7_disp_asset_dual':dict(gate=market&disp_ok, af=dual,k=3),
      'P8_breadth_asset_dual':dict(gate=market&b120, af=dual,k=3),
      'P9_asset_triple':dict(gate=market, af=triple,k=3),
      'P10_top2_asset_dual':dict(gate=market, af=dual,k=2),
      'P11_consensus':dict(gate=market&disp_ok&b120, af=(dual&own200),k=3),
    }
    out={n:pd.DataFrame(0.0,index=px.index,columns=px.columns) for n in specs}
    for dt in px.index:
        base_ok=raw.loc[dt].notna()&vol.loc[dt].notna()&px.loc[dt].notna()&(raw.loc[dt]>0)
        if liq is not None:
            liq_ok=liq.loc[dt].notna()&base_ok
            eligible=list(liq.loc[dt,liq_ok].sort_values(ascending=False).index[:15])
            universe_mask=pd.Series(False,index=px.columns); universe_mask.loc[eligible]=True
            base_ok &= universe_mask
        for name,sp in specs.items():
            if not bool(sp['gate'].loc[dt]): continue
            ok=base_ok.copy()
            if sp['af'] is not None: ok &= sp['af'].loc[dt].fillna(False)
            names=list(raw.loc[dt,ok].sort_values(ascending=False).index[:sp['k']])
            if not names: continue
            inv=1/vol.loc[dt,names].clip(lower=.10); w=inv/inv.sum()
            avg=float((w*vol.loc[dt,names]).sum()); scalar=min(1.0,.12/max(avg,1e-9))
            out[name].loc[dt,names]=w*scalar
    rb=v3.rebalance_mask(px.index,'W')
    return {n:p.where(rb,np.nan).ffill().fillna(0.0) for n,p in out.items()}


def _eval(px,pos):
    hist=audit.diag(px,pos,'2021-01-01','2026-04-24',BASE)
    s40=audit.diag(px,pos,'2021-01-01','2026-04-24',C40)
    s75=audit.diag(px,pos,'2021-01-01','2026-04-24',C75)
    delay=audit.diag(px,pos,'2021-01-01','2026-04-24',BASE,extra_delay=1)
    folds=[]
    for y in range(2021,2026):
        d=audit.diag(px,pos,f'{y}-01-01',f'{y}-12-31',BASE); d['year']=y; folds.append(d)
    meaningful=[f for f in folds if f.get('active_day_pct',0)>=10]
    positives=sum(f.get('net_pct',0)>0 for f in meaningful)
    rolls=audit.rolling_windows(px.loc[px.index<=LOCK],pos.loc[pos.index<=LOCK],window=180,step=30,cost=BASE)
    required=max(3,math.ceil(.75*len(meaningful))) if meaningful else 99
    gate=(hist['net_pct']>0 and hist['sharpe']>=.55 and hist['pf']>=1.12 and hist['max_dd_pct']<=15 and
          s40['net_pct']>0 and s75['net_pct']>0 and delay['net_pct']>0 and hist.get('active_day_pct',0)>=15 and
          len(meaningful)>=3 and positives>=required and rolls.get('meaningful_count',0)>=20 and
          rolls.get('positive_fraction_meaningful',0)>=.60)
    score=(3.0*hist['pf']+1.7*hist['sharpe']+2.5*rolls.get('positive_fraction_meaningful',0)
           -.07*hist['max_dd_pct']+.012*s75['net_pct']+.008*hist.get('active_day_win_pct',0))
    return {'history':hist,'stress40':s40,'stress75':s75,'delay2_total':delay,'folds':folds,
            'rolling180':{k:v for k,v in rolls.items() if k!='windows'},'gate':bool(gate),'precision_score':float(score)}


def _post(px,pos):
    return {'base':audit.diag(px,pos,'2026-04-25',None,BASE),'stress40':audit.diag(px,pos,'2026-04-25',None,C40),
            'stress75':audit.diag(px,pos,'2026-04-25',None,C75),'delay2_total':audit.diag(px,pos,'2026-04-25',None,BASE,extra_delay=1)}


def _compare_upgrade(res,winner):
    base=res['P0_T2_baseline']['universes']; win=res[winner]['universes']
    pf_better=sum(win[u]['history']['pf']>base[u]['history']['pf'] for u in base)
    dd_nonworse=sum(win[u]['history']['max_dd_pct']<=base[u]['history']['max_dd_pct'] for u in base)
    winrate_better=sum(win[u]['history'].get('active_day_win_pct',0)>=base[u]['history'].get('active_day_win_pct',0) for u in base)
    return {'pf_better_universes':pf_better,'dd_nonworse_universes':dd_nonworse,'active_winrate_nonworse_universes':winrate_better,
            'precision_upgrade':bool(winner!='P0_T2_baseline' and pf_better>=2 and dd_nonworse>=2 and winrate_better>=2)}


def main():
    now=datetime.now(timezone.utc); cut=pd.Timestamp(now.date(),tz='UTC')
    cur,cur_err=v3.load_prices(); cur=cur.loc[cur.index<cut]
    hs,hmeta=snap.load(); hs=hs.loc[hs.index<cut]
    dp,dq,dmeta=dyn.load(); dp=dp.loc[dp.index<cut]; dq=dq.reindex(dp.index)
    universes={'current_liquid':(cur,None),'historical_snapshot':(hs,None),'dynamic_liquidity_52':(dp,dq)}
    cand={u:_positions(px,qv) for u,(px,qv) in universes.items()}
    names=list(next(iter(cand.values())).keys()); results={}
    for n in names:
        per={u:_eval(px,cand[u][n]) for u,(px,_) in universes.items()}
        results[n]={'universes':per,'gate_count':sum(int(x['gate']) for x in per.values()),
                    'worst_precision_score':min(x['precision_score'] for x in per.values()),
                    'worst_pf':min(x['history']['pf'] for x in per.values()),
                    'worst_dd':max(x['history']['max_dd_pct'] for x in per.values())}
    winner=max(names,key=lambda n:(results[n]['gate_count'],results[n]['worst_precision_score']))
    upgrade=_compare_upgrade(results,winner)
    post={u:_post(px,cand[u][winner]) for u,(px,_) in universes.items()}
    # Coinbase is diagnostic only: it has already been viewed and is never used to select the winner.
    cbpx,cberr,cbmeta=cb.load(); cbpx=cbpx.loc[cbpx.index<cut]; cbpos=_positions(cbpx,None)[winner]
    cbd={'prelock':_eval(cbpx,cbpos),'postlock_reused':_post(cbpx,cbpos),'errors':cberr,'metadata':cbmeta}
    state={'version':'quantbot-v5-precision','updated':now.isoformat(),
      'goal':'Reduce weak/false entries via literature-inspired confirmation layers while preserving the frozen v4 forward rule.',
      'selection_end':'2026-04-24','candidate_count':len(names),'selected':winner,'upgrade_vs_T2':upgrade,
      'selection_universes':['current_liquid','historical_snapshot','dynamic_liquidity_52'],
      'coinbase_status':'DIAGNOSTIC_ONLY_NOT_USED_FOR_SELECTION','candidate_results':results,
      'selected_postlock_reused_diagnostic':post,'selected_coinbase_diagnostic':cbd,
      'research_status':'PRECISION_UPGRADE_FOUND' if results[winner]['gate_count']==3 and upgrade['precision_upgrade'] and cbd['prelock']['gate'] else 'KEEP_T2_BASELINE',
      'live_money_authorized':False,
      'governance':['Do not change frozen T2 forward tracker with these reused data.','A v5 winner must beat T2 on precision across multiple universes, not just return.','Coinbase and 2026-04-25+ are diagnostic only.','No live orders.'],
      'data_notes':{'current_errors':cur_err,'snapshot_metadata':hmeta,'dynamic_metadata':dmeta}}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'winner':winner,'gate_count':results[winner]['gate_count'],'upgrade':upgrade,'status':state['research_status'],
      'winner_summary':{u:{'net':x['history']['net_pct'],'sharpe':x['history']['sharpe'],'pf':x['history']['pf'],'dd':x['history']['max_dd_pct'],'win':x['history'].get('active_day_win_pct',0),'s75':x['stress75']['net_pct']} for u,x in results[winner]['universes'].items()},
      'coinbase':{'gate':cbd['prelock']['gate'],'net':cbd['prelock']['history']['net_pct'],'pf':cbd['prelock']['history']['pf'],'dd':cbd['prelock']['history']['max_dd_pct']}},indent=2))
if __name__=='__main__': main()
