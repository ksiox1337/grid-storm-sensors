"""Fleet sizing before launch: how many drones does this grid / area need?

For every candidate K: place K bases (same optimiser as placement/), run N simulated events through
the full response model, and score
    value = consequence-weighted faults found per event x outage_hour x hours_saved_per_find
    net   = value - K x drone_cost_per_event
Choice (one-standard-error rule, robust to simulation noise): among K whose bases reach min_coverage
of the line, take the smallest K whose net is within one standard error of the best net. If no K has a
positive net, the fleet does not pay for itself at these costs: say so and return the smallest K that
reaches min_coverage. All numbers come from common/config.json ("fleet", "cost"); nothing is grid-specific.
Pass a smaller data folder (one area / one component) to size the fleet for that area alone.
"""
import argparse, importlib.util, os, sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "bench"))
import benchmark as bench  # real module name, so worker processes can import run_job


def size_fleet(data, cfg, sims=None, ks=None, pool=None, extra=None):
    F, C = cfg["fleet"], cfg["cost"]
    sims = sims or F["sims"]; ks = ks or list(range(F["k_min"], F["k_max"] + 1))
    p = dict(bench.DEFAULT, r=F["radius_km"], charge_h=cfg["limits"]["charge_h"], pd=cfg["sensors"]["detect_prob"], **(extra or {}))
    run_cfg = bench.cfg_for(cfg, p)
    places = {k: bench.place(data, cfg, k, p["r"]) for k in ks}
    jobs = [(data, run_cfg, places[k][0], dict(p, k=k), "bayes", "prior", sims) for k in ks]
    res = (pool.map if pool else map)(bench.run_job, jobs)
    rows = []
    for k, r in zip(ks, res):
        value = r["cons_found_per_event"] * C["outage_hour"] * F["hours_saved_per_find"]
        se = r["cons_found_se"] * C["outage_hour"] * F["hours_saved_per_find"]
        rows.append(dict(k=k, coverage=places[k][1], value=value, net=value - k * F["drone_cost_per_event"], net_se=se, **r))
    ok = [r for r in rows if r["coverage"] >= F["min_coverage"]] or rows
    top = max(ok, key=lambda r: r["net"])
    if top["net"] <= 0:
        return dict(min(ok, key=lambda r: r["k"]), verdict="not cost-effective: no K has positive net value; minimum K for coverage"), rows
    best = min((r for r in ok if r["net"] >= top["net"] - top["net_se"]), key=lambda r: r["k"])
    if top["net"] - top["net_se"] <= 0:
        return dict(best, verdict=f"marginal: K={best['k']}, but even the best net ({top['net']:.2f} ± {top['net_se']:.2f} at K={top['k']}) "
                                  "is not clearly positive; drones barely pay off at these costs"), rows
    return dict(best, verdict=f"K={best['k']} (best net at K={top['k']}: {top['net']:.2f} ± {top['net_se']:.2f}; smallest K within 1 SE)"), rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", required=True, help="one or more data folders (grids or areas)")
    ap.add_argument("--sims", type=int); ap.add_argument("--kmax", type=int)
    ap.add_argument("--charge-h", type=float, help="override charge time (h)")
    ap.add_argument("--window-h", type=float, help="override time window (h)")
    a = ap.parse_args()
    cfg = bench.gio.load_config()
    if a.kmax: cfg["fleet"]["k_max"] = a.kmax
    extra = {}
    if a.charge_h is not None: cfg["limits"]["charge_h"] = a.charge_h
    if a.window_h is not None: extra["window_h"] = a.window_h
    with Pool() as pool:
        for data in a.data:
            best, rows = size_fleet(data, cfg, a.sims, pool=pool, extra=extra)
            print(f"\n== {os.path.basename(os.path.normpath(data))}: {rows[0]['total_km']:.0f} km, {rows[0]['faults_per_run']:.1f} faults/event")
            print(" K  cover  found%  consFound  AUCprior AUCpost  value   net ± se     false/ev")
            for r in rows:
                mark = "  <- chosen" if r["k"] == best["k"] else ""
                f = lambda v: "  -  " if v is None else f"{v:.3f}"
                print(f"{r['k']:2d}  {100*r['coverage']:4.0f}%  {r['found_pct']:5.1f}   {r['cons_found_per_event']:6.2f}    {f(r['auc_prior'])}   {f(r['auc_post'])}  {r['value']:5.2f} {r['net']:6.2f} ± {r['net_se']:.2f}  {r['false_calls']:.2f}{mark}")
            print("decision:", best["verdict"])


if __name__ == "__main__":
    main()
