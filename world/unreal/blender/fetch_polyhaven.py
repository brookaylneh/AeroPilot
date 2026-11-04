"""Download the CC0 Poly Haven texture maps pinned in polyhaven.lock.json and verify them."""

import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

from world.unreal.blender.spec import LOCK, ROOT, TEXTURES


def main():
    lock = json.loads(LOCK.read_text())
    assert lock["license"] == "CC0-1.0"
    for entry in lock["files"]:
        target = TEXTURES / entry["asset"] / Path(entry["url"]).name
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
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], target
        print(json.dumps({"path": str(target.relative_to(ROOT)), "sha256": entry["sha256"]}))


if __name__ == "__main__":
    main()
