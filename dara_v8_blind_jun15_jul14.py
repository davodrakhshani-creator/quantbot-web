from datetime import datetime
from pathlib import Path
import dara_v8_progress_guard as v8
import dara_v1_sep1 as b

START=datetime(2026,6,15,0,0,tzinfo=b.TEHRAN)
END=datetime(2026,7,15,0,0,tzinfo=b.TEHRAN)
OUT=Path('data/dara_v8_blind_jun15_jul14.json')

if __name__=='__main__':
    v8.run(START,END,OUT)
