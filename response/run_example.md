# Example run (hypothetical readings)

224 cells of ~1 km, 5 drones at the all-year bases. Limits: {'flight_min': 30, 'reserve_min': 6, 'cruise_kmh': 54, 'inspect_kmh': 20, 'charge_h': 3, 'window_h': 4}.

## Starting priorities (prior P x consequence)

| cell | P(fault) | consequence | from |
|---|---|---|---|
| LINE_004#8 | 0.050 | 3.0 | REPORT_05 |
| LINE_004#7 | 0.049 | 3.0 | REPORT_05 |
| LINE_004#6 | 0.048 | 3.0 | REPORT_05 |
| LINE_004#5 | 0.046 | 3.0 | REPORT_05 |
| LINE_004#4 | 0.046 | 3.0 | REPORT_05 |
| LINE_004#3 | 0.046 | 3.0 | REPORT_05 |
| LINE_004#2 | 0.046 | 3.0 | REPORT_05 |
| LINE_004#1 | 0.040 | 3.0 | REPORT_05 |

## What one reading does to the top cell and to the next target

| reading on LINE_004#8 | P after | decision | next target |
|---|---|---|---|
| strong (visual + thermal) | 0.77 | physical inspection | LINE_004#7 (P 0.10) |
| weak | 0.09 | need more evidence | LINE_004#8 (P 0.09) |
| unclear | 0.05 | need more evidence | LINE_004#8 (P 0.05) |
| clear | 0.01 | cleared | LINE_004#7 (P 0.05) |

**T+0.00 h, D1 sortie 1**: fly 7.2 km to LINE_004 cells 5-9 (5.1 km), 23 of 24 min used, lands on the same line.
- LINE_004#5: 3D position reading **strong**, P 0.05 -> 0.28 => **need more evidence** (P=0.28)
- LINE_004#8: visual reading **unclear**, P 0.05 -> 0.05 => **need more evidence** (P=0.05)
- 3 cell(s) read clear and dropped below P=0.02: search moves on

**T+0.00 h, D2 sortie 1**: fly 7.5 km to LINE_031 cells 1-5 (4.9 km), 23 of 24 min used, lands on the same line.
- LINE_031#1: 3D position reading **unclear**, P 0.03 -> 0.03 => **need more evidence** (P=0.03)
- 4 cell(s) read clear and dropped below P=0.02: search moves on

**T+0.00 h, D3 sortie 1**: fly 11.0 km to LINE_004 cells 2-4 (3.1 km), 21 of 24 min used, lands on the same line.
- LINE_004#2: 3D position reading **weak**, P 0.05 -> 0.09 => **need more evidence** (P=0.09)
- 2 cell(s) read clear and dropped below P=0.02: search moves on

**T+0.00 h, D4 sortie 1**: fly 7.9 km to LINE_026 cells 2-6 (4.9 km), 24 of 24 min used, lands on the same line.
- LINE_026#2: temperature reading **unclear**, P 0.09 -> 0.09 => **need more evidence** (P=0.09)
- LINE_026#3: visual reading **clear**, P 0.09 -> 0.02 => **need more evidence** (P=0.02)
- LINE_026#5: visual reading **weak**, P 0.08 -> 0.16 => **need more evidence** (P=0.16)
- LINE_026#6: visual reading **weak**, P 0.12 -> 0.21 => **need more evidence** (P=0.21)
- 1 cell(s) read clear and dropped below P=0.02: search moves on

**T+0.00 h, D5 sortie 1**: fly 0.2 km to LINE_024 cells 1-6 (5.9 km), 21 of 24 min used, lands on nearest live line (target is dead).
- LINE_024#4: visual reading **unclear**, P 0.09 -> 0.09 => **need more evidence** (P=0.09)
- LINE_024#5: visual reading **weak**, P 0.04 -> 0.08 => **need more evidence** (P=0.08)
- 4 cell(s) read clear and dropped below P=0.02: search moves on

**T+3.35 h, D5 sortie 2**: fly 5.5 km to LINE_004 cells 5-5 (1.0 km), 9 of 24 min used, lands on the same line.
- LINE_004#5: visual reading **strong**, P 0.28 -> 0.76 => **physical inspection** (P=0.76, evidence present but not conclusive; expected loss 10.2 > crew cost 1.0)

**T+3.36 h, D3 sortie 2**: fly 10.3 km to LINE_026 cells 5-8 (3.9 km), 23 of 24 min used, lands on the same line.
- LINE_026#5: visual reading **strong**, P 0.18 -> 0.64 => **physical inspection** (P=0.64, evidence present but not conclusive; expected loss 2.9 > crew cost 1.0)
- LINE_026#6: 3D position reading **strong**, P 0.28 -> 0.76 => **physical inspection** (P=0.76, evidence present but not conclusive; expected loss 3.4 > crew cost 1.0)
- LINE_026#7: visual reading **clear**, P 0.09 -> 0.02 => **need more evidence** (P=0.02)
- 1 cell(s) read clear and dropped below P=0.02: search moves on

**T+3.38 h, D2 sortie 2**: fly 17.5 km to LINE_004 cells 2-2 (1.0 km), 23 of 24 min used, lands on the same line.
- LINE_004#2: visual reading **clear**, P 0.09 -> 0.02 => **need more evidence** (P=0.02)

**T+3.39 h, D1 sortie 2**: fly 0.0 km to LINE_004 cells 10-16 (7.2 km), 22 of 24 min used, lands on the same line.
- LINE_004#10: visual reading **unclear**, P 0.02 -> 0.02 => **need more evidence** (P=0.02)
- LINE_004#11: temperature reading **unclear**, P 0.02 -> 0.02 => **need more evidence** (P=0.02)
- 5 cell(s) read clear and dropped below P=0.02: search moves on

**T+3.39 h, D4 sortie 2**: fly 5.1 km to LINE_036 cells 1-6 (6.1 km), 24 of 24 min used, lands on the same line.
- 6 cell(s) read clear and dropped below P=0.02: search moves on

## Outcome after the 4 h window

- **repair**: 0 cell(s)
- **physical inspection**: 3 cell(s): LINE_004#5, LINE_026#5, LINE_026#6
- **need more evidence**: 10 cell(s): LINE_004#2, LINE_004#8, LINE_004#10, LINE_004#11, LINE_024#4, LINE_024#5, LINE_026#2, LINE_026#3, LINE_026#7, LINE_031#1
- **cleared**: 26 cell(s)
- sorties flown: 10 (30 min flight + 3 h charge allows at most 2 per drone in 4 h)
- hypothetical faults in this run: LINE_004#5, LINE_022#5, LINE_024#13, LINE_026#5, LINE_026#6, LINE_027#2, LINE_035#11; found (repair/inspection): LINE_004#5, LINE_026#5, LINE_026#6

## Monte Carlo, 200 hypothetical storms

- line inspected per 4 h window: 41 km of 198 km
- faults per storm: 6.3; sent to inspection or repair within 4 h: 9%
- repair calls correct: 46/46; inspection calls correct: 63/64
