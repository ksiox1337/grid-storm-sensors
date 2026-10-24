"""Report classifier: free-text field report -> hazard class probabilities -> likelihood ratio.

Backends (config["ml"]["report_classifier"]):
  "keywords"  offline: first keyword match in config["reports"]["lr_by_keyword"] (the original rule).
  "jev"       online (drones are always connected): TypeSafe AI's JEV, POST {base}/v1/systemone with
              state + model + typed questions; it returns calibrated probabilities per option.
              Key: env TYPESAFE_API_KEY (or JEV_API_KEY). Base URL: env JEV_URL, else config
              ml.jev_base_url (default https://api.typesafe.ai; a gateway such as LiteLLM/Zuplo also works).
              Any error or missing key falls back to "keywords" for that report: the mission never blocks.

Two questions are asked per report:
  hazard        choice over HAZARDS          -> probability per hazard class
  grid_related  noul (yes/no probability)     -> is the report about a power line at all?
LR = P(grid) x sum_h P(h) x LR_h + (1 - P(grid)) x 1, then scaled by the report's confidence as before.

  check key:  python3 -I ml/report_classifier.py check
  classify:   python3 -I ml/report_classifier.py classify --data PACKAGE_FOLDER
"""
import argparse, json, os, sys, urllib.error, urllib.request

HAZARDS = {  # class -> (LR for "a fault in this report's area", description for JEV)
    "vegetation_contact": (3.0, "Trees, branches or vegetation touching or threatening an overhead line"),
    "protection_trip": (2.5, "Breaker trip, protection event, reclose or line switched off"),
    "thermal_hotspot": (3.0, "Overheating, hot joint, warmth, buzzing or ticking from equipment"),
    "conductor_motion": (3.0, "Conductor swinging, galloping, sagging or abnormal clearance"),
    "flash_or_arc": (1.5, "Flash, spark or arc that might come from the grid"),
    "weather_only": (1.0, "General weather warning without a specific grid observation"),
    "unrelated": (1.0, "Not about the power grid"),
}
_CACHE = {}


def _text(r):
    return " ".join(str(r.get(k, "")) for k in ("information_type", "type", "description", "text", "location_or_asset") if r.get(k))


def _conf():
    key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY")
    return key


def _base(cfg):
    return (os.environ.get("JEV_URL") or cfg.get("ml", {}).get("jev_base_url") or "https://api.typesafe.ai").rstrip("/")


def _call(method, url, key, body=None, timeout=10):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "grid-storm-sensors/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:  # 401 bad key, 422 bad field (body names it), 429/529 rate/overload
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None


def keywords(r, cfg):
    t = _text(r).lower()
    return next((lr for word, lr in cfg["reports"]["lr_by_keyword"] if word in t), cfg["reports"]["default_lr"]), "keywords", None


def jev_answers(text, cfg, key):
    ml = cfg.get("ml", {})
    body = {"model": ml.get("jev_model", "jev-latest"), "state": text, "questions": {
        "hazard": {"type": "choice", "instructions": "A field report received during a storm near overhead power lines. "
                   "Which kind of grid hazard does it describe?", "criteria": {k: v[1] for k, v in HAZARDS.items()}},
        "grid_related": {"type": "noul", "instructions": "Is this report plausibly about damage or a fault on a power line?"}}}
    data = _call("POST", _base(cfg) + "/v1/systemone", key, body, ml.get("jev_timeout_s", 10))
    a = data["answers"]
    return dict(model=data.get("model"), hazard=a["hazard"]["probabilities"], choice=a["hazard"].get("choice"),
                confidence=a["hazard"].get("confidence"), grid=float(a["grid_related"]["noul"]))


def jev(r, cfg):
    key = _conf()
    if not key:
        return keywords(r, cfg)[0], "keywords (no TYPESAFE_API_KEY)", None
    text = _text(r)
    if text not in _CACHE:
        try:
            _CACHE[text] = jev_answers(text, cfg, key)
        except Exception as e:  # network, auth, schema: never block the mission on the classifier
            return keywords(r, cfg)[0], f"keywords (JEV error: {str(e)[:120]})", None
    ans = _CACHE[text]; probs = {h: float(p) for h, p in ans["hazard"].items() if h in HAZARDS}; s = sum(probs.values()) or 1
    lr_h = sum(p / s * HAZARDS[h][0] for h, p in probs.items())
    return ans["grid"] * lr_h + (1 - ans["grid"]) * 1.0, "jev", ans


def report_lr(r, cfg):
    backend = cfg.get("ml", {}).get("report_classifier", "keywords")
    return (jev if backend == "jev" else keywords)(r, cfg)


def main():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
    import grid_io
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check"); c = sub.add_parser("classify"); c.add_argument("--data", required=True)
    a = ap.parse_args(); cfg = grid_io.load_config(); key = _conf()
    if not key: sys.exit("TYPESAFE_API_KEY is not set (create a key at console.typesafe.ai/keys)")
    if a.cmd == "check":
        models = _call("GET", _base(cfg) + "/v1/models", key)
        print("key OK. models:", ", ".join(m.get("name", "?") for m in models.get("models", [])))
        return
    cfg.setdefault("ml", {})["report_classifier"] = "jev"
    segs = grid_io.load_segments(grid_io.find(a.data, "*segment*.geojson", "*line*.geojson", "*.geojson"), cfg)
    reports = grid_io.load_scenario(a.data, cfg, segs)["reports"]
    for r in reports:
        lr_k = keywords(r, cfg)[0]; lr, how, ans = jev(r, cfg)
        rid = r.get("information_id") or r.get("report_id") or "report"
        if ans:
            top = sorted(ans["hazard"].items(), key=lambda x: -float(x[1]))[:2]
            print(f"{rid}: JEV {ans['model']} -> {', '.join(f'{h} {float(p):.2f}' for h, p in top)}, grid-related {ans['grid']:.2f}; "
                  f"LR {lr:.2f} (keywords {lr_k:.2f})")
        else:
            print(f"{rid}: {how}; LR {lr:.2f}")


if __name__ == "__main__":
    main()
