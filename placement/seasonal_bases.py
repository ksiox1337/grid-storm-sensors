"""Seasonal base placement: one 5-base plan per season plus one fixed all-year plan.

Each 100 m piece of line gets a fault risk per season:
  risk = base(line) * wind(season, crosswind) * ice * vegetation * lightning * heat
Crosswind = |sin(angle between line bearing and season's wind direction)|: lines broadside to the
wind carry the most load. Multipliers live in seasonal_config.json (assumptions, editable).
Weight = piece length * risk, then the same max-coverage + p-median optimiser as place_bases.py.
All-year plan uses expected risk (season_share weighted) and reports coverage in every season.
"""
import argparse, importlib.util, json, math, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("pb", os.path.join(HERE, "place_bases.py"))
pb = importlib.util.module_from_spec(spec); spec.loader.exec_module(pb)
COLS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4"]


def pieces(feats, step, lat0, lon0):
    rows = []
    for f in feats:
        pr, xy = f["properties"], pb.to_xy(np.array(f["geometry"]["coordinates"], float), lat0, lon0)
        for a, b in zip(xy[:-1], xy[1:]):
            L = float(np.hypot(*(b - a))); n = max(1, math.ceil(L / step))
            bearing = math.atan2(b[0] - a[0], b[1] - a[1])  # radians from north
            for t in (np.arange(n) + 0.5) / n:
                rows.append((a + t * (b - a), L / n, bearing, pr))
    return rows


def risk(pr, bearing, s, lf):
    v = pr["voltage_kv"] or 110
    single = str(pr.get("cables") or "").split(";")[0] in ("3", "4")
    cross = abs(math.sin(bearing - math.radians(s["wind_from_deg"])))
    r = 1 + (s["wind"] - 1) * (0.3 + 0.7 * cross) if s["wind"] >= 1 else s["wind"]
    r *= s["ice"] * (lf["ice_single_circuit"] if single else 1) if s["ice"] > 1 else 1
    r *= 1 + (s["vegetation"] - 1) * (lf["vegetation_110kv"] if v < 200 else 0.5)
    r *= 1 + (s["lightning"] - 1) * (lf["lightning_380kv"] if v >= 200 else 1)
    r *= s["heat"] if pr["length_km"] >= lf["heat_long_line_km"] else 1
    r *= lf["missing_data"] if "missing" in (pr.get("source_data_quality") or "") and pr.get("circuits") is None else 1
    return r


def plan(cover, dist, w, k, R, starts=()):
    """Greedy start plus any extra starts (e.g. other seasons' plans); keep the best local optimum."""
    w32 = w.astype(np.float32)
    best, best_score = None, None
    for start in [pb.solve(cover, w, k)[0], *starts]:
        ch, _ = pb.refine(dist, w32, R, list(start))
        near = dist[ch].min(0)
        score = (round(float(w[near <= R].sum()), 6), -float(w @ near))
        if best is None or score > best_score:
            best, best_score = ch, score
    return best


def svg(path, title, sub, feats, nodes, pts_ll, w_norm, bases_ll, R, lat0, lon0):
    k = math.cos(math.radians(48.235)); lon_min, lon_max, lat_min, lat_max = 10.25, 11.08, 47.95, 48.52
    W = 900; sx = W / ((lon_max - lon_min) * k); H = int((lat_max - lat_min) * sx)
    P = lambda lon, lat: ((lon - lon_min) * k * sx, (lat_max - lat) * sx)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" style="background:#fafafa;font-family:sans-serif">']
    x0, y0 = P(10.45, 48.38); x1, y1 = P(10.88, 48.09)
    o.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1-x0:.1f}" height="{y1-y0:.1f}" fill="none" stroke="#999" stroke-dasharray="6 4"/>')
    for i, (lon, lat) in enumerate(bases_ll):
        c = pb.to_xy(np.array([lon, lat]), lat0, lon0); t = np.linspace(0, 2 * np.pi, 73)
        ring = pb.to_lonlat(c + R * np.stack([np.cos(t), np.sin(t)], 1), lat0, lon0)
        o.append(f'<polygon points="{" ".join("%.1f,%.1f" % P(*p) for p in ring)}" fill="{COLS[i]}" fill-opacity="0.07" stroke="{COLS[i]}" stroke-width="1.8"/>')
    for f in feats:
        o.append(f'<polyline points="{" ".join("%.1f,%.1f" % P(*p) for p in f["geometry"]["coordinates"])}" fill="none" stroke="#ccc" stroke-width="5"/>')
    for (lon, lat), v in zip(pts_ll, w_norm):  # risk heat: yellow -> red
        x, y = P(lon, lat); g = int(220 * (1 - v))
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{1.6 + 2.4 * v:.1f}" fill="rgb(230,{g},20)"/>')
    for f in nodes:
        if f["properties"]["node_type"] == "abstract_substation":
            x, y = P(*f["geometry"]["coordinates"]); o.append(f'<rect x="{x-3.5:.1f}" y="{y-3.5:.1f}" width="7" height="7" fill="#fff" stroke="#333"/>')
    for i, (lon, lat) in enumerate(bases_ll):
        x, y = P(lon, lat)
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="{COLS[i]}" stroke="#fff" stroke-width="2.5"/>')
        o.append(f'<text x="{x+12:.1f}" y="{y-10:.1f}" font-size="14" font-weight="bold" fill="{COLS[i]}" stroke="#fff" stroke-width="3" paint-order="stroke">B{i+1} ({lat:.4f}, {lon:.4f})</text>')
    o.append(f'<text x="18" y="30" font-size="21" font-weight="bold">{title}</text>')
    for j, line in enumerate(sub):
        o.append(f'<text x="18" y="{54 + 18*j}" font-size="13" fill="#444">{line}</text>')
    o.append(f'<text x="18" y="{H-14}" font-size="12" fill="#666">Line colour/size = fault risk (yellow low, red high). White squares = substations. Dashed = study area. Bases kept on live lines.</text>')
    open(path, "w").write("\n".join(o + ["</svg>"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", default=os.path.join(HERE, "grid_segments.geojson"))
    ap.add_argument("--nodes", default=os.path.join(HERE, "grid_nodes.geojson"))
    ap.add_argument("--config", default=os.path.join(HERE, "seasonal_config.json"))
    ap.add_argument("-k", type=int, default=5); ap.add_argument("-r", type=float, default=15.0)
    ap.add_argument("--step-km", type=float, default=0.1); ap.add_argument("--cand-every", type=int, default=2)
    ap.add_argument("--out-dir", default=os.path.join(HERE, "seasonal_maps"))
    a = ap.parse_args()
    cfg = json.load(open(a.config)); lf = cfg["line_factors"]
    feats = json.load(open(a.segments))["features"]; nodes = json.load(open(a.nodes))["features"]
    allc = np.vstack([f["geometry"]["coordinates"] for f in feats]); lat0, lon0 = allc[:, 1].mean(), allc[:, 0].mean()
    rows = pieces(feats, a.step_km, lat0, lon0)
    pts = np.array([r[0] for r in rows]); L = np.array([r[1] for r in rows])
    seg = np.array([r[3]["segment_id"] for r in rows])
    live = ~np.isin(seg, cfg.get("dead_lines", []))
    cand = pts[live][:: a.cand_every]  # E-VRP: base must sit on a live line
    d = np.sqrt(((cand[:, None] - pts[None]) ** 2).sum(-1)).astype(np.float32)
    cover = d <= a.r
    W = {s: L * np.array([risk(r[3], r[2], p, lf) for r in rows]) for s, p in cfg["seasons"].items()}
    share = cfg["season_share"]
    W["all-year"] = sum(share[s] * W[s] for s in cfg["seasons"])
    os.makedirs(a.out_dir, exist_ok=True)
    pts_ll = pb.to_lonlat(pts, lat0, lon0); summary = {}; plans = {}
    for name, w in W.items():
        ch = plans[name] = plan(cover, d, w, a.k, a.r, starts=list(plans.values()))
        near = d[ch].min(0); cov = near <= a.r
        bases_ll = pb.to_lonlat(cand[ch], lat0, lon0).tolist()
        risk_dist = float((w * near).sum() / w.sum())
        sub = [f"Covered: {L[cov].sum():.1f} of {L.sum():.1f} km line, {100*w[cov].sum()/w.sum():.1f}% of {name} fault risk",
               f"Risk-weighted mean distance line -> nearest base: {risk_dist:.2f} km"]
        if name == "all-year":
            sub.append("Fixed plan, scored per season: " + ", ".join(
                f"{s} {100*W[s][cov].sum()/W[s].sum():.0f}% / {(W[s]*near).sum()/W[s].sum():.2f} km" for s in cfg["seasons"]))
        top = sorted({sid: W[name][seg == sid].sum() / L[seg == sid].sum() for sid in set(seg)}.items(), key=lambda x: -x[1])[:3]
        sub.append("Highest-risk lines (per km): " + ", ".join(f"{s} x{v:.2f}" for s, v in top))
        wn = w / L; wn = (wn - wn.min()) / (np.ptp(wn) or 1)
        svg(os.path.join(a.out_dir, f"bases_{name}.svg"), f"{name.capitalize()}: 5 bases, {a.r:g} km radius",
            sub, feats, nodes, pts_ll, wn, bases_ll, a.r, lat0, lon0)
        summary[name] = {"bases": [{"base": f"B{i+1}", "lat": round(la, 5), "lon": round(lo, 5)} for i, (lo, la) in enumerate(bases_ll)],
                         "risk_weighted_mean_km": round(risk_dist, 2), "top_lines": [t[0] for t in top]}
        print(name, "|", " ".join(f"B{i+1}({la:.4f},{lo:.4f})" for i, (lo, la) in enumerate(bases_ll)), "|", sub[1])
    json.dump(summary, open(os.path.join(a.out_dir, "seasonal_bases.json"), "w"), indent=1)
    # one page with all five maps
    cards = "".join(f'<figure><img src="bases_{n}.svg"><figcaption>{n}</figcaption></figure>' for n in W)
    open(os.path.join(a.out_dir, "index.html"), "w").write(
        "<!doctype html><meta charset=utf-8><title>Seasonal bases</title><style>body{font-family:sans-serif;margin:16px;background:#fff}"
        "div{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:12px}img{width:100%;border:1px solid #ddd}"
        "figcaption{text-align:center;font-weight:bold}</style><h1>Seasonal base placement</h1><div>" + cards + "</div>")
    print("wrote", a.out_dir)


if __name__ == "__main__":
    main()
