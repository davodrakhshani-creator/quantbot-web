from datetime import datetime
from pathlib import Path
import dara_v7_confirmed_expansion as v7
import dara_v1_sep1 as b

START=datetime(2026,7,15,0,0,tzinfo=b.TEHRAN)
END=datetime(2026,8,10,0,0,tzinfo=b.TEHRAN)
OUT=Path('data/dara_v7_blind_jul15_aug09.json')

if __name__=='__main__':
    v7.run(START,END,OUT)
