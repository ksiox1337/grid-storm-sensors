# Example run (hypothetical readings)

224 cells, 5 drones from `seasonal_bases.json`, window 4 h, season: autumn. Limits: {'flight_min': 30, 'reserve_min': 6, 'cruise_kmh': 54, 'inspect_kmh': 20, 'charge_h': 3}. Dead lines: LINE_024.

## Starting priorities (prior P x consequence)

| cell | P(fault) | consequence | raised by |
|---|---|---|---|
| LINE_004#8 | 0.050 | 3.0 | SYN_REPORT_05 |
| LINE_004#7 | 0.049 | 3.0 | SYN_REPORT_05 |
| LINE_004#6 | 0.048 | 3.0 | SYN_REPORT_05 |
| LINE_004#5 | 0.046 | 3.0 | SYN_REPORT_05 |
| LINE_004#4 | 0.046 | 3.0 | SYN_REPORT_05 |
| LINE_004#3 | 0.046 | 3.0 | SYN_REPORT_05 |
| LINE_004#2 | 0.046 | 3.0 | SYN_REPORT_05 |
| LINE_004#1 | 0.040 | 3.0 | SYN_REPORT_05 |

## What one reading does to the top cell and to the next target

| reading on LINE_004#8 | P after | decision | next target |
|---|---|---|---|
| strong (visual + temperature) | 0.77 | physical inspection | LINE_004#7 (P 0.10) |
| weak | 0.09 | need more evidence | LINE_004#8 (P 0.09) |
| unclear | 0.05 | need more evidence | LINE_004#8 (P 0.05) |
| clear | 0.01 | cleared | LINE_004#7 (P 0.05) |

**T+0.00 h, D1 sortie 1**: fly 7.4 km to LINE_004 cells 5-9 (5.1 km), 24 of 24 min used, lands on the same line.
- LINE_004#5: 3D position reading **strong**, P 0.05 -> 0.28 => **need more evidence** (P=0.28)
- LINE_004#8: visual reading **unclear**, P 0.05 -> 0.05 => **need more evidence** (P=0.05)
- 3 cell(s) read clear and dropped below P=0.02: search moves on

**T+0.00 h, D2 sortie 1**: fly 15.8 km to LINE_026 cells 5-6 (2.0 km), 23 of 24 min used, lands on the same line.
- LINE_026#5: 3D position reading **weak**, P 0.10 -> 0.18 => **need more evidence** (P=0.18)
- LINE_026#6: visual reading **clear**, P 0.13 -> 0.04 => **need more evidence** (P=0.04)

**T+0.00 h, D3 sortie 1**: fly 11.3 km to LINE_004 cells 2-4 (3.1 km), 22 of 24 min used, lands on the same line.
- LINE_004#4: 3D position reading **weak**, P 0.07 -> 0.13 => **need more evidence** (P=0.13)
- 2 cell(s) read clear and dropped below P=0.02: search moves on

**T+0.00 h, D4 sortie 1**: fly 7.0 km to LINE_026 cells 1-4 (3.9 km), 20 of 24 min used, lands on the same line.
- LINE_026#1: temperature reading **clear**, P 0.10 -> 0.03 => **need more evidence** (P=0.03)
- LINE_026#2: 3D position reading **clear**, P 0.10 -> 0.03 => **need more evidence** (P=0.03)
- LINE_026#3: temperature reading **unclear**, P 0.10 -> 0.10 => **need more evidence** (P=0.10)
- LINE_026#4: visual reading **clear**, P 0.10 -> 0.03 => **need more evidence** (P=0.03)

**T+0.00 h, D5 sortie 1**: fly 0.2 km to LINE_024 cells 1-6 (5.9 km), 21 of 24 min used, lands on nearest live line (target is dead).
- LINE_024#4: 3D position reading **clear**, P 0.08 -> 0.02 => **need more evidence** (P=0.02)
- 5 cell(s) read clear and dropped below P=0.02: search moves on

**T+3.33 h, D4 sortie 2**: fly 11.3 km to LINE_004 cells 4-5 (2.1 km), 19 of 24 min used, lands on the same line.
- LINE_004#4: visual reading **clear**, P 0.13 -> 0.03 => **need more evidence** (P=0.03)
- LINE_004#5: visual reading **strong**, P 0.32 -> 0.79 => **physical inspection** (P=0.79, evidence present but not conclusive; expected loss 10.6 > crew cost 1.0)

**T+3.35 h, D5 sortie 2**: fly 13.3 km to LINE_026 cells 3-5 (2.9 km), 24 of 24 min used, lands on the same line.
- LINE_026#3: 3D position reading **clear**, P 0.10 -> 0.03 => **need more evidence** (P=0.03)
- LINE_026#5: 3D position reading **strong**, P 0.18 -> 0.63 => **physical inspection** (P=0.63, evidence present but not conclusive; expected loss 2.9 > crew cost 1.0)
- 1 cell(s) read clear and dropped below P=0.02: search moves on

**T+3.36 h, D3 sortie 2**: fly 5.1 km to LINE_004 cells 10-14 (5.1 km), 21 of 24 min used, lands on the same line.
- LINE_004#11: temperature reading **unclear**, P 0.02 -> 0.02 => **need more evidence** (P=0.02)
- 4 cell(s) read clear and dropped below P=0.02: search moves on

**T+3.39 h, D2 sortie 2**: fly 0.0 km to LINE_026 cells 6-11 (5.9 km), 18 of 24 min used, lands on the same line.
- LINE_026#6: visual reading **strong**, P 0.06 -> 0.32 => **need more evidence** (P=0.32)
- LINE_026#7: temperature reading **unclear**, P 0.09 -> 0.09 => **need more evidence** (P=0.09)
- 4 cell(s) read clear and dropped below P=0.02: search moves on

**T+3.39 h, D1 sortie 2**: fly 4.7 km to LINE_002 cells 1-4 (3.8 km), 17 of 24 min used, lands on the same line.
- 4 cell(s) read clear and dropped below P=0.02: search moves on

## Outcome after the 4 h window

- **repair**: 0 cell(s)
- **physical inspection**: 2 cell(s): LINE_004#5, LINE_026#5
- **need more evidence**: 9 cell(s): LINE_004#4, LINE_004#8, LINE_004#11, LINE_024#4, LINE_026#1, LINE_026#2, LINE_026#3, LINE_026#6, LINE_026#7
- **cleared**: 23 cell(s)
- sorties flown: 10 (at most 2 per drone in 4 h with 30 min flight + 3 h charge)
- hypothetical faults in this run: LINE_004#5, LINE_022#5, LINE_024#13, LINE_026#5, LINE_026#6, LINE_027#2, LINE_035#11; found (repair/inspection): LINE_004#5, LINE_026#5

## Monte Carlo, 200 hypothetical events

- line inspected per 4 h window: 38 km of 198 km
- faults per event: 6.0; sent to inspection or repair within the window: 10%
- repair calls correct: 45/45; inspection calls correct: 69/71
