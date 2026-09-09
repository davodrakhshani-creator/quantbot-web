import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v1_2_sep1 as v12

TEHRAN=b.TEHRAN
TEST_START=datetime(2026,9,3,0,0,tzinfo=TEHRAN)
TEST_END=datetime(2026,9,9,0,0,tzinfo=TEHRAN)
OUT=Path('data/dara_v1_2_sep3_8.json')
MIN_STOP=0.0014


def summarize_block(trades,start_eq,end_eq,maxdd,noise):
    gp=sum(max(t['net_pnl'],0) for t in trades)
    gl=-sum(min(t['net_pnl'],0) for t in trades)
    wins=sum(t['net_pnl']>0 for t in trades)
    return {
        'start_equity':round(start_eq,6),'final_equity':round(end_eq,6),
        'net_pnl':round(end_eq-start_eq,6),'return_pct':round((end_eq/start_eq-1)*100,3) if start_eq else None,
        'trades':len(trades),'wins':wins,'losses':len(trades)-wins,
        'win_rate':round(100*wins/len(trades),1) if trades else None,
        'gross_pnl':round(sum(t['gross_pnl'] for t in trades),6),
        'modeled_costs':round(sum(t['cost'] for t in trades),6),
        'pf_net':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),
        'max_dd_pct':round(maxdd*100,3),'add_events':sum(bool(t['added']) for t in trades),
        'noise_guard_rejections':noise
    }


def main():
    raw=[]
    d=datetime(2026,8,30,tzinfo=timezone.utc)
    while d.date()<=datetime(2026,9,8,tzinfo=timezone.utc).date():
        raw+=b.get_daily(d)
        d+=timedelta(days=1)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m1=b.minute_features(raw)
    f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)}
    f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)}

    equity=100.0
    global_peak=equity
    global_dd=0.0
    all_trades=[]
    daily=[]
    total_noise=0

    for day_offset in range(6):
        day_start=TEST_START+timedelta(days=day_offset)
        day_end=day_start+timedelta(days=1)
        start_ms=int(day_start.astimezone(timezone.utc).timestamp()*1000)
        end_ms=int(day_end.astimezone(timezone.utc).timestamp()*1000)
        day_start_eq=equity
        day_peak=equity
        day_dd=0.0
        trades=[]
        last_exit=-10**9
        consec=0
        pause_reg=None
        rejected_noise=0

        i=0
        while i<len(m1)-1 and m1[i]['t']<start_ms:
            i+=1
        while i<len(m1)-1:
            x=m1[i]
            if x['t']>=end_ms: break
            if len(trades)>=b.MAX_TRADES or equity<=day_start_eq*(1+b.DAILY_STOP): break
            if i-last_exit<b.COOLDOWN:
                i+=1; continue
            a15=f15.get(b.last_closed(x['t'],15))
            if pause_reg is not None:
                if a15 and a15['regime']!=pause_reg:
                    pause_reg=None; consec=0
                else:
                    i+=1; continue
            sig=v12.event_v12(i,m1,f5,f15)
            if not sig:
                i+=1; continue
            setup,side,raw_stop=sig
            entry_i=i+1
            if entry_i>=len(m1) or m1[entry_i]['t']>=end_ms: break
            entry=m1[entry_i]['o']
            sp=(entry-raw_stop)/entry if side=='LONG' else (raw_stop-entry)/entry
            if sp<MIN_STOP:
                rejected_noise+=1; total_noise+=1; i+=1; continue
            sim=b.simulate(m1,entry_i,setup,side,raw_stop,equity)
            if sim is None:
                i+=1; continue
            j,px,reason,gross,cost,net,added,notional=sim
            # Do not let a trade spill past the requested Tehran day. If base simulator did,
            # force close at final minute of this day using mark-to-market and full roundtrip cost.
            if m1[j]['t']>=end_ms:
                k=entry_i
                while k+1<len(m1) and m1[k+1]['t']<end_ms: k+=1
                px=m1[k]['c']; j=k
                r=(px/entry-1) if side=='LONG' else (entry/px-1)
                gross=notional*r; cost=notional*b.COST; net=gross-cost; reason='DAY_END'
            before=equity
            equity+=net
            day_peak=max(day_peak,equity)
            day_dd=max(day_dd,(day_peak-equity)/day_peak if day_peak else 0)
            global_peak=max(global_peak,equity)
            global_dd=max(global_dd,(global_peak-equity)/global_peak if global_peak else 0)
            et=datetime.fromtimestamp(m1[entry_i]['t']/1000,timezone.utc).astimezone(TEHRAN)
            xt=datetime.fromtimestamp(m1[j]['t']/1000,timezone.utc).astimezone(TEHRAN)
            stop_pct=(m1[entry_i]['o']-raw_stop)/m1[entry_i]['o'] if side=='LONG' else (raw_stop-m1[entry_i]['o'])/m1[entry_i]['o']
            tr={'day':day_start.strftime('%Y-%m-%d'),'setup':setup,'side':side,'entry_time':et.isoformat(),'exit_time':xt.isoformat(),
                'entry':round(m1[entry_i]['o'],2),'exit':round(px,2),'structural_stop_pct':round(stop_pct*100,3),'reason':reason,
                'added':added,'notional':round(notional,6),'gross_pnl':round(gross,6),'cost':round(cost,6),'net_pnl':round(net,6),
                'equity_before':round(before,6),'equity_after':round(equity,6)}
            trades.append(tr); all_trades.append(tr)
            if net>0:
                consec=0; pause_reg=None
            else:
                consec+=1
                if consec>=2: pause_reg=a15['regime'] if a15 else 'UNKNOWN'
            last_exit=j; i=j+1

        daily.append({'day':day_start.strftime('%Y-%m-%d'),**summarize_block(trades,day_start_eq,equity,day_dd,rejected_noise)})

    gp=sum(max(t['net_pnl'],0) for t in all_trades)
    gl=-sum(min(t['net_pnl'],0) for t in all_trades)
    wins=sum(t['net_pnl']>0 for t in all_trades)
    overall={
        'start_equity':100.0,'final_equity':round(equity,6),'net_pnl':round(equity-100,6),'return_pct':round((equity/100-1)*100,3),
        'trades':len(all_trades),'wins':wins,'losses':len(all_trades)-wins,
        'win_rate':round(100*wins/len(all_trades),1) if all_trades else None,
        'gross_pnl':round(sum(t['gross_pnl'] for t in all_trades),6),'modeled_costs':round(sum(t['cost'] for t in all_trades),6),
        'pf_net':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),'max_dd_pct':round(global_dd*100,3),
        'add_events':sum(bool(t['added']) for t in all_trades),'noise_guard_rejections':total_noise,
        'positive_days':sum(d['net_pnl']>0 for d in daily),'negative_days':sum(d['net_pnl']<0 for d in daily),'flat_days':sum(d['net_pnl']==0 for d in daily)
    }
    payload={
        'version':'DARA-v1.2-FROZEN-Sep3-8-Validation','period_tehran':[TEST_START.isoformat(),TEST_END.isoformat()],
        'symbol':'BTCUSDT USD-M perpetual',
        'rules_note':'Exact DARA v1.2 logic retained: 0.14% Noise/Cost Guard; BreakAccept native timing; Sweep one-minute follow-through; TP1 0.33% with 50/50 runner; one add after +1R; risk 0.25%; max exposure 3x; max 5 trades/day; daily stop -1%.',
        'methodology_notes':['No DARA threshold changed after Sep2 result.','Account is continuous from $100 on Sep3 through Sep8; daily trade/risk controls reset each Tehran day.','Historical full L2 unavailable; Binance 1m taker-buy volume is the order-flow proxy.','Sep3-8 were seen by older strategies but were not used to tune DARA v1.2.'],
        'daily':daily,'overall':overall,'trades':all_trades
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(overall,indent=2))
    print(json.dumps(daily,indent=2))

if __name__=='__main__': main()
