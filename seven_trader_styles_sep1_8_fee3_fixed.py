import json
from datetime import timedelta, timezone
from pathlib import Path
import seven_trader_styles_sep1_8_fee3 as m

OUT=Path('data/seven_trader_styles_sep1_8_fee3.json')


def neutralize_pre_day(bars, ds, mode):
    out=[]
    for x in bars:
        y=dict(x)
        if y['t']<ds:
            if mode=='flow':
                y['flow']=1.0
                y['v']=0.0
            elif mode=='cis':
                y['v']=0.0
            elif mode=='hougaard':
                y['o']=y['c']
        out.append(y)
    return out


def overall_stats(trades, final_eq, daily, name):
    gp=sum(max(t['net_pnl'],0) for t in trades)
    gl=-sum(min(t['net_pnl'],0) for t in trades)
    w=sum(t['net_pnl']>0 for t in trades)
    return {
        'trades':len(trades),'wins':w,'losses':len(trades)-w,
        'win_rate':round(100*w/len(trades),1) if trades else None,
        'net_pnl':round(final_eq-100,6),'final_equity':round(final_eq,6),
        'pf':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else None),
        'positive_days':sum(r['traders'][name]['status']=='POSITIVE' for r in daily),
        'negative_days':sum(r['traders'][name]['status']=='NEGATIVE' for r in daily),
        'flat_days':sum(r['traders'][name]['status']=='FLAT' for r in daily),
        'gross_pnl':round(sum(t['gross_pnl'] for t in trades),6),
        'costs':round(sum(t['cost'] for t in trades),6)
    }


def main():
    b5s=m.build_5s()
    b5m=m.base.prepare(m.base.aggregate(b5s,300))
    aug=m.base.fetch_aug_daily(); sma25=sum(aug[-25:])/25
    traders=['Paul Rotter','Testa','BNF','cis','Tom Hougaard','Jun FX','Hansan']
    eq={n:100.0 for n in traders}; alltr={n:[] for n in traders}; daily=[]

    for d in range(8):
        day=m.START+timedelta(days=d)
        ds=int(day.astimezone(timezone.utc).timestamp()*1000)
        de=int((day+timedelta(days=1)).astimezone(timezone.utc).timestamp()*1000)
        raw5s=[x for x in b5s if ds-3600_000<=x['t']<de]
        raw5m=[x for x in b5m if ds-6*3600_000<=x['t']<de]
        day_actual_5m=[x for x in b5m if ds<=x['t']<de]
        row={'day':day.strftime('%Y-%m-%d'),'traders':{}}

        jobs={
            'Paul Rotter':(m.run_rotter,neutralize_pre_day(raw5s,ds,'flow')),
            'Testa':(m.run_testa,neutralize_pre_day(raw5s,ds,'flow')),
            'Jun FX':(m.run_jun,neutralize_pre_day(raw5s,ds,'flow')),
            'Hansan':(m.run_hansan,neutralize_pre_day(raw5s,ds,'flow')),
            'cis':(m.run_cis,neutralize_pre_day(raw5m,ds,'cis')),
            'Tom Hougaard':(m.run_hougaard,neutralize_pre_day(raw5m,ds,'hougaard')),
        }
        for n,(fn,bars) in jobs.items():
            start_eq=eq[n]
            neweq,tr=fn(bars,start_eq)
            bad=[t for t in tr if int(__import__('datetime').datetime.fromisoformat(t['entry_time']).astimezone(timezone.utc).timestamp()*1000)<ds]
            if bad:
                raise RuntimeError(f'warmup trade leak for {n} on {day.date()}: {bad[0]}')
            eq[n]=neweq; alltr[n].extend(tr); row['traders'][n]=m.summarize(tr,start_eq,neweq)

        start_eq=eq['BNF']
        neweq,tr=m.run_bnf(day_actual_5m,start_eq,sma25)
        eq['BNF']=neweq; alltr['BNF'].extend(tr); row['traders']['BNF']=m.summarize(tr,start_eq,neweq)
        daily.append(row)

    overall={n:overall_stats(alltr[n],eq[n],daily,n) for n in traders}
    payload={
        'version':'Seven-Trader-Styles-Sep1-8-3xFeeGate-v1-WARMUP-FIXED',
        'period_tehran':[m.START.isoformat(),m.END.isoformat()],
        'symbol':'BTCUSDT USD-M perpetual',
        'change_from_prior_test':'Entry logic and trader-specific stops unchanged. Profitable exits below +0.33% gross price move are blocked; hard stop/negative invalidation remains allowed. Day-end force-close is the only positive exit below target.',
        'warmup_fix':'Pre-day bars remain available for indicators/lookback but are neutralized so they cannot generate entries. No strategy threshold changed.',
        'target_gross_pct':m.TARGET_PCT*100,'roundtrip_cost_pct':m.base.ROUNDTRIP_COST*100,
        'risk_budget_per_trade_pct':m.base.RISK*100,'max_leverage':m.base.MAX_LEV,
        'data':'Binance official USD-M aggTrades aggregated to 5-second bars; 5m derived. No historical full L2 order book claimed.',
        'daily':daily,'overall':overall,'trades':alltr
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(overall,indent=2)); print(json.dumps(daily,indent=2))

if __name__=='__main__': main()
