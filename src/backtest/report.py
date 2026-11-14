"""Write a self-contained HTML dashboard for one or more backtest result folders.

    uv run python src/backtest/report.py RUN_DIR [RUN_DIR ...] [--out FILE.html]

One folder: full report (P&L, positions, prices with our fills, per-instrument
table). Several folders: comparison (overlaid P&L curves and a summary table),
useful for comparing strategies on the same data. Data is embedded as JSON;
charts use Chart.js from a CDN, so viewing needs an internet connection.
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

MAX_POINTS = 2000  # equity points per run sent to the browser
MAX_FILLS = 20000  # fills per run sent to the browser


def _float(text: str) -> float | None:
    return None if text in ("", None) else float(text)


def _seconds(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def load_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    summary = json.loads((run_dir / "summary.json").read_text())
    with open(run_dir / "equity.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    step = max(1, len(rows) // MAX_POINTS)
    rows = rows[::step] + ([rows[-1]] if (len(rows) - 1) % step else [])
    t0 = _seconds(rows[0]["sim_ts"]) if rows else 0.0
    instruments = sorted(name[4:] for name in (rows[0] if rows else {}) if name.startswith("pos_"))
    equity = {
        "t": [round((_seconds(r["sim_ts"]) - t0) / 60, 4) for r in rows],  # minutes since start
        "pnl_mid": [_float(r["pnl_mid"]) for r in rows],
        "pnl_last": [_float(r["pnl_last"]) for r in rows],
        "pos": {iid: [int(r[f"pos_{iid}"]) for r in rows] for iid in instruments},
        "mid": {iid: [_float(r[f"mid_{iid}"]) for r in rows] for iid in instruments},
    }
    fills = []
    with open(run_dir / "fills.csv", newline="") as f:
        for r in list(csv.DictReader(f))[:MAX_FILLS]:
            fills.append({"t": round((_seconds(r["sim_ts"]) - t0) / 60, 4), "iid": r["instrument_id"],
                          "side": r["side"], "price": float(r["price"]), "volume": int(r["volume"]),
                          "reason": r["reason"]})
    return {"name": run_dir.name, "summary": summary, "instruments": instruments,
            "equity": equity, "fills": fills}


def write_report(run_dirs: list[Path], out_path: Path) -> None:
    runs = [load_run(d) for d in run_dirs]
    html = TEMPLATE.replace("__DATA__", json.dumps(runs).replace("</", "<\\/"))
    Path(out_path).write_text(html)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backtest Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg: #f7f7f5; --card: #ffffff; --text: #1d1d1b; --muted: #6b6b66; --border: #e3e2dc;
  --pos: #1a7f4b; --neg: #c0392b;
  --s1: #2f6fdf; --s2: #e07b22; --s3: #2a9d8f; --s4: #b5418f; --s5: #7a5cd6; --s6: #c9a227; --s7: #5d8a3a; --s8: #d1495b;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161615; --card: #20201e; --text: #ecebe6; --muted: #9c9b94; --border: #34342f;
    --pos: #4cc38a; --neg: #ef6b5b;
    --s1: #6a9bf0; --s2: #f0a056; --s3: #4fc1b2; --s4: #d77cb7; --s5: #a28cf0; --s6: #e3c35a; --s7: #8cbc67; --s8: #ef7d8d;
  }
}
:root[data-theme="dark"] {
  --bg: #161615; --card: #20201e; --text: #ecebe6; --muted: #9c9b94; --border: #34342f;
  --pos: #4cc38a; --neg: #ef6b5b;
  --s1: #6a9bf0; --s2: #f0a056; --s3: #4fc1b2; --s4: #d77cb7; --s5: #a28cf0; --s6: #e3c35a; --s7: #8cbc67; --s8: #ef7d8d;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 1200px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 0 0 12px; }
.meta { color: var(--muted); margin-bottom: 20px; word-break: break-all; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 16px; }
.card, section { background: var(--card); border: 1px solid var(--border); border-radius: 8px; }
.card { padding: 12px 14px; }
.card .label { color: var(--muted); font-size: 12px; }
.card .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
section { padding: 16px; margin-bottom: 16px; }
.chart { position: relative; height: 300px; }
.pos { color: var(--pos); } .neg { color: var(--neg); }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 6px 8px; border-bottom: 1px solid var(--border); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--muted); font-weight: 500; font-size: 12px; }
.scroll { overflow-x: auto; }
select { font: inherit; color: var(--text); background: var(--card); border: 1px solid var(--border);
         border-radius: 6px; padding: 4px 8px; margin-left: 8px; }
.note { color: var(--muted); font-size: 12px; margin-top: 8px; }
</style>
</head>
<body>
<main id="app"></main>
<script>
const RUNS = __DATA__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const series = i => css(`--s${(i % 8) + 1}`);
const fmt = (v, d = 2) => v === null || v === undefined ? "–" : Number(v).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d});
const signed = v => v === null || v === undefined ? "" : (v >= 0 ? "pos" : "neg");
const app = document.getElementById("app");
const el = (tag, attrs = {}, html = "") => { const e = document.createElement(tag); Object.assign(e, attrs); e.innerHTML = html; return e; };

function baseOptions(yTitle) {
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--border");
  return {
    animation: false, maintainAspectRatio: false, parsing: false, normalized: true,
    interaction: {mode: "nearest", axis: "x", intersect: false},
    scales: {x: {type: "linear", title: {display: true, text: "minutes since start"}},
             y: {title: {display: true, text: yTitle}}},
    plugins: {legend: {labels: {boxWidth: 12}}},
    elements: {point: {radius: 0}, line: {borderWidth: 1.5}},
  };
}
const xy = (t, ys) => t.map((x, i) => ({x, y: ys[i]})).filter(p => p.y !== null);
function chartSection(title, extra) {
  const s = el("section"); s.append(el("h2", {}, title));
  if (extra) s.firstChild.append(extra);
  const box = el("div", {className: "chart"}); const canvas = el("canvas"); box.append(canvas); s.append(box);
  app.append(s); return [s, canvas];
}
function card(label, value, cls = "") {
  return `<div class="card"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`;
}

function single(run) {
  const s = run.summary, eq = run.equity;
  const rejections = Object.values(s.rejections).reduce((a, b) => a + b, 0);
  app.append(el("h1", {}, "Backtest report"));
  app.append(el("div", {className: "meta"},
    `${s.strategy} · data ${s.data_start} → ${s.data_end} (${fmt(s.sim_duration_s / 60, 1)} min) · ${s.fill_model}`));
  app.append(el("div", {className: "cards"},
    card("P&L (mid)", fmt(s.pnl_mid), signed(s.pnl_mid)) + card("P&L (last trade)", fmt(s.pnl_last), signed(s.pnl_last)) +
    card("Max drawdown (mid)", fmt(s.max_drawdown_mid)) + card("Fills / lots", `${s.fills} / ${s.traded_volume}`) +
    card("End gross position", s.end_gross_position) + card("Rejections", rejections, rejections ? "neg" : "") +
    card("Rate violations", s.rate_violations, s.rate_violations ? "neg" : "")));

  let [, c] = chartSection("P&L");
  new Chart(c, {type: "line", options: baseOptions("P&L"), data: {datasets: [
    {label: "mid mark", data: xy(eq.t, eq.pnl_mid), borderColor: series(0), backgroundColor: series(0)},
    {label: "last-trade mark", data: xy(eq.t, eq.pnl_last), borderColor: series(1), backgroundColor: series(1), borderDash: [4, 3]}]}});

  [, c] = chartSection("Position (lots)");
  const posOpts = baseOptions("lots"); posOpts.elements.line.stepped = true;
  new Chart(c, {type: "line", options: posOpts, data: {datasets: run.instruments.map((iid, i) =>
    ({label: iid, data: xy(eq.t, eq.pos[iid]), borderColor: series(i), backgroundColor: series(i)}))}});

  const select = el("select");
  run.instruments.forEach(iid => select.append(el("option", {value: iid}, iid)));
  const busiest = run.instruments.map(iid => [iid, run.fills.filter(f => f.iid === iid).length]).sort((a, b) => b[1] - a[1])[0];
  if (busiest) select.value = busiest[0];
  const [priceSection, pc] = chartSection("Mid price and our fills", select);
  priceSection.append(el("div", {className: "note"}, "Triangles: our buys (up) and sells (down), at the fill price. Mid sampled once per second."));
  const priceChart = new Chart(pc, {type: "line", options: baseOptions("price"), data: {datasets: []}});
  const drawPrice = () => {
    const iid = select.value, f = run.fills.filter(x => x.iid === iid);
    const pt = side => f.filter(x => x.side === side).map(x => ({x: x.t, y: x.price, v: x.volume}));
    priceChart.data.datasets = [
      {label: `${iid} mid`, data: xy(eq.t, eq.mid[iid]), borderColor: series(0), backgroundColor: series(0)},
      {label: "buy", type: "scatter", data: pt("bid"), pointStyle: "triangle", pointRadius: 5, backgroundColor: css("--pos"), borderColor: css("--pos")},
      {label: "sell", type: "scatter", data: pt("ask"), pointStyle: "triangle", rotation: 180, pointRadius: 5, backgroundColor: css("--neg"), borderColor: css("--neg")}];
    priceChart.update();
  };
  select.onchange = drawPrice; drawPrice();

  const t = el("section"); t.append(el("h2", {}, "Per instrument"));
  const rows = run.instruments.map(iid => { const p = s.per_instrument[iid];
    return `<tr><td>${iid}</td><td>${p.fills}</td><td>${p.bought}</td><td>${p.sold}</td><td>${p.max_abs_position}</td><td>${p.end_position}</td></tr>`; }).join("");
  t.append(el("div", {className: "scroll"}, `<table><tr><th>Instrument</th><th>Fills</th><th>Bought</th><th>Sold</th><th>Max |pos|</th><th>End pos</th></tr>${rows}</table>`));
  if (rejections) {
    t.append(el("h2", {style: "margin-top:16px"}, "Rejections"));
    t.append(el("div", {className: "scroll"}, `<table><tr><th>Reason</th><th>Count</th></tr>${
      Object.entries(s.rejections).map(([r, n]) => `<tr><td>${r}</td><td>${n}</td></tr>`).join("")}</table>`));
  }
  app.append(t);
}

function compare(runs) {
  app.append(el("h1", {}, "Backtest comparison"));
  app.append(el("div", {className: "meta"}, `${runs.length} runs · P&L curves are aligned at each run's own start`));
  const [, c] = chartSection("P&L (mid mark)");
  new Chart(c, {type: "line", options: baseOptions("P&L"), data: {datasets: runs.map((r, i) =>
    ({label: r.name, data: xy(r.equity.t, r.equity.pnl_mid), borderColor: series(i), backgroundColor: series(i)}))}});
  const t = el("section"); t.append(el("h2", {}, "Summary"));
  const rows = runs.map(r => { const s = r.summary;
    return `<tr><td>${r.name}</td><td>${s.strategy.split("/").pop()}</td><td class="${signed(s.pnl_mid)}">${fmt(s.pnl_mid)}</td>` +
      `<td class="${signed(s.pnl_last)}">${fmt(s.pnl_last)}</td><td>${fmt(s.max_drawdown_mid)}</td><td>${s.fills}</td>` +
      `<td>${s.traded_volume}</td><td>${s.end_gross_position}</td><td>${Object.values(s.rejections).reduce((a, b) => a + b, 0)}</td>` +
      `<td>${s.rate_violations}</td><td>${fmt(s.sim_duration_s / 60, 1)}</td></tr>`; }).join("");
  t.append(el("div", {className: "scroll"}, `<table><tr><th>Run</th><th>Strategy</th><th>P&L mid</th><th>P&L last</th>` +
    `<th>Max DD</th><th>Fills</th><th>Lots</th><th>End gross pos</th><th>Rejections</th><th>Rate viol.</th><th>Minutes</th></tr>${rows}</table>`));
  app.append(t);
}

if (typeof Chart === "undefined") app.append(el("p", {}, "Chart.js could not be loaded (offline?). Summary data is embedded in this file."));
else if (RUNS.length === 1) single(RUNS[0]); else compare(RUNS);
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Write an HTML report for backtest result folders")
    parser.add_argument("run_dirs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, help="default: <RUN_DIR>/report.html, or compare.html in the parent folder")
    args = parser.parse_args()
    if args.out:
        out = args.out
    elif len(args.run_dirs) == 1:
        out = args.run_dirs[0] / "report.html"
    else:
        out = args.run_dirs[0].parent / "compare.html"
    write_report(args.run_dirs, out)
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
