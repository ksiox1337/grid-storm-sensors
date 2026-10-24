"""Generic input layer: reads any power-line GeoJSON (+ optional nodes, context areas, reports) into
one normalised form. No dataset-specific IDs, names or coordinates: property names come from
config["schema"], scenario cues from config["scenario"], everything missing degrades gracefully.
"""
import glob, json, math, os, re

EARTH_KM = 6371.0088
HERE = os.path.dirname(os.path.abspath(__file__))


def load_config(path=None):
    return json.load(open(path or os.path.join(HERE, "config.json")))


def pick(props, names, default=None):
    for n in names:
        v = props.get(n)
        if v not in (None, ""):
            return v
    return default


def num(v):
    if isinstance(v, (int, float)): return float(v)
    if isinstance(v, str):
        m = re.findall(r"[\d.]+", v.replace(",", "."))
        return max(float(x) for x in m) if m else None  # "380000;110000" -> highest
    if isinstance(v, list):
        vals = [num(x) for x in v]; vals = [x for x in vals if x is not None]
        return max(vals) if vals else None
    return None


def km(a, b):
    """Great-circle distance between two lon/lat points in km."""
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def pip(x, y, poly):
    c = False; j = len(poly) - 1
    for i in range(len(poly)):
        (a, b), (d, e) = poly[i][:2], poly[j][:2]
        if (b > y) != (e > y) and x < (d - a) * (y - b) / (e - b) + a: c = not c
        j = i
    return c


def find(data, *patterns):
    for p in patterns:
        hits = sorted(glob.glob(os.path.join(data, p)))
        if hits: return hits[0]
    return None


def load_segments(path, cfg):
    sc = cfg["schema"]; segs = []
    for n, f in enumerate(json.load(open(path))["features"]):
        g, pr = f.get("geometry") or {}, f.get("properties") or {}
        parts = [g["coordinates"]] if g.get("type") == "LineString" else g.get("coordinates", []) if g.get("type") == "MultiLineString" else []
        for j, coords in enumerate(parts):
            if len(coords) < 2: continue
            sid = str(pick(pr, sc["segment_id"], f"SEG_{n+1}")) + (f"_{j+1}" if len(parts) > 1 else "")
            kv = num(pick(pr, sc["voltage_kv"]))
            if kv and kv > 1000: kv /= 1000  # OSM-style volts
            cables = num(pick(pr, sc["cables"]))
            circuits = num(pick(pr, sc["circuits"]))
            length = sum(km(a, b) for a, b in zip(coords[:-1], coords[1:]))
            segs.append(dict(id=sid, coords=[tuple(c[:2]) for c in coords], kv=kv, length_km=length,
                             circuits=circuits, cables=cables,
                             single_circuit=(circuits == 1) or (circuits is None and cables is not None and cables <= cfg["line_factors"]["single_circuit_max_cables"]),
                             missing_data=circuits is None or kv is None,
                             from_node=pick(pr, sc["from_node"]), to_node=pick(pr, sc["to_node"]),
                             fault_prob=num(pick(pr, sc["fault_prob"]))))
    kvs = sorted(s["kv"] for s in segs if s["kv"])
    lo, hi, med = (kvs[0], kvs[-1], kvs[len(kvs) // 2]) if kvs else (None, None, None)
    for s in segs:  # relative voltage class: works for 20 kV distribution grids and 400 kV transmission alike
        v = s["kv"] or med
        s["v_rel"] = 0.0 if not v or hi == lo else (v - lo) / (hi - lo)
        s["v_low"] = bool(v and med and v < med) or (hi == lo)
        s["v_high"] = bool(v and hi and v >= hi and hi != lo)
    return segs


def load_nodes(path, cfg, segs):
    """Node degree from the segment endpoints (topology), node type from the file if present."""
    sc = cfg["schema"]; nodes = {}
    if path:
        for f in json.load(open(path))["features"]:
            pr = f.get("properties") or {}
            nid = pick(pr, sc["node_id"])
            if nid is not None:
                nodes[str(nid)] = dict(type=str(pick(pr, sc["node_type"], "")).lower(), coords=tuple(f["geometry"]["coordinates"][:2]), degree=0)
    for s in segs:
        for end in (s["from_node"], s["to_node"]):
            if end is not None:
                nodes.setdefault(str(end), dict(type="", coords=None, degree=0))["degree"] += 1
    return nodes


def load_scenario(data, cfg, segs):
    """Live context: event area, report areas, dead lines and time window, from whatever is present."""
    sw = cfg["scenario"]; out = dict(event_area=None, report_areas=[], reports=[], dead_lines=set(), window_h=sw["default_window_h"], bounds=None)
    reports = []
    for p in glob.glob(os.path.join(data, "*.json")):
        try: d = json.load(open(p))
        except Exception: continue
        if not isinstance(d, dict): continue
        for k, v in d.items():
            if "window" in k and isinstance(v, (int, float)) and "hour" in k: out["window_h"] = float(v)
            if "bounds" in k and isinstance(v, list) and len(v) == 4: out["bounds"] = v
        ctx = d.get("public_context", d)
        if isinstance(ctx, dict) and isinstance(ctx.get("reports"), list): reports += ctx["reports"]
    for p in glob.glob(os.path.join(data, "*.geojson")):
        try: d = json.load(open(p))
        except Exception: continue
        for f in d.get("features", []):
            g = f.get("geometry") or {}
            if g.get("type") != "Polygon": continue
            pr = f.get("properties") or {}; text = " ".join(str(v) for v in pr.values()).lower()
            rid = pr.get("information_id") or pr.get("report_id")
            if rid:
                rep = next((r for r in reports if r.get("information_id") == rid or r.get("report_id") == rid), pr)
                out["report_areas"].append((rep, g["coordinates"][0]))
            elif any(w in text for w in sw["event_area_words"]) and out["event_area"] is None:
                out["event_area"] = g["coordinates"][0]
    out["reports"] = reports
    ids = {s["id"] for s in segs}
    for r in reports:  # a report that says a named line is de-energised makes it unusable for landing/charging
        text = " ".join(str(v) for v in r.values() if isinstance(v, str))
        if any(w in text.lower() for w in sw["dead_line_words"]):
            named = {r.get("asset_reference")} | set(re.findall(r"[A-Za-z]+[_-]?\d+", text))
            out["dead_lines"] |= {n for n in named if n in ids}
    return out


IMPUTE_LOG = {}


def load_all(data, cfg, segments=None, nodes=None):
    seg_path = segments or find(data, "*segment*.geojson", "*line*.geojson", "*.geojson")
    segs = load_segments(seg_path, cfg)
    node_path = nodes or find(data, "*node*.geojson")
    node_map = load_nodes(node_path, cfg, segs)
    if cfg.get("ml", {}).get("impute"):  # ML: fill missing attributes (ml/impute.py), mark them as imputed
        import importlib.util
        spec = importlib.util.spec_from_file_location("ml_impute", os.path.join(os.path.dirname(HERE), "ml", "impute.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        IMPUTE_LOG.clear(); m.impute(segs, cfg, {k: v["degree"] for k, v in node_map.items()}, IMPUTE_LOG)
        mx = cfg["line_factors"]["single_circuit_max_cables"]
        for s in segs:
            c = s["circuits"]
            s["single_circuit"] = (c == 1) or (c is None and s["cables"] is not None and s["cables"] <= mx)
    return segs, node_map, load_scenario(data, cfg, segs)


def projection(segs):
    """Local equirectangular km projection centred on the data (fine for regional grids, < ~500 km)."""
    pts = [p for s in segs for p in s["coords"]]
    lat0 = sum(p[1] for p in pts) / len(pts); lon0 = sum(p[0] for p in pts) / len(pts)
    kx = math.radians(1) * EARTH_KM * math.cos(math.radians(lat0)); ky = math.radians(1) * EARTH_KM
    fwd = lambda lon, lat: ((lon - lon0) * kx, (lat - lat0) * ky)
    inv = lambda x, y: (lon0 + x / kx, lat0 + y / ky)
    return fwd, inv
