"""Benchmark: the full system on several maps and parameter settings, N Monte Carlo runs each.

Maps: the challenge package (if given) + generated grids of very different shape, region and voltage,
all written as plain GeoJSON/JSON and read through the same generic loader (common/grid_io.py).
Per map: one-at-a-time sweeps of drones K, radius R, charge time, sensor detection, time window;
plus two controls at the default setting:
  - strategy: Bayesian search (P x consequence) vs a probability-blind patrol (unvisited line first),
    and vs patrol + confirm (no probabilities, but revisits any cell with a positive reading)
  - truth: faults drawn from the model's prior ("model right") vs uniform per km ("model wrong")
Readings are simulated (hypothetical). Writes bench/out/results.json, charts and report.html.
"""
import argparse, base64, copy, io, json, math, os, random, sys, time
from multiprocessing import Pool
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import importlib.util


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


gio = _load("gio", os.path.join(ROOT, "common", "grid_io.py"))
sb = _load("sb", os.path.join(ROOT, "placement", "seasonal_bases.py"))
md = _load("md", os.path.join(ROOT, "response", "model.py"))

DEFAULT = dict(k=5, r=15.0, charge_h=3.0, pd=0.75, window_h=4.0)
SWEEPS = dict(k=[3, 5, 8], r=[10.0, 15.0, 20.0], charge_h=[3.0, 1.0, 0.25], pd=[0.6, 0.75, 0.9], window_h=[4.0, 8.0, 12.0])
LABELS = dict(k="drones K", r="radius R, km", charge_h="charge time, h", pd="detection probability", window_h="time window, h")


# ---------- generated maps ----------
def _line(a, b, rng, wiggle, n=10):
    pts = [a]
    for t in range(1, n):
        u = t / n
        pts.append((a[0] + u * (b[0] - a[0]) + rng.uniform(-wiggle, wiggle), a[1] + u * (b[1] - a[1]) + rng.uniform(-wiggle, wiggle) * 0.6))
    return pts + [b]


def _write(folder, lines, nodes=None, event=None, reports=None, window=None):
    os.makedirs(folder, exist_ok=True)
    json.dump({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": c}, "properties": p} for c, p in lines]},
        open(os.path.join(folder, "lines.geojson"), "w"))
    if nodes:
        json.dump({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": c}, "properties": p} for c, p in nodes]},
            open(os.path.join(folder, "nodes.geojson"), "w"))
    feats = []
    if event: feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [event]}, "properties": {"kind": "storm footprint"}})
    for r in reports or []:
        if "area" in r:
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [r.pop("area")]}, "properties": {"information_id": r["information_id"]}})
    if feats: json.dump({"type": "FeatureCollection", "features": feats}, open(os.path.join(folder, "context.geojson"), "w"))
    if reports or window: json.dump({"reports": reports or [], "window_hours": window or 4}, open(os.path.join(folder, "event.json"), "w"))


def box(lon, lat, dlon, dlat):
    return [[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat], [lon - dlon, lat + dlat], [lon - dlon, lat - dlat]]


def gen_maps(base):
    maps = {}
    # 1) radial distribution feeders, 20/10 kV, around Lyon: a tree from one substation
    rng = random.Random(11); lon0, lat0 = 4.84, 45.76; lines, nodes = [], [((lon0, lat0), {"node_id": "S1", "node_type": "substation"})]
    frontier = [("S1", (lon0, lat0), 0)]; n = 0
    while frontier and n < 26:
        nid, p, depth = frontier.pop(0)
        for _ in range(rng.choice([2, 3]) if depth < 2 else rng.choice([0, 1, 2])):
            ang = rng.uniform(0, 2 * math.pi); L = rng.uniform(0.04, 0.09)
            q = (p[0] + L * math.cos(ang) / 0.7, p[1] + L * math.sin(ang)); n += 1; qid = f"J{n}"
            lines.append((_line(p, q, rng, 0.004, 6), {"id": f"F{n:02d}", "from": nid, "to": qid, "voltage": "20000" if depth < 2 else "10000", "circuits": 1}))
            nodes.append((q, {"node_id": qid, "node_type": "junction"})); frontier.append((qid, q, depth + 1))
    _write(os.path.join(base, "radial_lyon"), lines, nodes, event=box(lon0 + 0.05, lat0, 0.12, 0.08),
           reports=[{"information_id": "R1", "information_type": "vegetation report", "confidence": "medium", "area": box(lon0 - 0.05, lat0 + 0.03, 0.05, 0.04)}])
    maps["radial_lyon"] = "Radial distribution, 20/10 kV (Lyon)"
    # 2) meshed transmission 400/220 kV, Texas: 4x4 hubs, 15 km apart
    rng = random.Random(12); lon0, lat0 = -97.0, 31.0; dl = 15 / 95.5; dt = 15 / 111.2; hubs = {}
    for i in range(4):
        for j in range(4): hubs[(i, j)] = (lon0 + i * dl + rng.uniform(-.02, .02), lat0 + j * dt + rng.uniform(-.02, .02))
    lines = []; nodes = [(p, {"node_id": f"H{i}{j}", "node_type": "substation"}) for (i, j), p in hubs.items()]
    for (i, j), p in hubs.items():
        for di, dj in ((1, 0), (0, 1)):
            if (i + di, j + dj) in hubs and rng.random() < 0.85:
                lines.append((_line(p, hubs[(i + di, j + dj)], rng, 0.01), {"segment_id": f"T{i}{j}{'E' if di else 'N'}",
                              "from_node": f"H{i}{j}", "to_node": f"H{i+di}{j+dj}", "voltage_kv": rng.choice([400, 220, 220]), "circuits": 2}))
    _write(os.path.join(base, "mesh_texas"), lines, nodes, event=box(lon0 + 1.5 * dl, lat0 + 1.5 * dt, 1.2 * dl, 2.2 * dt),
           reports=[{"information_id": "R1", "information_type": "protection trip summary", "confidence": "high", "area": box(lon0 + 2 * dl, lat0 + dt, 0.6 * dl, 0.6 * dt)},
                    {"information_id": "R2", "information_type": "conductor motion", "confidence": "low", "area": box(lon0 + dl, lat0 + 2.5 * dt, 0.5 * dl, 0.5 * dt)}])
    maps["mesh_texas"] = "Meshed transmission, 400/220 kV (Texas)"
    # 3) long mountain corridor 132 kV with spurs, Norway; one spur reported tripped (dead)
    rng = random.Random(13); lon0, lat0 = 8.6, 61.2; lines = []; prev = (lon0, lat0)
    for s in range(8):
        nxt = (prev[0] + 0.30 + rng.uniform(-.05, .05), prev[1] + rng.uniform(-0.04, 0.06))
        lines.append((_line(prev, nxt, rng, 0.01), {"name": f"C{s+1}", "voltage": "132000", "circuits": 2})); prev = nxt
        if s % 2 == 1:
            spur = (nxt[0] + rng.uniform(-.05, .05), nxt[1] + rng.choice([-1, 1]) * rng.uniform(0.06, 0.12))
            lines.append((_line(nxt, spur, rng, 0.005, 6), {"name": f"S{s+1}", "voltage": "66000", "circuits": 1}))
    _write(os.path.join(base, "corridor_norway"), lines, event=box(lon0 + 1.2, lat0 + 0.05, 0.7, 0.2),
           reports=[{"information_id": "R1", "information_type": "operator log", "confidence": 0.9, "description": "Breaker for S4 tripped; S4 is out of service."}], window=4)
    maps["corridor_norway"] = "Long corridor + spurs, 132/66 kV (Norway)"
    # 4) dense urban cluster 66/22 kV, Tokyo: many short lines in a 15 km box
    rng = random.Random(14); lon0, lat0 = 139.70, 35.68; pts = [(lon0 + rng.uniform(-.08, .08), lat0 + rng.uniform(-.065, .065)) for _ in range(18)]
    lines = []
    for i, p in enumerate(pts):
        near = sorted(pts, key=lambda q: (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2)[1:3]
        for q in near:
            if p < q: lines.append((_line(p, q, rng, 0.002, 4), {"id": f"U{len(lines)+1:02d}", "voltage": rng.choice(["66000", "22000", "22000"]), "circuits": rng.choice([1, 2])}))
    _write(os.path.join(base, "urban_tokyo"), lines, event=box(lon0, lat0, 0.1, 0.08),
           reports=[{"information_id": "R1", "information_type": "flash report", "confidence": "low to medium", "area": box(lon0 + 0.03, lat0 - 0.02, 0.03, 0.03)}])
    maps["urban_tokyo"] = "Dense urban cluster, 66/22 kV (Tokyo)"
    return maps


# ---------- placement and simulation ----------
def place(data, cfg, k, r):
    segs, nodes, scen = gio.load_all(data, cfg)
    fwd, inv = gio.projection(segs)
    rows = sb.pieces(segs, 0.2, fwd)
    pts = np.array([q[0] for q in rows]); L = np.array([q[1] for q in rows]); seg = np.array([q[3]["id"] for q in rows])
    live = ~np.isin(seg, list(scen["dead_lines"]))
    cand = pts[live][::2]
    d = np.sqrt(((cand[:, None] - pts[None]) ** 2).sum(-1)).astype(np.float32)
    w = sum(cfg["season_share"][s] * L * np.array([sb.risk(q[3], q[2], p, cfg["line_factors"]) for q in rows]) for s, p in cfg["seasons"].items())
    ch = sb.plan(d <= r, d, w, k, r)
    cov = float(L[d[ch].min(0) <= r].sum() / L.sum())
    return [inv(*cand[i]) for i in ch], cov


_CACHE = {}


def auc(scores, labels):
    """ROC AUC (Mann-Whitney): chance a faulty cell is ranked above a healthy one. Ties count half."""
    s = np.asarray(scores, float); y = np.asarray(labels, bool)
    npos, nneg = int(y.sum()), int((~y).sum())
    if not npos or not nneg: return None
    order = np.argsort(s, kind="mergesort"); ranks = np.empty(len(s)); sorted_s = s[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and sorted_s[j + 1] == sorted_s[i]: j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1; i = j + 1
    return float((ranks[y].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def run_job(job):
    data, cfg, bases_ll, p, strategy, truth_mode, n_sims = job
    key = (data, json.dumps(cfg, sort_keys=True))
    if key not in _CACHE:
        cells, fwd, inv, scen = md.build_cells(data, cfg)
        _CACHE[key] = (cells, fwd)
    cells0, fwd = _CACHE[key]
    bases = [fwd(*b) for b in bases_ll]
    total_km = sum(c["len"] for c in cells0); mean_p = sum(c["p0"] for c in cells0) / total_km
    def patrol_confirm(c):  # fair baseline: no probabilities, but revisit any cell with a positive reading
        if any(o["reading"] in ("strong", "weak") for o in c["obs"]): return 2.0
        return 0.0 if c["obs"] else 1.0
    value = {"bayes": md.default_value, "patrol": lambda c: 0.0 if c["obs"] else 1.0, "patrol_confirm": patrol_confirm}[strategy]
    s_prior, s_post, labels, per_event = [], [], [], []
    agg = dict(faults=0, found=0, cons_total=0.0, cons_found=0.0, rep=0, rep_ok=0, insp=0, insp_ok=0, km=0.0, sorties=0)
    for sd in range(n_sims):
        cells = copy.deepcopy(cells0); rng = random.Random(10_000 + sd)
        if truth_mode == "prior":
            truth = {c["id"] for c in cells if rng.random() < c["p0"]}
        else:  # model wrong: faults uniform per km, same expected count
            truth = {c["id"] for c in cells if rng.random() < 1 - math.exp(-mean_p * c["len"])}
        tr, drones = md.simulate(cells, bases, cfg, p["window_h"], sd, log=lambda s: None, value=value, truth=truth)
        byid = {c["id"]: c for c in cells}
        before = agg["cons_found"]
        agg["faults"] += len(tr); agg["sorties"] += sum(dd["sorties"] for dd in drones)
        agg["km"] += sum(c["len"] for c in cells if c["obs"])
        for f in tr:
            c = byid[f]; agg["cons_total"] += c["cons"]
            if c["status"] in ("repair", "physical inspection"): agg["found"] += 1; agg["cons_found"] += c["cons"]
        per_event.append(agg["cons_found"] - before)
        for c in cells:
            s_prior.append(c["p0"]); s_post.append(c["p"]); labels.append(c["id"] in tr)
            if c["status"] == "repair": agg["rep"] += 1; agg["rep_ok"] += c["id"] in tr
            if c["status"] == "physical inspection": agg["insp"] += 1; agg["insp_ok"] += c["id"] in tr
    n = n_sims
    return dict(found_pct=100 * agg["found"] / max(agg["faults"], 1), cons_found_pct=100 * agg["cons_found"] / max(agg["cons_total"], 1e-9),
                repair_precision=100 * agg["rep_ok"] / agg["rep"] if agg["rep"] else None,
                inspect_precision=100 * agg["insp_ok"] / agg["insp"] if agg["insp"] else None,
                crew_calls=(agg["rep"] + agg["insp"]) / n, false_calls=(agg["rep"] - agg["rep_ok"] + agg["insp"] - agg["insp_ok"]) / n,
                auc_prior=auc(s_prior, labels), auc_post=auc(s_post, labels),
                cons_found_per_event=agg["cons_found"] / n, cons_found_se=float(np.std(per_event, ddof=1) / math.sqrt(n)) if n > 1 else 0.0, found_per_event=agg["found"] / n,
                km_inspected=agg["km"] / n, total_km=total_km, faults_per_run=agg["faults"] / n, sorties_per_run=agg["sorties"] / n)


def cfg_for(base_cfg, p):
    cfg = copy.deepcopy(base_cfg); cfg["limits"]["charge_h"] = p["charge_h"]; cfg["sensors"]["detect_prob"] = p["pd"]
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", help="challenge package folder (optional, added as a map)")
    ap.add_argument("--sims", type=int, default=200)
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    base_cfg = gio.load_config()
    maps = {}
    if a.package: maps["bavaria"] = ("Challenge package (Bavaria, real lines)", a.package)
    for k, title in gen_maps(os.path.join(a.out, "maps_data")).items():
        maps[k] = (title, os.path.join(a.out, "maps_data", k))
    t0 = time.time()
    # placements needed: (map, K, R)
    need = {(m, DEFAULT["k"], DEFAULT["r"]) for m in maps} | {(m, k, DEFAULT["r"]) for m in maps for k in SWEEPS["k"]} | \
           {(m, DEFAULT["k"], r) for m in maps for r in SWEEPS["r"]}
    placements = {}
    for (m, k, r) in sorted(need):
        placements[(m, k, r)] = place(maps[m][1], base_cfg, k, r)
    print(f"placements: {len(placements)} in {time.time()-t0:.0f}s", flush=True)
    jobs, meta = [], []
    for m in maps:
        settings = [("default", None, DEFAULT[x] if False else None)]
        variants = [dict(DEFAULT, tag="default", strategy="bayes", truth="prior"),
                    dict(DEFAULT, tag="patrol", strategy="patrol", truth="prior"),
                    dict(DEFAULT, tag="patrol_confirm", strategy="patrol_confirm", truth="prior"),
                    dict(DEFAULT, tag="default_wrong", strategy="bayes", truth="uniform"),
                    dict(DEFAULT, tag="patrol_confirm_wrong", strategy="patrol_confirm", truth="uniform"),
                    dict(DEFAULT, tag="patrol_wrong", strategy="patrol", truth="uniform")]
        for param, vals in SWEEPS.items():
            for v in vals:
                if v != DEFAULT[param]: variants.append(dict(DEFAULT, **{param: v}, tag=f"{param}={v}", strategy="bayes", truth="prior"))
        for p in variants:
            bases, cov = placements[(m, p["k"], p["r"])]
            jobs.append((maps[m][1], cfg_for(base_cfg, p), bases, p, p["strategy"], p["truth"], a.sims))
            meta.append((m, p, cov))
    print(f"{len(jobs)} settings x {a.sims} simulations = {len(jobs)*a.sims} runs", flush=True)
    with Pool() as pool:
        res = pool.map(run_job, jobs, chunksize=1)
    results = [dict(map=m, title=maps[m][0], coverage_pct=100 * cov, **{k: v for k, v in p.items()}, **r) for (m, p, cov), r in zip(meta, res)]
    json.dump(dict(sims=a.sims, default=DEFAULT, sweeps=SWEEPS, results=results,
                   placements={f"{m}|{k}|{r}": dict(bases=b, coverage_pct=100 * c) for (m, k, r), (b, c) in placements.items()},
                   maps={m: dict(title=t, data=d) for m, (t, d) in maps.items()}), open(os.path.join(a.out, "results.json"), "w"), indent=1)
    print(f"done in {time.time()-t0:.0f}s -> {a.out}/results.json", flush=True)


if __name__ == "__main__":
    main()
