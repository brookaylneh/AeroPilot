"""Acquire CC0 review assets, render metric previews and assemble the candidate sheet."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "world/unreal/valley-assets.lock.json"
OUT = ROOT / ".local/world/valley/asset-previews"
RESERVE = 40 * 1024**3
QUOTA = 3_000_000_000


def size():
    return sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file() and not p.is_symlink())


def capacity(additional):
    free = shutil.disk_usage(ROOT).free
    used = size()
    assert free - additional >= RESERVE, ("disk reserve", free, additional, RESERVE)
    assert used + additional <= QUOTA, ("asset review quota", used, additional, QUOTA)
    print(
        json.dumps({"diskFreeBytes": free, "reviewBytes": used, "additionalBytes": additional}),
        flush=True,
    )


def download(entry, target):
    assert not target.exists(), target
    capacity(entry["size"])
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        entry["url"], headers={"User-Agent": "SNR-Valley-Asset-Review/1.0"}
    )
    with urllib.request.urlopen(request, timeout=60) as response, target.open("xb") as stream:
        shutil.copyfileobj(response, stream)
    data = target.read_bytes()
    assert len(data) == entry["size"], target
    algorithm = entry["digestAlgorithm"]
    assert hashlib.new(algorithm, data).hexdigest() == entry["digest"], target
    return {
        "path": str(target.relative_to(ROOT)),
        "url": entry["url"],
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def toolchain(lock, evidence):
    entry = lock["previewToolchain"]
    archive = OUT / entry["archive"]
    assert archive.is_file(), "Run download-toolchain first"
    data = archive.read_bytes()
    assert len(data) == entry["size"]
    assert hashlib.sha256(data).hexdigest() == entry["digest"]
    record = {"url": entry["url"], "bytes": len(data), "sha256": entry["digest"]}
    del data
    with tarfile.open(archive) as source:
        members = source.getmembers()
        assert not (OUT / Path(members[0].name).parts[0]).exists()
        capacity(sum(item.size for item in members))
        for item in members:
            target = (OUT / item.name).resolve()
            assert target.is_relative_to(OUT.resolve()), item.name
            assert item.isfile() or item.isdir() or item.issym(), item.name
            if item.issym():
                assert (target.parent / item.linkname).resolve().is_relative_to(OUT.resolve())
        source.extractall(OUT, members=members)
    record["extractedBytes"] = sum(item.size for item in members)
    with (evidence / "toolchain-extract.json").open("x") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")


def tool_environment():
    environment = {
        **os.environ,
        "LD_LIBRARY_PATH": str(OUT / "runtime-libs/usr/lib/x86_64-linux-gnu"),
    }
    for key, name in (
        ("BLENDER_USER_CONFIG", "config"),
        ("BLENDER_USER_SCRIPTS", "scripts"),
        ("BLENDER_USER_DATAFILES", "datafiles"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_CONFIG_HOME", "xdg-config"),
        ("TMPDIR", "tmp"),
    ):
        path = OUT / ("blender-user-" + name)
        path.mkdir(exist_ok=True)
        environment[key] = str(path)
    return environment


def dependencies(lock, evidence):
    for entry in lock["previewToolchain"]["runtimeDependencies"]:
        target = OUT / Path(entry["url"]).name
        record = download(entry, target)
        subprocess.run(
            ["dpkg-deb", "-x", str(target), str(OUT / "runtime-libs")], check=True, timeout=30
        )
        with (evidence / (entry["name"] + ".json")).open("x") as stream:
            stream.write(json.dumps(record, indent=2) + "\n")


def verify_toolchain(lock, evidence):
    entry = lock["previewToolchain"]
    archive = OUT / entry["archive"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == entry["digest"]
    executable = ROOT / entry["executable"]
    with tarfile.open(archive) as source:
        member = source.getmember(str(executable.relative_to(OUT)))
        assert (
            hashlib.sha256(source.extractfile(member).read()).digest()
            == hashlib.sha256(executable.read_bytes()).digest()
        )
    result = subprocess.run(
        [str(executable), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=tool_environment(),
    )
    report = {
        "url": entry["url"],
        "archiveBytes": entry["size"],
        "archiveSha256": entry["digest"],
        "executableSha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "versionOutput": result.stdout,
        "archiveRemovedAfterVerification": True,
    }
    archive.unlink()
    with (evidence / "toolchain.json").open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


def acquire(candidate):
    assert candidate["license"]["id"] == "CC0-1.0"
    assert candidate["acquisition"]["status"] in ("proposed", "acquired_for_preview")
    folder = OUT / candidate["id"]
    folder.mkdir()
    files = candidate["sourceFiles"]
    capacity(sum(item["size"] for item in files))
    records = [download(item, folder / item["relativePath"]) for item in files]
    candidate["acquisition"] = {"status": "acquired_for_preview", "files": records, "reason": None}
    return candidate


def render(lock, candidate, evidence):
    assert candidate["acquisition"]["status"] in ("acquired_for_preview", "original_code")
    capacity(16 * 1024**2)
    target = OUT / candidate["id"] / "renders"
    assert not target.exists(), target
    target.mkdir(parents=True)
    renderer = ROOT / "world/unreal/render_asset_preview.py"
    source = renderer.read_bytes()
    source_receipt = evidence / f"{candidate['id']}-renderer.py"
    with source_receipt.open("xb") as stream:
        stream.write(source)
    command = [
        "taskset",
        "-c",
        "8,9",
        str(ROOT / lock["previewToolchain"]["executable"]),
        "--background",
        "--factory-startup",
        "--threads",
        "2",
        "--python-exit-code",
        "1",
        "--python",
        str(renderer),
        "--",
        candidate["id"],
    ]
    with (evidence / f"{candidate['id']}-render.log").open("x") as log:
        subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=600,
            env=tool_environment(),
        )
    measured = json.loads((target / "metrics.json").read_text())
    assert measured["generatorSha256"] == hashlib.sha256(source).hexdigest()
    measured["rendererSource"] = str(source_receipt.relative_to(ROOT))
    candidate["preview"] = measured
    if candidate["previewInput"]["kind"] == "procedural":
        measured["generatedAsset"]["license"] = candidate["license"]["id"]
        candidate["version"]["sha256"] = measured["generatorSha256"]
    return candidate


def contact(lock, evidence):
    target = evidence / "contact-sheet.png"
    assert not target.exists(), target
    font = ROOT / "web/public/fonts/dm-sans.ttf"
    normal = ImageFont.truetype(str(font), 24)
    small = ImageFont.truetype(str(font), 18)
    title = ImageFont.truetype(str(font), 36)
    row_height = 480
    sheet = Image.new("RGB", (1920, 145 + len(lock["candidates"]) * row_height), "#14221d")
    draw = ImageDraw.Draw(sheet)
    draw.text((32, 20), "Sierra valley · asset candidates", font=title, fill="#f1ead5")
    draw.text(
        (32, 72),
        "Stage 1 · CC0 sources and original concepts · 30 m nadir, 70° horizontal FOV, 16:9",
        font=normal,
        fill="#bed0bf",
    )
    for index, candidate in enumerate(lock["candidates"]):
        y = 135 + index * row_height
        draw.rectangle((20, y, 1900, y + row_height - 12), fill="#1e3028")
        draw.text(
            (40, y + 18),
            f"{candidate['category'].upper()}  /  {candidate['name']}",
            font=normal,
            fill="#f1ead5",
        )
        preview = candidate["preview"]
        if preview is None:
            for x, label in ((40, "30 m nadir unavailable"), (710, "Close view unavailable")):
                draw.rectangle((x, y + 66, x + 640, y + 326), fill="#293730")
                draw.text((x + 20, y + 160), label, font=normal, fill="#bed0bf")
            summary = [
                "PROPOSED / UNACQUIRED",
                candidate["acquisition"]["reason"],
                "No measured geometry or render budget",
            ]
        else:
            for x, key in ((40, "nadir"), (710, "close")):
                image = Image.open(ROOT / preview[key]["path"]).convert("RGB")
                image.thumbnail((640, 360))
                sheet.paste(image, (x, y + 66))
            summary = [
                f"{preview['singleAssetTriangles']:,} triangles per asset",
                f"{preview['instances']} preview instance(s); {preview['triangles']:,} triangles",
                f"{preview['texturePixels']:,} texture pixels (incl. derived)",
                "Preview bounds m: " + " × ".join(f"{v:.2f}" for v in preview["boundsMetres"]),
                "Source scale × " + str(preview["scale"]),
            ]
        summary += [
            "Source: "
            + (
                "Poly Haven"
                if candidate["previewInput"]["kind"] == "gltf"
                else "Original procedural concept"
            ),
            candidate["license"]["id"],
            "AI-assisted original; public licence ungranted"
            if candidate["previewInput"]["kind"] == "procedural"
            else "AI use allowed; human-authored source",
            "Asset price: USD " + str(candidate["costUsd"]),
            candidate["artDirection"],
        ]
        cursor = y + 70
        for text in summary:
            words, line = text.split(), ""
            for word in words:
                if draw.textlength(line + " " + word, font=small) > 475:
                    draw.text((1380, cursor), line, font=small, fill="#bed0bf")
                    cursor += 24
                    line = word
                else:
                    line = (line + " " + word).strip()
            draw.text((1380, cursor), line, font=small, fill="#bed0bf")
            cursor += 29
    sheet.save(target)
    overview = Image.new("RGB", (1920, 120 + 340 * ((len(lock["candidates"]) + 2) // 3)), "#14221d")
    overview_draw = ImageDraw.Draw(overview)
    overview_draw.text(
        (24, 20), "Sierra valley · Stage 1 candidate overview", font=title, fill="#f1ead5"
    )
    overview_draw.text(
        (24, 70),
        "Each pair: 30 m nadir / close · CC0 sources and original concepts · details in the full sheet",
        font=normal,
        fill="#bed0bf",
    )
    for index, candidate in enumerate(lock["candidates"]):
        x, y = index % 3 * 640, 120 + index // 3 * 340
        overview_draw.rectangle((x + 5, y + 5, x + 635, y + 335), fill="#1e3028")
        overview_draw.text(
            (x + 14, y + 15),
            candidate["category"].upper() + " / " + candidate["name"],
            font=small,
            fill="#f1ead5",
        )
        preview = candidate["preview"]
        assert preview is not None, candidate["id"]
        for offset, key in ((0, "nadir"), (320, "close")):
            tile = Image.open(ROOT / preview[key]["path"]).convert("RGB")
            tile.thumbnail((310, 175))
            overview.paste(tile, (x + offset + 5, y + 54))
        overview_draw.text(
            (x + 14, y + 241),
            f"{preview['singleAssetTriangles']:,} triangles · USD {candidate['costUsd']} · "
            + candidate["license"]["id"],
            font=small,
            fill="#bed0bf",
        )
        overview_draw.text(
            (x + 14, y + 270),
            "AI-assisted original · public licence ungranted"
            if candidate["previewInput"]["kind"] == "procedural"
            else "AI use allowed · Poly Haven source",
            font=small,
            fill="#bed0bf",
        )
        overview_draw.text(
            (x + 14, y + 299),
            "Preview scale × "
            + str(preview["scale"])
            + f" · {preview['instances']} nadir instance(s)",
            font=small,
            fill="#bed0bf",
        )
    overview_path = evidence / "contact-overview.png"
    assert not overview_path.exists(), overview_path
    overview.save(overview_path)
    report = {
        "contactSheet": str(target.relative_to(ROOT)),
        "width": sheet.width,
        "height": sheet.height,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "overview": str(overview_path.relative_to(ROOT)),
        "overviewWidth": overview.width,
        "overviewHeight": overview.height,
        "overviewSha256": hashlib.sha256(overview_path.read_bytes()).hexdigest(),
        "candidateCount": len(lock["candidates"]),
        "categoryCount": len({c["category"] for c in lock["candidates"]}),
        "acceptedViewCount": 2 * len(lock["candidates"]),
        "assetPurchaseUsd": sum(c["costUsd"] for c in lock["candidates"]),
        "assetSourceDownloadBytes": sum(
            f["bytes"] for c in lock["candidates"] for f in c["acquisition"]["files"]
        ),
        "reviewDirectoryBytes": size(),
        "diskFreeBytes": shutil.disk_usage(ROOT).free,
        "acceptedPreviewRenderSeconds": sum(
            c["preview"][view]["renderSeconds"]
            for c in lock["candidates"]
            for view in ("nadir", "close")
        ),
        "rendererSourceHashes": sorted(
            {c["preview"]["generatorSha256"] for c in lock["candidates"]}
        ),
        "unrealImports": 0,
        "runtimeBudgetValidated": False,
    }
    with (evidence / "summary.json").open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report))


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "download-toolchain",
            "install-toolchain",
            "dependencies",
            "verify-toolchain",
            "acquire",
            "render",
            "contact",
        ),
    )
    parser.add_argument("--candidate")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence = ROOT / args.evidence_dir
    assert evidence.resolve().is_relative_to((ROOT / ".local/evidence/world").resolve())
    evidence.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK.read_text())
    if args.action == "download-toolchain":
        download(lock["previewToolchain"], OUT / lock["previewToolchain"]["archive"])
    elif args.action == "install-toolchain":
        toolchain(lock, evidence)
    elif args.action == "dependencies":
        dependencies(lock, evidence)
    elif args.action == "verify-toolchain":
        verify_toolchain(lock, evidence)
    elif args.action == "contact":
        contact(lock, evidence)
    else:
        assert args.candidate is not None
        match = [item for item in lock["candidates"] if item["id"] == args.candidate]
        assert len(match) == 1, args.candidate
        candidate = match[0]
        if args.action == "acquire":
            acquire(candidate)
        else:
            render(lock, candidate, evidence)
        LOCK.write_text(json.dumps(lock, indent=2) + "\n")


if __name__ == "__main__":
    main()
