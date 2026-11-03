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
test -f .local/runtime.env
test -x .local/toolchains/bin/caddy
test -f .local/dist/index.html
test -x .local/unreal/Live/ScenarioStudio/Binaries/Linux/ScenarioStudio
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
