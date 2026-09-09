import math


def ema(values, period):
    if not values:
        return []
    a = 2.0/(period+1.0)
    out=[values[0]]
    for x in values[1:]:
        out.append(a*x + (1-a)*out[-1])
    return out


def rsi(values, period=14):
    n=len(values); out=[50.0]*n
    if n<=period: return out
    gains=[]; losses=[]
    for i in range(1,n):
        d=values[i]-values[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains[:period])/period; al=sum(losses[:period])/period
    out[period]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(period+1,n):
        g=gains[i-1]; l=losses[i-1]
        ag=(ag*(period-1)+g)/period; al=(al*(period-1)+l)/period
        out[i]=100 if al==0 else 100-100/(1+ag/al)
    return out


def sma(values, period):
    out=[]; s=0.0
    for i,x in enumerate(values):
        s+=x
        if i>=period: s-=values[i-period]
        out.append(s/min(i+1,period))
    return out


def std(values, period):
    out=[]
    for i in range(len(values)):
        a=values[max(0,i-period+1):i+1]
        m=sum(a)/len(a)
        out.append(math.sqrt(sum((x-m)**2 for x in a)/len(a)))
    return out


def macd(values, fast=12, slow=26, signal=9):
    ef=ema(values,fast); es=ema(values,slow)
    line=[a-b for a,b in zip(ef,es)]
    sig=ema(line,signal)
    hist=[a-b for a,b in zip(line,sig)]
    return line,sig,hist


def stochastic(rows, period=14):
    out=[]
    for i,z in enumerate(rows):
        a=rows[max(0,i-period+1):i+1]
        hi=max(x['h'] for x in a); lo=min(x['l'] for x in a)
        out.append(50.0 if hi==lo else 100*(z['c']-lo)/(hi-lo))
    return out


def _body(z): return abs(z['c']-z['o'])
def _range(z): return max(z['h']-z['l'],1e-9)
def _bull(z): return z['c']>z['o']
def _bear(z): return z['c']<z['o']


def enrich_indicators(rows):
    c=[z['c'] for z in rows]
    e9=ema(c,9); e21=ema(c,21); rs=rsi(c,14); ml,ms,mh=macd(c)
    mid=sma(c,20); sd=std(c,20); st=stochastic(rows,14)
    for i,z in enumerate(rows):
        z['_ema9']=e9[i]; z['_ema21']=e21[i]; z['_rsi']=rs[i]
        z['_macd']=ml[i]; z['_macd_sig']=ms[i]; z['_macd_hist']=mh[i]
        z['_bb_mid']=mid[i]; z['_bb_up']=mid[i]+2*sd[i]; z['_bb_dn']=mid[i]-2*sd[i]
        z['_bb_width']=0 if mid[i]==0 else (4*sd[i])/mid[i]
        z['_stoch']=st[i]
    return rows


def detect(rows, i):
    if i<30: return []
    z=rows[i]; p=rows[i-1]; p2=rows[i-2]
    out=[]
    def add(name,family,side,q,*reasons):
        out.append({'pattern':name,'family':family,'side':side,'quality':round(float(q),3),'reasons':list(reasons)})

    # Candlestick reversals
    if _bear(p) and _bull(z) and z['o']<=p['c'] and z['c']>=p['o'] and _body(z)>=.8*_body(p):
        add('BULL_ENGULF','candlestick_reversal','LONG',1.0,'bullish body engulf','confirmation still required')
    if _bull(p) and _bear(z) and z['o']>=p['c'] and z['c']<=p['o'] and _body(z)>=.8*_body(p):
        add('BEAR_ENGULF','candlestick_reversal','SHORT',1.0,'bearish body engulf','confirmation still required')
    rng=_range(z); body=_body(z); lowwick=min(z['o'],z['c'])-z['l']; upwick=z['h']-max(z['o'],z['c'])
    if lowwick>=2.2*max(body,1e-9) and upwick<=.45*rng and z['c']>=z['l']+.58*rng:
        add('HAMMER_RECLAIM','wick_reversal','LONG',.9,'long lower wick','close near upper half')
    if upwick>=2.2*max(body,1e-9) and lowwick<=.45*rng and z['c']<=z['l']+.42*rng:
        add('SHOOTING_STAR_REJECT','wick_reversal','SHORT',.9,'long upper wick','close near lower half')

    # Morning/evening star approximations
    if _bear(p2) and _body(p2)>.55*_range(p2) and _body(p)<.45*_range(p) and _bull(z) and z['c']>=(p2['o']+p2['c'])/2:
        add('MORNING_STAR','three_candle_reversal','LONG',1.0,'impulse-pause-recovery')
    if _bull(p2) and _body(p2)>.55*_range(p2) and _body(p)<.45*_range(p) and _bear(z) and z['c']<=(p2['o']+p2['c'])/2:
        add('EVENING_STAR','three_candle_reversal','SHORT',1.0,'impulse-pause-rejection')

    # EMA pullback continuation
    if z['_ema9']>z['_ema21'] and p['l']<=p['_ema9']*1.001 and z['c']>z['_ema9'] and _bull(z) and z['_ema9']>p['_ema9']:
        add('EMA_PULLBACK_LONG','trend_pullback','LONG',.85,'ema9>ema21','pullback then reclaim')
    if z['_ema9']<z['_ema21'] and p['h']>=p['_ema9']*.999 and z['c']<z['_ema9'] and _bear(z) and z['_ema9']<p['_ema9']:
        add('EMA_PULLBACK_SHORT','trend_pullback','SHORT',.85,'ema9<ema21','rally then reject')

    # VWAP reclaim/reject; raw cross is deliberately not enough
    if 'vwap15' in z and 'vwap15' in p:
        if p['c']<p['vwap15'] and z['c']>z['vwap15'] and _bull(z) and z['l']<=z['vwap15']*1.001:
            add('VWAP_RECLAIM_LONG','intraday_value','LONG',.75,'reclaim VWAP','needs hold/follow-through')
        if p['c']>p['vwap15'] and z['c']<z['vwap15'] and _bear(z) and z['h']>=z['vwap15']*.999:
            add('VWAP_REJECT_SHORT','intraday_value','SHORT',.75,'loss/reject VWAP','needs hold/follow-through')

    # Bollinger squeeze then expansion
    recent=[x['_bb_width'] for x in rows[i-20:i]]; med=sorted(recent)[len(recent)//2]
    squeezed=p['_bb_width']<.75*med if med>0 else False
    if squeezed and z['_bb_width']>p['_bb_width']*1.15 and z['c']>z['_bb_up'] and _bull(z):
        add('BB_SQUEEZE_EXPAND_LONG','volatility_expansion','LONG',1.05,'bandwidth expansion','close above upper band')
    if squeezed and z['_bb_width']>p['_bb_width']*1.15 and z['c']<z['_bb_dn'] and _bear(z):
        add('BB_SQUEEZE_EXPAND_SHORT','volatility_expansion','SHORT',1.05,'bandwidth expansion','close below lower band')

    # RSI divergence over two local windows
    a=rows[i-12:i-6]; b=rows[i-5:i+1]
    ia=min(range(len(a)),key=lambda k:a[k]['l']); ib=min(range(len(b)),key=lambda k:b[k]['l'])
    if b[ib]['l']<a[ia]['l'] and b[ib]['_rsi']>a[ia]['_rsi']+3 and z['c']>p['h']:
        add('RSI_BULL_DIVERGENCE','momentum_divergence','LONG',.9,'lower price low','higher RSI low','structure confirmation')
    ia=max(range(len(a)),key=lambda k:a[k]['h']); ib=max(range(len(b)),key=lambda k:b[k]['h'])
    if b[ib]['h']>a[ia]['h'] and b[ib]['_rsi']<a[ia]['_rsi']-3 and z['c']<p['l']:
        add('RSI_BEAR_DIVERGENCE','momentum_divergence','SHORT',.9,'higher price high','lower RSI high','structure confirmation')

    # MACD re-acceleration / cross
    if p['_macd']<=p['_macd_sig'] and z['_macd']>z['_macd_sig'] and z['_macd_hist']>p['_macd_hist']:
        add('MACD_REACCEL_LONG','momentum_continuation','LONG',.7,'bullish MACD cross')
    if p['_macd']>=p['_macd_sig'] and z['_macd']<z['_macd_sig'] and z['_macd_hist']<p['_macd_hist']:
        add('MACD_REACCEL_SHORT','momentum_continuation','SHORT',.7,'bearish MACD cross')

    # Stochastic range reversal
    if p['_stoch']<20 and z['_stoch']>=20 and z['c']>p['c']:
        add('STOCH_RANGE_REV_LONG','range_reversal','LONG',.55,'stochastic exits oversold')
    if p['_stoch']>80 and z['_stoch']<=80 and z['c']<p['c']:
        add('STOCH_RANGE_REV_SHORT','range_reversal','SHORT',.55,'stochastic exits overbought')

    # Flag / displacement continuation. Designed to echo DARA's best surviving family.
    imp_up=rows[i-6]['c']>0 and rows[i-3]['c']/rows[i-6]['c']-1>=.0022
    imp_dn=rows[i-3]['c']/rows[i-6]['c']-1<=-.0022
    pause=abs(p['c']/rows[i-3]['c']-1)<=.0018
    if imp_up and pause and z['c']>max(x['h'] for x in rows[i-3:i]) and _bull(z):
        add('BULL_FLAG_BREAK','continuation','LONG',1.15,'up displacement','controlled pause','renewed breakout')
        add('DISPLACEMENT_CONT_LONG','price_action_core','LONG',1.25,'displacement + pause + renewal')
    if imp_dn and pause and z['c']<min(x['l'] for x in rows[i-3:i]) and _bear(z):
        add('BEAR_FLAG_BREAK','continuation','SHORT',1.15,'down displacement','controlled pause','renewed breakdown')
        add('DISPLACEMENT_CONT_SHORT','price_action_core','SHORT',1.25,'displacement + pause + renewal')

    return sorted(out,key=lambda x:x['quality'],reverse=True)


def side_score(matches, side):
    xs=[x for x in matches if x['side']==side]
    if not xs: return 0.0,[]
    # Diverse families matter more than duplicate signals from one family.
    fam={}
    for x in xs: fam[x['family']]=max(fam.get(x['family'],0),x['quality'])
    score=sum(fam.values())
    return round(score,3),xs


def best_signal(rows,i):
    m=detect(rows,i)
    ls,lm=side_score(m,'LONG'); ss,sm=side_score(m,'SHORT')
    if ls==ss==0: return None
    side='LONG' if ls>ss else 'SHORT'; score=max(ls,ss)
    return {'side':side,'pattern_score':score,'matches':lm if side=='LONG' else sm}
