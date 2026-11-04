#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
version=2.11.7
archive=caddy_${version}_linux_amd64.tar.gz
checksum=727b91701a392de6ebc5027509f548bf39979e5216340d0faed8fa5e69c84f8b
tools=$PWD/.local/toolchains
test ! -e "$tools/caddy-$version"
curl --fail --silent --show-error --location --connect-timeout 15 --max-time 120 --retry 0 \
  "https://github.com/caddyserver/caddy/releases/download/v$version/$archive" \
  --output "$tools/downloads/$archive"
printf '%s  %s\n' "$checksum" "$tools/downloads/$archive" | sha256sum --check --strict
mkdir "$tools/caddy-$version"
tar -xzf "$tools/downloads/$archive" -C "$tools/caddy-$version" caddy LICENSE README.md
ln -s "../caddy-$version/caddy" "$tools/bin/caddy"
"$tools/bin/caddy" version | grep -E "^v$version "
