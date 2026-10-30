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
import copy
import json
import sys
import unittest
from pathlib import Path

private = ('profile_pipeline', 'compare_prompts', 'bench_prompt_equivalence', 'analyze_spacing', 'reduce_detection', 'evaluate_commander', 'infer_calibration', 'glimmer_calibration', 'analyze_calibration', 'calibrate', 'report_flight', 'scenario_creator')
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    assert not any(part in name.split('.') for part in private), 'Private module imported'
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
def audit(event, arguments):
    if event == 'open' and isinstance(arguments[0], (str, bytes)):
        path = str(arguments[0])
        assert not any(part in path for part in ('data/calibration/', '/scenarios/', '/truth/')) and not path.endswith('poison-truth.json'), 'Private truth opened'
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
from mission_planner.search.behavior import condition, domain_for
from mission_planner.search.detections import Detections
from mission_planner.search.coverage import capture_pod
from mission_planner.search.occlusion import aerial_visibility, footprint_fraction
from mission_planner.search.places import location_support, proposal
from mission_planner.search.prior import initialize
from world.cosys.landmarks import directions, find, load, WORLD_PATH
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
assert evidence.mass(detection['localization'])['region_cell_count'] > 0

world = json.loads(WORLD_PATH.read_text())
bounds = world['searchBoundsCm']
center = {axis: (bounds['min'][axis] + bounds['max'][axis]) / 2 for axis in 'xyz'}
bridge = {'id': 'bridge', 'name': 'Test Bridge', 'aliases': ['Crossing'], 'kind': 'bridge', 'heightM': 0, 'geometry': {'type': 'point', 'points': [center]}}
creek = {'id': 'creek', 'name': 'Test Creek', 'aliases': [], 'kind': 'creek', 'heightM': 0, 'geometry': {'type': 'polyline', 'points': [{**center, 'x': center['x'] + delta, 'y': center['y'] + 2000} for delta in (-2000, 2000)]}}
document = {'schemaVersion': 1, 'worldId': world['worldId'], 'coordinateSystem': world['coordinateSystem'], 'landmarks': [bridge, creek]}
landmark_path = Path(sys.argv[1]).parent / 'public-landmarks.json'
landmark_path.write_text(json.dumps(document))
landmarks = load(landmark_path)
assert find(landmarks, 'CROSSING')['matches'][0]['id'] == 'bridge'
heading = directions(landmarks, center['x'] / 100 + 3, center['y'] / 100 + 4)
assert heading['nearest']['distance_m'] == 5 and heading['linear']['side'] == 'west'
fence = [{'north_m': bounds[n]['x'] / 100, 'east_m': bounds[e]['y'] / 100} for n, e in (('min', 'min'), ('min', 'max'), ('max', 'max'), ('max', 'min'))]
briefing = {'lastKnownLocation': 'Phone last pinged on Test Creek'}
location = proposal(landmarks, 'creek', [{'field': 'lastKnownLocation', 'quote': briefing['lastKnownLocation']}], briefing, fence)
positions, weights = location_support(location, 4)
assert len(positions) > 1 and abs(float(weights.sum()) - 1) < 1e-12
prior, retained, fit = initialize(grid, location, 'dementia', 'temperate_mountainous')
assert abs(float(prior.sum()) - 1) < 1e-9 and 0 < retained <= 1
assert fit['location_support']['method'] == 'uniform arclength midpoint quadrature'
features = []
for kind, east in (('road', -6), ('trail', 0), ('creek', 6)):
    features.append({'id': kind, 'name': kind, 'aliases': [], 'kind': kind, 'heightM': 0, 'geometry': {'type': 'polyline', 'points': [{'x': (point[0]+n)*100, 'y': (point[1]+east)*100, 'z': 0} for n in (-10, 10)]}})
for kind, north, east in (('structure', -8, -8), ('pond', 4, 4), ('forest', 0, -8)):
    features.append({'id': kind, 'name': kind, 'aliases': [], 'kind': kind, 'heightM': 0 if kind == 'pond' else 5, 'geometry': {'type': 'polygon', 'points': [{'x': (point[0]+north+n)*100, 'y': (point[1]+east+e)*100, 'z': 0} for n, e in ((0,0),(0,4),(4,4),(4,0))]}})
landmark_path.write_text(json.dumps({**document, 'landmarks': features}))
behavior, details = condition(grid, prior, load(landmark_path))
assert domain_for('dementia') == 'temperate_mountainous'
assert np.isfinite(behavior).all() and abs(float(behavior.sum())-1) < 1e-9
assert details['unresolved_weight'] == .2
visible = aerial_visibility(grid, features)
assert visible.any() and (~visible).any()
pod, footprint = capture_pod(record, grid, model, visible)
assert (pod[~visible] == 0).all() and (pod[visible] > 0).any()
ground_evidence = Evidence(behavior, grid, model['false_alarm_rate'])
ground_ledger = Detections()
ground_ledger.add_frame('checked-camera', [])
ground_evidence.apply('checked-camera', pod, footprint, ground_ledger, True)
assert (ground_evidence.maximum_pod[~visible] == 0).all()
structure = next(feature for feature in features if feature['kind'] == 'structure')
fractions = footprint_fraction(grid, structure['geometry'])
expected_removed = float(ground_evidence.probability @ fractions)
camera_history = ground_evidence.maximum_pod.copy()
effect = ground_evidence.ground_negative('operator-confirmed-task', structure['id'], structure['geometry'])
assert abs(effect['removed_mass'] - expected_removed) < 1e-12
assert abs(effect['removed_mass'] + effect['retained_mass'] - 1) < 1e-12
assert ground_evidence.structure_mass(structure['id'], structure['geometry']) == 0
np.testing.assert_array_equal(ground_evidence.prior, behavior)
np.testing.assert_array_equal(ground_evidence.maximum_pod, camera_history)
ground_evidence.recompute()
assert abs(float(ground_evidence.probability.sum()) - 1) < 1e-9
assert ground_evidence.structure_mass(structure['id'], structure['geometry']) == 0
for level in ('document', 'landmark', 'geometry', 'point'):
    for field in ('subject', 'clue'):
        poison = copy.deepcopy(document)
        target = poison if level == 'document' else poison['landmarks'][0] if level == 'landmark' else poison['landmarks'][0]['geometry'] if level == 'geometry' else poison['landmarks'][0]['geometry']['points'][0]
        target[field] = {'placement': str(sys.argv[2])}
        landmark_path.write_text(json.dumps(poison))
        with check.assertRaises(AssertionError):
            load(landmark_path)
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
