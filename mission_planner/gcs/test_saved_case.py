"""Saved public briefs stay independent of private authoring snapshots."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mission_planner.gcs.service import read_case
from world.cosys.terrain import BOUNDS


class SavedCase(unittest.TestCase):
    def test_public_record_round_trip_and_private_data_rejection(self):
        identity = "9b6e308e-3257-43d4-bf5d-5e1bcf3a9811"
        record = {
            "schemaVersion": 2,
            "id": identity,
            "name": "Meadow hiker",
            "report": {
                "brief": "A hiker, 34 years old, missing for about 3 hours.",
                "portrait": "",
            },
            "world": {
                "environmentId": "open-meadow",
                "assetVersion": "test",
                "coordinateSystem": "UE_LH_Z_UP_CENTIMETERS",
                "searchBounds": [
                    {
                        "id": "open-meadow",
                        "minX": BOUNDS["min"]["x"],
                        "maxX": BOUNDS["max"]["x"],
                        "minY": BOUNDS["min"]["y"],
                        "maxY": BOUNDS["max"]["y"],
                    }
                ],
            },
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"SNR_SCENARIO_DATA_DIR": directory}),
        ):
            root = Path(directory)
            (root / f"{identity}.json").write_text(json.dumps({"authoring": "private"}))
            with self.assertRaises(FileNotFoundError):
                read_case(identity)
            public = root / "briefings" / f"{identity}.json"
            public.parent.mkdir()
            public.write_text(json.dumps(record))
            self.assertEqual(read_case(identity), record)
            invalid = [
                {**record, "authoring": {"position": [1, 2, 3]}},
                {**record, "schemaVersion": 1},
                {**record, "report": {**record["report"], "situationId": "lost"}},
                {**record, "report": {"brief": "x" * 4001, "portrait": ""}},
            ]
            moved = copy.deepcopy(record)
            moved["world"]["searchBounds"][0]["maxX"] += 1
            invalid.append(moved)
            for value in invalid:
                public.write_text(json.dumps(value))
                with self.assertRaises(AssertionError):
                    read_case(identity)
