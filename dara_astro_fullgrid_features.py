"""DARA full 11-factor astronomical feature grid.

Research features only. Geocentric tropical ecliptic positions are computed with
Swiss Ephemeris/Moshier. Earth is the reference frame; the 11th factor is the
true lunar node axis (North Node; South Node is implicit at +180 degrees).
No astro feature may open a production trade by itself.
"""
from __future__ import annotations
from datetime import datetime, timezone
from itertools import combinations
import swisseph as swe

BODIES={
 'Sun':swe.SUN,'Moon':swe.MOON,'Mercury':swe.MERCURY,'Venus':swe.VENUS,
 'Mars':swe.MARS,'Jupiter':swe.JUPITER,'Saturn':swe.SATURN,'Uranus':swe.URANUS,
 'Neptune':swe.NEPTUNE,'Pluto':swe.PLUTO,'TrueNode':swe.TRUE_NODE,
}
RETRO_BODIES=('Mercury','Venus','Mars','Jupiter','Saturn','Uranus','Neptune','Pluto')
ZODIAC=('ARIES','TAURUS','GEMINI','CANCER','LEO','VIRGO','LIBRA','SCORPIO','SAGITTARIUS','CAPRICORN','AQUARIUS','PISCES')
ASPECTS={'CONJ':0.0,'SEXTILE':60.0,'SQUARE':90.0,'TRINE':120.0,'OPPOSITION':180.0}
ALL_PAIRS=list(combinations(BODIES.keys(),2))

def utc(dt):
    if dt.tzinfo is None:return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def jd_ut(dt):
    d=utc(dt); hour=d.hour+d.minute/60+d.second/3600+d.microsecond/3.6e9
    return swe.julday(d.year,d.month,d.day,hour,swe.GREG_CAL)

def body_position(name,dt):
    # Moshier avoids external ephemeris-file dependency and is ample for degree-scale aspects.
    xx,_=swe.calc_ut(jd_ut(dt),BODIES[name],swe.FLG_MOSEPH|swe.FLG_SPEED)
    lon=float(xx[0])%360.0
    return {'lon':lon,'lat':float(xx[1]),'distance':float(xx[2]),'speed_lon_deg_day':float(xx[3]),'sign':ZODIAC[int(lon//30)%12]}

def separation(a,b):
    d=abs((a-b)%360.0)
    return min(d,360.0-d)

def aspect_for(sep,orb=3.0):
    best=min(((abs(sep-v),k,v) for k,v in ASPECTS.items()),key=lambda z:z[0])
    if best[0]>orb:return None
    return {'aspect':best[1],'target_deg':best[2],'orb_deg':round(best[0],4),'strength':round(max(0.0,1-best[0]/orb),4)}

def snapshot(dt,orb=3.0):
    dt=utc(dt)
    pos={k:body_position(k,dt) for k in BODIES}
    aspects=[]
    for a,b in ALL_PAIRS:
        sep=separation(pos[a]['lon'],pos[b]['lon'])
        hit=aspect_for(sep,orb)
        if hit:aspects.append({'pair':f'{a}-{b}','separation_deg':round(sep,4),**hit})
    retro={k:{'retrograde':pos[k]['speed_lon_deg_day']<0,'speed_deg_day':round(pos[k]['speed_lon_deg_day'],6)} for k in RETRO_BODIES}
    node=pos['TrueNode']['lon']; south=(node+180.0)%360.0
    return {
      'utc':dt.isoformat(),'frame':'geocentric tropical ecliptic','earth_role':'reference frame',
      'positions':{k:{'lon':round(v['lon'],6),'lat':round(v['lat'],6),'distance':round(v['distance'],9),'speed_lon_deg_day':round(v['speed_lon_deg_day'],6),'sign':v['sign']} for k,v in pos.items()},
      'retrograde':retro,'major_aspects':aspects,
      'north_node_lon':round(node,6),'south_node_lon':round(south,6),
      'pair_count':len(ALL_PAIRS),
    }

if __name__=='__main__':
    import json,sys
    dt=datetime.fromisoformat(sys.argv[1]) if len(sys.argv)>1 else datetime.now(timezone.utc)
    print(json.dumps(snapshot(dt),indent=2))
