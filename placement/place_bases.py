"""Place K bases of radius R to cover as much (weighted) power line as possible.

Generic: works on any LineString GeoJSON. Maximal Covering Location Problem over line length:
  maximise  sum_j w_j * y_j   s.t.  y_j <= sum_{i covers j} x_i,  sum_i x_i = K
where j are short line pieces (length-weighted, optionally x fault probability) and i candidate sites.
Solved with greedy + 1-swap local search (greedy alone is a (1-1/e) approximation).

Weights per segment (Bayesian search): w = length * P(fault). P comes from a property
(--prob-field) or is uniform; bayes_update() gives the posterior after an unsuccessful inspection.
CARP / E-VRP hooks: --on-line keeps bases on a live line (sortie must end on one), and the report
estimates sorties per base from the --sortie-km inspection capacity and --charge-h recharge.
"""
import argparse, json, math
import numpy as np

EARTH_KM = 6371.0088


def bayes_update(p, detect_prob):
    """Posterior fault probability after an inspection that found nothing."""
    return p * (1 - detect_prob) / (1 - p * detect_prob)


def load_lines(path, prob_field):
    feats = json.load(open(path))["features"]
    out = []
    for f in feats:
        g = f["geometry"]
        parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        p = f["properties"].get(prob_field) if prob_field else None
        for coords in parts:
            out.append((f["properties"].get("segment_id"), np.array(coords, float), 1.0 if p is None else float(p)))
    return out


def to_xy(lonlat, lat0, lon0):
    x = np.radians(lonlat[..., 0] - lon0) * EARTH_KM * math.cos(math.radians(lat0))
    y = np.radians(lonlat[..., 1] - lat0) * EARTH_KM
    return np.stack([x, y], -1)


def to_lonlat(xy, lat0, lon0):
    lon = lon0 + np.degrees(xy[..., 0] / (EARTH_KM * math.cos(math.radians(lat0))))
    lat = lat0 + np.degrees(xy[..., 1] / EARTH_KM)
    return np.stack([lon, lat], -1)


def densify(lines, step, lat0, lon0):
    """Midpoints of pieces <= step km, with weight = piece length * P(fault)."""
    pts, w, seg = [], [], []
    for sid, ll, p in lines:
        xy = to_xy(ll, lat0, lon0)
        for a, b in zip(xy[:-1], xy[1:]):
            L = float(np.hypot(*(b - a)))
            n = max(1, math.ceil(L / step))
            t = (np.arange(n) + 0.5) / n
            pts.append(a + np.outer(t, b - a))
            w += [L / n * p] * n
            seg += [sid] * n
    return np.vstack(pts), np.array(w), np.array(seg)


def solve(cover, w, k):
    chosen = []
    covered = np.zeros(cover.shape[1], bool)
    for _ in range(k):  # greedy
        gain = cover[:, ~covered].astype(np.float32) @ w[~covered]
        i = int(np.argmax(gain)); chosen.append(i); covered |= cover[i]
    improved = True
    while improved:  # 1-swap local search
        improved = False
        best = w[covered].sum()
        for s in range(k):
            others = np.zeros(cover.shape[1], bool)
            for t, i in enumerate(chosen):
                if t != s: others |= cover[i]
            gain = cover[:, ~others].astype(np.float32) @ w[~others] + w[others].sum()
            i = int(np.argmax(gain))
            if gain[i] > best + 1e-9 and i not in chosen:
                chosen[s] = i; covered = others | cover[i]; best = gain[i]; improved = True
    return chosen, covered


def refine(dist, w, r, chosen):
    """Stage 2, lexicographic: keep weighted coverage, then minimise weighted distance to the nearest
    base (p-median). Shorter transit = more of each capacity-limited sortie spent inspecting."""
    w = np.asarray(w, np.float64)
    tol = 1e-9 * max(float(w.sum()), 1e-12)  # relative, so any weight scale works
    def score(mind):
        return float(w[mind <= r].sum()), -float(w @ mind)
    better = lambda a, b: a[0] > b[0] + tol or (a[0] >= b[0] - tol and a[1] > b[1] + tol)
    chosen = list(chosen)
    best = score(dist[chosen].min(0))
    improved = True
    while improved:
        improved = False
        for s in range(len(chosen)):
            rest = [c for t, c in enumerate(chosen) if t != s]
            others = dist[rest].min(0) if rest else np.full(dist.shape[1], np.inf, np.float32)
            newmin = np.minimum(dist, others)
            cov = (newmin <= r) @ w
            cost = np.where(np.isfinite(newmin), newmin, 1e6) @ w
            ok = np.where(cov >= best[0] - tol)[0]
            if not len(ok): continue
            i = int(ok[np.argmin(cost[ok])])
            cand = score(newmin[i])
            if i not in chosen and better(cand, best):
                chosen[s], best, improved = i, cand, True
    return chosen, dist[chosen].min(0) <= r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("-r", "--radius-km", type=float, default=15.0)
    ap.add_argument("--step-km", type=float, default=0.05, help="line sampling")
    ap.add_argument("--grid-km", type=float, default=0.25, help="candidate grid spacing")
    ap.add_argument("--on-line", action="store_true", help="bases only on a line (E-VRP: land on live line)")
    ap.add_argument("--prob-field", default=None, help="segment property holding P(fault)")
    ap.add_argument("--sortie-km", type=float, default=8.0)
    ap.add_argument("--charge-h", type=float, default=3.0)
    ap.add_argument("-o", "--out", default="bases.geojson")
    a = ap.parse_args()

    lines = load_lines(a.segments, a.prob_field)
    allc = np.vstack([l[1] for l in lines])
    lat0, lon0 = allc[:, 1].mean(), allc[:, 0].mean()
    pts, w, seg = densify(lines, a.step_km, lat0, lon0)
    lengths = densify([(s, c, 1.0) for s, c, _ in lines], a.step_km, lat0, lon0)[1]

    if a.on_line:
        cand = pts[:: max(1, int(a.grid_km / a.step_km))]
    else:
        lo, hi = pts.min(0) - a.radius_km, pts.max(0) + a.radius_km
        gx, gy = np.meshgrid(np.arange(lo[0], hi[0], a.grid_km), np.arange(lo[1], hi[1], a.grid_km))
        cand = np.stack([gx.ravel(), gy.ravel()], 1)
    d2 = ((cand[:, None, :] - pts[None, :, :]) ** 2).sum(-1)
    cover = d2 <= a.radius_km ** 2
    dist = np.sqrt(d2).astype(np.float32)
    del d2
    # drop candidates that cover nothing new vs. a dominating neighbour is optional; size is fine here
    chosen, covered = solve(cover, w, a.k)
    chosen, covered = refine(dist, w.astype(np.float32), a.radius_km, chosen)
    near = dist[chosen].min(0)
    print(f"mean distance line->nearest base {np.average(near, weights=lengths):.2f} km, max {near.max():.2f} km")

    total_km, total_w = lengths.sum(), w.sum()
    print(f"lines: {total_km:.1f} km, samples {len(pts)}, candidates {len(cand)}")
    print(f"covered: {lengths[covered].sum():.1f} km ({100*lengths[covered].sum()/total_km:.1f}%), "
          f"weighted {100*w[covered].sum()/total_w:.1f}%")
    ll = to_lonlat(cand[chosen], lat0, lon0)
    feats, owned = [], np.zeros(len(pts), bool)
    for n, (i, (lon, lat)) in enumerate(zip(chosen, ll), 1):
        mine = (dist[chosen].argmin(0) == n - 1) & cover[i]
        km_all, km_new = lengths[cover[i]].sum(), lengths[mine].sum()
        sorties = math.ceil(km_new / a.sortie_km) if km_new else 0
        print(f"B{n}: lat {lat:.5f}, lon {lon:.5f} | in reach {km_all:.1f} km, assigned (nearest) {km_new:.1f} km "
              f"-> ~{sorties} sorties ({sorties * a.charge_h:.0f} h charging)")
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": {"base": f"B{n}", "radius_km": a.radius_km,
                                     "line_km_in_reach": round(km_all, 2), "unique_km": round(km_new, 2)}})
        ring = to_lonlat(cand[i] + a.radius_km * np.stack([np.cos(t := np.linspace(0, 2*np.pi, 73)), np.sin(t)], 1), lat0, lon0)
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring.tolist()]},
                      "properties": {"base": f"B{n}", "kind": "coverage"}})
    unc = sorted(set(seg[~covered]))
    print("segments not fully covered:", ", ".join(map(str, unc)) or "none")
    json.dump({"type": "FeatureCollection", "features": feats}, open(a.out, "w"))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
