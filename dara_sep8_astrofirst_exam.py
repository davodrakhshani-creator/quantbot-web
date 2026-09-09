import json
from datetime import datetime, timezone, date
from pathlib import Path
import dara_v18_prototype_memory_sep7 as v
from dara_astro_day_bias_policy import as_jsonable

v.START=datetime(2026,9,8,0,0,tzinfo=v.TEHRAN)
v.END=datetime(2026,9,9,0,0,tzinfo=v.TEHRAN)
v.S=int(v.START.astimezone(timezone.utc).timestamp()*1000)
v.E=int(v.END.astimezone(timezone.utc).timestamp()*1000)
v.OUT=Path('data/dara_sep8_astrofirst_exam_base.json')

v.main()
base=json.loads(v.OUT.read_text())
astro=as_jsonable(date(2026,9,8))

# Astro-first policy is frozen before Sep8 market inspection. When bias is NEUTRAL,
# the policy intentionally makes no directional threshold/risk change to v18.

def stat(xs):
    return v.stat(xs)

def group_side(xs):
    return {side:stat([x for x in xs if x.get('side')==side]) for side in ('LONG','SHORT')}

seq=base.get('sequential_trades',[])
opp=base.get('opportunities',[])
out={
  'version':'DARA-Sep8-AstroFirst-Exam-v1',
  'period_tehran':[v.START.isoformat(),v.END.isoformat()],
  'forecast_frozen_before_market':True,
  'astro_day_bias':astro,
  'astro_effect_on_intraday_engine':'NONE because day bias is NEUTRAL' if astro['bias']=='NEUTRAL' else 'directional weighting applies',
  'engine':'Frozen DARA v18 Prototype Memory trained only on Sep1-Sep6 experiences; no Sep8 result used in selection.',
  'opportunity_inventory':base['opportunity_inventory'],
  'opportunities_by_side':group_side(opp),
  'sequential':base['sequential'],
  'sequential_by_side':group_side(seq),
  'opportunities':opp,
  'sequential_trades':seq,
}
Path('data/dara_sep8_astrofirst_exam.json').write_text(json.dumps(out,indent=2))
print(json.dumps({
 'astro':{'bias':astro['bias'],'score':astro['score'],'confidence':astro['confidence'],'activity':astro['activity_regime'],'directional_hits':astro['active_directional_rules'],'activity_hits':astro['active_activity_rules']},
 'opportunity_inventory':out['opportunity_inventory'],
 'opportunities_by_side':out['opportunities_by_side'],
 'sequential':out['sequential'],
 'sequential_by_side':out['sequential_by_side']
},indent=2))
