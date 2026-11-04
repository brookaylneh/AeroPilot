"""Model workers bootstrap the installed entry point without importing flight control."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from mission_planner.environment import ROOT


def worker_imports(directory, workers):
    directory = Path(directory)
    (directory / str(os.getpid())).touch(exist_ok=False)
    deadline = time.monotonic() + 15
    while len(list(directory.iterdir())) < workers:
        assert time.monotonic() < deadline, "All model workers must finish starting"
        time.sleep(0.01)
    return {
        "pid": os.getpid(),
        "main": sys.modules["__main__"].__spec__.name,
        "flight_imported": "mission_planner.gcs.service" in sys.modules,
    }


class HostSpawnTests(unittest.TestCase):
    def test_spawned_wonder_and_review_workers_do_not_load_flight_control(self):
        source = """
import __main__
import importlib.util
import json
import sys
from mission_planner.agents.model_process import model_pool
from mission_planner.gcs.test_host import worker_imports

__main__.__spec__ = importlib.util.find_spec("mission_planner.gcs.host")
workers = int(sys.argv[2])
with model_pool(workers) as pool:
    futures = [pool.submit(worker_imports, sys.argv[1], workers) for _ in range(workers)]
    print(json.dumps([future.result(timeout=20) for future in futures]))
"""
        for workers in (1, 4):
            with (
                self.subTest(workers=workers),
                tempfile.TemporaryDirectory(dir=ROOT / ".local") as directory,
            ):
                result = subprocess.run(
                    [sys.executable, "-c", source, directory, str(workers)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                )
                rows = json.loads(result.stdout)
                self.assertEqual(len({row["pid"] for row in rows}), workers)
                self.assertTrue(all(row["main"] == "mission_planner.gcs.host" for row in rows))
                self.assertFalse(any(row["flight_imported"] for row in rows))
