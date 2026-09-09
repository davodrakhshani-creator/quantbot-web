import json
from pathlib import Path
src=json.loads(Path('data/dara_astro_fullgrid_summary_2022_2026.json').read_text())
out={
 'version':src['version'],
 'days':src['days'],
 'candidate_count':src['candidate_count'],
 'factors':src['factors'],
 'top_return':src['top_by_metric']['return'][:12],
 'top_buy_imbalance':src['top_by_metric']['buy_imbalance'][:12],
 'top_volume':src['top_by_metric']['volume_surprise'][:12],
 'top_abs_return':src['top_by_metric']['abs_return'][:12],
 'factor_best':src['best_recurrent_signal_touching_each_factor'],
 'warning':src['warning'],
}
Path('data/dara_astro_fullgrid_digest.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
