"""Response model: from K base positions to inspect / repair / need-more-evidence decisions, for any grid.

Follows the team flow diagram:
  reports backlog -> model -> evaluate criteria -> drones fly to a location -> collect data
  (temperature, visual, wind, 3D position, voltage) -> model -> evaluate criteria ->
  not enough evidence (back to backlog) | physical inspection | repair.

Information types follow data/datatypes.txt.rtf:
  background (exposure, vegetation proxy, season) -> prior per cell
  network characteristics + consequence           -> consequence weight
  live event data (reports with confidence)        -> likelihood ratios on the prior
  drone inspection data                            -> Bayesian update per sortie (search theory)

Nothing here refers to a specific dataset: inputs are read by common/grid_io.py, every number
(limits, likelihood ratios, thresholds, costs) comes from common/config.json.
Drone readings in the simulation are HYPOTHETICAL: faults are drawn from the prior.
"""
import argparse, importlib.util, json, math, os, random

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


gio = _load("gio", os.path.join(ROOT, "common", "grid_io.py"))
sb = _load("sb", os.path.join(ROOT, "placement", "seasonal_bases.py"))


def bayes(p, lr):
    p = min(max(p, 1e-9), 1 - 1e-9); o = p / (1 - p) * lr
    return o / (1 + o)


_rc = _load("rc", os.path.join(ROOT, "ml", "report_classifier.py"))


def report_lr(r, cfg):
    """LR of a field report: keyword rule offline, or the JEV classifier online (ml/report_classifier.py)."""
    return _rc.report_lr(r, cfg)[0]


def report_conf(r, cfg):
    c = r.get("confidence")
    if isinstance(c, (int, float)): return float(c)
    c = str(c or "").lower().split(" for")[0]
    return next((v for word, v in cfg["reports"]["confidence_words"] if c.startswith(word)), cfg["reports"]["default_confidence"])


def season_of(cfg):
    """Event season from config, else the expected (share-weighted) season."""
    name = cfg["scenario"].get("event_season")
    if name: return [(1.0, cfg["seasons"][name])]
    return [(cfg["season_share"][s], p) for s, p in cfg["seasons"].items()]


FEATURES = ["v_rel", "v_low", "v_high", "single_circuit", "missing_data", "log_line_km",
            "cross_winter", "cross_spring", "cross_summer", "cross_autumn", "radial", "max_end_degree"]
_RISK_MODEL = {}


def background_features(sg, bearing, cfg, radial, nodes):
    """Static per-cell features for the learned background-risk model (no live event data)."""
    deg = [nodes[str(e)]["degree"] for e in (sg["from_node"], sg["to_node"]) if e is not None and str(e) in nodes] or [0]
    f = dict(v_rel=sg["v_rel"], v_low=float(sg["v_low"]), v_high=float(sg["v_high"]), single_circuit=float(sg["single_circuit"]),
             missing_data=float(sg["missing_data"]), log_line_km=math.log1p(sg["length_km"]), radial=float(radial), max_end_degree=max(deg))
    for name, s in cfg["seasons"].items():
        f[f"cross_{name}"] = abs(math.sin(bearing - math.radians(s["wind_from_deg"])))
    return [f.get(k, 0.0) for k in FEATURES]


def learned_rate(cfg):
    """Background fault rate per km per event from a trained model (ml/risk_model.py), or None."""
    path = cfg.get("ml", {}).get("risk_model")
    if not path: return None
    path = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if path not in _RISK_MODEL:
        import joblib; _RISK_MODEL[path] = joblib.load(path)
    return _RISK_MODEL[path]


def build_cells(data, cfg, segments=None, nodes_path=None):
    """Split every line into cells (~cell_km) with prior fault probability and consequence."""
    segs, nodes, scen = gio.load_all(data, cfg, segments, nodes_path)
    fwd, inv = gio.projection(segs)
    pr_cfg, cons_cfg, lf = cfg["prior"], cfg["consequence"], cfg["line_factors"]
    seasons = season_of(cfg); dead = scen["dead_lines"]
    cells = []
    for sg in segs:
        ll = sg["coords"]; xy = [fwd(*p) for p in ll]
        dense, acc = [(xy[0], ll[0], 0.0)], 0.0
        for (a, la), (b, lb) in zip(zip(xy[:-1], ll[:-1]), zip(xy[1:], ll[1:])):
            L = math.dist(a, b); n = max(1, math.ceil(L / 0.05))
            for t in range(1, n + 1):
                u = t / n; acc += L / n
                dense.append(((a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1])), (la[0] + u * (lb[0] - la[0]), la[1] + u * (lb[1] - la[1])), acc))
        total = dense[-1][2]; n_cells = max(1, round(total / pr_cfg["cell_km"]))
        # consequence: voltage position in this grid, radial feed into a substation, already dead
        radial = any(str(e) in nodes and any(w in nodes[str(e)]["type"] for w in cons_cfg["substation_words"]) and nodes[str(e)]["degree"] == 1
                     for e in (sg["from_node"], sg["to_node"]) if e is not None)
        cons = (1 + (cons_cfg["voltage_max_factor"] - 1) * sg["v_rel"]) * (cons_cfg["radial_feed"] if radial else 1) \
               * (cons_cfg["dead_line"] if sg["id"] in dead else 1) * cons_cfg["overrides"].get(sg["id"], 1)
        for k in range(n_cells):
            lo, hi = total * k / n_cells, total * (k + 1) / n_cells
            sub = [p for p in dense if lo <= p[2] <= hi] or [dense[-1]]
            mid = sub[len(sub) // 2]; length = hi - lo
            bearing = math.atan2(sub[-1][0][0] - sub[0][0][0], sub[-1][0][1] - sub[0][0][1])
            exposure = 1.0 if scen["event_area"] is None or gio.pip(*mid[1], scen["event_area"]) else pr_cfg["outside_event_area_factor"]
            feat = background_features(sg, bearing, cfg, radial, nodes)
            model = learned_rate(cfg)
            if model is not None:  # ML: background rate learned from fault history
                lam = float(model.predict([feat])[0]) * exposure * (sg["fault_prob"] or 1.0)
            else:                  # hand-set prior from config
                season_risk = sum(w * sb.risk(sg, bearing, s, lf) for w, s in seasons)
                lam = pr_cfg["faults_per_km_in_event_area"] * exposure * season_risk * (sg["fault_prob"] or 1.0)
            p = 1 - math.exp(-lam * length); why = []
            for r, poly in scen["report_areas"]:  # "somewhere in this area" reports
                if gio.pip(*mid[1], poly):
                    lr = report_lr(r, cfg)
                    if lr != 1.0:
                        p = bayes(p, 1 + (lr - 1) * report_conf(r, cfg)); why.append(str(r.get("information_id") or r.get("report_id") or "report"))
            if sg["id"] in dead:
                p = bayes(p, pr_cfg["dead_line_lr"]); why.append("dead line")
            cells.append(dict(feat=feat, exposure=exposure, id=f"{sg['id']}#{k+1}", seg=sg["id"], k=k, xy=mid[0], ll=mid[1], a=sub[0][0], b=sub[-1][0],
                              len=length, p=p, p0=p, cons=cons, live=sg["id"] not in dead, why=why, obs=[], status="open"))
    return cells, fwd, inv, scen


def decide(c, cfg):
    """When to act. Expected-cost rule with an evidence requirement for repair."""
    D, C = cfg["decision"], cfg["cost"]
    risk_wait = c["p"] * c["cons"] * C["outage_hour"] * C["crew_eta_h"]  # what waiting one crew-ETA costs
    strong = sum(o["reading"] == "strong" for o in c["obs"]); weak = sum(o["reading"] == "weak" for o in c["obs"])
    channels = {o["channel"] for o in c["obs"] if o["reading"] in ("strong", "weak")}
    if c["p"] < D["p_clear"]:
        return "cleared", f"P={c['p']:.3f} below {D['p_clear']}"
    if c["p"] >= D["p_repair"] and (strong >= 1 and len(channels) >= 2 or strong >= 2) and risk_wait > C["repair_crew"] * 0.25:
        return "repair", f"P={c['p']:.2f}, {strong} strong reading(s) on {len(channels)} channel(s): fault located"
    if c["p"] >= D["p_inspect"] and (strong or weak >= 2) and risk_wait > C["crew_inspection"]:
        return "physical inspection", f"P={c['p']:.2f}, evidence present but not conclusive; expected loss {risk_wait:.1f} > crew cost {C['crew_inspection']}"
    n = D["unclear_passes_for_crew"]
    if len(c["obs"]) >= n and all(o["reading"] == "unclear" for o in c["obs"][-n:]) and c["p"] * c["cons"] > D["unclear_min_priority"]:
        return "physical inspection", f"{n} unclear passes: drone cannot resolve it, send a crew"
    return "need more evidence", f"P={c['p']:.2f}"


def default_value(c):
    return c["p"] * c["cons"]


def plan_sortie(drone, cells, cfg, t_h, value=default_value):
    """Bayesian search step: the run of adjacent open cells on one line with the highest expected
    consequence-weighted detection, reachable and ending on a live line within the flight budget."""
    L = cfg["limits"]; budget = L["flight_min"] - L["reserve_min"]
    live = [c for c in cells if c["live"]]
    if not live: return None
    open_cells = [c for c in cells if c["status"] in ("open", "need more evidence") and c.get("claimed_until", -1) <= t_h]
    by_seg, best = {}, None
    for c in open_cells: by_seg.setdefault(c["seg"], []).append(c)
    for cs in by_seg.values():
        cs.sort(key=lambda c: c["k"])
        for i in range(len(cs)):
            run, km = [], 0.0
            for c in cs[i:]:
                if run and c["k"] != run[-1]["k"] + 1: break
                if km + c["len"] > L["inspect_kmh"] * budget / 60: break
                run.append(c); km += c["len"]
                ferry_in = min(math.dist(drone["xy"], run[0]["a"]), math.dist(drone["xy"], run[0]["b"]))
                end = run[-1]["b"]
                land = end if run[-1]["live"] else min((x["xy"] for x in live), key=lambda q: math.dist(q, end))
                minutes = 60 * (ferry_in + math.dist(end, land)) / L["cruise_kmh"] + 60 * km / L["inspect_kmh"]
                if minutes > budget: break
                # the charge dwarfs flight time, so a sortie is the scarce unit: value per sortie, tiny tie-break on time
                score = sum(value(x) * drone["pd"] for x in run) - 1e-4 * minutes
                if best is None or score > best[0]:
                    best = (score, list(run), land, minutes, ferry_in)
    return best


def reading_for(cell, truth, pd, rng):
    if cell["id"] in truth:
        if rng.random() < pd: return "strong" if rng.random() < 0.7 else "weak"
        return "unclear" if rng.random() < 0.6 else "clear"
    r = rng.random()
    return "weak" if r < 0.06 else "unclear" if r < 0.14 else "clear"


def apply_reading(cell, reading, pd, channel, cells, cfg):
    """Search-theory update. Clear = 'looked and did not see it': LR = (1 - pd).
    A finding raises neighbouring cells on the same line (damage clusters, fault may sit just outside view)."""
    lr = (1 - pd) if reading == "clear" else cfg["sensors"]["lr"][reading]
    before = cell["p"]; cell["p"] = bayes(cell["p"], lr)
    cell["obs"].append(dict(reading=reading, channel=channel))
    nlr = cfg["sensors"]["neighbour_lr"].get(reading)
    if nlr:
        for n in cells:
            if n["seg"] == cell["seg"] and abs(n["k"] - cell["k"]) == 1 and n["status"] in ("open", "need more evidence"):
                n["p"] = bayes(n["p"], nlr)
    return before


def simulate(cells, bases, cfg, window_h, seed, log=print, value=default_value, truth=None, trace=None):
    """value: search priority per cell (default P x consequence); truth: fault cell ids (default: drawn from the prior);
    trace: optional list that receives one record per sortie (for visualisation)."""
    rng = random.Random(seed); L = cfg["limits"]; pd = cfg["sensors"]["detect_prob"]
    if truth is None:
        truth = {c["id"] for c in cells if rng.random() < c["p0"]}  # hypothetical faults drawn from the prior
    drones = [dict(name=f"D{i+1}", xy=b, ready=0.0, pd=pd, sorties=0) for i, b in enumerate(bases)]
    budget = L["flight_min"] - L["reserve_min"]
    while True:
        d = min(drones, key=lambda x: x["ready"]); t = d["ready"]
        if t + L["flight_min"] / 60 > window_h: break
        best = plan_sortie(d, cells, cfg, t, value)
        if not best: d["ready"] = 1e9; continue
        _, run, land, minutes, ferry = best
        d["sorties"] += 1
        for c in run: c["claimed_until"] = t + minutes / 60
        log(f"\n**T+{t:.2f} h, {d['name']} sortie {d['sorties']}**: fly {ferry:.1f} km to {run[0]['seg']} cells "
            f"{run[0]['k']+1}-{run[-1]['k']+1} ({sum(c['len'] for c in run):.1f} km), {minutes:.0f} of {budget} min used, "
            f"lands on {'the same line' if run[-1]['live'] else 'nearest live line (target is dead)'}.")
        rec = None
        if trace is not None:
            rec = dict(drone=d["name"], t0=t, minutes=minutes, start=tuple(d["xy"]), land=tuple(land), seg=run[0]["seg"],
                       cells=[], score=_)
            trace.append(rec)
        for c in run:
            ch = rng.choice(cfg["sensors"]["channels"])
            reading = reading_for(c, truth, d["pd"], rng)
            nb_before = {n["id"]: n["p"] for n in cells if n["seg"] == c["seg"] and abs(n["k"] - c["k"]) == 1}
            before = apply_reading(c, reading, d["pd"], ch, cells, cfg)
            c["status"], why = decide(c, cfg)
            if rec is not None:
                rec["cells"].append(dict(id=c["id"], channel=ch, reading=reading, p_before=before, p_after=c["p"],
                                         status=c["status"], why=why, fault=c["id"] in truth,
                                         neighbours={n["id"]: n["p"] for n in cells if n["id"] in nb_before}))
            if reading != "clear" or c["status"] != "cleared":
                log(f"- {c['id']}: {ch} reading **{reading}**, P {before:.2f} -> {c['p']:.2f} => **{c['status']}** ({why})")
        cleared = sum(1 for c in run if c["status"] == "cleared")
        if cleared: log(f"- {cleared} cell(s) read clear and dropped below P={cfg['decision']['p_clear']}: search moves on")
        d["xy"] = land; d["ready"] = t + minutes / 60 + L["charge_h"]  # land, then charge on the live line
    return truth, drones


def load_bases(spec, plan_key):
    """Bases from seasonal_bases.json (plan key), a GeoJSON of points, or 'lat,lon;lat,lon'."""
    if os.path.exists(spec):
        d = json.load(open(spec))
        if "features" in d:
            return [tuple(f["geometry"]["coordinates"][:2]) for f in d["features"] if f["geometry"]["type"] == "Point"]
        return [(b["lon"], b["lat"]) for b in d[plan_key]["bases"]]
    return [(float(p.split(",")[1]), float(p.split(",")[0])) for p in spec.split(";")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="folder with the line GeoJSON (+ optional nodes, context, reports)")
    ap.add_argument("--segments"); ap.add_argument("--nodes")
    ap.add_argument("--bases", default=os.path.join(ROOT, "placement", "maps", "seasonal_bases.json"))
    ap.add_argument("--plan", default="all-year", help="which plan in the bases JSON")
    ap.add_argument("--config", default=os.path.join(ROOT, "common", "config.json"))
    ap.add_argument("--season", help="event season (overrides config)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mc", type=int, default=200, help="Monte Carlo runs for the summary")
    ap.add_argument("--out", default=os.path.join(HERE, "run_example.md"))
    a = ap.parse_args()
    cfg = gio.load_config(a.config)
    if a.season: cfg["scenario"]["event_season"] = a.season
    fresh = lambda: build_cells(a.data, cfg, a.segments, a.nodes)
    cells, fwd, inv, scen = fresh()
    bases = [fwd(*b) for b in load_bases(a.bases, a.plan)]
    window = scen["window_h"]; L = cfg["limits"]
    out = []; log = out.append
    log("# Example run (hypothetical readings)\n")
    log(f"{len(cells)} cells, {len(bases)} drones from `{os.path.basename(a.bases)}`, window {window:g} h, "
        f"season: {cfg['scenario']['event_season'] or 'expected over the year'}. Limits: {L}. "
        f"Dead lines: {', '.join(sorted(scen['dead_lines'])) or 'none'}.\n")
    top = sorted(cells, key=lambda c: -c["p"] * c["cons"])[:8]
    log("## Starting priorities (prior P x consequence)\n\n| cell | P(fault) | consequence | raised by |\n|---|---|---|---|")
    for c in top: log(f"| {c['id']} | {c['p']:.3f} | {c['cons']:.1f} | {', '.join(c['why']) or 'exposure only'} |")

    log("\n## What one reading does to the top cell and to the next target\n")
    log("| reading on " + top[0]["id"] + " | P after | decision | next target |\n|---|---|---|---|")
    pd = cfg["sensors"]["detect_prob"]; ch = cfg["sensors"]["channels"]
    for reading in ("strong", "weak", "unclear", "clear"):
        cs = fresh()[0]; c = next(x for x in cs if x["id"] == top[0]["id"])
        apply_reading(c, reading, pd, ch[0], cs, cfg)
        if reading == "strong": apply_reading(c, "strong", pd, ch[1 % len(ch)], cs, cfg)
        st, _ = decide(c, cfg); c["status"] = st
        rest = [x for x in cs if x["status"] in ("open", "need more evidence") and x is not c]
        nxt = max(rest, key=lambda x: x["p"] * x["cons"]) if rest else c
        if st == "need more evidence" and c["p"] * c["cons"] >= nxt["p"] * nxt["cons"]: nxt = c
        label = reading + (f" ({ch[0]} + {ch[1 % len(ch)]})" if reading == "strong" else "")
        log(f"| {label} | {c['p']:.2f} | {st} | {nxt['id']} (P {nxt['p']:.2f}) |")

    cells = fresh()[0]
    truth, drones = simulate(cells, bases, cfg, window, a.seed, log=log)
    log(f"\n## Outcome after the {window:g} h window\n")
    for st in ("repair", "physical inspection", "need more evidence", "cleared"):
        ids = [c["id"] for c in cells if c["status"] == st]
        log(f"- **{st}**: {len(ids)} cell(s){': ' + ', '.join(ids[:12]) if ids and st != 'cleared' else ''}")
    per = int(window // (L["flight_min"] / 60 + L["charge_h"])) + (1 if window % (L["flight_min"] / 60 + L["charge_h"]) >= L["flight_min"] / 60 else 0)
    log(f"- sorties flown: {sum(d['sorties'] for d in drones)} (at most {per} per drone in {window:g} h with "
        f"{L['flight_min']} min flight + {L['charge_h']} h charge)")
    log(f"- hypothetical faults in this run: {', '.join(sorted(truth)) or 'none'}; found (repair/inspection): "
        f"{', '.join(sorted(c['id'] for c in cells if c['id'] in truth and c['status'] in ('repair', 'physical inspection'))) or 'none'}")

    n_f = n_found = n_rep = n_rep_ok = n_insp = n_insp_ok = km = 0
    for sd in range(1000, 1000 + a.mc):
        cs = fresh()[0]
        tr, _ = simulate(cs, bases, cfg, window, sd, log=lambda s: None)
        n_f += len(tr); km += sum(c["len"] for c in cs if c["obs"])
        for c in cs:
            if c["status"] == "repair": n_rep += 1; n_rep_ok += c["id"] in tr
            if c["status"] == "physical inspection": n_insp += 1; n_insp_ok += c["id"] in tr
        n_found += sum(1 for c in cs if c["id"] in tr and c["status"] in ("repair", "physical inspection"))
    if a.mc:
        log(f"\n## Monte Carlo, {a.mc} hypothetical events\n")
        log(f"- line inspected per {window:g} h window: {km/a.mc:.0f} km of {sum(c['len'] for c in cells):.0f} km")
        log(f"- faults per event: {n_f/a.mc:.1f}; sent to inspection or repair within the window: {100*n_found/max(n_f,1):.0f}%")
        log(f"- repair calls correct: {n_rep_ok}/{n_rep}; inspection calls correct: {n_insp_ok}/{n_insp}")
    open(a.out, "w").write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
