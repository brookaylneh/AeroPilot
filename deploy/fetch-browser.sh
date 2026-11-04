#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
version=156.0.8078.4
archive=chrome-linux64-$version.zip
checksum=43fd31766c7533a02dda2ded5ad50c2f861cd5400be7509c0abad71129f79d79
tools=$PWD/.local/toolchains
test ! -e "$tools/chromium-$version"
test "$("$tools/bin/node" -p 'require("./node_modules/playwright-core/browsers.json").browsers[0].browserVersion')" = "$version"
curl --fail --silent --show-error --location --connect-timeout 15 --max-time 180 --retry 0 \
  "https://cdn.playwright.dev/builds/cft/$version/linux64/chrome-linux64.zip" --output "$tools/downloads/$archive"
printf '%s  %s\n' "$checksum" "$tools/downloads/$archive" | sha256sum --check --strict
mkdir "$tools/chromium-$version"
unzip -q "$tools/downloads/$archive" -d "$tools/chromium-$version"
ln -s "../chromium-$version/chrome-linux64/chrome" "$tools/bin/chromium"
"$tools/bin/chromium" --version
