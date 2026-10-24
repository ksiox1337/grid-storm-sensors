# ML layer

| Part | What it learns | Status |
|---|---|---|
| `impute.py` | Missing line fields (for example `circuits`) from the lines where the field is present (random forest, cross-validated) | On by default (`ml.impute`). Only fills a gap when confidence ≥ 0.7; filled values are marked as imputed |
| `risk_model.py` | Background fault rate per km per storm from a fault history (Poisson GLM or gradient boosting, chosen by leave-one-grid-out deviance) | Used once a model is trained and `ml.risk_model` points to it; until then the hand-set prior from the config is used |
| `report_classifier.py` | Free-text report → hazard class probabilities → likelihood ratio | `keywords` (offline) or `jev` (online, `JEV_API_KEY` + `JEV_URL`). The JEV request format is a guess until their API is documented; any error falls back to keywords |

```sh
python3 -I ml/risk_model.py train --pair DATA_FOLDER history.csv --out ml/risk_model.joblib   # history.csv: segment_id,events,faults
python3 -I ml/risk_model.py demo --maps DATA1 DATA2 ... --e2e 200                           # synthetic check, see below
```

## Results

**Imputation (challenge package):** `circuits` is missing on 21 of 64 lines.
- Cross-validated accuracy is 0.95, against 0.76 for always guessing the most common class; AUC 0.89.
- 7 gaps were filled with confidence ≥ 0.7.

**Risk model, demo on synthetic history:** a hidden "true" risk that differs from the config prior, 25 past storms per grid. Each grid is tested after training on the other four, so the model has never seen it.

| Held-out grid | AUC hand prior | AUC learned | AUC truth (ceiling) | Faults found: hand → learned | Precision |
|---|---|---|---|---|---|
| Bavaria | 0.622 | 0.694 | 0.743 | 6.8% → 26.1% | 99% → 96% |
| Lyon | 0.630 | 0.674 | 0.741 | 5.3% → 8.3% | 99% → 96% |
| Texas | 0.614 | 0.651 | 0.711 | 1.6% → 10.4% | 96% → 99% |
| Norway | 0.869 | 0.883 | 0.892 | 0.6% → 34.5% | 100% → 99% |
| Tokyo | 0.682 | 0.709 | 0.731 | 19.0% → 41.5% | 100% → 99% |

Most of the gain in faults found comes from **calibration**, not ranking. The hand prior underestimated the fault rate 2–5× (mean P / true 0.19–0.46). The learned one is close (0.52–1.56), so evidence reaches the decision thresholds sooner, and precision stays at 96–99%.

How large the gain is depends on how wrong the hand prior is. Here that gap was chosen by me, so real history is needed for real numbers.
