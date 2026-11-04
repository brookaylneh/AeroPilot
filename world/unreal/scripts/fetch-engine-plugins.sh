#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
engine=${UE_ROOT:?Set UE_ROOT to the licensed Unreal Engine 5.8.2 SDK}
destination=${1:?Usage: fetch-engine-plugins.sh NEW_PLUGIN_DIRECTORY}
destination=$(realpath -m "$destination")
assert_version=$(jq -r '[.MajorVersion,.MinorVersion,.PatchVersion,.Changelist] | join(".")' \
  "$engine/Engine/Build/Build.version")
test "$assert_version" = 5.8.2.56702186
manifest=$PWD/world/unreal/engine-plugin-sources.sha256
(cd "$engine/Engine/Plugins" && sha256sum --check --strict "$manifest")
for entry in Media/PixelStreaming2 Experimental/AVCodecs/LibVpxCodecs; do
  plugin=${entry##*/}
  test ! -e "$destination/$plugin"
done
mkdir -p "$destination"
for entry in Media/PixelStreaming2 Experimental/AVCodecs/LibVpxCodecs; do
  plugin=${entry##*/}
  rsync -a --exclude=Binaries --exclude=Intermediate --exclude=Saved --exclude=DerivedDataCache \
    --exclude=__pycache__ --exclude='*.pyc' "$engine/Engine/Plugins/$entry/" "$destination/$plugin/"
  patch --batch --fuzz=0 -p1 -d "$destination/$plugin" <"world/unreal/${plugin,,}-ue58.patch"
done
