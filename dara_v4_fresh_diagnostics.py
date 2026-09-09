import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v3_sep1_8 as v3
import dara_v4_sep1_8 as v4

OUT=Path('data/dara_v4_fresh_diagnostics.json')

def age(mp,k,step):
    z=mp.get(k)
    if not z:return 999
    r=z['regime'];n=0
    while n<30:
        q=mp.get(k-n*step)
        if not q or q['regime']!=r:break
        n+=1
    return n

def main():
    src=json.loads(Path('data/dara_v5_exit_sweep.json').read_text())['BASE_033_50_3']['trades']
    raw=[]
    for d in [datetime(2026,8,30,tzinfo=timezone.utc)+timedelta(days=k) for k in range(10)]:raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t']);m1=v3.enrich_m1(raw);idx={x['t']:i for i,x in enumerate(m1)}
    f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)};f60={x['t']:x for x in b.features(b.aggregate(raw,60),60)}
    out=[]
    for t in src:
        e=datetime.fromisoformat(t['entry_time']).astimezone(timezone.utc); sigms=int((e-timedelta(minutes=1)).timestamp()*1000);i=idx.get(sigms)
        if i is None:continue
        x=m1[i];k15=b.last_closed(x['t'],15);k60=b.last_closed(x['t'],60);a15=f15[k15];a60=f60[k60]
        rng=max(x['prev240h']-x['prev240l'],1e-9);pos=(x['c']-x['prev240l'])/rng
        side=t['side']; directional=lambda r: r if side=='LONG' else -r
        rets={n:(x['c']/m1[i-n]['c']-1) for n in [15,30,60,120,240,720]}
        out.append({'day':t['day'],'entry_time':t['entry_time'],'side':side,'winner':t['net_pnl']>0,'net':t['net_pnl'],
         'signal_flow':round(x['flow'],3),'signal_volratio':round(x['v']/x['medv'],3),'signal_body_pct':round(x['body']*100,4),
         'age15':age(f15,k15,15*60000),'age60':age(f60,k60,60*60000),'rsi15':round(a15['rsi'],2),'rsi60':round(a60['rsi'],2),
         'range240_pos':round(pos,4),'dir_range_pos':round(pos if side=='LONG' else 1-pos,4),
         'vwap_dist_dir_pct':round(directional(x['c']/x['vwap240']-1)*100,4),
         'room_dir_pct':round(((x['prev240h']-x['c'])/x['c'] if side=='LONG' else (x['c']-x['prev240l'])/x['c'])*100,4),
         'dir_ret15_pct':round(directional(rets[15])*100,4),'dir_ret30_pct':round(directional(rets[30])*100,4),'dir_ret60_pct':round(directional(rets[60])*100,4),'dir_ret120_pct':round(directional(rets[120])*100,4),'dir_ret240_pct':round(directional(rets[240])*100,4),'dir_ret720_pct':round(directional(rets[720])*100,4),
         'dir_dist15ema_atr':round(directional((a15['c']-a15['ema21'])/max(a15['atr'],1e-9)),3),'dir_dist60ema_atr':round(directional((a60['c']-a60['ema21'])/max(a60['atr'],1e-9)),3),
         'atr15_pct':round(a15['atrp']*100,4),'atr60_pct':round(a60['atrp']*100,4)})
    OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
