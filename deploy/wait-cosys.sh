#!/usr/bin/env bash
# The world binds Cosys before its physics thread can receive the first SITL motor reply.
set -euo pipefail
[[ "${SNR_COSYS_PORT:?}" =~ ^[1-9][0-9]*$ ]]
deadline=$((SECONDS + 120))
until ss -H -ltn "sport = :$SNR_COSYS_PORT" | rg -Fq "127.0.0.1:$SNR_COSYS_PORT"; do
  if (( SECONDS >= deadline )); then
    echo "Cosys listener did not start on 127.0.0.1:$SNR_COSYS_PORT within 120 seconds" >&2
    exit 1
  fi
  sleep 1
done
