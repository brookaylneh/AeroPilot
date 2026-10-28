#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
revision=$(node -p "require('./world/unreal/dependencies.lock.json').pixelStreaming.commit")
if [[ ! "$revision" =~ ^[a-f0-9]{40}$ ]]; then
  echo 'Invalid Pixel Streaming revision in dependency lock.' >&2
  exit 2
fi
if [ ! -d vendor/pixel-streaming/.git ]; then
  git clone --depth 1 --branch UE5.8 https://github.com/EpicGames/PixelStreamingInfrastructure.git vendor/pixel-streaming
fi
if [ "$(git -C vendor/pixel-streaming rev-parse HEAD)" != "$revision" ]; then
  git -C vendor/pixel-streaming fetch --depth 1 origin "$revision"
  git -C vendor/pixel-streaming checkout --detach "$revision"
fi
cd vendor/pixel-streaming
# These components need no native install scripts or SFU service.
npm ci --ignore-scripts --no-audit --no-fund \
  --workspace Common --workspace Signalling --workspace SignallingWebServer \
  --workspace Frontend/library --workspace Frontend/ui-library \
  --workspace Frontend/implementations/typescript --include-workspace-root
npm run build:all:cjs
