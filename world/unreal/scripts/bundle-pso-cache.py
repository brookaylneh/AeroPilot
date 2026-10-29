"""Expand a measured Vulkan SM6 cache and install it for the next package cook."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from mission_planner.environment import load_env


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", type=Path)
    parser.add_argument("shader_keys", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    inputs = sorted(path.resolve() for path in args.recordings.glob("*.rec.upipelinecache"))
    assert inputs, args.recordings
    keys = [
        args.shader_keys / f"ShaderStableInfo-{name}-SF_VULKAN_SM6.shk"
        for name in ("Global", "ScenarioStudio")
    ]
    assert all(path.is_file() for path in keys), keys
    project = Path("world/unreal/ScenarioStudio/ScenarioStudio.uproject").resolve()
    target = project.parent / "Build/Linux/PipelineCaches/ScenarioStudio_SF_VULKAN_SM6.spc"
    assert not target.exists(), target
    args.output.mkdir()
    cache = args.output.resolve() / target.name
    editor = Path(os.environ["UE_ROOT"]) / "Engine/Binaries/Linux/UnrealEditor-Cmd"
    command = [
        "bash",
        "world/unreal/scripts/unreal-container.sh",
        "exec",
        str(editor),
        str(project),
        "-run=ShaderPipelineCacheTools",
        "Expand",
        str(args.recordings.resolve() / "*.rec.upipelinecache"),
        *map(str, (key.resolve() for key in keys)),
        str(cache),
        "-nullrhi",
        "-unattended",
        "-nosound",
        "-nocrashreports",
        "-stdout",
        "-FullStdOutLogOutput",
    ]
    with (args.output / "expand.log").open("x") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
    assert cache.is_file() and cache.stat().st_size > 0, cache
    log_text = (args.output / "expand.log").read_text()
    counts = re.findall(r"Generated (\d+) stable PSOs total", log_text)
    assert len(counts) == 1 and int(counts[0]) > 0, counts
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cache, target)
    artifacts = [
        {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in inputs + keys + [target]
    ]
    (args.output / "result.json").write_text(
        json.dumps(
            {"command": command, "stable_pso_count": int(counts[0]), "artifacts": artifacts},
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"cache": str(target), "bytes": target.stat().st_size}))


if __name__ == "__main__":
    main()
