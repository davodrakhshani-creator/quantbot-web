import json
import os
from pathlib import Path
from flask import Flask, jsonify, Response

app = Flask(__name__)

DATA = Path("data")
CORE = DATA / "v4_core_state.json"
PAPER = DATA / "v4_paper_t2.json"
CONSENSUS = DATA / "v4_consensus.json"


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": f"{path}: {exc}"}


def snapshot() -> dict:
    core = _read(CORE)
    paper = _read(PAPER)
    consensus = _read(CONSENSUS)
    errors = [x["_error"] for x in (core, paper, consensus) if "_error" in x]
    health = core.get("health", {}) if not errors else {"ok": False, "errors": errors}
    return {
        "ok": bool(health.get("ok", False)) and not errors,
        "core": core,
        "paper": paper,
        "consensus": consensus,
        "errors": errors,
    }


@app.get("/health")
def health():
    s = snapshot()
    return jsonify({"ok": s["ok"], "mode": "PAPER_ONLY", "rule": "T2_upup"}), (200 if s["ok"] else 503)


@app.get("/api/state")
def api_state():
    return jsonify(snapshot())


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>QuantBot v4</title>
<style>
:root{color-scheme:dark;--bg:#090b10;--card:#131722;--line:#262d3a;--mut:#929daf;--good:#54e398;--bad:#ff6f7f;--warn:#ffd36a;--blue:#79a9ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#f4f7fb;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:920px;margin:auto;padding:18px}.top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.brand{font-size:24px;font-weight:850}.sub,.mut{color:var(--mut)}.pill{border:1px solid var(--line);background:#0e121a;border-radius:999px;padding:8px 11px;font-size:12px;font-weight:750}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px;margin:12px 0}.hero{display:grid;grid-template-columns:1.3fr .7fr;gap:12px}.big{font-size:34px;font-weight:900;letter-spacing:-1px}.cash{color:var(--warn)}.on{color:var(--good)}.off{color:var(--bad)}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.metric{background:#0e121a;border:1px solid #1c2330;border-radius:14px;padding:12px}.metric span{display:block;color:var(--mut);font-size:12px}.metric b{display:block;font-size:20px;margin-top:5px}.row{display:flex;justify-content:space-between;gap:16px;padding:9px 0;border-bottom:1px solid #222938}.row:last-child{border-bottom:0}.bar{height:9px;border-radius:99px;background:#242b38;overflow:hidden}.bar>i{display:block;height:100%;background:linear-gradient(90deg,#78a9ff,#54e398);width:0}.weights{display:flex;gap:8px;flex-wrap:wrap}.chip{background:#0d1219;border:1px solid #263043;border-radius:12px;padding:9px 11px}.tests{display:grid;grid-template-columns:1fr 1fr;gap:8px}.test{padding:10px;border-radius:12px;background:#0e121a;border:1px solid #202838}.test b{float:right;color:var(--good)}.danger{border-color:#4b2830;background:#1b1015}.footer{font-size:12px;color:var(--mut);padding:12px 2px 28px}.tiny{font-size:12px}.err{white-space:pre-wrap;color:var(--bad)}@media(max-width:700px){.hero,.grid,.tests{grid-template-columns:1fr}.top{align-items:center}.big{font-size:30px}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="brand">QuantBot v4</div><div class="sub">Audited regime-aware momentum • frozen T2_upup</div></div><div class="pill">PAPER ONLY</div></div>

<div class="hero">
 <div class="card"><div class="mut tiny">OFFICIAL ACTION</div><div id="action" class="big">Loading…</div><div id="signalDate" class="mut"></div><div class="weights" id="weights" style="margin-top:12px"></div></div>
 <div class="card"><div class="mut tiny">ENGINE HEALTH</div><div id="health" class="big">—</div><div id="status" class="mut"></div></div>
</div>

<div class="card"><b>BTC regime gate</b><div class="grid" style="margin-top:12px">
 <div class="metric"><span>BTC close / SMA200</span><b id="btc">—</b></div>
 <div class="metric"><span>60-day momentum</span><b id="m60">—</b></div>
 <div class="metric"><span>120-day momentum</span><b id="m120">—</b></div>
</div><div class="row"><span>UP-UP + MA200 gate</span><b id="gate">—</b></div></div>

<div class="card"><b>Fresh forward proof</b><div class="grid" style="margin-top:12px">
 <div class="metric"><span>Completed forward days</span><b id="fdays">—</b></div>
 <div class="metric"><span>Active days</span><b id="active">—</b></div>
 <div class="metric"><span>Net @13bps</span><b id="fnet">—</b></div>
 <div class="metric"><span>Profit factor</span><b id="fpf">—</b></div>
 <div class="metric"><span>Max drawdown</span><b id="fdd">—</b></div>
 <div class="metric"><span>Net @40bps</span><b id="stress">—</b></div>
</div><div style="margin-top:13px"><div class="mut tiny">180-day evidence gate progress</div><div class="bar"><i id="progress"></i></div></div><div id="forwardStatus" class="mut tiny" style="margin-top:9px"></div></div>

<div class="card"><b>Research evidence before fresh forward</b><div class="grid" style="margin-top:12px">
 <div class="metric"><span>Current liquid universe</span><b id="e1">—</b><small id="e1s" class="mut"></small></div>
 <div class="metric"><span>2021 historical snapshot</span><b id="e2">—</b><small id="e2s" class="mut"></small></div>
 <div class="metric"><span>Dynamic 52-asset pool</span><b id="e3">—</b><small id="e3s" class="mut"></small></div>
</div></div>

<div class="card"><b>Independent gates</b><div class="tests" id="tests" style="margin-top:12px"></div></div>
<div class="card danger"><b>Live-money guardrail</b><div class="row"><span>Authenticated exchange/order capability</span><b class="off">DISABLED</b></div><div class="mut tiny">Passing the forward paper gate does not automatically enable live trading. The frozen rule must first pass the pre-registered forward criteria and a separate execution/venue/slippage review.</div></div>
<div id="errors" class="err"></div><div class="footer">Data are based on fully completed UTC daily bars. The strategy is weekly long/cash; intraday price noise is intentionally not used as a trading signal.</div>
</div>
<script>
const pc=(x,d=2)=>Number.isFinite(Number(x))?Number(x).toFixed(d)+'%':'—';
const num=(x,d=2)=>Number.isFinite(Number(x))?Number(x).toFixed(d):'—';
function metricText(m){return `${pc(m.net_pct)} • Sharpe ${num(m.sharpe)} • DD ${pc(m.max_dd_pct)}`}
async function load(){
 try{
  const r=await fetch('/api/state',{cache:'no-store'}),s=await r.json(),c=s.core||{},p=s.paper||{},q=s.consensus||{};
  const w=c.target_weights||{}, gross=Number(c.gross_target||0), has=Object.keys(w).length>0&&gross>1e-9;
  action.textContent=has?'REBALANCE / HOLD':'CASH'; action.className='big '+(has?'on':'cash');
  weights.innerHTML=has?Object.entries(w).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<div class="chip"><b>${k.replace('USDT','')}</b> ${(100*v).toFixed(2)}%</div>`).join(''):'<div class="chip">No market exposure</div>';
  signalDate.textContent='Signal: '+(c.signal_date||'—')+' • Gross '+(gross*100).toFixed(2)+'%';
  health.textContent=s.ok?'PASS':'FAIL'; health.className='big '+(s.ok?'on':'off'); status.textContent=c.consensus_status||'';
  const g=c.regime||{}; btc.textContent=(g.btc_close?Number(g.btc_close).toLocaleString():'—')+' / '+(g.btc_sma200?Number(g.btc_sma200).toLocaleString(undefined,{maximumFractionDigits:0}):'—');
  m60.textContent=pc(g.btc_mom60_pct);m60.className=Number(g.btc_mom60_pct)>0?'on':'off';m120.textContent=pc(g.btc_mom120_pct);m120.className=Number(g.btc_mom120_pct)>0?'on':'off';
  gate.textContent=g.upup_gate?'OPEN':'CLOSED';gate.className=g.upup_gate?'on':'off';
  const f=p.forward_metrics_13bps||{},fs=p.forward_metrics_40bps||{},days=Number(f.n_days||0),act=Number(p.forward_active_days||0);
  fdays.textContent=days+' / 180';active.textContent=act+' / 20';fnet.textContent=pc(f.net_pct);fpf.textContent=num(f.pf);fdd.textContent=pc(f.max_dd_pct);stress.textContent=pc(fs.net_pct);
  progress.style.width=Math.min(100,days/180*100)+'%'; forwardStatus.textContent=(p.promotion_gate_pre_registered||{}).passed?'Paper gate passed; manual operational review required.':'Forward gate not yet complete; rule remains frozen.';
  const km=q.key_prelock_metrics||{},a=km.current_liquid||{},b=km.historical_2021_snapshot||{},d=km.dynamic_liquidity_universe_52_pool||{};
  e1.textContent=pc(a.net_pct);e1s.textContent=`Sharpe ${num(a.sharpe)} • DD ${pc(a.max_drawdown_pct)}`;e2.textContent=pc(b.net_pct);e2s.textContent=`Sharpe ${num(b.sharpe)} • DD ${pc(b.max_drawdown_pct)}`;e3.textContent=pc(d.net_pct);e3s.textContent=`Sharpe ${num(d.sharpe)} • DD ${pc(d.max_drawdown_pct)}`;
  const why=q.why_selected||{};tests.innerHTML=Object.entries(why).map(([k,v])=>`<div class="test">${k.replaceAll('_',' ')}<b>${String(v).startsWith('REJECTED')?'FILTERED':'PASS'}</b><div class="mut tiny">${v}</div></div>`).join('');
  errors.textContent=(s.errors||[]).join('\n');
 }catch(e){errors.textContent='Dashboard error: '+e}
}
load();setInterval(load,30000);
</script></body></html>'''


@app.get("/")
def home():
    return Response(PAGE, mimetype="text/html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
