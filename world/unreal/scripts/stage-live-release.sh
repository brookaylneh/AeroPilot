#!/usr/bin/env bash
# Keep packaging output separate from the executable serving browsers.
set -euo pipefail
cd "$(dirname "$0")/../../.."
test -x .local/unreal/Packaged/Linux/ScenarioStudio.sh
release_id="$(date -u +%Y%m%dT%H%M%SZ)"
release_path="$PWD/.local/unreal/releases/$release_id"
mkdir -p "$release_path"
cp -a --reflink=auto .local/unreal/Packaged/Linux/. "$release_path/"
cp world/cosys/settings.json "$release_path/cosys-settings.json"
cp world/cosys/world.json "$release_path/world.json"
ln -s "releases/$release_id" .local/unreal/Live.next
mv -Tf .local/unreal/Live.next .local/unreal/Live
echo "Release staged: $release_id. Restart snr-unreal only after validation and a scene checkpoint."
