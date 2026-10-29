"""Download the CC0 Poly Haven texture maps pinned in a lock file and verify them."""

import argparse
import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / ".local/vendor/polyhaven"


def path(entry):
    return VENDOR / entry["asset"] / Path(entry["url"]).name


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads((ROOT / args.lock).read_text())
    assert lock["license"] == "CC0-1.0"
    for entry in lock["files"]:
        target = path(entry)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            # Poly Haven's CDN rejects urllib's default agent.
            request = urllib.request.Request(entry["url"], headers={"User-Agent": "SNR-Valley/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                with target.open("xb") as output:
                    shutil.copyfileobj(response, output)
        data = target.read_bytes()
        assert len(data) == entry["bytes"], target
        assert hashlib.md5(data).hexdigest() == entry["md5"], target
        print(
            json.dumps(
                {"path": str(target.relative_to(ROOT)), "sha256": hashlib.sha256(data).hexdigest()}
            )
        )


if __name__ == "__main__":
    main()
