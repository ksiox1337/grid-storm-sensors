# Response model: what happens next, and when to act

`model.py` implements the team flow diagram for **any grid**. It starts from K base positions (the all-year plan from `placement/`, any GeoJSON of points, or `"lat,lon;lat,lon"`) and the information types in `data/datatypes.txt.rtf`. Inputs are read by `common/grid_io.py`. Every number (limits, likelihood ratios, thresholds, costs, report keywords) is in `common/config.json`; the code holds no dataset values.

```
reports backlog --> model --> evaluate criteria --> 5 drones --> location --> collect data
     ^                                                             (temperature, visual, wind,
     |                                                              3D position, voltage)
     |                                                                      |
     +---- not enough evidence <-- evaluate criteria <-- model <-----------+
                                        |
                              physical inspection | repair
```

```sh
python3 -I response/model.py --data path/to/data_folder [--bases bases.json] [--season autumn] [--seed 8]   # writes response/run_example.md
```

Drone readings are **hypothetical**: no ground truth is available. Each run draws a fictional set of faults from the prior and simulates what the sensors would report.

What is read from the data folder, if present:
- **Event area:** a polygon whose properties mention storm, footprint, warning, and so on.
- **Report areas:** polygons tied to a report id.
- **Reports:** matched to a likelihood ratio by keywords in their type or text, scaled by their confidence (a word or a number).
- **Dead lines:** found from report text such as "de-energized" or "tripped" plus a line id.
- **Time window:** any `*window*hours` value.

Everything missing falls back to neutral defaults. `tests/test_generic.py` checks this on a different grid. The examples and numbers below come from the challenge package (Bavaria, 198 km), run with `--season autumn`, because the scenario is a windstorm.

## 1. How the information types become numbers

| Datatype (datatypes.txt) | In the model |
|---|---|
| Background: terrain, exposure, trees, season | Prior fault rate per cell: event area x seasonal risk (crosswind, vegetation on lower voltages, single circuit, ...) from `common/config.json` |
| Network topology, criticality, redundancy | Consequence weight: up to x3 for the highest voltage in the grid, radial feed into a substation x1.5, already-dead line x1.3 |
| Consequence: population, critical facilities | `consequence.overrides` in the config (per line id) |
| Live reports + confidence | Likelihood ratio on every cell inside the report area: `1 + (LR - 1) x confidence` |
| Measured conditions (wind, warnings) | Lower detection probability in the storm (pd = 0.75); wind explains motion readings |
| Grid logs (a line reported de-energised) | Line counts as dead: no landing or charging there, prior x1.5 |
| Drone inspection data | Bayesian update per cell after each pass (below) |
| Past experience | Replace the assumed priors with historical fault rates per line |

## 2. What happens next: how a reading changes where we look

Every pass over a cell is a Bayesian search update. A reading changes the cell's fault probability and its neighbours'. The next sortie then goes wherever the expected value is highest: probability x consequence x detection chance, summed over the cells one sortie can inspect.

| Reading | Update | Effect on the search |
|---|---|---|
| **Finding** (strong: visual damage, hot joint, abnormal sag) | odds x8; neighbours on the same line x1.5 | Cell jumps to the top. The next drone goes there with a **different channel** to confirm, and the cells either side are checked (storm damage clusters; the fault may sit just outside view) |
| **Weak** (ambiguous anomaly) | odds x2 | Cell stays open with higher priority; a second pass is planned |
| **Unclear** (gusts, rain, bad angle) | odds x1: nothing learned | The cell keeps its priority and gets a revisit. **Two unclear passes in a row** on an important cell mean the drone cannot resolve it: send a crew instead |
| **Lack of evidence** (clear pass) | odds x(1 - pd) = x0.25 | Probability drops; below 2% the cell is cleared and the search moves to the next-best cell or line |

Challenge package example, top-priority cell (LINE_004#8, P = 0.05 prior):

| Reading | P after | Decision | Next target |
|---|---|---|---|
| strong (visual + temperature) | 0.77 | physical inspection | neighbour LINE_004#7 (raised to 0.10) |
| weak | 0.09 | need more evidence | same cell again |
| unclear | 0.05 | need more evidence | same cell again |
| clear | 0.01 | cleared | LINE_004#7 |

### Flight and charging limits

- **Per sortie:** 30 min flight, 6 min reserve, 54 km/h ferry, 20 km/h inspection, so at most **8 km of line inspected** per sortie, minus the ferry time.
- **Planner (CARP-style):** picks a run of adjacent cells on one line and checks that ferry in + inspection + ferry to a landing point fits in 24 min.
- **Landing (E-VRP):** a sortie must end on a line that reads **live** (voltage channel). Over a dead line the drone must reach the nearest live line within the reserve. The drone then **charges 3 h where it landed**, so its next sortie starts from there. The drones drift toward the evidence.
- **Reservations:** a cell another drone is flying is reserved until that drone lands. Its result isn't known yet, so two drones don't search the same line blind.
- **The bottleneck is charging.** In the 4 h scenario window each drone flies **2 sorties**: 10 in total, about 40 of 198 km of line. Most of the grid is never seen by a drone in the first 4 h, so the ranking (priors x consequence) decides where crews go first.

Example: [`run_example.md`](run_example.md) logs every sortie of a seed-8 run on the challenge package.
- After a first strong reading, the next free drone returns to the same cell with another channel.
- Confirmed cells go to **physical inspection**.
- A drone that starts over the dead line must land on the nearest live line.

## 3. When to act

Decision rule per cell, run after every reading (`decide()` in `model.py`):

| Decision | When | Why |
|---|---|---|
| **Repair** | P >= 0.8 **and** direct evidence: 2 strong readings, or 1 strong + another channel agreeing, **and** the outage cost of waiting justifies a repair crew | A repair crew is the most expensive response and needs a located fault; one sensor alone can fool you (a flash can be a generator test) |
| **Physical inspection** | P >= 0.5 with evidence (1 strong or 2 weak) and expected loss of waiting (P x consequence x outage cost x crew ETA) > crew cost; **or** 2 unclear passes on an important cell | It is cheaper to send people than to keep waiting for a 3 h recharge, or the drone has proven it cannot see the problem |
| **Need more evidence** | Anything between | Back to the backlog with its updated probability; the next sortie decides whether a revisit is worth it |
| **Cleared** | P < 2% | Enough clear passes; stop spending sorties here |

Costs and thresholds (`cost`, `decision` in `common/config.json`) are relative units. Replace them with the operator's crew costs, outage cost per hour and crew ETA.

When the evidence must be stronger:
- **The prior is low.** Two strong readings on a 5% prior only reach about 77%, so that gives an inspection, not a repair.
- **The weather lowers detection.** Gusts lower pd, so a clear pass counts for less.

When to act with less evidence:
- **Consequence is high.** On the highest-voltage lines or a radial feed to hospitals, the expected loss passes the crew cost at a lower probability.

## 4. How good is it (challenge package, 200 hypothetical storms)

| Metric | Value |
|---|---|
| Line inspected by drones in 4 h | ~38 of 198 km |
| Hypothetical faults per storm | 6.0 |
| Faults sent to inspection/repair within 4 h | ~10% |
| Repair calls that were real faults | 45 / 45 |
| Inspection calls that were real faults | 69 / 71 |

The decisions are reliable: almost no crew is sent for nothing. Coverage is limited by the 3 h charge, not by the logic. The levers are:
1. **More drones, or a swap battery** instead of a 3 h charge (2 sorties per 4 h is the binding limit).
2. **Faster confirmation:** a second channel in the same pass. A thermal + visual agreement immediately reaches the repair threshold.
3. **Crews on priors:** for high-consequence lines no drone will reach in time (in the example, the dead 27 km LINE_024), send a crew directly once P x consequence passes the inspection cost.

## 5. Fleet size before launch (`fleet.py`)

How many drones does this grid or area need? For each K from `fleet.k_min` to `fleet.k_max`:
1. Place K bases with the same optimiser as `placement/`.
2. Run N simulated events through the full response model.
3. Score the fleet: value = consequence-weighted faults found per event × outage cost × hours saved per find; net = value − K × drone cost per event.

The choice uses the one-standard-error rule: among the K whose bases cover at least `min_coverage` of the line, take the smallest K whose net is within one standard error of the best net. Two cases are flagged instead:
- **Marginal:** the best net is not clearly positive.
- **Not cost-effective:** no K pays off.

The output also shows the ROC AUC of the fault probabilities, before the flights (prior) and after them (posterior).

```sh
python3 -I response/fleet.py --data path/to/grid_or_area [more folders ...] --sims 300 [--charge-h 0.25] [--window-h 8]
```

To size each area separately, pass one folder per area.
