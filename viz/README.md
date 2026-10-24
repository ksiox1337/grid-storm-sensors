# Animation: how the system works

`animation.html` is a self-contained 16:9 (1920×1080) presentation page. It replays **one real run** of the pipeline on the challenge package:
1. **Grid:** real lines, the ML-filled fields, the dead line from a report.
2. **Storm & reports:** each report classified by JEV, with its evidence strength.
3. **Risk:** fault risk × consequence per 1 km cell.
4. **How many drones:** net value ± SE per K; the one-standard-error rule picks K.
5. **Bases:** placement with 15 km circles.
6. **Mission:** drones fly real sorties; every reading shows odds × LR → new P and the decision. Drones charge 3 h where they land.
7. **Outcome:** repair / inspect / cleared / need evidence, checked against the simulated ground truth.

**Controls:** space = play/pause, ← → = previous/next scene; click a scene in the bar to jump to it.

**Rebuild:**
```sh
python3 -I viz/export_trace.py --data DATA_FOLDER   # runs the pipeline -> viz/trace.json
python3 -I viz/build.py                             # -> viz/animation.html
```

Faults and drone readings are simulated (hypothetical), as everywhere in this repo. Report classification used live JEV (`jev-1.14.0`) when this trace was made.
