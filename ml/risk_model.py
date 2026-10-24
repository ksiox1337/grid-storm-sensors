"""Learned background fault risk: replaces the hand-set prior once a fault history exists.

Training data: one or more (data folder, history CSV) pairs. History CSV columns:
    segment_id, events, faults      (faults observed on that line over `events` past storms)
Model: Poisson regression of faults per km per event on static line features (response/model.py
FEATURES: relative voltage, single circuit, data gaps, line length, crosswind per season, radial feed,
endpoint degree). Two learners, chosen by leave-one-grid-out deviance: a Poisson GLM (robust on small
data) and gradient boosting with Poisson loss (captures interactions). Live data (event area,
reports) is NOT in the model; the response model applies it on top, as before.

  train:  python3 -I ml/risk_model.py train --pair DATA HISTORY.csv [--pair ...] --out ml/risk_model.joblib
          then set "ml": {"risk_model": "ml/risk_model.joblib"} in common/config.json
  demo:   python3 -I ml/risk_model.py demo --maps DIR [DIR ...]
          synthetic history from a hidden "true" risk, leave-one-grid-out AUC: hand prior vs learned vs truth
"""
import argparse, copy, csv, importlib.util, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("md", os.path.join(ROOT, "response", "model.py"))
md = importlib.util.module_from_spec(spec); spec.loader.exec_module(md)
sys.path.insert(0, os.path.join(ROOT, "bench")); from benchmark import auc  # noqa: E402


def cells_for(data, cfg):
    c = copy.deepcopy(cfg); c.setdefault("ml", {})["risk_model"] = None
    return md.build_cells(data, c)[0]


def segment_table(cells):
    """Length-weighted segment features (history is usually recorded per line, not per km)."""
    by = {}
    for c in cells: by.setdefault(c["seg"], []).append(c)
    rows = {}
    for sid, cs in by.items():
        L = sum(c["len"] for c in cs)
        rows[sid] = (np.average([c["feat"] for c in cs], axis=0, weights=[c["len"] for c in cs]), L)
    return rows


def fit(X, y_rate, w, kind):
    from sklearn.linear_model import PoissonRegressor
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), PoissonRegressor(alpha=1e-2, max_iter=1000)) if kind == "glm" else \
        HistGradientBoostingRegressor(loss="poisson", max_iter=200, learning_rate=0.05, min_samples_leaf=10, max_depth=3, random_state=0)
    if kind == "glm": m.fit(X, y_rate, poissonregressor__sample_weight=w)
    else: m.fit(X, y_rate, sample_weight=w)
    return m


def deviance(m, X, y_rate, w):
    mu = np.clip(m.predict(X), 1e-9, None); y = y_rate
    return float(np.sum(w * 2 * (np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0) - (y - mu))))


def train(tables, kinds=("glm", "gbm")):
    """tables: list of (X, faults, exposure_km_events) per grid. Pick learner by leave-one-grid-out deviance."""
    score = {}
    for kind in kinds:
        if len(tables) < 2: score[kind] = 0; continue
        dev = 0.0
        for i in range(len(tables)):
            tr = [t for j, t in enumerate(tables) if j != i]
            X = np.vstack([t[0] for t in tr]); f = np.concatenate([t[1] for t in tr]); e = np.concatenate([t[2] for t in tr])
            m = fit(X, f / e, e, kind); dev += deviance(m, tables[i][0], tables[i][1] / tables[i][2], tables[i][2])
        score[kind] = dev
    best = min(score, key=score.get)
    X = np.vstack([t[0] for t in tables]); f = np.concatenate([t[1] for t in tables]); e = np.concatenate([t[2] for t in tables])
    return fit(X, f / e, e, best), best, score


# ---------------- demo with a hidden "true" risk ----------------
TRUE_W = dict(v_low=1.2, v_high=-0.6, single_circuit=0.5, log_line_km=0.3, cross_autumn=0.9, cross_winter=0.4, radial=0.3)


def true_rate(cells, rng):
    """Hidden truth, deliberately different from the hand-set config prior, + unobserved per-line noise."""
    noise = {}
    out = []
    for c in cells:
        u = noise.setdefault(c["seg"], rng.normal(0, 0.5))
        z = sum(TRUE_W.get(k, 0) * v for k, v in zip(md.FEATURES, c["feat"]))
        out.append(0.01 * math.exp(z + u))
    return np.array(out)


def end_to_end(g, model, cfg, sims, seed):
    """Run the full response model on the unseen grid with faults from the hidden truth:
    hand prior vs learned prior. Returns % of faults sent to inspection/repair."""
    import tempfile, joblib, random
    from benchmark import place
    bases_ll, _ = place(g["data"], cfg, 5, 15.0)
    tmp = os.path.join(tempfile.mkdtemp(), "m.joblib"); joblib.dump(model, tmp)
    out = []
    for path in (None, tmp):
        c = copy.deepcopy(cfg); c["ml"]["risk_model"] = path
        cells0, fwd, _, scen = md.build_cells(g["data"], c); bases = [fwd(*b) for b in bases_ll]
        rng = np.random.default_rng(seed); found = total = calls = good = 0; mean_p = []
        for sd in range(sims):
            cells = copy.deepcopy(cells0)
            truth = {cc["id"] for cc, p in zip(cells, 1 - np.exp(-g["rate"] * g["L"] * np.array([cc["exposure"] for cc in cells]))) if rng.random() < p}
            md.simulate(cells, bases, c, scen["window_h"], sd, log=lambda s: None, truth=truth)
            total += len(truth); found += sum(1 for cc in cells if cc["id"] in truth and cc["status"] in ("repair", "physical inspection"))
            for cc in cells:
                if cc["status"] in ("repair", "physical inspection"): calls += 1; good += cc["id"] in truth
        true_mean = float(np.mean(1 - np.exp(-g["rate"] * g["L"] * np.array([cc["exposure"] for cc in cells0]))))
        out.append((100 * found / max(total, 1), 100 * good / max(calls, 1), float(np.mean([cc["p0"] for cc in cells0])) / true_mean))
    return out


def demo(maps, cfg, past_storms=25, test_storms=300, seed=0, e2e_sims=0):
    rng = np.random.default_rng(seed)
    grids = []
    for d in maps:
        cells = cells_for(d, cfg); rate = true_rate(cells, rng)
        L = np.array([c["len"] for c in cells]); seg = [c["seg"] for c in cells]
        hist = rng.poisson(np.outer(np.ones(past_storms), rate * L)).sum(0)  # past faults per cell
        st = segment_table(cells); sids = list(st)
        faults = np.array([sum(h for h, s in zip(hist, seg) if s == sid) for sid in sids], float)
        X = np.array([st[s][0] for s in sids]); expo = np.array([st[s][1] * past_storms for s in sids])
        hand = np.array([c["p0"] / c["exposure"] for c in cells])  # hand prior without the live event area
        grids.append(dict(data=d, name=os.path.basename(os.path.normpath(d)), cells=cells, rate=rate, L=L, table=(X, faults, expo), hand=hand))
    print(f"{'held-out grid':18} {'learner':7} {'AUC hand prior':>15} {'AUC learned':>12} {'AUC truth (ceiling)':>20}")
    res = []
    for i, g in enumerate(grids):
        model, kind, _ = train([h["table"] for j, h in enumerate(grids) if j != i])
        learned = model.predict(np.array([c["feat"] for c in g["cells"]]))
        p_true = 1 - np.exp(-g["rate"] * g["L"])
        labels = rng.random((test_storms, len(p_true))) < p_true  # future storms on the unseen grid
        lab = labels.ravel(); tile = lambda s: np.tile(s, test_storms)
        r = (auc(tile(g["hand"]), lab), auc(tile(learned), lab), auc(tile(g["rate"]), lab))
        e2e = end_to_end(g, model, cfg, e2e_sims, seed) if e2e_sims else None
        res.append(r); print(f"{g['name']:18} {kind:7} {r[0]:15.3f} {r[1]:12.3f} {r[2]:20.3f}" +
                             (f"   found {e2e[0][0]:4.1f}% -> {e2e[1][0]:4.1f}%  precision {e2e[0][1]:3.0f}% -> {e2e[1][1]:3.0f}%  mean P / true {e2e[0][2]:.2f} -> {e2e[1][2]:.2f}" if e2e else ""))
    m = np.mean(res, axis=0); print(f"{'mean':18} {'':7} {m[0]:15.3f} {m[1]:12.3f} {m[2]:20.3f}")
    return res


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train"); t.add_argument("--pair", nargs=2, action="append", metavar=("DATA", "HISTORY"), required=True)
    t.add_argument("--out", default=os.path.join(HERE, "risk_model.joblib"))
    d = sub.add_parser("demo"); d.add_argument("--maps", nargs="+", required=True); d.add_argument("--past", type=int, default=25)
    d.add_argument("--e2e", type=int, default=0, help="also run N full simulations per held-out grid (hand vs learned prior)")
    a = ap.parse_args(); cfg = md.gio.load_config()
    if a.cmd == "demo":
        demo(a.maps, cfg, a.past, e2e_sims=a.e2e); return
    tables = []
    for data, hist in a.pair:
        st = segment_table(cells_for(data, cfg)); rows = {r["segment_id"]: r for r in csv.DictReader(open(hist))}
        sids = [s for s in st if s in rows]
        tables.append((np.array([st[s][0] for s in sids]), np.array([float(rows[s]["faults"]) for s in sids]),
                       np.array([st[s][1] * float(rows[s]["events"]) for s in sids])))
    model, kind, score = train(tables)
    import joblib; joblib.dump(model, a.out)
    print(f"trained {kind} on {sum(len(t[1]) for t in tables)} lines from {len(tables)} grid(s); leave-one-grid-out deviance {score}; saved {a.out}")


if __name__ == "__main__":
    main()
