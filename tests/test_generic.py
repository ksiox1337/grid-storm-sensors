"""Generality check: the whole pipeline must run on a grid that has nothing in common with the
challenge package (other country, other schema, other voltages, no node or context files).
Run: python3 -I tests/test_generic.py   (or with pytest)
"""
import json, math, os, random, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = [sys.executable, "-I"]


def make_grid(folder, with_report):
    """Random radial/meshed grid around Bergen, Norway: OSM-like schema, volts as text, one MultiLineString."""
    rng = random.Random(3); lat0, lon0 = 60.39, 5.32
    hubs = [(lon0 + rng.uniform(-0.35, 0.35), lat0 + rng.uniform(-0.18, 0.18)) for _ in range(7)]
    feats = []
    for i in range(10):
        a, b = rng.sample(hubs, 2); pts = [a]
        for t in range(1, 8):
            u = t / 8; pts.append((a[0] + u * (b[0] - a[0]) + rng.uniform(-.01, .01), a[1] + u * (b[1] - a[1]) + rng.uniform(-.005, .005)))
        pts.append(b)
        geom = {"type": "LineString", "coordinates": pts} if i != 4 else \
               {"type": "MultiLineString", "coordinates": [pts[:5], pts[4:]]}
        feats.append({"type": "Feature", "geometry": geom,
                      "properties": {"name": f"N{i+1:02d}", "voltage": rng.choice(["132000", "66000;22000", "22000"]),
                                     "circuits": rng.choice([1, 2, None])}})
    json.dump({"type": "FeatureCollection", "features": feats}, open(os.path.join(folder, "lines.geojson"), "w"))
    if with_report:
        rep = {"information_id": "R1", "information_type": "operator_log", "confidence": 0.9,
               "description": "Breaker on N03 tripped, line is out of service."}
        json.dump({"reports": [rep], "window_hours": 6}, open(os.path.join(folder, "event.json"), "w"))
        ring = [[lon0 - .4, lat0 - .2], [lon0 + .4, lat0 - .2], [lon0 + .4, lat0 + .2], [lon0 - .4, lat0 + .2], [lon0 - .4, lat0 - .2]]
        json.dump({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"kind": "storm warning area"}}]},
            open(os.path.join(folder, "context.geojson"), "w"))


def run(*args):
    r = subprocess.run(PY + list(args), capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


def check(with_report):
    with tempfile.TemporaryDirectory() as d:
        make_grid(d, with_report)
        out = run("placement/place_bases.py", os.path.join(d, "lines.geojson"), "-k", "3", "-r", "10",
                  "--on-line", "--step-km", "0.2", "--grid-km", "0.4", "-o", os.path.join(d, "b.geojson"))
        assert "covered:" in out
        maps = os.path.join(d, "maps")
        out = run("placement/seasonal_bases.py", "--data", d, "-k", "3", "-r", "10", "--out-dir", maps)
        plans = json.load(open(os.path.join(maps, "seasonal_bases.json")))
        assert set(plans) == {"winter", "spring", "summer", "autumn", "all-year"}
        assert all(len(p["bases"]) == 3 for p in plans.values())
        assert all(abs(p["bases"][0]["lat"] - 60.39) < 1 for p in plans.values())  # bases are where the grid is
        assert os.path.getsize(os.path.join(maps, "bases_all-year.svg")) > 1000
        if with_report:
            assert "dead lines (no bases): N03" in out, out
        out = run("response/model.py", "--data", d, "--bases", os.path.join(maps, "seasonal_bases.json"),
                  "--mc", "20", "--seed", "1", "--out", os.path.join(d, "run.md"))
        assert "## Outcome after the" in out and "sorties flown:" in out
        if with_report:
            assert "window 6 h" in out and "Dead lines: N03" in out, out[:600]
        else:
            assert "window 4 h" in out and "Dead lines: none" in out
        return out


def test_bare_grid():
    check(with_report=False)


def test_grid_with_event_data():
    check(with_report=True)


if __name__ == "__main__":
    test_bare_grid(); print("bare grid (lines only): ok")
    test_grid_with_event_data(); print("grid + event area + report: ok")
