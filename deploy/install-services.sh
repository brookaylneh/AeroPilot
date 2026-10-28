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
render() {
  sed -e "s|@ROOT@|$project|g" -e "s|@USER@|$service_user|g" -e "s|@GROUP@|$service_group|g" \
    -e "s|@UID@|$service_uid|g" -e "s|@GID@|$service_gid|g" "$1"
}
for unit in deploy/systemd/*.service; do
  name=$(basename "$unit")
  render "$unit" >"/etc/systemd/system/$name"
  install -d "/etc/systemd/system/$name.d"
  render deploy/systemd/common.conf >"/etc/systemd/system/$name.d/runtime.conf"
done
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/snr-*.service
systemctl enable snr-web snr-mission snr-signalling snr-unreal snr-turn snr-caddy
printf 'Units installed and enabled. Start them explicitly after verifying the runtime configuration.\n'
