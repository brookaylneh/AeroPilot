"""The only module that opens private scenario files: subject positions and public briefings."""

import json
import os
from pathlib import Path


def scenario_dir():
    return Path(os.environ["SNR_SCENARIO_DATA_DIR"])


def subject_position(case_id):
    """The authored subject position in mission metres north/east."""
    value = json.loads((scenario_dir() / f"{case_id}.json").read_text())
    assert value["id"].lower() == case_id.lower()
    position = value["authoring"]["position"]
    return {"north_m": position["x"] / 100, "east_m": position["y"] / 100}


def briefings():
    """Every public briefing as (case id, name, brief text), sorted by case id."""
    rows = []
    for path in sorted((scenario_dir() / "briefings").glob("*.json")):
        value = json.loads(path.read_text())
        rows.append((value["id"], value["name"], value["report"]["brief"]))
    assert rows, "No public briefings found"
    return rows
