# Storm Drone Response: submission

**In one paragraph.** After a windstorm, five line-sitting sensor packages search the Bavarian grid section for three wind-driven problems: vegetation on a line, conductors swinging or hanging too low, and damaged connections running hot. Every 1 km of line gets a fault probability. It is built from the storm area and line properties, then strengthened or weakened by the six reports, which JEV classifies. The packages start on live lines next to the highest-risk, highest-consequence corridors. Each sortie updates the probabilities with Bayes' rule:
- **a finding** brings the next package back with a different sensor;
- **an unclear reading** keeps the stretch in the queue;
- **a clean pass** lowers the probability, and the search moves on.

A crew is sent for **inspection** when the expected cost of waiting exceeds the cost of a visit. A **repair** crew needs two independent strong readings. Everything else stays "need more evidence".

**Deliverables:**
- animation (`viz/animation.html`, MP4);
- the code (`common/`, `placement/`, `response/`, `ml/`);
- full technical reference: [`docs/SYSTEM.md`](docs/SYSTEM.md).

> All sensor readings, faults and outcomes below are **hypothetical** (simulated). The package contains no live readings and no confirmed fault locations. The grid geometry is real (OpenStreetMap); the storm and reports are the fictional ones supplied.

---

## 1. Problem focus

The event is a **windstorm**, so we focus on the three problems wind causes and the reports point at:

| Problem | Why it matters here | Report evidence |
|---|---|---|
| **Vegetation contact**: trees or branches on or near a line | most common windstorm cause of trips | REPORT_02 (branch, trees brushing a route), REPORT_03 (protection trip) |
| **Conductor motion and clearance loss**: swinging wires, clashing, low sag | can cause phase-to-phase faults and damaged fittings | REPORT_06 (span moving more than its neighbours) |
| **Damaged or overheating connections** | a loose or damaged joint heats up before it fails | REPORT_05 (ticking, thermal contrast), REPORT_04 (orange flash) |

Ice and fire are out of scope for this event. The model has seasonal factors for ice, so the same code covers a winter storm.

---

## 2. What we measure

Each package carries five sensors. The **voltage sensor** is mandatory, because a package may only charge on an energised line.

| Sensor | What it can tell us | What it **cannot** tell us | Typical false signals |
|---|---|---|---|
| **RGB camera** (visual) | branches on or over conductors, broken strands, damaged insulators or spacers, fallen trees in the corridor | anything at night, in heavy rain or fog, or hidden behind foliage; internal damage; whether a branch touches a live conductor or only overhangs it | shadows, birds, debris on the ground |
| **Thermal camera** (temperature) | hot joints, clamps or connectors, seen as contrast against the neighbouring span | anything on a **de-energised or lightly loaded** line (no current, no heat); exact temperature without calibration | sun-heated surfaces, reflections, viewing angle |
| **3D position** (LiDAR or stereo) | sag and ground or vegetation clearance; sway amplitude while the package is there | motion that happens only in gusts the package did not witness; tower foundation damage | wind gusts during the scan, mapping error near crossings |
| **Wind + IMU** (anemometer, motion) | local gust speed; explains motion readings; lowers trust in visual readings during gusts | a fault by itself (it is context, not evidence) | — |
| **Voltage / electric field** | whether the line is **live**: charging allowed, and evidence for dead stretches | why a line is dead (a fault vs a precautionary switch, as on LINE_024); customer impact | induced voltage from a parallel live line |

**How readings become decisions.** Each pass over a 1 km cell produces one fused reading. Its weight is a likelihood ratio (LR), how much more likely the reading is if there is a fault:

| Reading | Meaning | LR (odds multiplier) |
|---|---|---|
| **strong** | clear visual damage, a clear hot spot, or clearance clearly violated | × 8 |
| **weak** | ambiguous anomaly | × 2 |
| **unclear** | could not see: gusts, angle, rain | × 1 (nothing learned) |
| **clear** | looked and saw nothing | × (1 − detection probability) = × 0.25 |

What the package as a whole cannot do:
- confirm an outage or customer impact (the README states supply routes are unknown);
- see inside equipment;
- observe a stretch continuously: it sees a cell only while flying over it.

---

## 3. Where we look first: starting positions (dataset IDs)

**Method:**
1. Every km of line is weighted by **fault probability × consequence**:
   - fault probability comes from storm exposure, crosswind, vegetation risk on 110 kV and the report evidence;
   - consequence is higher for 380 kV lines and for radial feeds into substations.
2. Five starting points are chosen **on live lines** so that a 15 km reach covers the most weighted line (maximal covering location: greedy, then swaps).
3. The points are then pulled toward the risk (p-median), so less of each 30-minute flight is spent in transit.

LINE_024 cannot host a package: REPORT_03 confirms it was switched off.

| Package | Sits on line | Nearest node | First priorities (highest risk × consequence it covers) |
|---|---|---|---|
| **P1** | **LINE_015** (110 kV) | SUBSTATION_15 *Umspannwerk Schwabmünchen*, 3.0 km | LINE_026, LINE_004, LINE_015 |
| **P2** | **LINE_062** (110 kV) | SUBSTATION_11, 1.5 km | LINE_031, LINE_035, LINE_062 |
| **P3** | **LINE_039** (110 kV) | SUBSTATION_12 *Umspannwerk Leitershofen*, 1.1 km | LINE_054, LINE_003, LINE_039 |
| **P4** | **LINE_064** (110 kV) | SUBSTATION_01 *UW Balzhausen*, 1.9 km | LINE_024 (dead, inspect only), LINE_064, LINE_035 |
| **P5** | **LINE_004** (380 kV) | NODE_026 (junction beside SUBSTATION_07 *Umspannwerk Oberottmarshausen*), 0.8 km | LINE_004, LINE_024, LINE_002 |

Together they reach **100% of the 198 km** of mapped line.

**Is five the right number?** A sizing check simulates 100 storms for each fleet size from 1 to 10 and compares the value of faults found with the cost of the packages. Three packages would already be within one standard error of the best result. Five leave margin for unclear readings, a dead line and the 3-hour charge.

---

## 4. Assumptions

| Topic | Assumption |
|---|---|
| Start | All five packages are **fully charged at T+0**, sitting on their start lines |
| Flight | 30 min total, of which **6 min is a reserve** for reaching a live line, so 24 min usable |
| Speeds | **54 km/h** transit, **20 km/h** while inspecting, so at most **8 km** inspected per sortie |
| Charging | **3 h**, only on a line the voltage sensor confirms is live; the package charges where it lands |
| Live lines | Every line is assumed live unless a report says otherwise (LINE_024 is dead). The voltage sensor checks on landing; if the line is dead, the reserve takes the package to the nearest live line |
| Detection | A real fault in a cell is seen with probability **0.75** per pass (storm conditions) |
| False signals | A healthy cell gives a weak reading **6%** of the time and an unclear one **8%** of the time |
| Time window | **4 h** (enough for two sorties per package) |
| Reports | Used only as starting evidence for the areas they describe. Strength = JEV hazard class × "about the grid?" probability × the stated confidence; never treated as a confirmed location |
| Costs (relative) | crew inspection 1, repair crew 4, outage 3 per hour, crew travel time 1.5 h |
| Prior fault rate | 0.01 faults per km inside the storm area, 0.2× outside, with seasonal multipliers (assumed; replaceable by a model trained on outage history) |

All values live in `common/config.json`.

---

## 5. What happens next: how readings change the search

**Rules:**
- **Finding** (strong or weak):
  - the cell's odds rise (×8 or ×2), and the cells either side on the same line rise too (×1.5), because storm damage clusters;
  - the next free package returns **with a different sensor** to confirm.
- **Unclear reading:**
  - nothing is learned, so the cell keeps its priority and is revisited;
  - **two unclear passes in a row** on an important cell send a crew instead: the package has shown it cannot resolve it.
- **No evidence** (clear pass):
  - the odds fall (×0.25), and the search moves to the next-best cell;
  - below 2% the cell is cleared.
- **Flight and charging limits:**
  - a sortie must fit transit + inspection + landing on a live line into 24 min;
  - after landing, a package charges 3 h **where it landed**, so its next sortie starts there and the fleet drifts toward the evidence;
  - while a package flies, its cells are reserved so two packages do not search the same stretch blind.

### Walkthrough (hypothetical observations, simulated run, seed 26)

P1–P5 are the packages; the animation labels them D1–D5 and their start points B1–B5.

| Time | Package | Observation (**hypothetical**) | Effect |
|---|---|---|---|
| T+0:00 | P1 → LINE_004 (cells 5–9) | cell #6 **visual: strong** | P 0.04 → 0.23, need more evidence; neighbours #5 and #7 raised |
| T+0:00 | P1 | cell #8 **unclear** (gusts) | P unchanged (0.04); stays in the queue |
| T+0:00 | P2 → LINE_031 (cells 1–5) | cell #3 **unclear** | stays in the queue; the rest of the run is clear, so those cells are cleared |
| T+0:00 | P3 → LINE_004 (cells 2–4) | all clear | cells cleared; the search on LINE_004 narrows to #6 |
| T+0:00 | P4 → LINE_026 (cells 2–6) | #2 **temperature: strong**, #3 **3D: strong**, #6 **visual: strong**; #4, #5 clear | #3 reaches P 0.51 → **physical inspection**; #2 at 0.41 and #6 at 0.45 wait for confirmation |
| T+0:00 | P5 → LINE_024 (dead, cells 1–6) | all clear | P drops on the dead line; P5 **cannot charge there** and lands on the nearest live line |
| T+0:21–0:24 → T+3:21–3:24 | all | each charges 3 h where it landed | no flights; the crew is dispatched to LINE_026#3 |
| T+3:22 | P5 → LINE_004 #6 | **3D position: strong** (second channel) | P 0.23 → 0.71 → **physical inspection** |
| T+3:23 | P2 → LINE_026 #2 | **visual: strong** (second channel after temperature) | P 0.51 → 0.89 → **REPAIR**: two strong readings on two different sensors |
| T+3:22 | P3 → LINE_026 (cells 6–9) | #6 **3D position: clear** | P 0.45 → 0.17: **a missed fault** (the hidden fault was there; the pass did not see it) |
| T+3:23 | P4, P1 | LINE_004 #11–14, LINE_015 #6–10 | all clear → cleared |

**Result after 4 h:**

| Decision | Cells |
|---|---|
| **Repair** | 1: LINE_026#2 |
| **Physical inspection** | 2: LINE_004#6, LINE_026#3 |
| **Cleared** | 27 |
| **Need more evidence** | 6 |

Checked against the hypothetical ground truth:
- all 3 crew calls are real faults;
- 5 of the 8 hidden faults were not found:
  - LINE_024#11 and #22, on the dead 27 km line beyond the stretch a package could reach;
  - LINE_036#7 and LINE_054#3, which were never flown, because 10 sorties cover about 40 km;
  - LINE_026#6, missed by a clear pass.

---

## 6. When we act

Each cell is evaluated after every reading:

| Decision | When | Why |
|---|---|---|
| **Repair** | P ≥ 0.8 **and** direct evidence: 2 strong readings, or 1 strong + another sensor agreeing | a repair crew is the most expensive response and needs a located fault; one sensor alone can be fooled (REPORT_04's flash may be a generator test) |
| **Physical inspection** | P ≥ 0.5 with evidence, **and** expected loss of waiting (P × consequence × outage cost × crew travel time) > crew cost | cheaper to send people than to wait 3 h for the next sortie |
| **Physical inspection** | 2 unclear passes in a row on an important cell | the package has shown it cannot resolve it |
| **Need more evidence** | anything in between | back to the queue with the updated probability |
| **Cleared** | P < 2% | stop spending sorties there |

What lowers and raises the bar:
- **Low prior:** a 4% cell needs two strong readings to reach about 70%, so a single sighting gives an inspection at most.
- **High consequence** (380 kV, radial feed): the expected loss passes the crew cost earlier, so we act with less evidence.
- **Dead and long lines** (LINE_024): packages cannot charge there, and its 27 km are out of reach. If P × consequence is high, the right call is to **send a crew on the prior alone**.

---

## 7. How well does it work, honestly

All numbers come from **simulated** storms (200 per setting), since no real fault data exists.

| Scenario (5 packages, 4 h, Bavaria) | Faults found | Crew calls that were real faults |
|---|---|---|
| Sensors behave as the model assumes | 12.9% | 98% |
| More false alarms (weak 15%, strong 2%) | 13.6% | 88% |
| Weaker detection (55%) | 7.1% | 97% |
| Both | 7.9% | 81% |
| Both, and the risk model is wrong (faults spread uniformly) | 2.8% | **58%** |

**Across 5 different grids** (this one plus four generated ones):
- crew-call precision is 91–100% when the assumptions hold, and **56–88%** when sensors and the risk model are worse than assumed;
- the Bayesian search finds more than a probability-blind patrol on 4 of 5 grids. It loses on a small dense grid, where an even sweep sees almost everything.

**The bottleneck is charging, not the logic.** A 15-minute battery swap instead of a 3-hour charge raises the faults found in 4 h from 7% to 49% (mean of the 5 grids).

The system is cautious: it rarely sends crews for nothing, but in 4 h it finds only a minority of faults.

---

## 8. Limitations

- **No ground truth.** All performance numbers come from our own simulator. The simulated faults follow the same risk model the search uses, which flatters the results (see the "risk model wrong" row).
- **Sensor reliability is assumed.** Precision drops quickly with more false alarms (§7). Real detection rates per sensor and per hazard are unknown.
- **One sortie sees a cell once.** Intermittent motion and gust-only clashing can be missed, and a clear pass can hide a real fault (LINE_026#6 in the walkthrough).
- **Thermal sensing needs current.** On a dead or lightly loaded line, hot joints are invisible.
- **"Live" is assumed until proven.** The data cannot confirm which lines are energised (README). We rely on the voltage sensor at landing.
- **No customer or supply model.** Consequence uses voltage and radial feeds, not real customer numbers or critical facilities (the model has a hook for them).
- **Reports.** JEV's classification of the six reports looks right, but it was not measured against labels; the keyword fallback is cruder.
- **Prior rates are assumed.** The learned risk model (`ml/risk_model.py`) needs real outage history; its demo uses a synthetic one.

## 9. What we would test next

1. **Sensor field trials** on known defects: detection rate and false-alarm rate per sensor and per hazard, in wind, rain and at night. These replace the assumed LRs.
2. **Historical replay:** train the risk model on the operator's outage history, then replay past storms to measure how many faults the plan would have found and how fast.
3. **Live-line check:** how often a line assumed live is actually dead, and what the reserve-to-live-line rule costs.
4. **Charging alternatives:** battery swap or induction charging, which our sensitivity analysis shows is the biggest lever.
5. **Consequence data:** customers and critical sites per line, so the search ranks by impact on people.
6. **Operator usability:** whether operators trust and follow the inspect/repair recommendations, and what explanation they need.

---

## 10. Run it

```sh
python3 -I viz/export_trace.py --data participant_package && python3 -I viz/build.py   # rebuild the animation
python3 -I response/model.py --data participant_package --season autumn               # mission log
python3 -I response/fleet.py --data participant_package                               # sizing check
python3 -I bench/benchmark.py --package participant_package --stress                  # stress test (§7)
```
