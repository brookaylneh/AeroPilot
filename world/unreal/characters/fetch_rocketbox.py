"""Fetch or verify the pinned MIT Rocketbox assets."""

import argparse
import concurrent.futures
import hashlib
import json
import urllib.request
from pathlib import Path

from mission_planner.environment import load_env

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def verify(data, item):
    assert len(data) == item["bytes"], item["path"]
    blob = b"blob " + str(len(data)).encode() + b"\0" + data
    assert hashlib.sha1(blob).hexdigest() == item["git_blob_sha1"], item["path"]
    return {**item, "sha256": hashlib.sha256(data).hexdigest()}


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    vendor = ROOT / manifest["vendor"]
    args.output.mkdir(parents=True, exist_ok=False)
    if args.verify:
        assert vendor.is_dir(), vendor
    else:
        vendor.mkdir(parents=True, exist_ok=False)

    def acquire(item):
        path = vendor / item["path"]
        assert path.resolve().is_relative_to(vendor.resolve())
        url = (
            "https://raw.githubusercontent.com/microsoft/Microsoft-Rocketbox/"
            + manifest["commit"]
            + "/"
            + item["path"]
        )
        if args.verify:
            data = path.read_bytes()
        else:
            with urllib.request.urlopen(url, timeout=60) as response:
                assert response.status == 200
                data = response.read()
            verify(data, item)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
        return {**verify(data, item), "url": url}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        files = list(pool.map(acquire, manifest["files"]))
    result = {"commit": manifest["commit"], "files": files, "bytes": sum(f["bytes"] for f in files)}
    (args.output / "files.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"files": len(files), "bytes": result["bytes"], "verified": True}))


if __name__ == "__main__":
    main()
