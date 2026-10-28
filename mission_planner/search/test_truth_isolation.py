"""A live processing pass succeeds with private truth files and imports denied."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mission_planner.agents.perception.configuration import (
    CAPTURE_FIELDS,
    STATE_FIELDS,
    read_capture,
)
from mission_planner.environment import ROOT

ISOLATED_PASS = """
import builtins
import sys
import unittest
from pathlib import Path

private = ('profile_pipeline', 'compare_prompts', 'bench_prompt_equivalence', 'analyze_spacing', 'reduce_detection', 'evaluate_commander', 'infer_calibration', 'glimmer_calibration', 'analyze_calibration', 'calibrate', 'report_flight')
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    assert not any(part in name.split('.') for part in private), 'Private module imported'
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
def audit(event, arguments):
    if event == 'open' and isinstance(arguments[0], (str, bytes)):
        path = str(arguments[0])
        assert 'data/calibration/' not in path and not path.endswith('poison-truth.json'), 'Private truth opened'
sys.addaudithook(audit)
check = unittest.TestCase()
with check.assertRaisesRegex(AssertionError, 'Private truth opened'):
    Path(sys.argv[2]).read_text()
with check.assertRaisesRegex(AssertionError, 'Private module imported'):
    __import__('mission_planner.agents.perception.reduce_detection')

import numpy as np
from mission_planner.gcs.service import Mission
from mission_planner.agents.models import Detection
from mission_planner.agents.perception.configuration import load_configuration, read_capture
from mission_planner.agents.perception.pipeline import Processor
from mission_planner.search.belief import Evidence
from mission_planner.search.detections import Detections
class Sam:
    def detect(self, jpeg, prompts, threshold, *, timing):
        assert prompts == ['person'] and threshold == 0.3
        return {'person': [Detection((950, 500, 970, 550), 0.8, 800)]}
class Glimmer:
    def decide(self, images, question, *, timing):
        assert len(images) == 1 and question == 'Does this image show a person?'
        return {'yes': 0.9, 'no': 0.1, 'outside_mass': 0.01}
config, model = load_configuration()
record, payload = read_capture(sys.argv[1])
directory = Path(sys.argv[1]).parent / 'processed'
processor = Processor(directory, ['person'], config, Sam(), Glimmer())
result = processor.process(record, payload, 'frame', 'recorded', 'Drone4', lambda _: None)
assert result['calibrated_complete'] and len(result['detections']) == 1
detection = result['detections'][0]
point = np.array([detection['localization']['ground'][key] for key in ('north_m', 'east_m')])
n, e = np.meshgrid(np.arange(-10, 10, 4), np.arange(-10, 10, 4), indexing='ij')
low = np.column_stack([n.ravel(), e.ravel()]) + point
grid = {'low': low, 'high': low + 4, 'centers': low + 2, 'areas': np.full(len(low), 16.0)}
ledger = Detections()
ledger.add_frame('frame', result['detections'])
evidence = Evidence(np.full(len(low), 1 / len(low)), grid, model['false_alarm_rate'])
footprint = [{'north_m': float(point[0] + n), 'east_m': float(point[1] + e)} for n, e in ((-10,-10), (-10,10), (10,10), (10,-10))]
effect = evidence.apply('frame', np.full(len(low), 0.8), footprint, ledger, True)
assert effect['kind'] == 'positive' and abs(float(evidence.probability.sum()) - 1) < 1e-9
assert not any(any(part in name.split('.') for part in private) for name in sys.modules)
"""


class TruthIsolation(unittest.TestCase):
    def test_truth_free_processor_pass_and_poison_fixture(self):
        source = ROOT / ".local/data/calibration/adult-kelvin/frame-00-+00-Drone1.json"
        frame = json.loads(source.read_text())
        clean = {key: frame[key] for key in CAPTURE_FIELDS}
        for key in ("vehicleStateBefore", "vehicleStateAfter"):
            clean[key] = {field: clean[key][field] for field in STATE_FIELDS}
        with tempfile.TemporaryDirectory(dir=ROOT / ".local") as temporary:
            directory = Path(temporary)
            shutil.copyfile(source.parent / clean["file"], directory / clean["file"])
            capture = directory / "capture.json"
            capture.write_text(json.dumps(clean))
            poison = directory / "poison-truth.json"
            poison.write_text(json.dumps({"observation": {"true": True}}))
            accepted, payload = read_capture(capture)
            self.assertEqual(accepted, clean)
            self.assertEqual(payload, (source.parent / clean["file"]).read_bytes())
            subprocess.run(
                [
                    str(ROOT / ".venv-cosys/bin/python"),
                    "-c",
                    ISOLATED_PASS,
                    str(capture),
                    str(poison),
                ],
                cwd=ROOT,
                check=True,
                timeout=30,
            )
            capture.write_text(json.dumps({**clean, "observation": {"true": True}}))
            with self.assertRaisesRegex(AssertionError, "only the camera contract"):
                read_capture(capture)


if __name__ == "__main__":
    unittest.main()
