"""Build selected roster characters, with one cloud worker and one graphics worker."""

import argparse
import asyncio
import copy
import json
import os
import time
from pathlib import Path

from mission_planner.environment import load_env

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ENGINE = ROOT / ".local/toolchains/UnrealEngine-5.8.2"


def cameras(recipe):
    profile = json.loads((HERE / "review-cameras.json").read_text())
    result = copy.deepcopy(profile)
    ratio = recipe["body"]["height_cm"] / 178
    for view in result["views"]:
        for field in ("position_cm", "target_cm"):
            view[field] = [value * ratio for value in view[field]]
        if view["name"] == "face":
            view["position_cm"][1] = 95
    return result


async def run_stage(recipe_path, output, stage):
    script = f"{HERE / 'author_roster.py'} {recipe_path} {stage} {output}"
    command = ["bash", str(ROOT / "world/unreal/scripts/unreal-container.sh")]
    common = [
        "-EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities,MetaHumanCharacter,MetaHumanGenerator",
        "-unattended",
        "-nosound",
        "-NoSplash",
        "-nocrashreports",
        "-NoMetaHumanAccountPortalLoginFallback",
        "-ScriptErrorsAreFatal",
        "-stdout",
        "-FullStdOutLogOutput",
        "-ddc=InstalledNoZenLocalFallback",
    ]
    project = str(ROOT / "world/unreal/ScenarioStudio/ScenarioStudio.uproject")
    if stage in ("prepare", "style"):
        command += [
            "exec",
            "timeout",
            "1200",
            str(ENGINE / "Engine/Binaries/Linux/UnrealEditor-Cmd"),
            project,
            "-run=pythonscript",
            "-script=" + script,
            "-nullrhi",
        ]
        cpus, memory = "4", "32g"
    else:
        settings = [
            "[/Script/Engine.RendererSettings]:r.RayTracing=1",
            "[/Script/Engine.RendererSettings]:r.DynamicGlobalIlluminationMethod=1",
            "[/Script/Engine.RendererSettings]:r.ReflectionMethod=1",
            "[/Script/Engine.RendererSettings]:r.Lumen.HardwareRayTracing=1",
            "[/Script/Engine.RendererSettings]:r.Shadow.Virtual.Enable=1",
            "[/Script/LinuxTargetPlatform.LinuxTargetSettings]:-TargetedRHIs=SF_VULKAN_SM5",
            "[/Script/LinuxTargetPlatform.LinuxTargetSettings]:+TargetedRHIs=SF_VULKAN_SM6",
        ]
        command += [
            "character-authoring",
            "timeout",
            "1200",
            str(ENGINE / "Engine/Binaries/Linux/UnrealEditor"),
            project,
            "/Engine/Maps/Entry",
            "-ExecutePythonScript=" + script,
            "-vulkan",
            "-sm6",
            "-ini:Engine:" + ",".join(settings),
            "-ExecCmds=t.MaxFPS 30",
            "-ResX=1600",
            "-ResY=1600",
        ]
        cpus, memory = "8", "64g"
    command += common
    environment = dict(os.environ, UE_ROOT=str(ENGINE), SNR_CPU_LIMIT=cpus, SNR_MEMORY_LIMIT=memory)
    environment["SNR_CONTAINER_NAME"] = "snr-character-" + recipe_path.stem
    started = time.monotonic()
    print(
        json.dumps({"character": recipe_path.stem, "stage": stage, "state": "started"}), flush=True
    )
    with (output / (stage + ".log")).open("xb") as log:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=ROOT, env=environment, stdout=log, stderr=asyncio.subprocess.STDOUT
        )
        code = await process.wait()
    report = {"exit_code": code, "elapsed_seconds": time.monotonic() - started, "command": command}
    with (output / (stage + "-process.json")).open("x") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({"character": recipe_path.stem, "stage": stage, **report}), flush=True)
    assert code == 0, (recipe_path.stem, stage, code)
    report_path = output / ("render/result.json" if stage == "render" else stage + ".json")
    assert report_path.is_file(), report_path
    print(
        json.dumps({"character": recipe_path.stem, "stage": stage, "state": "complete"}), flush=True
    )


async def build(args):
    cloud = asyncio.Semaphore(1)
    graphics = asyncio.Semaphore(1)

    async def character(selection):
        identifier, start_at = selection.split(":")
        recipe_path = HERE / "recipes" / (identifier + ".json")
        recipe = json.loads(recipe_path.read_text())
        assert recipe["character_id"] == identifier
        output = args.output.resolve() / identifier
        if start_at == "prepare":
            output.mkdir(exist_ok=False, parents=True)
            with (output / "cameras.json").open("x") as stream:
                json.dump(cameras(recipe), stream, indent=2)
        else:
            assert output.is_dir() and (output / "cameras.json").is_file()
        if start_at in ("prepare", "style"):
            async with cloud:
                await run_stage(recipe_path, output, start_at)
        async with graphics:
            if start_at != "render":
                stage = "materials" if start_at == "materials" else "finish"
                await run_stage(recipe_path, output, stage)
            await run_stage(recipe_path, output, "render")
        return identifier

    results = await asyncio.gather(*(character(i) for i in args.characters), return_exceptions=True)
    errors = [str(result) for result in results if isinstance(result, BaseException)]
    assert not errors, errors


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("characters", nargs="+", metavar="ID:STAGE")
    args = parser.parse_args()
    known = {item["id"] for item in json.loads((HERE / "roster.json").read_text())["characters"]}
    selections = [value.split(":") for value in args.characters]
    assert all(len(value) == 2 for value in selections)
    identifiers = [value[0] for value in selections]
    assert len(set(identifiers)) == len(identifiers)
    assert set(identifiers) <= known
    assert all(
        value[1] in ("prepare", "style", "finish", "materials", "render") for value in selections
    )
    asyncio.run(build(args))


if __name__ == "__main__":
    main()
