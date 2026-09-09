"""DARA astronomical timing features.

Objective ephemeris calculations only. Any financial interpretation is a hypothesis
that must be calibrated separately and may never create a production entry alone.
Requires: pip install astronomy-engine
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import math
import astronomy

BODIES = {
    'Sun': astronomy.Body.Sun,
    'Moon': astronomy.Body.Moon,
    'Mercury': astronomy.Body.Mercury,
    'Venus': astronomy.Body.Venus,
    'Mars': astronomy.Body.Mars,
    'Jupiter': astronomy.Body.Jupiter,
    'Saturn': astronomy.Body.Saturn,
    'Uranus': astronomy.Body.Uranus,
    'Neptune': astronomy.Body.Neptune,
    'Pluto': astronomy.Body.Pluto,
}
RETRO_BODIES = ['Mercury','Venus','Mars','Jupiter','Saturn','Uranus','Neptune','Pluto']
ASPECTS = {'CONJ':0.0,'SEXTILE':60.0,'SQUARE':90.0,'TRINE':120.0,'OPPOSITION':180.0}
RESEARCH_PAIRS = [
    ('Moon','Mars'),('Moon','Jupiter'),('Moon','Saturn'),('Moon','Mercury'),('Moon','Venus'),
    ('Mercury','Mars'),('Mercury','Jupiter'),('Mercury','Saturn'),
    ('Venus','Mars'),('Venus','Jupiter'),('Venus','Saturn'),
    ('Mars','Jupiter'),('Mars','Saturn'),('Jupiter','Saturn')
]

def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def atime(dt: datetime):
    d=_utc(dt)
    return astronomy.Time.Make(d.year,d.month,d.day,d.hour,d.minute,d.second + d.microsecond/1e6)

def wrap360(x: float) -> float:
    return x % 360.0

def signed_angle_delta(new: float, old: float) -> float:
    return (new-old+180.0)%360.0-180.0

def geo_ecliptic(body_name: str, dt: datetime) -> dict:
    v=astronomy.GeoVector(BODIES[body_name], atime(dt), True)
    e=astronomy.Ecliptic(v)
    return {'lon':wrap360(float(e.elon)), 'lat':float(e.elat), 'distance_au':float(v.Length())}

def geo_lon(body_name: str, dt: datetime) -> float:
    return geo_ecliptic(body_name,dt)['lon']

def retrograde_speed_deg_day(body_name: str, dt: datetime, hours: int=6) -> float:
    a=geo_lon(body_name,dt-timedelta(hours=hours))
    b=geo_lon(body_name,dt+timedelta(hours=hours))
    return signed_angle_delta(b,a)/(2*hours/24.0)

def phase_octant(angle: float) -> str:
    names=['NEW','WAX_CRESCENT','FIRST_QUARTER','WAX_GIBBOUS','FULL','WAN_GIBBOUS','LAST_QUARTER','WAN_CRESCENT']
    return names[int(((angle+22.5)%360)//45)]

def aspect_for(pair_angle: float, orb: float=3.0):
    sep=min(pair_angle%360,360-(pair_angle%360))
    best=None
    for name,target in ASPECTS.items():
        err=abs(sep-target)
        if best is None or err<best[1]:best=(name,err,target)
    if best and best[1]<=orb:
        return {'aspect':best[0],'target_deg':best[2],'orb_deg':round(best[1],4),'strength':round(max(0.0,1-best[1]/orb),4)}
    return None

def snapshot(dt: datetime, aspect_orb: float=3.0) -> dict:
    dt=_utc(dt); t=atime(dt)
    phase=float(astronomy.MoonPhase(t))
    moon=geo_ecliptic('Moon',dt)
    # Simple geometric illumination proxy: 0 at new, 1 at full.
    illum=(1-math.cos(math.radians(phase)))/2
    positions={}
    for name in BODIES:
        positions[name]=geo_ecliptic(name,dt)
    retro={}
    for name in RETRO_BODIES:
        speed=retrograde_speed_deg_day(name,dt)
        retro[name]={'speed_deg_day':round(speed,6),'retrograde':speed<0}
    aspects=[]
    for a,b in RESEARCH_PAIRS:
        ang=float(astronomy.PairLongitude(BODIES[a],BODIES[b],t))
        hit=aspect_for(ang,aspect_orb)
        if hit:
            aspects.append({'pair':f'{a}-{b}','pair_longitude_deg':round(ang,4),**hit})
    # Eclipse-like geometry proxy only; not an eclipse prediction.
    phase_dist=min(abs(signed_angle_delta(phase,0)),abs(signed_angle_delta(phase,180)))
    eclipse_proxy=max(0.0,1-phase_dist/10.0)*max(0.0,1-abs(moon['lat'])/2.0)
    return {
        'utc':dt.isoformat(),
        'moon_phase_deg':round(phase,6),
        'moon_phase_octant':phase_octant(phase),
        'moon_illumination_proxy':round(illum,6),
        'moon_distance_au':round(moon['distance_au'],9),
        'moon_ecliptic_lat_deg':round(moon['lat'],6),
        'eclipse_geometry_proxy':round(eclipse_proxy,6),
        'positions':{k:{kk:round(vv,9 if kk=='distance_au' else 6) for kk,vv in v.items()} for k,v in positions.items()},
        'retrograde':retro,
        'major_aspects':aspects,
    }

if __name__=='__main__':
    import json,sys
    dt=datetime.fromisoformat(sys.argv[1]) if len(sys.argv)>1 else datetime.now(timezone.utc)
    print(json.dumps(snapshot(dt),indent=2))
