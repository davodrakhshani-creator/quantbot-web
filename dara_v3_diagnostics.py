import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v3_sep1_8 as v3

OUT=Path('data/dara_v3_diagnostics.json')

def regime_age(arr,idx):
    r=arr[idx]['regime']; n=0
    for j in range(idx,-1,-1):
        if arr[j]['regime']!=r:break
        n+=1
    return n

def main():
    result=json.loads(Path('data/dara_v3_sep1_8.json').read_text())
    raw=[]
    for d in [datetime(2026,8,30,tzinfo=timezone.utc)+timedelta(days=k) for k in range(10)]: raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m1=v3.enrich_m1(raw); a15=b.features(b.aggregate(raw,15),15); a60=b.features(b.aggregate(raw,60),60)
    idx1={x['t']:i for i,x in enumerate(m1)}; idx15={x['t']:i for i,x in enumerate(a15)}; idx60={x['t']:i for i,x in enumerate(a60)}
    out=[]
    for t in result['trades']:
        dt=datetime.fromisoformat(t['entry_time']).astimezone(timezone.utc); ms=int(dt.timestamp()*1000); # entry is next minute open
        i=idx1.get(ms)
        if i is None:continue
        x=m1[i]; k15=b.last_closed(x['t']-60000,15); k60=b.last_closed(x['t']-60000,60)
        j15=idx15.get(k15); j60=idx60.get(k60)
        f15=a15[j15]; f60=a60[j60]
        rng=max(x['prev240h']-x['prev240l'],1e-9); pos=(x['c']-x['prev240l'])/rng
        ret60=x['c']/m1[max(0,i-60)]['c']-1; ret240=x['c']/m1[max(0,i-240)]['c']-1; ret720=x['c']/m1[max(0,i-720)]['c']-1
        out.append({
            'day':t['day'],'entry_time':t['entry_time'],'side':t['side'],'net_pnl':t['net_pnl'],'winner':t['net_pnl']>0,
            'price':x['c'],'vwap240_dist_pct':round((x['c']/x['vwap240']-1)*100,4),'range240_pos':round(pos,4),
            'room_to_4h_low_pct':round((x['c']-x['prev240l'])/x['c']*100,4),'room_to_4h_high_pct':round((x['prev240h']-x['c'])/x['c']*100,4),
            'ret60_pct':round(ret60*100,4),'ret240_pct':round(ret240*100,4),'ret720_pct':round(ret720*100,4),
            'rsi15':round(f15['rsi'],2),'rsi60':round(f60['rsi'],2),'atrp15_pct':round(f15['atrp']*100,4),'atrp60_pct':round(f60['atrp']*100,4),
            'dist15ema_atr':round((f15['c']-f15['ema21'])/max(f15['atr'],1e-9),3),'dist60ema_atr':round((f60['c']-f60['ema21'])/max(f60['atr'],1e-9),3),
            'regime15':f15['regime'],'regime60':f60['regime'],'regime_age15_bars':regime_age(a15,j15),'regime_age60_bars':regime_age(a60,j60),
            'flow1m':round(x['flow'],3),'volratio1m':round(x['v']/x['medv'],3)
        })
    OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
