"""Report classifier: free-text field report -> hazard class probabilities -> likelihood ratio.

Backends (config["ml"]["report_classifier"]):
  "keywords"  offline: first keyword match in config["reports"]["lr_by_keyword"] (the original rule).
  "jev"       online (drones are always connected): TypeSafe AI's JEV model, which returns calibrated
              probabilities over a fixed set of typed options. Needs env JEV_API_KEY and JEV_URL.
              JEV's public API is not documented yet: the request/response shape below is an ADAPTER
              GUESS kept in one function (_jev_request) so it can be fixed in one place once the docs
              arrive. Any error or missing key falls back to "keywords" for that report.
The LR used by the response model is the probability-weighted LR of the hazard classes, scaled by the
report's stated confidence exactly as before: 1 + (LR - 1) x confidence.
"""
import json, os, urllib.request

HAZARDS = {  # class -> LR for "a fault in this report's area", same scale as config keywords
    "vegetation_contact": 3.0, "protection_trip": 2.5, "thermal_hotspot": 3.0, "conductor_motion": 3.0,
    "flash_or_arc": 1.5, "weather_only": 1.0, "unrelated": 1.0,
}
_CACHE = {}


def _text(r):
    return " ".join(str(r.get(k, "")) for k in ("information_type", "type", "description", "text", "location_or_asset"))


def keywords(r, cfg):
    t = _text(r).lower()
    return next((lr for word, lr in cfg["reports"]["lr_by_keyword"] if word in t), cfg["reports"]["default_lr"]), "keywords"


def _jev_request(text, key, url, timeout):
    """ADAPTER GUESS for JEV (typed question -> calibrated option probabilities). Adjust to the real API."""
    body = {"state": {"report": text},
            "questions": [{"id": "hazard", "type": "enum", "options": list(HAZARDS)}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "grid-storm-sensors/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    probs = data["answers"]["hazard"]["probabilities"]  # expected: {option: probability}
    return {k: float(v) for k, v in probs.items() if k in HAZARDS}


def jev(r, cfg):
    key, url = os.environ.get("JEV_API_KEY"), os.environ.get("JEV_URL")
    if not key or not url:
        return keywords(r, cfg)[0], "keywords (no JEV_API_KEY/JEV_URL)"
    text = _text(r)
    if text not in _CACHE:
        try:
            _CACHE[text] = _jev_request(text, key, url, cfg.get("ml", {}).get("jev_timeout_s", 5))
        except Exception as e:  # network, auth, schema: never block the mission on the classifier
            return keywords(r, cfg)[0], f"keywords (JEV error: {type(e).__name__})"
    probs = _CACHE[text]; s = sum(probs.values()) or 1
    return sum(p / s * HAZARDS[h] for h, p in probs.items()), "jev"


def report_lr(r, cfg):
    backend = cfg.get("ml", {}).get("report_classifier", "keywords")
    return (jev if backend == "jev" else keywords)(r, cfg)
