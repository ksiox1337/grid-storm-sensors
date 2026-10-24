# Drone line-inspection system: algorithms, files and data flow

This system plans drone inspection of overhead power lines after a storm.
- **Before launch:** how many drones to send, and where to put their bases.
- **During the mission:** where each drone flies next after every sensor reading.
- **For every stretch of line:** whether to clear it, ask for more evidence, send an inspection crew, or send a repair crew.

It is **generic**: no line IDs, coordinates or region-specific values appear in the code. Everything specific comes from the input data or from `common/config.json`.

---

## 1. Pipeline at a glance

```
              inputs                                    processing                                   outputs
 ┌──────────────────────────┐   ┌───────────────────────────────────────────────────────┐   ┌────────────────────────────┐
 │ lines GeoJSON (required) │──▶│ 1 load & normalise            common/grid_io.py       │   │                            │
 │ nodes GeoJSON            │──▶│ 2 fill missing fields (ML)    ml/impute.py            │   │                            │
 │ event / report areas     │──▶│ 3 classify reports (JEV)      ml/report_classifier.py │   │                            │
 │ reports JSON             │──▶│ 4 risk per 1 km cell          response/model.py       │   │                            │
 │ fault history CSV        │──▶│     (learned prior)           ml/risk_model.py        │   │                            │
 │ config.json              │──▶│ 5 fleet size K                response/fleet.py       │──▶│ recommended K + reasoning  │
 │ TYPESAFE_API_KEY (env)   │   │ 6 base placement              placement/*.py          │──▶│ base coordinates, maps     │
 │ drone readings (mission) │──▶│ 7 mission loop: plan sortie → read → Bayes update     │──▶│ mission log                │
 └──────────────────────────┘   │ 8 decision per cell                                   │──▶│ repair / inspect / clear   │
                                └───────────────────────────────────────────────────────┘   └────────────────────────────┘
                                 evaluation: Monte Carlo, bench/, tests/  ──────────────────▶ quality metrics, AUC
```

---

## 2. Inputs

All inputs sit in one **data folder**. Only the line file is required.

| Input | Format | Required | Used for |
|---|---|---|---|
| Power lines | GeoJSON `LineString` / `MultiLineString`. Properties tried by name from `config.schema`: id, voltage (kV or V), circuits, cables, from/to node, optional prior | **yes** | geometry, length, voltage class, topology |
| Nodes | GeoJSON points with id and type (`substation`, …) | no | endpoint degree, radial feeds |
| Event area | polygon whose properties mention `storm`, `footprint`, `warning`, … | no | storm exposure (cells outside × `outside_event_area_factor`) |
| Report areas | polygons carrying a report id | no | which cells a report applies to |
| Reports | any `*.json` with a `reports` list: type, description, confidence (word or number), optional `asset_reference` | no | evidence strength; **dead lines** (text such as "de-energized" or "tripped" plus a line id) |
| Scenario settings | any `*window*hours` key, `*bounds*` key | no | mission time window, study box on maps |
| Fault history | CSV `segment_id,events,faults` | no | training the learned risk model |
| JEV key | env `TYPESAFE_API_KEY` (optional `JEV_URL`) | no | online report classification |
| Drone readings | per cell: `strong` / `weak` / `unclear` / `clear`, with a channel (visual, temperature, 3D position; wind and voltage as context) | during the mission | Bayesian updates (simulated in this repo) |

Missing inputs degrade to neutral defaults: no event area means uniform exposure, no reports means no evidence, no nodes means topology comes from the line endpoints.

---

## 3. Algorithms

### 3.1 Loading and normalisation (`common/grid_io.py`)
- **Lines:**
  - Reads any line GeoJSON and maps property names through `config.schema`.
  - Converts volts to kV.
  - Computes length with the haversine formula.
  - Gives every line a voltage class **relative to this grid**: `v_low` (below the median), `v_high` (the top voltage), `v_rel` in 0..1. This is what makes a 20 kV distribution grid and a 400 kV transmission grid comparable.
- **Nodes:** node degree is counted from line endpoints.
- **Scenario:**
  - The event polygon, report polygons, reports and time window are read from whatever files exist.
  - Dead lines: report text containing a `dead_line_words` term, matched to an existing line id.
- **Projection:** local equirectangular km, centred on the data. Fine for regional grids up to about 500 km.

### 3.2 Missing-field imputation, ML (`ml/impute.py`)
- **Target:** each field in `ml.impute_fields` (default `circuits`).
- **Features:** voltage, relative voltage, cables, log length, max/min endpoint degree.
- **Model:** random forest (300 trees, balanced classes), trained on lines where the field is present.
- **Quality check:** stratified k-fold cross-validation (accuracy, majority baseline, one-vs-rest AUC) on classes with at least 2 examples.
- **Filling:** a gap is filled only if the top class probability is at least `ml.impute_min_prob` (0.7). Filled values are marked `seg["imputed"][field] = p`.
- **Challenge package:** accuracy 0.95 vs a 0.76 baseline, AUC 0.89; 7 of 21 gaps filled.

### 3.3 Report classification (`ml/report_classifier.py`)
Turns a free-text report into a likelihood ratio (LR) for "a fault inside this report's area".

- **`jev` backend (online).** TypeSafe JEV, `POST {base}/v1/systemone`, with two typed questions:
  - `hazard` (`choice`): probabilities over 7 classes, each with an LR:

    | Class | LR |
    |---|---|
    | vegetation_contact | 3.0 |
    | protection_trip | 2.5 |
    | thermal_hotspot | 3.0 |
    | conductor_motion | 3.0 |
    | flash_or_arc | 1.5 |
    | weather_only | 1.0 |
    | unrelated | 1.0 |

  - `grid_related` (`noul`): probability that the report is about a power line at all.
  - `LR = P(grid) · Σ P(h)·LR_h + (1 − P(grid)) · 1`
- **`keywords` backend (offline).** First match in `reports.lr_by_keyword`. It is also the automatic fallback on a missing key, a network error, or a 401/422/429/529 response, so a mission never blocks on the classifier.
- **Confidence scaling, both backends:** `LR_eff = 1 + (LR − 1) · confidence`.
- **CLI:** `check` verifies the key via `GET /v1/models`; `classify` compares JEV with keywords on a data folder.

### 3.4 Risk model per cell (`response/model.py`, `build_cells`)
Every line is cut into cells of about `prior.cell_km` (1 km). Each cell has:

1. **Background rate λ (faults per km per event).**
   - Learned: if `ml.risk_model` points to a trained model, `λ = model(features)`.
   - Hand-set otherwise: `λ = faults_per_km_in_event_area × seasonal risk` (§3.6).
   - Then `× exposure` (1 inside the event area, `outside_event_area_factor` outside) `× line fault_prob` (if the data has one).
2. **Prior** `P = 1 − exp(−λ · cell length)`.
3. **Live evidence:** for every report area containing the cell, `odds(P) × LR_eff` (§3.3). A dead line also gets `odds × dead_line_lr`.
4. **Consequence** `= (1 + (voltage_max_factor − 1) · v_rel) × radial_feed (if a substation endpoint has degree 1) × dead_line × overrides[id]`.
   - `overrides` is the hook for population and critical facilities.
5. **Features for ML:** `v_rel, v_low, v_high, single_circuit, missing_data, log_line_km, crosswind per season, radial, max_end_degree`.

### 3.5 Learned risk model, ML (`ml/risk_model.py`)
- **Data:** one row per line: length-weighted mean of its cell features, with target `faults / (km × events)` and weight `km × events` (Poisson exposure).
- **Learners:**
  - Poisson GLM (standardised features, L2 regularisation, robust on small data);
  - gradient boosting with Poisson loss (captures interactions).
- **Model selection:** leave-one-grid-out Poisson deviance. The final model is refit on all grids and saved with joblib.
- **Demo** (`demo --maps … --e2e N`): a hidden "true" risk that differs from the config prior, 25 synthetic past storms per grid, evaluated on a grid the model never saw.

  | Metric | Hand prior | Learned | Ceiling (true risk) |
  |---|---|---|---|
  | Mean AUC | 0.683 | 0.722 | 0.764 |

  - In the full system, the faults found rose on every grid (for example, Bavaria 6.8% → 26.1%) with crew-call precision at 96–99%.
  - Most of that gain is **calibration**: the hand prior underestimated the fault rate 2–5×.
  - The size of the gain depends on how wrong the hand prior is; real history is needed for real numbers.

### 3.6 Seasonal risk (`placement/seasonal_bases.py`, `risk`)
A multiplier per piece of line, with all values in `config.seasons` and `config.line_factors`:
- **wind:** `1 + (wind − 1)(0.3 + 0.7·|sin(bearing − wind_from)|)`; lines broadside to the wind carry more load;
- **ice:** extra factor for single-circuit lines;
- **vegetation:** stronger on lower voltages, weaker on the top voltage;
- **lightning:** stronger on the top voltage;
- **heat sag:** on long lines;
- **missing data:** a small uncertainty factor.

The all-year risk is the `season_share`-weighted mean. A mission uses `scenario.event_season` (or `--season`), else the all-year mean.

### 3.7 Base placement (`placement/place_bases.py`, `placement/seasonal_bases.py`)
Two problems are solved in order (a lexicographic objective):
1. **Maximal Covering Location Problem over line length.**
   - Line pieces of about 100 m with weight = length × risk.
   - Candidate sites: a grid, or points on live lines (`--on-line`).
   - Choose K sites maximising the weight within radius R: greedy (a (1 − 1/e) approximation), then 1-swap local search.
2. **p-median refinement.** Keep the coverage, then minimise the weighted distance from each piece to its nearest base. Shorter transit leaves more of each capacity-limited sortie for inspection. The tolerance is relative to the total weight, so any weight scale works.

`seasonal_bases.py` runs this per season and for the all-year plan, starting the search from every other season's plan as well and keeping the best result. It excludes dead lines and draws SVG maps whose frame comes from the data.

### 3.8 Fleet sizing (`response/fleet.py`)
- For each K in `fleet.k_min..k_max`:
  1. place K bases (§3.7);
  2. run N simulated events (§3.9–3.10);
  3. score:
     - `value = consequence-weighted faults found per event × outage_hour × hours_saved_per_find`
     - `net = value − K × drone_cost_per_event`, with its standard error.
- **Choice (one-standard-error rule):** among K with coverage ≥ `min_coverage`, the **smallest K whose net is within 1 SE of the best net**. This is robust to simulation noise.
- **Flags:** "marginal" (the best net is not clearly positive) or "not cost-effective" (no K is positive).
- **Results at default costs:** Bavaria 6, Lyon 3, Texas 7, Norway 6 (marginal), Tokyo not cost-effective.

### 3.9 Mission loop: Bayesian search with flight and charging limits (`response/model.py`)
- **Limits** (`config.limits`): 30 min flight, 6 min reserve, 54 km/h ferry, 20 km/h inspection, 3 h charge. So at most `inspect_kmh × 24/60` = 8 km inspected per sortie.
- **Planning a sortie** (`plan_sortie`, capacitated arc routing in spirit):
  - Candidates: runs of adjacent open cells on one line.
  - Feasibility: ferry in + inspection + ferry to a landing point must fit in 24 min. The landing point is the end of the run if that line is live, else the nearest live line, because the drone can only charge on a live line (E-VRP).
  - Score: `Σ value(cell) × detection_prob`. The default value is `P × consequence`; the strategy is pluggable for experiments.
  - Cells another drone is flying are reserved until it lands, since its results are not known yet.
- **Reading update** (`apply_reading`, search theory):

  | Reading | Odds × | Neighbours on the same line |
  |---|---|---|
  | strong | 8 | × 1.5 |
  | weak | 2 | × 1.2 |
  | unclear | 1 (nothing learned) | — |
  | clear | 1 − detection_prob (0.25) | — |

- **Charging:** after landing, the drone charges `charge_h` **where it landed**. Its next sortie starts there, so drones drift toward the evidence.
- **Simulation** (`simulate`): faults are drawn from the prior (or passed in as `truth`), and readings are drawn from the sensor model. The loop stops at the time window.

### 3.10 Decision rule (`response/model.py`, `decide`)
The rule runs after every reading. `risk_wait = P × consequence × outage_hour × crew_eta_h`.

| Decision | Condition |
|---|---|
| **cleared** | P < `p_clear` (0.02) |
| **repair** | P ≥ `p_repair` (0.8) **and** direct evidence (≥ 2 strong readings, or 1 strong + another channel) **and** risk_wait > ¼ repair-crew cost |
| **physical inspection** | P ≥ `p_inspect` (0.5) with evidence (1 strong or 2 weak) and risk_wait > crew cost; **or** `unclear_passes_for_crew` (2) unclear readings in a row on a cell with P × consequence > `unclear_min_priority` |
| **need more evidence** | otherwise; the cell goes back to the backlog with its new P |

---

## 4. Outputs

| Output | Producer | Location |
|---|---|---|
| Recommended number of drones, net value ± SE table, AUC per K | `response/fleet.py` | stdout |
| Base coordinates per season and all-year, covered km | `placement/seasonal_bases.py` | `placement/maps/seasonal_bases.json` |
| Maps (lines coloured by risk, bases, R circles) | `placement/seasonal_bases.py` | `placement/maps/*.svg`, `index.html` |
| Single-plan bases + coverage rings | `placement/place_bases.py` | GeoJSON (`-o`) |
| Mission log: starting priorities, what-if table, every sortie and reading, P before/after, decisions, Monte Carlo summary | `response/model.py` | `response/run_example.md` |
| Trained risk model | `ml/risk_model.py train` | `ml/risk_model.joblib` |
| Imputation log | `common/grid_io.py` | `grid_io.IMPUTE_LOG` |
| Benchmark results and report | `bench/` | `bench/out/results.json` (git-ignored), `bench/out/report.html` |

---

## 5. Evaluation

| Tool | What it measures |
|---|---|
| Monte Carlo in `response/model.py` | % of faults found, repair and inspection precision, km inspected |
| `bench/benchmark.py` | 5 grids (Bavaria real, generated Lyon radial, Texas mesh, Norway corridor, Tokyo urban) × 16 settings × 200 events. Sweeps of K, R, charge time, detection probability and window; Bayesian search vs patrol vs patrol + confirm; risk model right vs wrong. ROC AUC of prior and posterior |
| `response/fleet.py` | value, net ± SE, AUC per fleet size |
| `ml/risk_model.py demo` | leave-one-grid-out AUC of hand vs learned prior; end-to-end faults found and precision |
| `tests/test_generic.py` | full pipeline on a random grid in another country, with another schema, with and without event data |

**Key results:**
1. **Crew calls are reliable:** 96–100% precision, ≤ 0.05 false calls per event.
2. **Charging is the bottleneck:** cutting the charge from 3 h to 15 min raises the faults found in 4 h from 7% to 49% (mean over grids).
3. **Bayesian search beats patrols on 4 of 5 grids,** even when the risk model is wrong. It loses on a small dense grid, where an even sweep sees nearly everything.
4. **A learned prior** raises AUC from 0.68 to 0.72 on unseen grids.
5. **JEV** identified the hazard type in all 6 package reports, and down-weights reports that may not be about the grid.

---

## 6. Files

| Path | Role |
|---|---|
| `common/config.json` | **Every assumption:** schema aliases, scenario cues, seasons, line factors, prior, report rules, consequence, sensors, limits, decision thresholds, costs, fleet, ML switches |
| `common/grid_io.py` | Generic loader: lines, nodes, scenario, dead lines, projection; runs imputation |
| `ml/impute.py` | Random-forest imputation of missing line fields |
| `ml/risk_model.py` | Learned background risk (Poisson GLM/GBM): `train`, `demo` |
| `ml/report_classifier.py` | Report → LR via JEV (online) or keywords (offline): `check`, `classify` |
| `ml/README.md` | ML results and JEV setup |
| `placement/place_bases.py` | Coverage + p-median optimiser, single plan, any LineString GeoJSON |
| `placement/seasonal_bases.py` | Seasonal risk, per-season and all-year plans, SVG maps |
| `placement/maps/` | Generated plans and maps |
| `response/model.py` | Cells, priors, consequence, sortie planner, Bayesian updates, decision rule, simulation, mission log |
| `response/fleet.py` | Fleet sizing with the one-standard-error rule |
| `response/README.md` | "What happens next" and "when to act" explained |
| `bench/benchmark.py` | Multi-grid, multi-parameter Monte Carlo benchmark; map generators |
| `bench/report.py` | Builds the HTML benchmark report |
| `tests/test_generic.py` | Generality test on a random foreign grid |
| `data/datatypes.txt.rtf` | Information types the model is built around |
| `engine.js`, `template.html`, `build.py`, `index.html` | Earlier interactive page (Storm Line Patrol) |
| `requirements.txt` | numpy, scikit-learn, joblib |

---

## 7. Configuration (`common/config.json`)

| Section | Controls |
|---|---|
| `schema` | property names tried for each field |
| `scenario` | event-area words, dead-line words, default window, event season |
| `season_share`, `seasons`, `line_factors` | seasonal risk model |
| `prior` | base fault rate, outside-area factor, cell size, dead-line LR |
| `reports` | keyword → LR list, confidence words, defaults |
| `consequence` | voltage factor, radial feed, dead line, substation words, per-line overrides |
| `sensors` | detection probability, reading LRs, neighbour LRs, channels |
| `limits` | flight, reserve, speeds, charge time |
| `decision` | clear / inspect / repair thresholds, unclear-pass rule |
| `cost` | drone sortie, crew inspection, repair crew, outage per hour, crew ETA |
| `fleet` | K range, simulations, radius, value per find, drone cost, minimum coverage |
| `ml` | imputation on/off, fields, threshold; risk-model path; report classifier (`jev` / `keywords`), JEV base URL, model, timeout |

The values are **assumptions**. Replace them with operator data (outage statistics, crew costs, real sensor reliability) for real use.

---

## 8. Commands

```sh
pip install -r requirements.txt                                                 # numpy, scikit-learn, joblib
export TYPESAFE_API_KEY=...                                                     # optional: JEV online

python3 -I response/fleet.py --data DATA --sims 300                             # how many drones
python3 -I placement/seasonal_bases.py --data DATA -k 6 -r 15                   # where the bases go
python3 -I response/model.py --data DATA --season autumn --seed 8               # mission + decisions
python3 -I ml/report_classifier.py check                                        # JEV key check
python3 -I ml/report_classifier.py classify --data DATA                         # reports: JEV vs keywords
python3 -I ml/risk_model.py train --pair DATA history.csv                       # learn the risk model
python3 -I ml/risk_model.py demo --maps DATA1 DATA2 ... --e2e 200               # synthetic ML check
python3 -I bench/benchmark.py --package DATA --sims 200 && python3 -I bench/report.py   # benchmark
python3 -I tests/test_generic.py                                                # generality test
```

---

## 9. Limits and honest notes

- **Drone readings are simulated.** No ground-truth faults are published, so every performance number comes from simulated events.
- **The config values are assumptions:** seasonal factors, priors, sensor LRs, costs.
- **The learned risk model needs real fault history.** Its demo uses a synthetic hidden truth.
- **The JEV request format** follows third-party documentation; it was verified live against `jev-1.14.0`.
- **The local projection** is meant for regional grids, not continents.
- All storms, faults and reports in the challenge package are fictional. Grid data © OpenStreetMap contributors (ODbL).
