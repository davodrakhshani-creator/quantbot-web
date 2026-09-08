import json
import os
from pathlib import Path
from flask import Flask, jsonify, Response

app = Flask(__name__)
DATA = Path("data")
CORE = DATA / "v4_core_state.json"
PAPER = DATA / "v4_paper_t2.json"
CONSENSUS = DATA / "v4_consensus.json"
COINBASE = DATA / "v5_coinbase_validation.json"
PRECISION = DATA / "v5_precision_state.json"
FAST = DATA / "v5_fastlane_state.json"
PRECISION_PAPER = DATA / "v5_paper_precision.json"
FAST_PAPER = DATA / "v5_paper_fastlane.json"


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": f"{path}: {exc}"}


def snapshot() -> dict:
    core, paper, consensus, coinbase, precision, fast, precision_paper, fast_paper = map(
        _read, (CORE, PAPER, CONSENSUS, COINBASE, PRECISION, FAST, PRECISION_PAPER, FAST_PAPER)
    )
    required = (core, paper, consensus)
    errors = [x["_error"] for x in required if "_error" in x]
    health = core.get("health", {}) if not errors else {"ok": False, "errors": errors}
    return {
        "ok": bool(health.get("ok", False)) and not errors,
        "core": core,
        "paper": paper,
        "consensus": consensus,
        "coinbase": coinbase,
        "precision": precision,
        "fast": fast,
        "precision_paper": precision_paper,
        "fast_paper": fast_paper,
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
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>QuantBot Control Center</title>
<style>
:root{color-scheme:dark;--bg:#07090d;--card:#111722;--line:#243043;--mut:#91a0b5;--good:#4fe19a;--bad:#ff6d82;--warn:#ffd26a;--blue:#79a9ff;--vio:#a98bff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% -10%,#152038 0,#090d14 36%,var(--bg) 68%);color:#f4f7fb;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Tahoma,sans-serif}.wrap{max-width:1080px;margin:auto;padding:18px}.top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.brand{font-size:28px;font-weight:900;letter-spacing:-.6px}.sub,.mut{color:var(--mut)}.pill{border:1px solid var(--line);background:#0e141f;border-radius:999px;padding:8px 12px;font-size:12px;font-weight:800}.card{background:linear-gradient(180deg,#121a27,#0f151f);border:1px solid var(--line);border-radius:20px;padding:16px;margin:12px 0;box-shadow:0 14px 40px #0004}.hero{display:grid;grid-template-columns:1.35fr .65fr;gap:12px}.big{font-size:36px;font-weight:950;letter-spacing:-1px}.cash{color:var(--warn)}.on{color:var(--good)}.off{color:var(--bad)}.blue{color:var(--blue)}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.metric{background:#0b111a;border:1px solid #1f2a3b;border-radius:15px;padding:12px}.metric span{display:block;color:var(--mut);font-size:12px}.metric b{display:block;font-size:20px;margin-top:5px}.row{display:flex;justify-content:space-between;gap:16px;padding:9px 0;border-bottom:1px solid #202a38}.row:last-child{border-bottom:0}.bar{height:9px;border-radius:99px;background:#242e3e;overflow:hidden}.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--good));width:0}.weights{display:flex;gap:8px;flex-wrap:wrap}.chip{background:#0d141e;border:1px solid #2a3950;border-radius:12px;padding:9px 11px}.tests{display:grid;grid-template-columns:1fr 1fr;gap:8px}.test{padding:10px;border-radius:12px;background:#0c131d;border:1px solid #202c3e}.test b{float:left;color:var(--good)}.danger{border-color:#4b2830;background:#1b1015}.footer{font-size:12px;color:var(--mut);padding:12px 2px 28px}.tiny{font-size:12px}.err{white-space:pre-wrap;color:var(--bad)}.tag{display:inline-block;padding:4px 8px;border:1px solid #33415a;border-radius:999px;font-size:11px;color:#b7c8e4;margin:2px}.lead{font-size:14px;line-height:1.8;color:#cbd5e4}.ltr{direction:ltr;text-align:left}@media(max-width:760px){.hero,.grid,.grid3,.tests{grid-template-columns:1fr}.top{align-items:center}.big{font-size:30px}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="brand">QuantBot · Control Center</div><div class="sub">Core T2 + Precision v5 + Fast-Lane v5 · Paper execution</div></div><div class="pill">LIVE LOCKED 🔒</div></div>

<div class="hero">
 <div class="card"><div class="mut tiny">تصمیم رسمی موتور</div><div id="action" class="big">در حال بارگذاری…</div><div id="reason" class="lead"></div><div id="signalDate" class="mut tiny"></div><div class="weights" id="weights" style="margin-top:12px"></div></div>
 <div class="card"><div class="mut tiny">سلامت سیستم</div><div id="health" class="big">—</div><div id="status" class="mut"></div><div style="margin-top:10px"><span class="tag">No API keys</span><span class="tag">No live orders</span><span class="tag">Next-open audited</span></div></div>
</div>

<div class="card"><b>سه موتور تحقیقاتی</b><div class="grid3" style="margin-top:12px">
 <div class="metric"><span>Core</span><b>T2_upup</b><small class="mut">موتور فریز‌شده Forward</small></div>
 <div class="metric"><span>Precision v5</span><b id="precName">—</b><small id="precSub" class="mut"></small></div>
 <div class="metric"><span>Fast-Lane v5</span><b id="fastName">—</b><small id="fastSub" class="mut"></small></div>
</div><div class="row"><span>Precision upgrade vs T2</span><b id="precGate">—</b></div><div class="row"><span>Fast-Lane research gate</span><b id="fastGate">—</b></div></div>

<div class="card"><b>سیگنال‌های Forward امروز</b><div class="grid3" style="margin-top:12px">
 <div class="metric"><span>Core T2</span><b id="coreSig">—</b><small id="coreW" class="mut"></small></div>
 <div class="metric"><span>Precision P4</span><b id="precSig">—</b><small id="precW" class="mut"></small></div>
 <div class="metric"><span>Fast F0</span><b id="fastSig">—</b><small id="fastW" class="mut"></small></div>
</div><div class="lead">Fast-Lane یک Scout سریع‌تر است، نه مجوز خرید واقعی. اگر Core/Precision مخالف باشند، فعلاً فقط در Paper ثبت می‌شود.</div></div>

<div class="card"><b>گیت رژیم BTC</b><div class="grid" style="margin-top:12px">
 <div class="metric"><span>BTC Close</span><b id="btcClose">—</b></div>
 <div class="metric"><span>SMA200</span><b id="btcSma">—</b></div>
 <div class="metric"><span>Momentum 60d</span><b id="m60">—</b></div>
 <div class="metric"><span>Momentum 120d</span><b id="m120">—</b></div>
</div><div class="row"><span>شرط ورود Core: BTC>SMA200 + M60>0 + M120>0</span><b id="gate">—</b></div></div>

<div class="card"><b>شواهد مستقل از صرافی</b><div class="grid3" style="margin-top:12px">
 <div class="metric"><span>Binance / current universe</span><b id="binNet">—</b><small id="binSub" class="mut"></small></div>
 <div class="metric"><span>Coinbase independent venue</span><b id="cbNet">—</b><small id="cbSub" class="mut"></small></div>
 <div class="metric"><span>Historical dynamic 52-asset</span><b id="dynNet">—</b><small id="dynSub" class="mut"></small></div>
</div><div class="row"><span>Coinbase independent venue gate</span><b id="cbGate">—</b></div></div>

<div class="card"><b>Stress / execution evidence</b><div class="grid" style="margin-top:12px">
 <div class="metric"><span>Binance @40bps</span><b id="b40">—</b></div>
 <div class="metric"><span>Binance @75bps</span><b id="b75">—</b></div>
 <div class="metric"><span>Coinbase @40bps</span><b id="c40">—</b></div>
 <div class="metric"><span>Coinbase @75bps</span><b id="c75">—</b></div>
</div></div>

<div class="card"><b>Forward Proof · از 8 سپتامبر 2026</b><div class="grid" style="margin-top:12px">
 <div class="metric"><span>روزهای کامل Core</span><b id="fdays">—</b></div>
 <div class="metric"><span>روزهای فعال Core</span><b id="active">—</b></div>
 <div class="metric"><span>Net @13bps</span><b id="fnet">—</b></div>
 <div class="metric"><span>Profit Factor</span><b id="fpf">—</b></div>
 <div class="metric"><span>Max DD</span><b id="fdd">—</b></div>
 <div class="metric"><span>Net @40bps</span><b id="stress">—</b></div>
 <div class="metric"><span>Forward status</span><b id="fgate">—</b></div>
 <div class="metric"><span>Live authorization</span><b class="off">NO</b></div>
</div><div style="margin-top:13px"><div class="mut tiny">پیشرفت حداقل 180 روز Forward</div><div class="bar"><i id="progress"></i></div></div></div>

<div class="card"><b>Research / Red-Team consensus</b><div class="tests" id="tests" style="margin-top:12px"></div></div>
<div class="card danger"><b>قفل پول واقعی</b><div class="row"><span>ارسال سفارش به صرافی</span><b class="off">غیرفعال</b></div><div class="lead">Precision و Fast-Lane فعلاً فقط لایه‌های تحقیق/Paper هستند. هیچ‌کدام بدون Forward evidence و بررسی جداگانه‌ی execution، slippage و operational risk وارد پول واقعی نمی‌شوند.</div></div>
<div id="errors" class="err"></div><div class="footer">Refresh خودکار هر 30 ثانیه · داده فقط از کندل‌های کامل UTC · هیچ سفارش واقعی از این سرویس ارسال نمی‌شود.</div>
</div>
<script>
const pc=(x,d=2)=>Number.isFinite(Number(x))?Number(x).toFixed(d)+'%':'—';
const num=(x,d=2)=>Number.isFinite(Number(x))?Number(x).toFixed(d):'—';
const money=x=>Number.isFinite(Number(x))?Number(x).toLocaleString(undefined,{maximumFractionDigits:0}):'—';
const sig=(obj)=>{const s=(obj||{}).current_signal||{},w=s.weights||{},gross=Number(s.gross_exposure||0);return {active:Object.keys(w).length>0&&gross>1e-9,w,gross}};
const wt=(x)=>Object.entries(x.w).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`${k.replace('USDT','')} ${(100*v).toFixed(2)}%`).join(' · ')||'Exposure 0%';
async function load(){
 try{
  const r=await fetch('/api/state',{cache:'no-store'}),s=await r.json(),c=s.core||{},p=s.paper||{},q=s.consensus||{},cb=s.coinbase||{},pr=s.precision||{},fa=s.fast||{},pp=s.precision_paper||{},fp=s.fast_paper||{};
  const w=c.target_weights||{},gross=Number(c.gross_target||0),has=Object.keys(w).length>0&&gross>1e-9,g=c.regime||{};
  action.textContent=has?'ACTIVE / REBALANCE':'CASH';action.className='big '+(has?'on':'cash');
  reason.textContent=has?'Regime gate باز است؛ موتور فقط وزن‌های هدف Paper را نگه می‌دارد.':'Regime gate بسته است؛ موتور عمداً بیرون بازار می‌ماند تا شروط روند تأیید شوند.';
  weights.innerHTML=has?Object.entries(w).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<div class="chip ltr"><b>${k.replace('USDT','')}</b> ${(100*v).toFixed(2)}%</div>`).join(''):'<div class="chip">Exposure = 0%</div>';
  signalDate.textContent='Signal: '+(c.signal_date||'—')+' · Gross '+(gross*100).toFixed(2)+'%';
  health.textContent=s.ok?'PASS':'FAIL';health.className='big '+(s.ok?'on':'off');status.textContent=c.consensus_status||'';
  precName.textContent=pr.selected||'—';precSub.textContent=pr.research_status||'';precGate.textContent=(pr.upgrade_vs_T2||{}).precision_upgrade?'PASS':'NO UPGRADE';precGate.className=(pr.upgrade_vs_T2||{}).precision_upgrade?'on':'cash';
  fastName.textContent=fa.selected||'—';fastSub.textContent=fa.research_status||'';fastGate.textContent=fa.research_status==='FAST_PAPER_CANDIDATE'?'PASS':'FAIL';fastGate.className=fa.research_status==='FAST_PAPER_CANDIDATE'?'on':'off';
  const ps=sig(pp),fsig=sig(fp);coreSig.textContent=has?'ACTIVE':'CASH';coreSig.className=has?'on':'cash';coreW.textContent=has?wt({w,gross}):'Exposure 0%';precSig.textContent=ps.active?'ACTIVE':'CASH';precSig.className=ps.active?'on':'cash';precW.textContent=wt(ps);fastSig.textContent=fsig.active?'SCOUT ACTIVE':'CASH';fastSig.className=fsig.active?'blue':'cash';fastW.textContent=wt(fsig);
  btcClose.textContent=money(g.btc_close);btcSma.textContent=money(g.btc_sma200);m60.textContent=pc(g.btc_mom60_pct);m60.className=Number(g.btc_mom60_pct)>0?'on':'off';m120.textContent=pc(g.btc_mom120_pct);m120.className=Number(g.btc_mom120_pct)>0?'on':'off';gate.textContent=g.upup_gate?'OPEN':'CLOSED';gate.className=g.upup_gate?'on':'off';
  const km=q.key_prelock_metrics||{},b=km.current_liquid||{},d=km.dynamic_liquidity_universe_52_pool||{},cp=cb.prelock||{};
  binNet.textContent=pc(b.net_pct);binSub.textContent=`Sharpe ${num(b.sharpe)} · PF ${num(b.profit_factor)} · DD ${pc(b.max_drawdown_pct)}`;
  cbNet.textContent=pc(cp.net_pct);cbSub.textContent=`Sharpe ${num(cp.sharpe)} · PF ${num(cp.pf)} · DD ${pc(cp.max_dd_pct)}`;
  dynNet.textContent=pc(d.net_pct);dynSub.textContent=`Sharpe ${num(d.sharpe)} · PF ${num(d.profit_factor)} · DD ${pc(d.max_drawdown_pct)}`;
  cbGate.textContent=cb.independent_venue_gate?'PASS':'FAIL';cbGate.className=cb.independent_venue_gate?'on':'off';
  b40.textContent=pc(b.stress40_net_pct);b75.textContent=pc(b.stress75_net_pct);c40.textContent=pc((cb.stress40||{}).net_pct);c75.textContent=pc((cb.stress75||{}).net_pct);
  const f=p.forward_metrics_13bps||{},f40=p.forward_metrics_40bps||{},days=Number(f.n_days||0),act=Number(p.forward_active_days||0),fg=(p.promotion_gate_pre_registered||{}).passed;
  fdays.textContent=days+' / 180';active.textContent=act+' / 20';fnet.textContent=pc(f.net_pct);fpf.textContent=num(f.pf);fdd.textContent=pc(f.max_dd_pct);stress.textContent=pc(f40.net_pct);fgate.textContent=fg?'PASS':'PENDING';fgate.className=fg?'on':'cash';progress.style.width=Math.min(100,days/180*100)+'%';
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
