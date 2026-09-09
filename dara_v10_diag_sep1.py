import json
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
import dara_v1_sep1 as b
import dara_v10_microstructure_sep1 as v

OUT=Path('data/dara_v10_diag_sep1.json')

def main():
    raw=[]
    for d in [datetime(2026,8,31,tzinfo=timezone.utc)+timedelta(days=k) for k in range(2)]: raw+=b.get_daily(d)
    raw=sorted({x['t']:x for x in raw}.values(),key=lambda x:x['t'])
    m=v.enrich(raw); f5={x['t']:x for x in b.features(b.aggregate(raw,5),5)}; f15={x['t']:x for x in b.features(b.aggregate(raw,15),15)}
    c=Counter(); samples=[]
    for i,x in enumerate(m[:-2]):
        if x['t']<v.START_MS or x['t']>=v.END_MS or i<20: continue
        a5=f5.get(b.last_closed(x['t'],5)); a15=f15.get(b.last_closed(x['t'],15))
        if not a5 or not a15: continue
        for side in ('LONG','SHORT'):
            aligned=(a15['regime']=='UP' and a5['regime']=='UP') if side=='LONG' else (a15['regime']=='DOWN' and a5['regime']=='DOWN')
            if not aligned: continue
            c[f'value_{side}_aligned']+=1
            prev=m[i-4:i]; fs=v.flow_state(i,m,side)
            if side=='LONG':
                value=(a5['ema21']+x['vwap15'])/2; touched=min(z['l'] for z in prev)<=value*1.0012; held=min(z['c'] for z in prev)>=a15['ema21']*0.9975; trigger=x['c']>max(z['h'] for z in m[i-2:i])*1.00005 and x['c']>x['o']; extension=(x['c']-value)/x['c']; room=(x['prev15h']-x['c'])/x['c']; dgood=x['delta']>=0.12
            else:
                value=(a5['ema21']+x['vwap15'])/2; touched=max(z['h'] for z in prev)>=value*0.9988; held=max(z['c'] for z in prev)<=a15['ema21']*1.0025; trigger=x['c']<min(z['l'] for z in m[i-2:i])*0.99995 and x['c']<x['o']; extension=(value-x['c'])/x['c']; room=(x['c']-x['prev15l'])/x['c']; dgood=x['delta']<=-0.12
            minute=datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(v.TEHRAN).minute; clock=(minute%5 in (0,1))
            cond=[touched,held,trigger,dgood,x['volr']>=1.10,fs['avg_delta']>=0.08,fs['persistence']>=2,fs['efficiency']>=0.30,extension<=0.0035,room>=0.0038,clock]
            score=sum(cond)
            if touched:c[f'value_{side}_touched']+=1
            if trigger:c[f'value_{side}_trigger']+=1
            if touched and trigger:c[f'value_{side}_touch_trigger']+=1
            if room>=0.0038:c[f'value_{side}_room']+=1
            if score>=8:c[f'value_{side}_score8']+=1
            if score>=9:c[f'value_{side}_score9']+=1
            if touched and trigger and score>=8 and len(samples)<20:
                samples.append({'time':datetime.fromtimestamp(x['t']/1000,timezone.utc).astimezone(v.TEHRAN).isoformat(),'type':'VALUE','side':side,'score':score,'room_pct':round(room*100,3),'delta':round(x['delta'],3),'volr':round(x['volr'],2),'avg_delta':round(fs['avg_delta'],3),'persistence':fs['persistence'],'efficiency':round(fs['efficiency'],3),'extension_pct':round(extension*100,3)})
        # sweep components
        y=m[i+1]; rng=max(x['h']-x['l'],1e-9); body=abs(x['c']-x['o']); upper=x['h']-max(x['o'],x['c']); lower=min(x['o'],x['c'])-x['l']
        if a15['regime']!='DOWN':
            pen=(x['prev15l']-x['l'])/x['prev15l'] if x['l']<x['prev15l'] else 0; event=pen>=0.0006 and x['c']>x['prev15l'] and lower>=max(body,0.35*rng) and x['delta']<=-0.10 and x['volr']>=1.30; hold=y['l']>=x['l']*0.9998 and y['c']>x['prev15l'] and y['delta']>=0.08 and y['c']>=y['o'] and y['volr']>=0.80; room=(x['prev15h']-y['c'])/y['c']
            if event:c['sweep_LONG_event']+=1
            if event and hold:c['sweep_LONG_hold']+=1
            if event and hold and room>=0.0038:c['sweep_LONG_room']+=1
        if a15['regime']!='UP':
            pen=(x['h']-x['prev15h'])/x['prev15h'] if x['h']>x['prev15h'] else 0; event=pen>=0.0006 and x['c']<x['prev15h'] and upper>=max(body,0.35*rng) and x['delta']>=0.10 and x['volr']>=1.30; hold=y['h']<=x['h']*1.0002 and y['c']<x['prev15h'] and y['delta']<=-0.08 and y['c']<=y['o'] and y['volr']>=0.80; room=(y['c']-x['prev15l'])/y['c']
            if event:c['sweep_SHORT_event']+=1
            if event and hold:c['sweep_SHORT_hold']+=1
            if event and hold and room>=0.0038:c['sweep_SHORT_room']+=1
    payload={'counts':dict(c),'samples':samples};OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(payload,indent=2));print(json.dumps(payload,indent=2))
if __name__=='__main__':main()
