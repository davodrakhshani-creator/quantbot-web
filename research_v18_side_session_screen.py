from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_v15_microstructure_edge_screen as v15

STATE=Path('data/v18_side_session_screen_state.json')
RULE='E1_break_accept';H=6;COST_RT=11.0

def events(x,t):
    side=v15.rule_masks(x,t)[RULE];entry=x.open.shift(-1);exitp=x.close.shift(-H)
    valid=(side!=0)&entry.notna()&exitp.notna()
    d=pd.DataFrame(index=x.index[valid]);d['side']=side[valid].astype(int);d['gross']=d.side*(exitp[valid]/entry[valid]-1)*10000
    d['hour']=d.index.hour;d['session']=pd.cut(d.hour,[-1,7,15,23],labels=['UTC00_08','UTC08_16','UTC16_24'])
    return d

def metric(d):
    if d.empty:return {'events':0,'gross_mean_bps':0,'net11_mean_bps':0,'win11_pct':0}
    n=d.gross-COST_RT
    return {'events':int(len(d)),'gross_mean_bps':float(d.gross.mean()),'net11_mean_bps':float(n.mean()),'median_net11_bps':float(n.median()),'win11_pct':float((n>0).mean()*100)}

def buckets(d):
    out={'ALL':metric(d),'LONG':metric(d[d.side>0]),'SHORT':metric(d[d.side<0])}
    for s in ['UTC00_08','UTC08_16','UTC16_24']:
        out[s]=metric(d[d.session==s])
        out[f'LONG_{s}']=metric(d[(d.side>0)&(d.session==s)])
        out[f'SHORT_{s}']=metric(d[(d.side<0)&(d.session==s)])
    return out

def main():
    b=v15.load_5m();fit=b[(b.index>=v15.START)&(b.index<=v15.FIT_END)];val=b[(b.index>=v15.VAL_START)&(b.index<=v15.VAL_END)];hold=b[b.index>=v15.HOLDOUT_START]
    t=v15.thresholds(fit);fd=events(fit,t);fb=buckets(fd)
    elig=[(m['net11_mean_bps'],k) for k,m in fb.items() if m['events']>=15 and m['net11_mean_bps']>0 and m['gross_mean_bps']>11]
    elig.sort(reverse=True);selected=elig[0][1] if elig else None
    valres=None;holdres=None;gate=False
    def take(d,k):
        if k=='ALL':return d
        if k=='LONG':return d[d.side>0]
        if k=='SHORT':return d[d.side<0]
        side=None;session=k
        if k.startswith('LONG_'):side=1;session=k[5:]
        elif k.startswith('SHORT_'):side=-1;session=k[6:]
        z=d[d.session==session]
        if side is not None:z=z[z.side==side]
        return z
    if selected:
        vd=events(val,t);valres=metric(take(vd,selected));vg=(valres['events']>=8 and valres['net11_mean_bps']>0)
        if vg:
            hd=events(hold,t);holdres=metric(take(hd,selected));gate=(holdres['events']>=8 and holdres['net11_mean_bps']>0)
    state={'version':'quantbot-v18-side-session','purpose':'ASYMMETRY_SCREEN_NO_LIVE','signal':f'{RULE}_h{H}','roundtrip_cost_bps':COST_RT,
           'fit_buckets':fb,'selected_fit_only':selected,'validation':valres,'holdout':holdres,'holdout_gate_pass':bool(gate),'live_orders':False,
           'warning':'Exploratory side/session screen; any survivor still requires larger forward L2 paper sample.',
           'interpretation':'V18_ASYMMETRY_SURVIVES' if gate else ('V18_VALIDATION_OPENED' if selected else 'NO_V18_ASYMMETRIC_EDGE')}
    STATE.parent.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps(state,indent=2),encoding='utf-8');print(json.dumps(state,indent=2))
if __name__=='__main__':main()
