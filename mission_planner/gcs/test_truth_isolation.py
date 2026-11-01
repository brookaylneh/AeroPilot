"""The live planner and review path cannot read evaluator truth."""

import ast
import subprocess
import sys
import unittest

from mission_planner.environment import ROOT


class TruthIsolation(unittest.TestCase):
    def test_runtime_imports_exclude_calibration_and_scenario_authoring(self):
        pending, checked = ["mission_planner.gcs.service"], set()
        while pending:
            module = pending.pop()
            if module in checked:
                continue
            self.assertFalse(module.startswith(("scenario_creator", "mission_planner.search")))
            self.assertFalse(module.startswith("mission_planner.agents.perception"))
            checked.add(module)
            path = ROOT.joinpath(*module.split(".")).with_suffix(".py")
            for node in ast.walk(ast.parse(path.read_text())):
                imports = (
                    [node.module, *(node.module + "." + alias.name for alias in node.names)]
                    if isinstance(node, ast.ImportFrom) and node.module is not None
                    else [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                for name in imports:
                    if name.startswith(("mission_planner.", "world.", "scenario_creator")):
                        if ROOT.joinpath(*name.split(".")).with_suffix(".py").is_file():
                            pending.append(name)
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for forbidden in (
                        "actorplacement",
                        "actortelemetry",
                        "runtime-actors",
                        "evaluator/",
                        "scenario.json",
                        ".local/data/calibration",
                        "labels.json",
                    ):
                        self.assertNotIn(forbidden, node.value.lower(), (path, node.value))
        self.assertIn("mission_planner.agents.review", checked)
        self.assertIn("mission_planner.gcs.inspection", checked)

    def test_candidate_review_runs_with_private_files_denied(self):
        script = """
import sys
import unittest

def audit(event, arguments):
    if event == 'open' and isinstance(arguments[0], (str, bytes)):
        path = str(arguments[0])
        assert not any(p in path for p in ('data/calibration/', '/scenarios/', '/truth/')), path
sys.addaudithook(audit)
try:
    open('/truth/forbidden.json')
except AssertionError:
    pass
else:
    raise AssertionError('Truth guard did not run')
suite = unittest.defaultTestLoader.loadTestsFromName(
    'mission_planner.gcs.test_rescue_flow.RescueFlow.test_uncertain_review_preserves_lock_and_evidence_until_wonder_releases'
)
result = unittest.TextTestRunner().run(suite)
assert result.wasSuccessful()
"""
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
