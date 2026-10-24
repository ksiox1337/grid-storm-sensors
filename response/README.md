# Response model: what happens next, and when to act

`model.py` implements the team flow diagram. It starts from the 5 all-year base positions in `placement/` and the information types in `data/datatypes.txt.rtf`:

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
python3 -I response/model.py --data /path/to/participant_package --seed 8   # writes response/run_example.md
```

Drone readings are **hypothetical**: the package publishes no ground truth. Each run draws a fictional set of faults from the prior and simulates what the sensors would report.

## 1. How the information types become numbers

| Datatype (datatypes.txt) | In the model |
|---|---|
| Background: terrain, exposure, trees, season | Prior fault rate per 1 km cell: storm footprint x seasonal risk (crosswind, 110 kV vegetation, single circuit) from `placement/seasonal_config.json` |
| Network topology, criticality, redundancy | Consequence weight: 380 kV x3, radial feed into a substation with one line x1.5, already-dead line x1.3 |
| Consequence: population, critical facilities | `consequence_overrides` hook in the config (not in the package) |
| Live reports + confidence | Likelihood ratio on every cell inside the report area: `1 + (LR - 1) x confidence` |
| Measured conditions (wind, warnings) | Lower detection probability in the storm (pd = 0.75); wind explains motion readings |
| Grid logs (LINE_024 de-energized) | Line counts as dead: no landing or charging there, prior x1.5 |
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

On the top-priority cell (LINE_004#8, P = 0.05 prior):

| Reading | P after | Decision | Next target |
|---|---|---|---|
| strong (visual + thermal) | 0.77 | physical inspection | neighbour LINE_004#7 (raised to 0.10) |
| weak | 0.09 | need more evidence | same cell again |
| unclear | 0.05 | need more evidence | same cell again |
| clear | 0.01 | cleared | LINE_004#7 |

### Flight and charging limits

- **Per sortie:** 30 min flight, 6 min reserve, 54 km/h ferry, 20 km/h inspection, so at most **8 km of line inspected** per sortie, minus the ferry time.
- **Planner (CARP-style):** picks a run of adjacent cells on one line and checks that ferry in + inspection + ferry to a landing point fits in 24 min.
- **Landing (E-VRP):** a sortie must end on a line that reads **live** (voltage channel). Over a dead line (LINE_024) the drone must reach the nearest live line within the reserve. The drone then **charges 3 h where it landed**, so its next sortie starts from there. The drones drift toward the evidence.
- **Reservations:** a cell another drone is flying is reserved until that drone lands. Its result isn't known yet, so two drones don't search the same line blind.
- **The bottleneck is charging.** In the 4 h scenario window each drone flies **2 sorties**: 10 in total, about 40 of 198 km of line. Most of the grid is never seen by a drone in the first 4 h, so the ranking (priors x consequence) decides where crews go first.

Example (seed 8, `run_example.md`):
- D1 finds a strong 3D-position anomaly on LINE_004#5 at T+0.
- After charging, D5 goes back with the visual channel. It confirms: P 0.28 -> 0.76, so LINE_004#5 goes to **physical inspection**.
- D4's first pass on LINE_026 gave two weak readings and one unclear. D3 returns there and finds two adjacent strong cells: both go to **physical inspection**.
- D5 started over the dead LINE_024 and had to land on the nearest live line.

## 3. When to act

Decision rule per cell, run after every reading (`decide()` in `model.py`):

| Decision | When | Why |
|---|---|---|
| **Repair** | P >= 0.8 **and** direct evidence: 2 strong readings, or 1 strong + another channel agreeing, **and** the outage cost of waiting justifies a repair crew | A repair crew is the most expensive response and needs a located fault; one sensor alone can fool you (a flash can be a generator test) |
| **Physical inspection** | P >= 0.5 with evidence (1 strong or 2 weak) and expected loss of waiting (P x consequence x outage cost x crew ETA) > crew cost; **or** 2 unclear passes on an important cell | It is cheaper to send people than to keep waiting for a 3 h recharge, or the drone has proven it cannot see the problem |
| **Need more evidence** | Anything between | Back to the backlog with its updated probability; the next sortie decides whether a revisit is worth it |
| **Cleared** | P < 2% | Enough clear passes; stop spending sorties here |

Costs (`COST`) and thresholds are relative units. Replace them with the operator's crew costs, outage cost per hour and crew ETA.

When the evidence must be stronger:
- **The prior is low.** Two strong readings on a 5% prior only reach about 77%, so that gives an inspection, not a repair.
- **The weather lowers detection.** Gusts lower pd, so a clear pass counts for less.

When to act with less evidence:
- **Consequence is high.** On a 380 kV line or a radial feed to hospitals, the expected loss passes the crew cost at a lower probability.

## 4. How good is it (200 hypothetical storms)

| Metric | Value |
|---|---|
| Line inspected by drones in 4 h | ~41 of 198 km |
| Hypothetical faults per storm | 6.3 |
| Faults sent to inspection/repair within 4 h | ~9% |
| Repair calls that were real faults | 46 / 46 |
| Inspection calls that were real faults | 63 / 64 |

The decisions are reliable: almost no crew is sent for nothing. Coverage is limited by the 3 h charge, not by the logic. The levers are:
1. **More drones, or a swap battery** instead of a 3 h charge (2 sorties per 4 h is the binding limit).
2. **Faster confirmation:** a second channel in the same pass. A thermal + visual agreement immediately reaches the repair threshold.
3. **Crews on priors:** for high-consequence lines no drone will reach in time (for example LINE_024, dead, 27 km), send a crew directly once P x consequence passes the inspection cost.
