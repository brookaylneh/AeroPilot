#!/usr/bin/env bash
set -Eeuo pipefail
trap 'printf "build-sitl.sh failed at line %s\n" "$LINENO" >&2' ERR
cd "$(dirname "$0")/../.."
project=$PWD
export PATH="$project/.venv-cosys/bin:$project/.local/toolchains/bin:$PATH"
python -c 'from mission_planner.environment import load_env; load_env()'
revision=$(node -p "require('./mission_planner/measurements/gcs-sitl.json').commit")
jobs=$(node -p "require('./mission_planner/measurements/gcs-sitl.json').build_jobs")
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
test "$jobs" = 4

fetch() {
  local destination=$1 url=$2 commit=$3
  [[ "$url" == https://github.com/* ]]
  [[ "$commit" =~ ^[0-9a-f]{40}$ ]]
  test ! -e "$destination/.git"
  git init -b main "$destination"
  git -C "$destination" remote add origin "$url"
  git -C "$destination" -c http.lowSpeedLimit=1 -c http.lowSpeedTime=30 \
    fetch --depth 1 origin "$commit"
  git -C "$destination" checkout --detach FETCH_HEAD
  test "$(git -C "$destination" rev-parse HEAD)" = "$commit"
  if [[ -s "$destination/.gitmodules" ]]; then
    local key relative child_url child_commit
    while read -r key relative; do
      [[ "$relative" != /* && "$relative" != *..* ]]
      child_url=$(git -C "$destination" config --file .gitmodules --get "${key%.path}.url")
      child_url=$(node -e 'console.log(new URL(process.argv[1], process.argv[2] + "/").href)' "$child_url" "$url")
      child_commit=$(git -C "$destination" ls-tree HEAD -- "$relative" | awk '{print $3}')
      fetch "$destination/$relative" "$child_url" "$child_commit"
    done < <(git -C "$destination" config --file .gitmodules --get-regexp '^submodule\..*\.path$')
  fi
}

fetch "$project/.local/vendor/ardupilot" https://github.com/ArduPilot/ardupilot.git "$revision"
cd "$project/.local/vendor/ardupilot"
patch --batch --fuzz=0 -p1 <"$project/mission_planner/gcs/sitl-loopback.patch"
python modules/waf/waf-light configure --board sitl --no-submodule-update
python modules/waf/waf-light copter -j"$jobs" --no-submodule-update
test -x build/sitl/bin/arducopter
sha256sum build/sitl/bin/arducopter
