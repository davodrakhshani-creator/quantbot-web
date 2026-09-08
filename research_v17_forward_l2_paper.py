from __future__ import annotations

import json, time, math
from pathlib import Path
from datetime import datetime, timezone
import requests

SYMBOL='BTCUSDT'
BASE='https://fapi.binance.com'
STATE=Path('data/v17_forward_l2_paper_state.json')
SAMPLE_SECONDS=150
POLL_SEC=1.0
DEPTH_LIMIT=20
MAKER_FEE_BPS=2.0
TAKER_FEE_BPS=5.0
ENTRY_IMB=0.22
MICROEDGE_BPS=0.35
MAX_HOLD_SEC=45
PAPER_NOTIONAL_USD=100.0


def utcnow(): return datetime.now(timezone.utc).isoformat()

def get_depth():
    r=requests.get(f'{BASE}/fapi/v1/depth',params={'symbol':SYMBOL,'limit':DEPTH_LIMIT},timeout=10)
    r.raise_for_status(); return r.json()

def get_trades(limit=1000):
    r=requests.get(f'{BASE}/fapi/v1/aggTrades',params={'symbol':SYMBOL,'limit':limit},timeout=10)
    r.raise_for_status(); return r.json()

def load_state():
    if STATE.exists():
        try: return json.loads(STATE.read_text())
        except Exception: pass
    return {'version':'quantbot-v17-forward-l2-maker-paper','symbol':SYMBOL,'live_orders':False,'runs':0,'observations':0,'signals':0,'paper_trades':[], 'open_paper':None,'daily':{},'created_at':utcnow()}

def save(st):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    st['updated_at']=utcnow(); STATE.write_text(json.dumps(st,indent=2),encoding='utf-8')

def metrics(d):
    bids=[(float(p),float(q)) for p,q in d['bids'][:10]]; asks=[(float(p),float(q)) for p,q in d['asks'][:10]]
    bb,bq=bids[0]; ba,aq=asks[0]
    mid=(bb+ba)/2; spread_bps=(ba-bb)/mid*1e4
    bsum=sum(q for _,q in bids); asum=sum(q for _,q in asks)
    imb=(bsum-asum)/(bsum+asum) if bsum+asum else 0.0
    micro=(ba*bq+bb*aq)/(bq+aq) if bq+aq else mid
    microedge=(micro-mid)/mid*1e4
    return {'bb':bb,'ba':ba,'mid':mid,'spread_bps':spread_bps,'imb':imb,'micro':micro,'microedge_bps':microedge}

def last_trade_flow(trades,lookback_ms=5000):
    if not trades: return 0.0
    end=max(int(t['T']) for t in trades); start=end-lookback_ms
    buy=sell=0.0
    for t in trades:
        if int(t['T'])<start: continue
        q=float(t['q'])*float(t['p'])
        if bool(t['m']): sell+=q  # buyer maker -> seller taker
        else: buy+=q
    tot=buy+sell
    return (buy-sell)/tot if tot else 0.0

def maybe_signal(m,flow):
    if m['spread_bps']<=0: return 0
    if m['imb']>=ENTRY_IMB and m['microedge_bps']>=MICROEDGE_BPS and flow>0.05: return 1
    if m['imb']<=-ENTRY_IMB and m['microedge_bps']<=-MICROEDGE_BPS and flow<-0.05: return -1
    return 0

def maker_fill(side, px, trades, since_ms):
    for t in trades:
        if int(t['T'])<since_ms: continue
        p=float(t['p']); buyer_maker=bool(t['m'])
        if side==1 and buyer_maker and p<=px: return True
        if side==-1 and (not buyer_maker) and p>=px: return True
    return False

def close_trade(st, m, reason):
    op=st.get('open_paper')
    if not op: return
    side=op['side']; exit_px=m['bb'] if side==1 else m['ba']  # conservative taker exit
    gross_bps=side*(exit_px/op['entry_px']-1)*1e4
    net_bps=gross_bps-MAKER_FEE_BPS-TAKER_FEE_BPS
    pnl=PAPER_NOTIONAL_USD*net_bps/1e4
    rec={**op,'exit_ts':utcnow(),'exit_px':exit_px,'gross_bps':gross_bps,'net_bps':net_bps,'pnl_usd_100':pnl,'reason':reason}
    st['paper_trades'].append(rec); st['paper_trades']=st['paper_trades'][-2000:]; st['open_paper']=None

def summarize(st):
    trs=st.get('paper_trades',[])
    if not trs:
        st['summary']={'closed':0,'net_usd_100':0.0,'avg_net_bps':None,'win_pct':None,'pf':None}
        return
    nets=[float(t['net_bps']) for t in trs]; wins=[x for x in nets if x>0]; losses=[x for x in nets if x<0]
    pf=sum(wins)/abs(sum(losses)) if losses else (999.0 if wins else None)
    st['summary']={'closed':len(trs),'net_usd_100':sum(float(t['pnl_usd_100']) for t in trs),'avg_net_bps':sum(nets)/len(nets),'win_pct':100*len(wins)/len(nets),'pf':pf,
                   'maker_fee_bps':MAKER_FEE_BPS,'taker_fee_bps':TAKER_FEE_BPS,'notional_usd':PAPER_NOTIONAL_USD}

def main():
    st=load_state(); st['runs']=int(st.get('runs',0))+1
    started=time.time(); candidate=None
    while time.time()-started<SAMPLE_SECONDS:
        try:
            d=get_depth(); m=metrics(d); trades=get_trades(500); flow=last_trade_flow(trades)
            st['observations']=int(st.get('observations',0))+1
            now_ms=int(time.time()*1000)
            op=st.get('open_paper')
            if op:
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(op['entry_ts'])).total_seconds()
                if age>=MAX_HOLD_SEC: close_trade(st,m,'time')
                else:
                    adverse=(op['side']==1 and m['imb']<-0.08) or (op['side']==-1 and m['imb']>0.08)
                    if adverse: close_trade(st,m,'adverse_book_flip')
            if not st.get('open_paper'):
                if candidate is None:
                    s=maybe_signal(m,flow)
                    if s:
                        candidate={'side':s,'px':m['bb'] if s==1 else m['ba'],'since_ms':now_ms,'created':time.time(),'imb':m['imb'],'microedge_bps':m['microedge_bps'],'flow5s':flow,'spread_bps':m['spread_bps']}
                        st['signals']=int(st.get('signals',0))+1
                else:
                    if maker_fill(candidate['side'],candidate['px'],trades,candidate['since_ms']):
                        st['open_paper']={'side':candidate['side'],'entry_px':candidate['px'],'entry_ts':utcnow(),'entry_imb':candidate['imb'],'entry_microedge_bps':candidate['microedge_bps'],'entry_flow5s':candidate['flow5s'],'entry_spread_bps':candidate['spread_bps']}
                        candidate=None
                    elif time.time()-candidate['created']>10:
                        candidate=None
            time.sleep(POLL_SEC)
        except Exception as e:
            st['last_error']=repr(e); time.sleep(2)
    summarize(st); save(st); print(json.dumps(st,indent=2))

if __name__=='__main__': main()
