"""Run DARA astro calibration from official Binance Vision monthly USD-M archives.
Avoids regional HTTP 451 on the live Futures REST endpoint in GitHub Actions.
"""
import csv, io, zipfile, urllib.request, time
from datetime import datetime, timezone
import dara_astro_daily_calibration as cal

UA='DARA-Astro-Archive/1.0'

def months(start,end):
    y,m=start.year,start.month
    while (y,m)<(end.year,end.month):
        yield y,m
        m+=1
        if m==13:y+=1;m=1

def fetch_archive_daily():
    rows=[]
    for y,m in months(cal.START,cal.END):
        ym=f'{y:04d}-{m:02d}'
        url=f'https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-{ym}.zip'
        err=None
        for k in range(4):
            try:
                req=urllib.request.Request(url,headers={'User-Agent':UA})
                with urllib.request.urlopen(req,timeout=45) as r:data=r.read()
                z=zipfile.ZipFile(io.BytesIO(data));raw=z.read(z.namelist()[0]).decode('utf-8')
                for x in csv.reader(io.StringIO(raw)):
                    if x and x[0].isdigit():rows.append(x)
                err=None;break
            except Exception as e:
                err=e;time.sleep(.5*(2**k))
        if err:raise RuntimeError(f'Archive fetch failed {ym}: {err}')
    uniq={int(x[0]):x for x in rows}
    s=int(cal.START.timestamp()*1000);e=int(cal.END.timestamp()*1000)
    return [uniq[k] for k in sorted(uniq) if s<=k<e]

if __name__=='__main__':
    cal.fetch_daily=fetch_archive_daily
    cal.main()
