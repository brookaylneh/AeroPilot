"""Convert JSON-array evidence registries to the append-only assets.txt; the originals stay."""

import argparse
import json
from pathlib import Path


def migrate(missions):
    converted = []
    for registry in sorted(missions.glob("*/evidence/assets.json")):
        names = json.loads(registry.read_text())
        assert isinstance(names, list) and all(
            isinstance(name, str) and name and "/" not in name for name in names
        ), f"{registry}: malformed registry"
        missing = [name for name in names if not (registry.parent / name).is_file()]
        assert not missing, f"{registry}: registered assets are missing: {missing}"
        with registry.with_name("assets.txt").open("x") as stream:
            stream.write("".join(name + "\n" for name in sorted(set(names))))
        converted.append(registry.parent)
    return converted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("missions", type=Path, nargs="+", help="mission data directories")
    for directory in parser.parse_args().missions:
        assert directory.is_dir(), f"Not a directory: {directory}"
        for evidence in migrate(directory):
            print(evidence)


if __name__ == "__main__":
    main()
