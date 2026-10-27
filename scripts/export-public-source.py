#!/usr/bin/env python3
"""Export only reviewed source and redistributable preview files; never the workspace."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
# A new source location needs explicit review before it becomes publishable.
PATTERNS = [
    "README.md",
    "CONTRIBUTING.md",
    ".gitignore",
    ".env.example",
    ".editorconfig",
    ".clang-format",
    ".prettierrc.json",
    ".prettierignore",
    "ruff.toml",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "vite.config.ts",
    "playwright.config.ts",
    "index.html",
    "src/*.ts",
    "src/*.tsx",
    "src/*.css",
    "server/*.ts",
    "shared/*.ts",
    "tests/*.test.ts",
    "tests/*.test.mjs",
    "tests/browser/*.ts",
    "tests/python/*.py",
    "simulation/phase2a/*.py",
    "simulation/phase2a/requirements.txt",
    "simulation/phase2a/world.json",
    "unreal/ScenarioStudio/Source/**/*.cpp",
    "unreal/ScenarioStudio/Source/**/*.h",
    "unreal/ScenarioStudio/Source/**/*.cs",
    "unreal/ScenarioStudio/Config/*.ini",
    "unreal/ScenarioStudio/Scripts/*.py",
    "unreal/ScenarioStudio/ScenarioStudio.uproject",
    "unreal/dependencies.lock.json",
    "unreal/Dockerfile",
    "unreal/wilderness-assets.lock.json",
    "unreal/equipment-assets.lock.json",
    "unreal/open-meadow-vegetation.json",
    "unreal/character-runtime.json",
    "unreal/creator-people.json",
    "unreal/children-assets.json",
    "unreal/children-runtime.json",
    "unreal/materials.csv",
    "unreal/cosys-settings.json",
    "unreal/cosys-four-drones.json",
    "unreal/cosys-four-drones-test.json",
    "public/assets/*",
    "public/portraits/*.jpg",
    "public/maps/open-meadow-topographic.png",
    "docs/setup.md",
    "docs/architecture.md",
    "docs/scenario-creator.md",
    "docs/asset-dependencies.md",
    "docs/public-source.md",
    "scripts/lib/*.mjs",
    "deploy/rtx/runtime.Dockerfile",
    "deploy/fab.Dockerfile",
    "deploy/rtx/runtime.env.example",
    "docs/rtx-migration.md",
    "docs/navigation-controls.md",
    "docs/stream-quality.md",
    "scripts/prepare-rtx-server.sh",
]
SCRIPTS = [
    "export-public-source.py",
    "fetch-assets.py",
    "fetch-cosys.py",
    "fetch-wilderness.py",
    "build-unreal.sh",
    "package-unreal.sh",
    "author-unreal.sh",
    "unreal-container.sh",
    "run-packaged.sh",
    "run-unreal-foundation.sh",
    "setup-pixel-streaming.sh",
    "start-pixel-streaming.cjs",
    "configure-turn.py",
    "configure-four-drones.py",
    "generate-open-meadow.py",
    "generate-minimap.py",
    "generate-vegetation-mask.py",
    "assemble-metahuman.sh",
    "epic-login-open.py",
    "stage-live-release.sh",
    "check-drone-api.mjs",
    "check-drone-browser.mjs",
    "check-drone-controls.mjs",
    "check-navigation.mjs",
    "check-mouse-look.mjs",
    "check-stream-performance.mjs",
    "check-stream-quality.mjs",
    "check-stream-reconnect.mjs",
    "check-native-workflow.mjs",
    "check-creator.mjs",
    "check-creator-activities.mjs",
    "rig-children.py",
    "checkpoint-live-scene.mjs",
]
PATTERNS += ["scripts/" + name for name in SCRIPTS]
BINARY_ASSETS = {
    "ground.jpg",
    "ground-normal.jpg",
    "rock.jpg",
    "rock-normal.jpg",
    "dm-sans.ttf",
    "manrope.ttf",
    "open-meadow-topographic.png",
}
# Reviewed final renders only; acquired character meshes and textures remain private.
BINARY_ASSETS.update(person["id"] + ".jpg" for person in json.loads((ROOT / "unreal/creator-people.json").read_text()))

FORBIDDEN = [
    ("signed download URL", re.compile(r"https?://[^\s\"\']*[?&]X-Amz-", re.I)),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "credential value",
        re.compile(
            r"(?:SecurityToken|api[_-]?key|password)\s*[:=]\s*[\"\']?[A-Za-z0-9+/=_-]{20,}", re.I
        ),
    ),
    ("local user path", re.compile(r"/home/(?!snr(?:/|\b)|snr-user(?:/|\b))[A-Za-z0-9_.-]+/")),
    ("deployment IP", re.compile(r"34\.28\.33\.18")),
]


def audit_file(path: Path, relative: Path) -> bytes:
    if path.is_symlink():
        raise ValueError(f"Symlinks are not exportable: {relative}")
    data = path.read_bytes()
    if relative.name in BINARY_ASSETS:
        assert relative.parts[0] == "public", relative
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"Unreviewed binary: {relative}") from error
    # The audit's own literal patterns must remain in its source.
    if relative.as_posix() != "scripts/export-public-source.py":
        for label, pattern in FORBIDDEN:
            if pattern.search(text):
                raise ValueError(f"{label} found in {relative}; review it before export")
    if relative.suffix == ".json":
        value = json.loads(text)
        if isinstance(value, dict) and {"authoring", "privateGroundTruthUeCm"} & value.keys():
            raise ValueError(f"Private scenario document: {relative}")
    return data


def export(destination: Path) -> dict:
    destination = destination.resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError("Export to a new directory outside the deployment workspace")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing export: {destination}")
    files = set()
    for pattern in PATTERNS:
        matches = sorted(ROOT.glob(pattern))
        if not matches:
            raise FileNotFoundError(f"Public source allowlist matched no files: {pattern}")
        files.update(p for p in matches if p.is_file())
    audited = {}
    for source in sorted(files):
        relative = source.relative_to(ROOT)
        if ROOT not in source.resolve().parents:
            raise ValueError(f"Path escapes the workspace: {relative}")
        # Parent symlinks could otherwise bypass the file-level check.
        if any(p.is_symlink() for p in [source, *source.parents] if p != ROOT.parent):
            raise ValueError(f"Symlink in source path: {relative}")
        audited[relative] = audit_file(source, relative)
    checksums = json.loads((ROOT / "public/assets/checksums.json").read_text())
    for name, digest in checksums.items():
        data = audited[Path("public/assets") / name]
        assert hashlib.sha256(data).hexdigest() == digest, f"Preview asset checksum changed: {name}"
    destination.mkdir(parents=True, mode=0o700)
    manifest = {"schemaVersion": 1, "projectLicense": "Not selected", "files": []}
    for relative, data in audited.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        shutil.copymode(ROOT / relative, target)
        manifest["files"].append(
            {
                "path": relative.as_posix(),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    (destination / "public-source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    result = export(args.destination)
    print(f"Audited and exported {len(result['files'])} files to {args.destination}")
