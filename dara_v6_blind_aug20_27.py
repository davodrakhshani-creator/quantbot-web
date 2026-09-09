from datetime import datetime
from pathlib import Path
import dara_v6_context_tiered as v6
import dara_v1_sep1 as b

START=datetime(2026,8,20,0,0,tzinfo=b.TEHRAN)
END=datetime(2026,8,28,0,0,tzinfo=b.TEHRAN)
OUT=Path('data/dara_v6_blind_aug20_27.json')

if __name__=='__main__':
    v6.run(START,END,OUT)
