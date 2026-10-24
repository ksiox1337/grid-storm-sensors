# Storm Line Patrol

Post-storm grid triage with five line-riding sensor packages (EHL hackathon, synthetic Bavarian windstorm scenario).

- `engine.js`: Bayesian fault ranking per line and hazard, sortie planner (30 min flight, 3 h charge on live lines only).
- `template.html`: the interactive page with map, flight/charge timeline, decision log and hazard playbooks.
- `build.py`: injects the participant package data and the engine into `index.html`.

```sh
python3 -I build.py /path/to/participant_package
open index.html
```
- `placement/`: placement of 5 bases with a 15 km radius (coverage, seasonal risk, maps); see `placement/README.md`.

All sensor readings in the walkthrough are hypothetical. Grid data © OpenStreetMap contributors, ODbL 1.0.

## Drone concept: half-propeller de-icer

![Line drone with half-propeller de-icer](docs/images/line-drone-deicer.jpeg)

AI-generated concept render (Gemini): inline-skate wheels roll the package along the conductor while a shrouded half propeller above the cable breaks ice off ahead of it, charging from the live line as it crawls.

![Labelled concept sketch](docs/images/drone-concept-sketch.png)
