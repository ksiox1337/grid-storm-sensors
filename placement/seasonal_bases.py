"""Seasonal base placement: one K-base plan per season plus one fixed all-year plan, for any grid.

Each ~100 m piece of line gets a fault risk per season:
  risk = wind(season, crosswind) x ice x vegetation x lightning x heat x data-quality
All multipliers and season definitions come from common/config.json; voltage classes are relative
to the input grid. Weight = piece length x risk (x a per-line prior if the data has one), then the
max-coverage + p-median optimiser from place_bases.py. Bases sit on live lines only.
"""
import argparse, importlib.util, json, math, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


pb = _load("pb", os.path.join(HERE, "place_bases.py"))
gio = _load("gio", os.path.join(ROOT, "common", "grid_io.py"))
COLS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#9a6324", "#469990", "#808000"]


def risk(seg, bearing, s, lf):
    """Seasonal fault-risk multiplier for one piece of line (1.0 = average)."""
    cross = abs(math.sin(bearing - math.radians(s["wind_from_deg"])))
    r = 1 + (s["wind"] - 1) * (0.3 + 0.7 * cross) if s["wind"] >= 1 else s["wind"]
    if s["ice"] > 1: r *= s["ice"] * (lf["ice_single_circuit"] if seg["single_circuit"] else 1)
    r *= 1 + (s["vegetation"] - 1) * (lf["vegetation_low_voltage"] if seg["v_low"] else lf["vegetation_high_voltage"] if seg["v_high"] else 1)
    r *= 1 + (s["lightning"] - 1) * (lf["lightning_high_voltage"] if seg["v_high"] else 1)
    r *= s["heat"] if seg["length_km"] >= lf["heat_long_line_km"] else 1
    r *= lf["missing_data"] if seg["missing_data"] else 1
    return r


def pieces(segs, step, fwd):
    rows = []
    for sg in segs:
        xy = [fwd(*p) for p in sg["coords"]]
        for a, b in zip(xy[:-1], xy[1:]):
            L = math.dist(a, b); n = max(1, math.ceil(L / step))
            bearing = math.atan2(b[0] - a[0], b[1] - a[1])
            for t in (np.arange(n) + 0.5) / n:
                rows.append(((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), L / n, bearing, sg))
    return rows


def plan(cover, dist, w, k, R, starts=()):
    """Greedy start plus any extra starts (e.g. other seasons' plans); keep the best local optimum."""
    w32 = w.astype(np.float32); best, best_score = None, None
    for start in [pb.solve(cover, w, k)[0], *starts]:
        ch, _ = pb.refine(dist, w32, R, list(start))
        near = dist[ch].min(0); score = (round(float(w[near <= R].sum()), 6), -float(w @ near))
        if best is None or score > best_score: best, best_score = ch, score
    return best


def svg(path, title, sub, segs, nodes, scen, pts_ll, w_norm, bases_ll, R, fwd, inv, sub_words):
    """Map frame computed from the data (+ base circles), so any region renders."""
    ring = lambda c: [inv(c[0] + R * math.cos(t), c[1] + R * math.sin(t)) for t in np.linspace(0, 2 * np.pi, 73)]
    rings = [ring(fwd(*b)) for b in bases_ll]
    allp = [p for s in segs for p in s["coords"]] + [p for r in rings for p in r]
    lon_min, lon_max = min(p[0] for p in allp), max(p[0] for p in allp)
    lat_min, lat_max = min(p[1] for p in allp), max(p[1] for p in allp)
    k = math.cos(math.radians((lat_min + lat_max) / 2)); pad = 0.04 * (lat_max - lat_min)
    lon_min -= pad / k; lon_max += pad / k; lat_min -= pad; lat_max += pad * 3
    W = 900; sx = W / ((lon_max - lon_min) * k); H = int((lat_max - lat_min) * sx) + 40
    P = lambda lon, lat: ((lon - lon_min) * k * sx, (lat_max - lat) * sx)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" style="background:#fafafa;font-family:sans-serif">']
    if scen.get("bounds"):
        w_, s_, e_, n_ = scen["bounds"]; x0, y0 = P(w_, n_); x1, y1 = P(e_, s_)
        o.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1-x0:.1f}" height="{y1-y0:.1f}" fill="none" stroke="#999" stroke-dasharray="6 4"/>')
    for i, rg in enumerate(rings):
        o.append(f'<polygon points="{" ".join("%.1f,%.1f" % P(*p) for p in rg)}" fill="{COLS[i % len(COLS)]}" fill-opacity="0.07" stroke="{COLS[i % len(COLS)]}" stroke-width="1.8"/>')
    for s in segs:
        o.append(f'<polyline points="{" ".join("%.1f,%.1f" % P(*p) for p in s["coords"])}" fill="none" stroke="#ccc" stroke-width="5"/>')
    for (lon, lat), v in zip(pts_ll, w_norm):
        x, y = P(lon, lat); g = int(220 * (1 - v))
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{1.6 + 2.4 * v:.1f}" fill="rgb(230,{g},20)"/>')
    for n in nodes.values():
        if n["coords"] and any(wd in n["type"] for wd in sub_words):
            x, y = P(*n["coords"]); o.append(f'<rect x="{x-3.5:.1f}" y="{y-3.5:.1f}" width="7" height="7" fill="#fff" stroke="#333"/>')
    for i, (lon, lat) in enumerate(bases_ll):
        x, y = P(lon, lat); c = COLS[i % len(COLS)]
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="{c}" stroke="#fff" stroke-width="2.5"/>')
        o.append(f'<text x="{x+12:.1f}" y="{y-10:.1f}" font-size="14" font-weight="bold" fill="{c}" stroke="#fff" stroke-width="3" paint-order="stroke">B{i+1} ({lat:.4f}, {lon:.4f})</text>')
    o.append(f'<text x="18" y="30" font-size="21" font-weight="bold">{title}</text>')
    for j, line in enumerate(sub):
        o.append(f'<text x="18" y="{54 + 18*j}" font-size="13" fill="#444">{line}</text>')
    o.append(f'<text x="18" y="{H-14}" font-size="12" fill="#666">Line colour/size = fault risk (yellow low, red high). White squares = substations. Bases kept on live lines.</text>')
    open(path, "w").write("\n".join(o + ["</svg>"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="folder with the line GeoJSON (+ optional nodes, context, reports)")
    ap.add_argument("--segments"); ap.add_argument("--nodes")
    ap.add_argument("--config", default=os.path.join(ROOT, "common", "config.json"))
    ap.add_argument("-k", type=int, default=5); ap.add_argument("-r", type=float, default=15.0)
    ap.add_argument("--step-km", type=float, default=0.1); ap.add_argument("--cand-every", type=int, default=2)
    ap.add_argument("--out-dir", default=os.path.join(HERE, "maps"))
    a = ap.parse_args()
    cfg = gio.load_config(a.config); lf = cfg["line_factors"]
    segs, nodes, scen = gio.load_all(a.data, cfg, a.segments, a.nodes)
    fwd, inv = gio.projection(segs)
    rows = pieces(segs, a.step_km, fwd)
    pts = np.array([r[0] for r in rows]); L = np.array([r[1] for r in rows])
    seg = np.array([r[3]["id"] for r in rows])
    prior = np.array([r[3]["fault_prob"] or 1.0 for r in rows])
    live = ~np.isin(seg, list(scen["dead_lines"]))
    cand = pts[live][:: a.cand_every]
    d = np.sqrt(((cand[:, None] - pts[None]) ** 2).sum(-1)).astype(np.float32); cover = d <= a.r
    W = {s: L * prior * np.array([risk(r[3], r[2], p, lf) for r in rows]) for s, p in cfg["seasons"].items()}
    share = cfg["season_share"]; W["all-year"] = sum(share[s] * W[s] for s in cfg["seasons"])
    os.makedirs(a.out_dir, exist_ok=True)
    pts_ll = [inv(*p) for p in pts]; summary = {}; plans = {}
    sub_words = cfg["consequence"]["substation_words"]
    for name, w in W.items():
        ch = plans[name] = plan(cover, d, w, a.k, a.r, starts=list(plans.values()))
        near = d[ch].min(0); cov = near <= a.r
        bases_ll = [inv(*cand[i]) for i in ch]
        risk_dist = float((w * near).sum() / w.sum())
        sub = [f"Covered: {L[cov].sum():.1f} of {L.sum():.1f} km line, {100*w[cov].sum()/w.sum():.1f}% of {name} fault risk",
               f"Risk-weighted mean distance line -> nearest base: {risk_dist:.2f} km"]
        if name == "all-year":
            sub.append("Fixed plan, scored per season: " + ", ".join(
                f"{s} {100*W[s][cov].sum()/W[s].sum():.0f}% / {(W[s]*near).sum()/W[s].sum():.2f} km" for s in cfg["seasons"]))
        per_line = {sid: W[name][seg == sid].sum() / L[seg == sid].sum() for sid in set(seg)}
        top = sorted(per_line.items(), key=lambda x: -x[1])[:3]
        sub.append("Highest-risk lines (per km): " + ", ".join(f"{s} x{v:.2f}" for s, v in top))
        wn = w / L; wn = (wn - wn.min()) / (np.ptp(wn) or 1)
        svg(os.path.join(a.out_dir, f"bases_{name}.svg"), f"{name.capitalize()}: {a.k} bases, {a.r:g} km radius",
            sub, segs, nodes, scen, pts_ll, wn, bases_ll, a.r, fwd, inv, sub_words)
        summary[name] = {"bases": [{"base": f"B{i+1}", "lat": round(la, 5), "lon": round(lo, 5)} for i, (lo, la) in enumerate(bases_ll)],
                         "covered_km": round(float(L[cov].sum()), 2), "risk_weighted_mean_km": round(risk_dist, 2), "top_lines": [t[0] for t in top]}
        print(name, "|", " ".join(f"B{i+1}({la:.4f},{lo:.4f})" for i, (lo, la) in enumerate(bases_ll)), "|", sub[0])
    json.dump(summary, open(os.path.join(a.out_dir, "seasonal_bases.json"), "w"), indent=1)
    cards = "".join(f'<figure><img src="bases_{n}.svg"><figcaption>{n}</figcaption></figure>' for n in W)
    open(os.path.join(a.out_dir, "index.html"), "w").write(
        "<!doctype html><meta charset=utf-8><title>Seasonal bases</title><style>body{font-family:sans-serif;margin:16px;background:#fff}"
        "div{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:12px}img{width:100%;border:1px solid #ddd}"
        "figcaption{text-align:center;font-weight:bold}</style><h1>Seasonal base placement</h1><div>" + cards + "</div>")
    print("dead lines (no bases):", ", ".join(sorted(scen["dead_lines"])) or "none", "| wrote", a.out_dir)


if __name__ == "__main__":
    main()
