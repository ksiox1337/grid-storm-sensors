"""Run the real pipeline once and export everything the animation needs to viz/trace.json.

Pipeline: load (+ ML imputation) -> report classification (JEV or keywords) -> cell priors ->
fleet sizing -> base placement -> mission simulation with a per-sortie trace.
Readings are simulated (hypothetical faults drawn from the prior), as everywhere in this repo.
"""
import argparse, copy, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "bench"))
import benchmark as bench  # noqa: E402
md, gio = bench.md, bench.gio
rc = md._rc


def story_score(cells, trace):
    st = [c["status"] for c in cells]
    reads = [x["reading"] for r in trace for x in r["cells"]]
    return (("repair" in st) * 3 + ("physical inspection" in st) * 2 + ("unclear" in reads) + ("strong" in reads),
            sum(s in ("repair", "physical inspection") for s in st))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--season", default="autumn")
    ap.add_argument("--seed", type=int); ap.add_argument("--fleet-sims", type=int, default=100)
    ap.add_argument("--out", default=os.path.join(HERE, "trace.json"))
    a = ap.parse_args()
    cfg = gio.load_config(); cfg["scenario"]["event_season"] = a.season
    segs, nodes, scen = gio.load_all(a.data, cfg); imputed = dict(gio.IMPUTE_LOG)
    reports = []
    for r, poly in scen["report_areas"]:
        lr, how, ans = rc.report_lr(r, cfg)
        top = sorted(ans["hazard"].items(), key=lambda x: -float(x[1]))[0] if ans else None
        reports.append(dict(id=r.get("information_id"), type=r.get("information_type"), text=(r.get("description") or "")[:220],
                            confidence=r.get("confidence"), poly=poly, lr=lr, backend=how,
                            jev=dict(model=ans["model"], top=top[0], p=float(top[1]), grid=ans["grid"]) if ans else None))
    cells0, fwd, inv, _ = md.build_cells(a.data, cfg)
    # fleet sizing (same function as response/fleet.py, sequential here)
    import fleet  # noqa: E402  (response/fleet.py)
    from multiprocessing import Pool
    with Pool() as pool:
        best, rows = fleet.size_fleet(a.data, cfg, a.fleet_sims, pool=pool)
    k = best["k"]; bases_ll, cover = bench.place(a.data, cfg, k, cfg["fleet"]["radius_km"])
    bases = [fwd(*b) for b in bases_ll]
    # mission: pick the most instructive seed unless one is given
    seeds = [a.seed] if a.seed is not None else range(1, 60)
    best_run = None
    for sd in seeds:
        cells = copy.deepcopy(cells0); trace = []
        truth, drones = md.simulate(cells, bases, cfg, scen["window_h"], sd, log=lambda s: None, trace=trace)
        sc = story_score(cells, trace)
        if best_run is None or sc > best_run[0]: best_run = (sc, sd, cells, trace, truth)
    _, seed, cells, trace, truth = best_run
    byid = {c["id"]: c for c in cells0}
    for r in trace:
        r["start"] = inv(*r["start"]); r["land"] = inv(*r["land"])
        for x in r["cells"]: x["ll"] = byid[x["id"]]["ll"]
    out = dict(
        title="Storm drone response", seed=seed, season=a.season, window_h=scen["window_h"],
        params=dict(limits=cfg["limits"], sensors=cfg["sensors"], decision=cfg["decision"], cost=cfg["cost"], fleet=cfg["fleet"],
                    radius_km=cfg["fleet"]["radius_km"], report_classifier=cfg["ml"]["report_classifier"]),
        lines=[dict(id=s["id"], kv=s["kv"], coords=s["coords"], dead=s["id"] in scen["dead_lines"],
                    imputed=s.get("imputed", {})) for s in segs],
        storm=scen["event_area"], reports=reports, imputation=imputed,
        cells=[dict(id=c["id"], seg=c["seg"], ll=c["ll"], p0=c["p0"], cons=c["cons"], why=c["why"]) for c in cells0],
        fleet=dict(k=k, verdict=best["verdict"], rows=[dict(k=r["k"], net=r["net"], se=r["net_se"], found=r["found_pct"],
                   coverage=r["coverage"], auc_post=r["auc_post"]) for r in rows]),
        bases=bases_ll, coverage=cover, trace=trace, truth=sorted(truth),
        final={c["id"]: dict(p=c["p"], status=c["status"]) for c in cells if c["obs"]},
        total_km=sum(s["length_km"] for s in segs))
    json.dump(out, open(a.out, "w"), default=float)
    st = [c["status"] for c in cells]
    print(f"seed {seed}: K={k} ({best['verdict'][:60]}), sorties {len(trace)}, repair {st.count('repair')}, "
          f"inspect {st.count('physical inspection')}, faults {len(truth)}; reports via {[r['backend'] for r in reports][:2]} -> {a.out}")


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "response"))
    main()
