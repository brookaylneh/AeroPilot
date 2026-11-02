# Evaluation

Offline scoring of the planner against private scenario truth. The mission runtime never imports
this package (`gcs/test_truth_isolation.py` forbids the `mission_planner.evaluation` prefix), and
`truth.py` is the only module that opens private scenario files. Run it only for an explicitly
requested evaluation, after loading `.local/runtime.env` (`SNR_SCENARIO_DATA_DIR`) and `.env`.

- `briefs.py`: parses the scenario generator's fixed briefing sentences into the subject category,
  the Point Last Seen landmark id, the compass direction and hours missing, and builds the
  `area_search` tasking Wonder is expected to request at the disclosed defaults. Elderly subjects
  have no published distance table, so the evaluation states a 2.4 km radius.
- `truth.py`: authored subject positions (`authoring.position`, Unreal centimetres to mission
  metres) and the public briefings.
- `region_eval.py`: for every public briefing, plans band 1 with four landed drones at 100 %
  battery and default fences, once with the whole sortie and once split for in-flight expansion,
  and reports which band contains the subject, band-1 area and probability mass, coverage, fit
  headroom and rejections.
- `replay.py`: scores recorded missions in `.local/data/missions/`: the case (mission start event
  or pasted briefing text), plan attempts and failures, time to first launch, and whether each
  launched plan's perimeter and assigned survey areas contain the subject.

```
set -a; . ./.local/runtime.env; . ./.env; set +a
P=.venv-cosys/bin/python
$P -m mission_planner.evaluation.region_eval --output .local/evidence/evaluation/<new-run>
$P -m mission_planner.evaluation.replay --output .local/evidence/evaluation/<new-run>
```

Both commands refuse an existing output directory. Accepted numbers are copied into
`mission_planner/measurements/tasking-planner.json`.
