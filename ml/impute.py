"""Fill missing line attributes with a classifier trained on the lines where the attribute is present.

Generic: works on the normalised segments from common/grid_io.py. For each target field in
config["ml"]["impute_fields"] that has gaps, train a random forest on the complete rows (features:
voltage, relative voltage, cables, length, endpoint degree), estimate accuracy with cross-validation,
and fill a gap only when the predicted class probability >= config["ml"]["impute_min_prob"].
Filled values are marked (seg["imputed"][field] = probability) so downstream code and reports know.
"""
import math
import numpy as np


def features(segs, node_degree):
    rows = []
    for s in segs:
        deg = [node_degree.get(str(e), 0) for e in (s["from_node"], s["to_node"]) if e is not None] or [0]
        rows.append([s["kv"] if s["kv"] is not None else -1, s["v_rel"], s["cables"] if s["cables"] is not None else -1,
                     math.log1p(s["length_km"]), max(deg), min(deg)])
    return np.array(rows, float)


def impute(segs, cfg, node_degree=None, report=None):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_predict, StratifiedKFold
    from sklearn.metrics import roc_auc_score
    ml = cfg.get("ml", {}); fields = ml.get("impute_fields", ["circuits"]); thr = ml.get("impute_min_prob", 0.7)
    X = features(segs, node_degree or {})
    out = {}
    for field in fields:
        y = [s.get(field) for s in segs]
        known = [i for i, v in enumerate(y) if v is not None]; missing = [i for i, v in enumerate(y) if v is None]
        if not missing or len(known) < 8: continue
        yk = np.array([str(y[i]) for i in known]); classes, counts = np.unique(yk, return_counts=True)
        if len(classes) < 2: continue
        clf = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, random_state=0, class_weight="balanced")
        cv_note = ""
        keep = np.isin(yk, classes[counts >= 2])  # quality estimate on classes with >= 2 examples
        kc, kn = np.unique(yk[keep], return_counts=True)
        if len(kc) >= 2:
            Xk, yy = X[known][keep], yk[keep]
            proba = cross_val_predict(clf, Xk, yy, cv=StratifiedKFold(max(2, min(5, int(kn.min()))), shuffle=True, random_state=0), method="predict_proba")
            pred = kc[proba.argmax(1)]
            acc = float((pred == yy).mean()); base = float(kn.max() / kn.sum())
            try: auc = float(roc_auc_score(yy, proba if len(kc) > 2 else proba[:, 1], multi_class="ovr", labels=list(kc)))
            except ValueError: auc = None
            cv_note = f"cv accuracy {acc:.2f} (majority baseline {base:.2f})" + (f", AUC {auc:.2f}" if auc is not None else "")
        clf.fit(X[known], yk)
        P = clf.predict_proba(X[missing]); filled = 0
        for i, p in zip(missing, P):
            j = int(p.argmax())
            if p[j] >= thr:
                v = clf.classes_[j]; segs[i][field] = float(v) if v.replace(".", "", 1).isdigit() else v
                segs[i].setdefault("imputed", {})[field] = float(p[j]); filled += 1
        out[field] = dict(known=len(known), missing=len(missing), filled=filled, note=cv_note)
    if report is not None: report.update(out)
    return out
