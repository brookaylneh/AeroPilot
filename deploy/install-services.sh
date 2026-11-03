#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test "$(id -u)" = 0
project=$PWD
service_user=$(stat -c %U "$project")
service_group=$(stat -c %G "$project")
service_uid=$(id -u "$service_user")
service_gid=$(id -g "$service_user")
test "$service_uid" = 1002 && test "$service_gid" = 1003
release_branch=$(runuser -u "$service_user" -- git rev-parse --abbrev-ref HEAD)
[[ "$release_branch" = HEAD ]] || {
  echo "Install services from a detached release checkout; live worker imports must not follow an editing branch" >&2
  exit 1
}
release_changes=$(runuser -u "$service_user" -- git status --porcelain --untracked-files=no)
[[ -z "$release_changes" ]] || {
  echo "The release checkout has uncommitted source changes" >&2
  exit 1
}
test -f .local/runtime.env
test -x .local/toolchains/bin/caddy
test -f .local/dist/index.html
[[ "$(realpath .local/dist)" = "$project/.local/dist" ]] || {
  echo "The browser bundle must be inside this release checkout, separate from the editable workspace" >&2
  exit 1
}
test -x .local/unreal/Live/ScenarioStudio/Binaries/Linux/ScenarioStudio
runuser -u "$service_user" -- "$project/.venv-cosys/bin/python" -c \
  'import cv2; assert hasattr(cv2, "TrackerCSRT_create"), "Install requirements.txt in .venv-cosys: OpenCV CSRT is missing"; cv2.TrackerCSRT_create(cv2.TrackerCSRT_Params())'
runuser -u "$service_user" -- "$project/.venv-cosys/bin/python" \
  -m unittest mission_planner.gcs.test_host
slots=$(sed -n 's/^SNR_SLOTS=//p' .local/runtime.env)
[[ "$slots" =~ ^[1-9][0-9]*$ ]]
for ((slot = 0; slot < slots; slot++)); do
  test -f ".local/slots/$slot/runtime.env" && test -f ".local/slots/$slot/cosys-settings.json"
done
render() {
  sed -e "s|@ROOT@|$project|g" -e "s|@USER@|$service_user|g" -e "s|@GROUP@|$service_group|g" \
    -e "s|@UID@|$service_uid|g" -e "s|@GID@|$service_gid|g" "$1"
}
# The single-world units and their drop-ins are replaced by the per-slot templates.
for name in unreal signalling mission vision; do
  state=$(systemctl show "snr-$name.service" -p ActiveState --value)
  [[ "$state" = inactive || "$state" = failed ]] || {
    echo "Stop snr-$name with the documented coupled shutdown before installing slots" >&2
    exit 1
  }
done
retired=()
for link in /etc/systemd/system/multi-user.target.wants/snr-*@*.service; do
  name=$(basename "$link")
  if [[ "$name" =~ ^snr-(unreal|signalling|mission|vision)@([0-9]+)\.service$ ]] &&
    ((10#${BASH_REMATCH[2]} >= slots)); then
    state=$(systemctl show "$name" -p ActiveState --value)
    [[ "$state" = inactive || "$state" = failed ]] || {
      echo "Stop $name with the documented coupled shutdown before reducing SNR_SLOTS" >&2
      exit 1
    }
    retired+=("$name")
  fi
done
if ((${#retired[@]})); then systemctl disable "${retired[@]}"; fi
for name in unreal signalling mission vision; do
  if [[ -f "/etc/systemd/system/snr-$name.service" ]]; then
    systemctl disable "snr-$name.service"
  fi
  rm -rf "/etc/systemd/system/snr-$name.service" "/etc/systemd/system/snr-$name.service.d"
done
for unit in deploy/systemd/*.service; do
  name=$(basename "$unit")
  render "$unit" >"/etc/systemd/system/$name"
  install -d "/etc/systemd/system/$name.d"
  render deploy/systemd/common.conf >"/etc/systemd/system/$name.d/runtime.conf"
  if [[ "$name" == *@.service ]]; then
    render deploy/systemd/slot.conf >"/etc/systemd/system/$name.d/slot.conf"
  fi
done
rm -f /etc/systemd/system/snr-web.service.d/source.conf
systemctl daemon-reload
instances=()
for ((slot = 0; slot < slots; slot++)); do
  instances+=("snr-unreal@$slot" "snr-signalling@$slot" "snr-mission@$slot" "snr-vision@$slot")
done
units=()
for instance in "${instances[@]}"; do units+=("/etc/systemd/system/$instance.service"); done
systemd-analyze verify /etc/systemd/system/snr-{web,turn,caddy}.service "${units[@]}"
systemctl enable snr-web snr-turn snr-caddy "${instances[@]}"
printf 'Units installed and enabled for %s slot(s). Start them explicitly after verifying the runtime configuration.\n' "$slots"
