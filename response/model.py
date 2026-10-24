"""Response model: from 5 base positions to inspect / repair / need-more-evidence decisions.

Follows the team flow diagram:
  reports backlog -> model -> evaluate criteria -> 5 drones fly to a location -> collect data
  (temperature, visual, wind, 3D position, voltage) -> model -> evaluate criteria ->
  not enough evidence (back to backlog) | physical inspection | repair.

Information types follow data/datatypes.txt.rtf:
  background (exposure, vegetation proxy, season) -> prior per 1 km cell
  network characteristics + consequence           -> consequence weight c
  live event data (reports with confidence)        -> likelihood ratios on the prior
  drone inspection data                            -> Bayesian update per sortie (search theory)

Limits (same as engine.js): 30 min flight, 6 min reserve, 54 km/h ferry, 20 km/h inspection
(= 8 km inspected per sortie), 3 h charge, and a sortie must end on a line that reads live.
Drone readings in the simulation are HYPOTHETICAL (no ground truth is published).
"""
import argparse, importlib.util, json, math, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


sb = load_module("sb", os.path.join(ROOT, "placement", "seasonal_bases.py"))
pb = sb.pb

LIMITS = dict(flight_min=30, reserve_min=6, cruise_kmh=54, inspect_kmh=20, charge_h=3, window_h=4)
# live report -> likelihood ratio on "a fault in this area" (if the report is true), scaled by confidence
REPORT_LR = {"vegetation_and_access_report": 3.0, "protection_event_summary": 2.5, "visual_flash_report": 1.5,
             "sound_and_warmth_report": 3.0, "conductor_motion_report": 3.0}
CONF = {"low": .35, "low to medium": .55, "medium": .7, "high": .9}
# fused drone reading -> LR = P(reading | fault in cell) / P(reading | no fault). Same scale as engine.js.
READ_LR = {"strong": 8.0, "weak": 2.0, "unclear": 1.0}
# what each channel can show (for the log / playbook)
CHANNELS = {"visual": "vegetation contact, broken strands, damaged insulator",
            "temperature": "hot joint or clamp (thermal contrast vs. neighbour spans)",
            "3D position": "sag, clearance loss, conductor motion",
            "wind": "context: gusts above ~20 m/s lower visual detection and explain motion",
            "voltage": "line live? (landing/charging allowed) and dead-line evidence"}
# decision costs (relative units; replace with operator numbers)
COST = dict(drone_sortie=0.2, crew_inspection=1.0, repair_crew=4.0, outage_hour=3.0, crew_eta_h=1.5)
P_CLEAR = 0.02


def bayes(p, lr):
    o = p / (1 - p) * lr
    return o / (1 + o)


def read_report_conf(r):
    c = r["confidence"].split(" for")[0].split(";")[0].strip()
    return CONF.get(c, 0.5)


def pip(x, y, poly):
    c = False; j = len(poly) - 1
    for i in range(len(poly)):
        (a, b), (d, e) = poly[i], poly[j]
        if (b > y) != (e > y) and x < (d - a) * (y - b) / (e - b) + a: c = not c
        j = i
    return c


def build_cells(data, cfg, cell_km=1.0):
    """Split every line into ~1 km cells with prior fault probability and consequence."""
    segs = json.load(open(os.path.join(data, "grid_segments.geojson")))["features"]
    nodes = {f["properties"]["node_id"]: f["properties"] for f in json.load(open(os.path.join(data, "grid_nodes.geojson")))["features"]}
    ctx = json.load(open(os.path.join(data, "scenario_context.geojson")))["features"]
    reports = {r["information_id"]: r for r in json.load(open(os.path.join(data, "scenario_public_config.json")))["public_context"]["reports"]}
    storm = next(f for f in ctx if f["properties"]["context_kind"] == "synthetic_storm_footprint")["geometry"]["coordinates"][0]
    areas = [(reports[f["properties"]["information_id"]], f["geometry"]["coordinates"][0]) for f in ctx
             if f["properties"]["context_kind"] == "synthetic_report_area"]
    allc = [p for f in segs for p in f["geometry"]["coordinates"]]
    lat0 = sum(p[1] for p in allc) / len(allc); lon0 = sum(p[0] for p in allc) / len(allc)
    season = cfg["seasons"][cfg.get("storm_season", "autumn")]
    dead = set(cfg.get("dead_lines", []))
    cells = []
    for f in segs:
        pr = f["properties"]; ll = f["geometry"]["coordinates"]
        xy = [tuple(pb.to_xy(__import__("numpy").array(p, float), lat0, lon0)) for p in ll]
        # resample the polyline at cell_km
        pts, acc = [(xy[0], ll[0], 0.0)], 0.0
        for (a, la), (b, lb) in zip(zip(xy[:-1], ll[:-1]), zip(xy[1:], ll[1:])):
            L = math.dist(a, b); n = max(1, math.ceil(L / 0.05))
            for t in range(1, n + 1):
                u = t / n; acc += L / n
                pts.append(((a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1])), (la[0] + u * (lb[0] - la[0]), la[1] + u * (lb[1] - la[1])), acc))
        total = pts[-1][2]; n_cells = max(1, round(total / cell_km))
        # consequence: voltage level, radial feed (no alternative path), dead line already costing supply
        deg = lambda nid: len(nodes.get(nid, {}).get("connected_segments", "").split(";")) if isinstance(nodes.get(nid, {}).get("connected_segments"), str) else len(nodes.get(nid, {}).get("connected_segments", []))
        radial = any(nodes.get(n, {}).get("node_type") == "abstract_substation" and deg(n) == 1 for n in (pr["from_node"], pr["to_node"]))
        cons = (3.0 if (pr["voltage_kv"] or 110) >= 380 else 1.0) * (1.5 if radial else 1.0) * (1.3 if pr["segment_id"] in dead else 1.0)
        cons *= cfg.get("consequence_overrides", {}).get(pr["segment_id"], 1.0)  # hook: population, hospitals, water plants
        for k in range(n_cells):
            lo, hi = total * k / n_cells, total * (k + 1) / n_cells
            sub = [p for p in pts if lo <= p[2] <= hi] or [pts[-1]]
            mid = sub[len(sub) // 2]; length = hi - lo
            bearing = math.atan2(sub[-1][0][0] - sub[0][0][0], sub[-1][0][1] - sub[0][0][1])
            # background prior: storm exposure x line vulnerability x crosswind (seasonal model from placement/)
            lam = 0.010 * (1.0 if pip(*mid[1], storm) else 0.2) * sb.risk(pr, bearing, season, cfg["line_factors"])
            p = 1 - math.exp(-lam * length)
            why = []
            for r, poly in areas:  # live reports ("somewhere in this area")
                lr = REPORT_LR.get(r["information_type"])
                if lr and pip(*mid[1], poly):
                    p = bayes(p, 1 + (lr - 1) * read_report_conf(r)); why.append(r["information_id"].replace("SYN_", ""))
            if pr["segment_id"] in dead:
                p = bayes(p, 1.5); why.append("dead line")
            cells.append(dict(id=f"{pr['segment_id']}#{k+1}", seg=pr["segment_id"], k=k, xy=mid[0], ll=mid[1],
                              a=sub[0][0], b=sub[-1][0], len=length, p=p, p0=p, cons=cons, live=pr["segment_id"] not in dead,
                              why=why, obs=[], status="open"))
    return cells, lat0, lon0


def decide(c):
    """When to act. Expected-cost rule with an evidence requirement for repair."""
    risk_wait = c["p"] * c["cons"] * COST["outage_hour"] * COST["crew_eta_h"]  # what waiting one crew-ETA costs
    strong = sum(o["reading"] == "strong" for o in c["obs"])
    weak = sum(o["reading"] == "weak" for o in c["obs"])
    channels = {o["channel"] for o in c["obs"] if o["reading"] in ("strong", "weak")}
    if c["p"] < P_CLEAR:
        return "cleared", f"P={c['p']:.3f} below {P_CLEAR}"
    if c["p"] >= 0.8 and (strong >= 1 and len(channels) >= 2 or strong >= 2) and risk_wait > COST["repair_crew"] * 0.25:
        return "repair", f"P={c['p']:.2f}, {strong} strong reading(s) on {len(channels)} channel(s): fault located"
    if c["p"] >= 0.5 and (strong or weak >= 2) and risk_wait > COST["crew_inspection"]:
        return "physical inspection", f"P={c['p']:.2f}, evidence present but not conclusive; expected loss {risk_wait:.1f} > crew cost {COST['crew_inspection']}"
    if c["obs"] and all(o["reading"] == "unclear" for o in c["obs"][-2:]) and len(c["obs"]) >= 2 and c["p"] * c["cons"] > 0.15:
        return "physical inspection", "two unclear passes: drone cannot resolve it, send a crew"
    return "need more evidence", f"P={c['p']:.2f}"


def plan_sortie(drone, cells, L, t_h):
    """Bayesian search step: pick the run of adjacent cells on one line with the highest expected
    consequence-weighted detection per minute, reachable and ending on a live line within 24 min."""
    budget = L["flight_min"] - L["reserve_min"]
    live = [c for c in cells if c["live"]]
    best = None
    # cells another drone is flying right now are reserved until it lands (its results are not known yet)
    open_cells = [c for c in cells if c["status"] in ("open", "need more evidence") and c.get("claimed_until", -1) <= t_h]
    by_seg = {}
    for c in open_cells: by_seg.setdefault(c["seg"], []).append(c)
    for seg, cs in by_seg.items():
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
                pd = drone["pd"]
                gain = sum(x["p"] * x["cons"] * pd for x in run) - COST["drone_sortie"] * 0.01
                # the 3 h charge dwarfs flight time, so a sortie is the scarce unit: maximise value per sortie,
                # with a small tie-break for shorter flights
                score = gain - 1e-4 * minutes
                if best is None or score > best[0]:
                    best = (score, list(run), land, minutes, ferry_in)
    return best


def reading_for(cell, truth, pd, rng, forced=None):
    if forced: return forced
    if cell["id"] in truth:
        if rng.random() < pd: return "strong" if rng.random() < 0.7 else "weak"
        return "unclear" if rng.random() < 0.6 else "clear"
    r = rng.random()
    return "weak" if r < 0.06 else "unclear" if r < 0.14 else "clear"


def apply_reading(cell, reading, pd, channel, cells):
    """Search-theory update. Clear = 'looked and did not see it': LR = (1 - pd).
    A finding raises neighbouring cells on the same line (storm damage clusters, fault may sit just outside view)."""
    lr = READ_LR.get(reading, 1 - pd)
    if reading == "clear": lr = 1 - pd
    before = cell["p"]; cell["p"] = bayes(cell["p"], lr)
    cell["obs"].append(dict(reading=reading, channel=channel))
    if reading in ("strong", "weak"):
        for n in cells:
            if n["seg"] == cell["seg"] and abs(n["k"] - cell["k"]) == 1 and n["status"] in ("open", "need more evidence"):
                n["p"] = bayes(n["p"], 1.5 if reading == "strong" else 1.2)
    return before


def simulate(cells, bases, L, seed, forced=None, log=print):
    rng = random.Random(seed)
    # hypothetical ground truth drawn from the prior (organizer faults are not public)
    truth = {c["id"] for c in cells if rng.random() < c["p0"]}
    drones = [dict(name=f"D{i+1}", xy=b, ready=0.0, pd=0.75, sorties=0) for i, b in enumerate(bases)]
    t, events = 0.0, []
    while True:
        d = min(drones, key=lambda x: x["ready"])
        t = d["ready"]
        if t + (L["flight_min"] / 60) > L["window_h"]: break
        best = plan_sortie(d, cells, L, t)
        if not best: d["ready"] = 1e9; continue
        score, run, land, minutes, ferry = best
        d["sorties"] += 1
        for c in run: c["claimed_until"] = t + minutes / 60
        log(f"\n**T+{t:.2f} h, {d['name']} sortie {d['sorties']}**: fly {ferry:.1f} km to {run[0]['seg']} cells "
            f"{run[0]['k']+1}-{run[-1]['k']+1} ({sum(c['len'] for c in run):.1f} km), {minutes:.0f} of 24 min used, "
            f"lands on {'the same line' if run[-1]['live'] else 'nearest live line (target is dead)'}.")
        for c in run:
            ch = rng.choice(["visual", "temperature", "3D position"])
            reading = reading_for(c, truth, d["pd"], rng, (forced or {}).get(c["id"]))
            before = apply_reading(c, reading, d["pd"], ch, cells)
            c["status"], why = decide(c)
            if reading != "clear" or c["status"] != "cleared":
                log(f"- {c['id']}: {ch} reading **{reading}**, P {before:.2f} -> {c['p']:.2f} => **{c['status']}** ({why})")
            events.append((t, d["name"], c["id"], reading, before, c["p"], c["status"]))
        cleared = sum(1 for c in run if c["status"] == "cleared")
        if cleared: log(f"- {cleared} cell(s) read clear and dropped below P={P_CLEAR}: search moves on")
        d["xy"] = land
        d["ready"] = t + minutes / 60 + L["charge_h"]  # landing then 3 h charge on the live line
    return truth, events, drones


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--bases", default=os.path.join(ROOT, "placement", "maps", "seasonal_bases.json"))
    ap.add_argument("--config", default=os.path.join(ROOT, "placement", "seasonal_config.json"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mc", type=int, default=200, help="Monte Carlo runs for the summary")
    ap.add_argument("--out", default=os.path.join(HERE, "run_example.md"))
    a = ap.parse_args()
    cfg = json.load(open(a.config)); cfg.setdefault("storm_season", "autumn")
    import numpy as np
    cells, lat0, lon0 = build_cells(a.data, cfg)
    bases_ll = [(b["lon"], b["lat"]) for b in json.load(open(a.bases))["all-year"]["bases"]]
    bases = [tuple(pb.to_xy(np.array(b), lat0, lon0)) for b in bases_ll]
    out = []
    log = lambda s: out.append(s)
    log("# Example run (hypothetical readings)\n")
    log(f"{len(cells)} cells of ~1 km, 5 drones at the all-year bases. Limits: {LIMITS}.\n")
    top = sorted(cells, key=lambda c: -c["p"] * c["cons"])[:8]
    log("## Starting priorities (prior P x consequence)\n\n| cell | P(fault) | consequence | from |\n|---|---|---|---|")
    for c in top: log(f"| {c['id']} | {c['p']:.3f} | {c['cons']:.1f} | {', '.join(c['why']) or 'exposure only'} |")

    # what-if on the top cell: how each kind of reading changes where we look next
    log("\n## What one reading does to the top cell and to the next target\n")
    log("| reading on " + top[0]["id"] + " | P after | decision | next target |\n|---|---|---|---|")
    for reading in ("strong", "weak", "unclear", "clear"):
        cs, _, _ = build_cells(a.data, cfg)
        c = next(x for x in cs if x["id"] == top[0]["id"])
        apply_reading(c, reading, 0.75, "visual", cs)
        if reading == "strong": apply_reading(c, "strong", 0.75, "temperature", cs)
        st, why = decide(c); c["status"] = st
        nxt = max((x for x in cs if x["status"] in ("open", "need more evidence") and x is not c), key=lambda x: x["p"] * x["cons"])
        if st == "need more evidence" and c["p"] * c["cons"] >= nxt["p"] * nxt["cons"]: nxt = c
        label = reading + (" (visual + thermal)" if reading == "strong" else "")
        log(f"| {label} | {c['p']:.2f} | {st} | {nxt['id']} (P {nxt['p']:.2f}) |")

    cells, _, _ = build_cells(a.data, cfg)
    truth, events, drones = simulate(cells, bases, LIMITS, a.seed, log=log)
    log("\n## Outcome after the 4 h window\n")
    for st in ("repair", "physical inspection", "need more evidence", "cleared"):
        ids = [c["id"] for c in cells if c["status"] == st]
        log(f"- **{st}**: {len(ids)} cell(s){': ' + ', '.join(ids[:12]) if ids and st != 'cleared' else ''}")
    log(f"- sorties flown: {sum(d['sorties'] for d in drones)} (30 min flight + 3 h charge allows at most 2 per drone in 4 h)")
    log(f"- hypothetical faults in this run: {', '.join(sorted(truth)) or 'none'}; found (repair/inspection): "
        f"{', '.join(sorted(c['id'] for c in cells if c['id'] in truth and c['status'] in ('repair', 'physical inspection'))) or 'none'}")
    # Monte Carlo over hypothetical truths: how good are the decisions, not just one story
    n_f = n_found = n_rep = n_rep_ok = n_insp = n_insp_ok = km = 0
    for sd in range(1000, 1000 + a.mc):
        cs, _, _ = build_cells(a.data, cfg)
        tr, _, _ = simulate(cs, bases, LIMITS, sd, log=lambda s: None)
        n_f += len(tr); km += sum(c["len"] for c in cs if c["obs"])
        for c in cs:
            if c["status"] == "repair": n_rep += 1; n_rep_ok += c["id"] in tr
            if c["status"] == "physical inspection": n_insp += 1; n_insp_ok += c["id"] in tr
        n_found += sum(1 for c in cs if c["id"] in tr and c["status"] in ("repair", "physical inspection"))
    log(f"\n## Monte Carlo, {a.mc} hypothetical storms\n")
    log(f"- line inspected per 4 h window: {km/a.mc:.0f} km of {sum(c['len'] for c in cells):.0f} km")
    log(f"- faults per storm: {n_f/a.mc:.1f}; sent to inspection or repair within 4 h: {100*n_found/max(n_f,1):.0f}%")
    log(f"- repair calls correct: {n_rep_ok}/{n_rep}; inspection calls correct: {n_insp_ok}/{n_insp}")
    open(a.out, "w").write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
