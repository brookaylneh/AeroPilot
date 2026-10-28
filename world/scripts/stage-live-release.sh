#!/usr/bin/env bash
# Keep packaging output separate from the executable serving browsers.
set -euo pipefail
cd "$(dirname "$0")/../.."
link_name=Live
case "${1:-}" in
  '') ;;
  --candidate-only) link_name=NavigationCandidate ;;
  --drone-candidate) link_name=DroneCandidate ;;
  *) echo 'Usage: stage-live-release.sh [--candidate-only]' >&2; exit 2 ;;
esac
test -x world/unreal/Packaged/Linux/ScenarioStudio.sh
release_id="$(date -u +%Y%m%dT%H%M%SZ)"
release_path="$PWD/world/unreal/releases/$release_id"
mkdir -p "$release_path"
cp -a --reflink=auto world/unreal/Packaged/Linux/. "$release_path/"
if [ "$link_name" = DroneCandidate ]; then
  cp world/unreal/cosys-four-drones.json "$release_path/cosys-settings.json"
  cp world/simulator/world.json "$release_path/world.json"
fi
ln -s "releases/$release_id" "world/unreal/$link_name.next"
mv -Tf "world/unreal/$link_name.next" "world/unreal/$link_name"
echo "$link_name release staged: $release_id. Restart snr-unreal only after validation and a scene checkpoint."
