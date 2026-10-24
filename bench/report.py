"""Turn bench/out/results.json into one self-contained HTML report (inline SVG, hover tooltips, light/dark)."""
import html, importlib.util, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


gio = _load("gio", os.path.join(ROOT, "common", "grid_io.py"))
md = _load("md", os.path.join(ROOT, "response", "model.py"))
E = html.escape
SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)"]
STRAT = {"bayes": ("Bayesian search", "var(--s1)"), "patrol_confirm": ("Patrol + confirm", "var(--s2)"), "patrol": ("Patrol only", "var(--muted)")}


def fmt(v, d=1):
    return "–" if v is None else f"{v:.{d}f}"


# ---------- map small multiple ----------
def map_svg(data, bases, r_km, cfg, title, sub):
    cells, fwd, inv, scen = md.build_cells(data, cfg)
    segs = gio.load_all(data, cfg)[0]
    pri = [c["p"] * c["cons"] for c in cells]; lo, hi = min(pri), max(pri) or 1
    pts = [p for s in segs for p in s["coords"]]
    rings = []
    for b in bases:
        c = fwd(*b); rings.append([inv(c[0] + r_km * math.cos(t / 36 * math.pi), c[1] + r_km * math.sin(t / 36 * math.pi)) for t in range(73)])
    allp = pts + [p for rg in rings for p in rg]
    x0, x1 = min(p[0] for p in allp), max(p[0] for p in allp); y0, y1 = min(p[1] for p in allp), max(p[1] for p in allp)
    k = math.cos(math.radians((y0 + y1) / 2)); W = 340; s = (W - 16) / max((x1 - x0) * k, (y1 - y0)); H = int((y1 - y0) * s) + 16
    P = lambda lon, lat: (8 + (lon - x0) * k * s, 8 + (y1 - lat) * s)
    o = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{E(title)}">']
    for rg in rings:
        o.append(f'<polygon points="{" ".join("%.1f,%.1f" % P(*p) for p in rg)}" class="ring"/>')
    for sg in segs:
        o.append(f'<polyline points="{" ".join("%.1f,%.1f" % P(*p) for p in sg["coords"])}" class="line"/>')
    for c, v in zip(cells, pri):
        t = (v - lo) / ((hi - lo) or 1); x, y = P(*c["ll"])
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{2 + 3.5 * t:.1f}" class="risk" style="opacity:{0.25 + 0.75 * t:.2f}">'
                 f'<title>{E(c["id"])}: P(fault) {c["p"]:.3f}, consequence {c["cons"]:.1f}{" (dead line)" if not c["live"] else ""}</title></circle>')
    for i, b in enumerate(bases):
        x, y = P(*b)
        o.append(f'<g class="hit"><circle cx="{x:.1f}" cy="{y:.1f}" r="12" fill="transparent"/><circle cx="{x:.1f}" cy="{y:.1f}" r="6" class="base"/>'
                 f'<title>Base B{i+1}: {b[1]:.4f}, {b[0]:.4f}</title></g>')
    o.append("</svg>")
    return f'<figure class="card"><figcaption><b>{E(title)}</b><span>{E(sub)}</span></figcaption>{"".join(o)}</figure>'


# ---------- grouped bars ----------
def bars_svg(groups, series, ymax, ylabel, aria):
    """groups: [(label, {series_key: (value, tooltip)})], series: [(key, label, color)]"""
    W, H, L, B, T = 640, 260, 44, 46, 14
    gw = (W - L - 10) / len(groups); bw = min(22, (gw - 18) / len(series))
    y = lambda v: T + (H - T - B) * (1 - v / ymax)
    o = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{E(aria)}">']
    for t in range(0, int(ymax) + 1, max(5, int(ymax / 5) // 5 * 5 or 5)):
        o.append(f'<line x1="{L}" x2="{W-10}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/><text x="{L-6}" y="{y(t)+4:.1f}" class="tick" text-anchor="end">{t}</text>')
    o.append(f'<text x="12" y="{T + (H-T-B)/2:.0f}" class="tick" transform="rotate(-90 12 {T + (H-T-B)/2:.0f})" text-anchor="middle">{E(ylabel)}</text>')
    for gi, (glabel, vals) in enumerate(groups):
        gx = L + gi * gw + (gw - bw * len(series) - 2 * (len(series) - 1)) / 2
        for si, (key, slabel, color) in enumerate(series):
            v, tip = vals.get(key, (None, ""))
            if v is None: continue
            x = gx + si * (bw + 2); top = y(v); h = max(y(0) - top, 0.5)
            o.append(f'<g class="hit"><rect x="{x:.1f}" y="{T}" width="{bw:.1f}" height="{H-T-B}" fill="transparent"/>'
                     f'<path d="M{x:.1f},{y(0):.1f} V{top+min(4,h):.1f} q0,-{min(4,h):.1f} {min(4,bw/2):.1f},-{min(4,h):.1f} H{x+bw-min(4,bw/2):.1f} q{min(4,bw/2):.1f},0 {min(4,bw/2):.1f},{min(4,h):.1f} V{y(0):.1f} Z" style="fill:{color}"/>'
                     f'<title>{E(glabel)} · {E(slabel)}: {v:.1f}% {E(tip)}</title></g>')
            o.append(f'<text x="{x+bw/2:.1f}" y="{top-4:.1f}" class="val" text-anchor="middle">{v:.0f}</text>')
        o.append(f'<text x="{L + gi*gw + gw/2:.1f}" y="{H-B+16}" class="tick" text-anchor="middle">{E(glabel)}</text>')
    o.append(f'<line x1="{L}" x2="{W-10}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="axis"/></svg>')
    return "".join(o)


# ---------- sweep small multiple ----------
def sweep_svg(xs, lines, ymax, xlabel, default_x):
    """lines: [(label, color, [(x, y, tip)])]; x positions are categorical (evenly spaced)."""
    W, H, L, B, T, R = 300, 210, 34, 40, 12, 12
    xpos = lambda i: L + (W - L - R) * (i / (len(xs) - 1) if len(xs) > 1 else 0.5)
    y = lambda v: T + (H - T - B) * (1 - v / ymax)
    o = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="found faults vs {E(xlabel)}">']
    for t in range(0, int(ymax) + 1, 20 if ymax > 60 else 10):
        o.append(f'<line x1="{L}" x2="{W-R}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/><text x="{L-5}" y="{y(t)+4:.1f}" class="tick" text-anchor="end">{t}</text>')
    for i, xv in enumerate(xs):
        o.append(f'<text x="{xpos(i):.1f}" y="{H-B+15}" class="tick{" strong" if xv == default_x else ""}" text-anchor="middle">{xv:g}</text>')
    o.append(f'<text x="{(L+W-R)/2:.0f}" y="{H-8}" class="tick" text-anchor="middle">{E(xlabel)}</text>')
    for label, color, ptsv in lines:
        d = " ".join(f'{"M" if j == 0 else "L"}{xpos(xs.index(x)):.1f},{y(v):.1f}' for j, (x, v, _) in enumerate(ptsv))
        o.append(f'<path d="{d}" class="ln" style="stroke:{color}"/>')
        for x, v, tip in ptsv:
            o.append(f'<g class="hit"><circle cx="{xpos(xs.index(x)):.1f}" cy="{y(v):.1f}" r="10" fill="transparent"/>'
                     f'<circle cx="{xpos(xs.index(x)):.1f}" cy="{y(v):.1f}" r="4" class="dot" style="fill:{color}"/><title>{E(label)} · {E(xlabel)} {x:g}: {v:.1f}% found{E(tip)}</title></g>')
    o.append(f'<line x1="{L}" x2="{W-R}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="axis"/></svg>')
    return "".join(o)


def main():
    out = os.path.join(HERE, "out"); d = json.load(open(os.path.join(out, "results.json")))
    res = d["results"]; maps = d["maps"]; mkeys = list(maps); dflt = d["default"]; cfg = gio.load_config()
    get = lambda m, tag: next(r for r in res if r["map"] == m and r["tag"] == tag)
    color = {m: SERIES[i % len(SERIES)] for i, m in enumerate(mkeys)}
    short = {m: maps[m]["title"].split("(")[-1].rstrip(")").replace("Bavaria, real lines", "Bavaria (real)") for m in mkeys}
    n_runs = len(res) * d["sims"]

    # maps
    map_cards = []
    for m in mkeys:
        pl = d["placements"][f'{m}|{dflt["k"]}|{dflt["r"]}']; r0 = get(m, "default")
        map_cards.append(map_svg(maps[m]["data"], [tuple(b) for b in pl["bases"]], dflt["r"], cfg, maps[m]["title"],
                                 f'{r0["total_km"]:.0f} km of line · {pl["coverage_pct"]:.0f}% within {dflt["r"]:g} km of a base · {r0["faults_per_run"]:.1f} faults per event'))

    # strategies
    def strat_groups(suffix):
        g = []
        for m in mkeys:
            vals = {}
            for key, tag in (("bayes", "default"), ("patrol_confirm", "patrol_confirm"), ("patrol", "patrol")):
                t = tag + suffix
                if any(r["map"] == m and r["tag"] == t for r in res):
                    r = get(m, t); vals[key] = (r["found_pct"], f"· {r['false_calls']:.2f} false crew calls per event")
            g.append((short[m], vals))
        return g
    sr = [(k, v[0], v[1]) for k, v in STRAT.items()]
    ymax_s = max(5, max(v[0] for _, vals in strat_groups("") + strat_groups("_wrong") for v in vals.values()) * 1.15)
    ymax_s = math.ceil(ymax_s / 5) * 5
    strat_right = bars_svg(strat_groups(""), sr, ymax_s, "% of faults found", "faults found by strategy, model right")
    strat_wrong = bars_svg(strat_groups("_wrong"), sr, ymax_s, "% of faults found", "faults found by strategy, model wrong")

    # sweeps
    sweep_cards = []
    for param, xs in d["sweeps"].items():
        lines = []
        for m in mkeys:
            pts = []
            for x in xs:
                r = get(m, "default" if x == dflt[param] else f"{param}={x}")
                pts.append((x, r["found_pct"], f" · {r['sorties_per_run']:.0f} sorties, {r['km_inspected']:.0f} km inspected, coverage {r['coverage_pct']:.0f}%"))
            lines.append((short[m], color[m], pts))
        ymax = max(10, math.ceil(max(v for _, _, p in lines for _, v, _ in p) / 10) * 10)
        lab = {"k": "drones K", "r": "radius R, km", "charge_h": "charge time, h", "pd": "detection probability", "window_h": "time window, h"}[param]
        sweep_cards.append(f'<figure class="card small"><figcaption><b>{E(lab.capitalize())}</b><span>default {dflt[param]:g} (bold)</span></figcaption>{sweep_svg(xs, lines, ymax, lab, dflt[param])}</figure>')
    legend = "".join(f'<span class="key"><i style="background:{color[m]}"></i>{E(short[m])}</span>' for m in mkeys)

    # decision quality table (also the table view of the charts)
    rows = []
    for m in mkeys:
        r = get(m, "default"); w = get(m, "default_wrong"); pc = get(m, "patrol_confirm")
        rows.append(f'<tr><th>{E(maps[m]["title"])}</th><td>{r["total_km"]:.0f}</td><td>{r["coverage_pct"]:.0f}%</td><td>{r["km_inspected"]:.0f}</td>'
                    f'<td>{r["found_pct"]:.1f}%</td><td>{pc["found_pct"]:.1f}%</td><td>{w["found_pct"]:.1f}%</td>'
                    f'<td>{fmt(r["repair_precision"], 0)}{"%" if r["repair_precision"] is not None else ""}</td><td>{fmt(r["inspect_precision"], 0)}{"%" if r["inspect_precision"] is not None else ""}</td><td>{r["false_calls"]:.2f}</td></tr>')
    sweep_rows = []
    for param, xs in d["sweeps"].items():
        for x in xs:
            cells = "".join(f'<td>{get(m, "default" if x == dflt[param] else f"{param}={x}")["found_pct"]:.1f}%</td>' for m in mkeys)
            sweep_rows.append(f'<tr><th>{param} = {x:g}{" (default)" if x == dflt[param] else ""}</th>{cells}</tr>')

    # headline numbers, computed
    def avg(tag): return sum(get(m, tag)["found_pct"] for m in mkeys) / len(mkeys)
    base_avg = avg("default"); fast = avg("charge_h=0.25"); k8 = avg("k=8"); pd9 = avg("pd=0.9"); w12 = avg("window_h=12.0")
    all_rep = [r for r in res if r["repair_precision"] is not None]; all_ins = [r for r in res if r["inspect_precision"] is not None]
    false_max = max(r["false_calls"] for r in res)
    wins = [short[m] for m in mkeys if get(m, "default")["found_pct"] > get(m, "patrol_confirm")["found_pct"]]
    losses = [short[m] for m in mkeys if get(m, "default")["found_pct"] <= get(m, "patrol_confirm")["found_pct"]]

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Drone Response Benchmark</title><style>
:root{{color-scheme:light;--bg:#fcfcfb;--card:#ffffff;--border:#e4e3df;--t1:#0b0b0b;--t2:#52514e;--muted:#a3a29c;--grid:#ecebe7;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--risk:#e34948;--base:#0b0b0b;--ring:#2a78d6}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;--bg:#141413;--card:#1a1a19;--border:#2e2e2b;--t1:#f0efec;--t2:#c3c2b7;--muted:#6f6e69;--grid:#262624;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--risk:#e66767;--base:#f0efec;--ring:#3987e5}}}}
:root[data-theme="dark"]{{color-scheme:dark;--bg:#141413;--card:#1a1a19;--border:#2e2e2b;--t1:#f0efec;--t2:#c3c2b7;--muted:#6f6e69;--grid:#262624;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--risk:#e66767;--base:#f0efec;--ring:#3987e5}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--t1);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px 16px 60px}}h1{{font-size:28px;margin:0 0 6px}}h2{{font-size:20px;margin:40px 0 6px}}
p.lead,.note{{color:var(--t2);max-width:860px;margin:4px 0 14px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:20px 0}}
.tile{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px}}.tile b{{display:block;font-size:26px}}.tile span{{color:var(--t2);font-size:13px}}
.grid{{display:grid;gap:12px}}.g3{{grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr))}}.g2{{grid-template-columns:repeat(auto-fit,minmax(min(460px,100%),1fr))}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px;margin:0}}
figcaption{{display:flex;flex-direction:column;margin-bottom:6px}}figcaption span{{color:var(--t2);font-size:13px}}
svg{{width:100%;height:auto;display:block}}svg text{{font:11px system-ui,sans-serif}}
.tick{{fill:var(--t2)}}.tick.strong{{font-weight:700;fill:var(--t1)}}.val{{fill:var(--t1);font-weight:600}}
.grid line,line.grid{{stroke:var(--grid);stroke-width:1}}line.axis{{stroke:var(--muted);stroke-width:1}}
.ln{{fill:none;stroke-width:2}}.dot{{stroke:var(--card);stroke-width:2}}
.line{{fill:none;stroke:var(--muted);stroke-width:2;stroke-linecap:round}}.risk{{fill:var(--risk)}}.ring{{fill:var(--ring);fill-opacity:.06;stroke:var(--ring);stroke-width:1.2}}
.base{{fill:var(--base);stroke:var(--card);stroke-width:2}}.hit:hover .dot,.hit:hover path,.hit:hover .base{{filter:brightness(1.15)}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;margin:8px 0;color:var(--t2);font-size:13px}}.key i{{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}}
table{{border-collapse:collapse;width:100%;font-size:13px;background:var(--card)}}th,td{{border-bottom:1px solid var(--border);padding:7px 8px;text-align:right}}th{{text-align:left;font-weight:600}}
thead th{{color:var(--t2);font-weight:600;text-align:right}}thead th:first-child{{text-align:left}}.scroll{{overflow-x:auto;border:1px solid var(--border);border-radius:10px}}
ul.find li{{margin:6px 0;max-width:900px}}details{{margin-top:12px}}summary{{cursor:pointer;color:var(--t2)}}
</style></head><body><main>
<h1>Drone response benchmark</h1>
<p class="lead">The full system (base placement → Bayesian search with flight and charging limits → inspect/repair decisions) on {len(mkeys)} different grids,
{len(res)} parameter settings and {d["sims"]} simulated events each. Faults and drone readings are <b>simulated</b>: each event draws hypothetical faults and the
system has to find them. Hover any mark for exact values; the tables at the bottom hold every number.</p>
<div class="tiles">
<div class="tile"><b>{n_runs:,}</b><span>simulated events</span></div>
<div class="tile"><b>{sum(r["repair_precision"] for r in all_rep)/len(all_rep):.0f}%</b><span>of repair calls hit a real fault (mean over settings)</span></div>
<div class="tile"><b>{base_avg:.0f}% → {fast:.0f}%</b><span>faults found in 4 h: 3 h charge → 15 min battery swap (mean of maps)</span></div>
<div class="tile"><b>≤ {false_max:.2f}</b><span>false crew calls per event, worst setting</span></div>
</div>

<h2>1. The five grids and where the system puts 5 bases</h2>
<p class="note">Grey = power lines, red dots = starting fault risk × consequence (bigger and darker = higher), black dots = bases with their {dflt["r"]:g} km circles.
The same code and config ran on all five; only the input folder changed.</p>
<div class="grid g3">{"".join(map_cards)}</div>

<h2>2. Is the Bayesian search worth it?</h2>
<p class="note">Share of faults that reach an inspection or repair decision within the 4 h window. <b>Patrol only</b> sweeps unseen line and never returns;
<b>patrol + confirm</b> uses no probabilities but goes back to any cell with a positive reading. Left: faults really follow the risk model. Right: the risk model is wrong
(faults spread uniformly per km).</p>
<div class="legend">{"".join(f'<span class="key"><i style="background:{c}"></i>{E(l)}</span>' for _, l, c in sr)}</div>
<div class="grid g2"><figure class="card"><figcaption><b>Risk model right</b></figcaption>{strat_right}</figure>
<figure class="card"><figcaption><b>Risk model wrong</b></figcaption>{strat_wrong}</figure></div>

<h2>3. Which parameter matters?</h2>
<p class="note">One parameter changed at a time from the default (K = {dflt["k"]}, R = {dflt["r"]:g} km, charge {dflt["charge_h"]:g} h, detection {dflt["pd"]}, window {dflt["window_h"]:g} h).
Y axis: % of faults found. Each line is one grid.</p>
<div class="legend">{legend}</div>
<div class="grid g3">{"".join(sweep_cards)}</div>

<h2>4. Findings</h2>
<ul class="find">
<li><b>Decisions are trustworthy on every grid.</b> Over all settings, {sum(r["repair_precision"] for r in all_rep)/len(all_rep):.0f}% of repair calls and
{sum(r["inspect_precision"] for r in all_ins)/len(all_ins):.0f}% of inspection calls were real faults; at most {false_max:.2f} false crew calls per event.</li>
<li><b>Charging is the bottleneck, not the logic.</b> Cutting the charge from 3 h to 15 min raises the mean from {base_avg:.0f}% to {fast:.0f}% of faults found in 4 h;
a longer window does the same thing more slowly ({w12:.0f}% at 12 h). More drones help less ({k8:.0f}% with 8) and better sensors even less ({pd9:.0f}% at 0.9 detection).</li>
<li><b>The radius barely matters once the grid is covered.</b> R changes the result only where coverage drops (mesh and corridor at small R or K = 3).</li>
<li><b>Bayesian search beats both patrols on {", ".join(wins) or "no grid"}</b>, and still does when the risk model is wrong: it revisits evidence and focuses scarce sorties.</li>
<li><b>Where it loses: {", ".join(losses) or "nowhere"}.</b> On a small dense grid the drones can see most of the line in one wave, so an even sweep with confirmation finds more than
focusing on the riskiest cells. Next improvement: add an exploration bonus for unseen line when the total line is small compared with what the fleet can fly.</li>
</ul>

<h2>5. All numbers (table view)</h2>
<div class="scroll"><table><thead><tr><th>Grid (default setting)</th><th>km</th><th>coverage</th><th>km inspected</th><th>found: Bayes</th><th>found: patrol + confirm</th>
<th>found: Bayes, model wrong</th><th>repair precision</th><th>inspection precision</th><th>false calls / event</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<details><summary>Parameter sweeps: % of faults found per grid</summary><div class="scroll"><table><thead><tr><th>Setting</th>{"".join(f"<th>{E(short[m])}</th>" for m in mkeys)}</tr></thead>
<tbody>{"".join(sweep_rows)}</tbody></table></div></details>
<p class="note">"–" = no repair call was made in that setting (the evidence never reached the repair threshold). Generated by <code>bench/benchmark.py</code> and <code>bench/report.py</code>. Assumptions (fault rates, sensor reliability, costs) are in <code>common/config.json</code>;
the four non-Bavarian grids are generated and the Bavarian lines are © OpenStreetMap contributors (ODbL). All storms, faults and readings are fictional.</p>
</main></body></html>"""
    path = os.path.join(out, "report.html"); open(path, "w").write(page); print(path, len(page))


if __name__ == "__main__":
    main()
