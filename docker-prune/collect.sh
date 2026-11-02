#!/usr/bin/env bash
set -Eeuo pipefail
# Telegraf captures exec stderr; use the container stream for live diagnostics.
if [[ -n "${TELEGRAF_TOOL_LOG:-}" ]] && { exec 3>>"$TELEGRAF_TOOL_LOG"; } 2>/dev/null; then
  :
else
  exec 3>&2
fi
log() { printf '%s %s [docker-prune] %s\n' "$(date -u +%FT%TZ)" "$1" "$2" >&3; }
started=$SECONDS
trap 'rc=$?; log ERROR "collection failed exit=$rc line=$LINENO duration=$((SECONDS-started))s"' ERR
# Forward native command diagnostics too, keeping stdout for metrics.
exec 2>&3
DEFAULT_REFRESH_INTERVAL="3h"
refresh_interval="${1:-$DEFAULT_REFRESH_INTERVAL}"
if [[ ! "$refresh_interval" =~ ^([1-9][0-9]*)([smh])$ ]]; then
  echo 'Usage: collect.sh [refresh interval, e.g. 30m, 1h, 3h, 6h]' >&2
  exit 64
fi
amount="${BASH_REMATCH[1]}"
case "${BASH_REMATCH[2]}" in
  s) multiplier=1 ;;
  m) multiplier=60 ;;
  h) multiplier=3600 ;;
esac
refresh_seconds=$((amount * multiplier))
cache_dir="${DOCKER_PRUNE_CACHE_DIR:-/var/cache/docker-prune}"
mkdir -p "$cache_dir"
exec 9>"$cache_dir/lock"
flock -w 70 9
now="$(date +%s)"
last_attempt="$(cat "$cache_dir/last-attempt" 2>/dev/null || echo 0)"
temporary="$cache_dir/.response.$$"
trap 'rm -f "$temporary"' EXIT

# Runs every 30s; the expensive API call respects the configured refresh interval.
# Failed attempts also wait until the next scan and publish success=0.
if (( now - last_attempt >= refresh_seconds )); then
  # Older daemons reject newer API paths (for example Engine 24 supports 1.43).
  # Cap newer daemons at 1.51 to retain the established disk-usage accounting.
  if ! api_version="$(curl --silent --show-error --fail --max-time 10 \
      --unix-socket /var/run/docker.sock http://localhost/version \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ApiVersion", ""))')"; then
    api_version=""
  fi
  if [[ "$api_version" =~ ^([0-9]+)\.([0-9]+)$ ]] && (( 10#${BASH_REMATCH[1]} == 1 )); then
    api_minor=$((10#${BASH_REMATCH[2]}))
    if (( api_minor > 51 )); then
      api_version="1.51"
    fi
    log INFO "scan started refresh_interval=${refresh_seconds}s api=${api_version}/system/df"
    if curl --silent --show-error --fail --max-time 60 \
        --unix-socket /var/run/docker.sock "http://localhost/v${api_version}/system/df" \
        --output "$temporary"; then
      mv "$temporary" "$cache_dir/disk-usage.json"
      printf '%s\n' "$now" > "$cache_dir/last-success"
      printf '1\n' > "$cache_dir/success"
      log INFO "scan completed duration=$((SECONDS-started))s response_bytes=$(wc -c < "$cache_dir/disk-usage.json")"
    else
      rc=$?
      log ERROR "scan failed curl_exit=$rc duration=$((SECONDS-started))s; previous data retained, success=0; retry_in=${refresh_seconds}s"
      printf '0\n' > "$cache_dir/success"
    fi
  else
    log ERROR "scan failed: could not determine a supported Docker API version; previous data retained, success=0; retry_in=${refresh_seconds}s"
    printf '0\n' > "$cache_dir/success"
  fi
  printf '%s\n' "$now" > "$cache_dir/last-attempt"
fi
success="$(cat "$cache_dir/success" 2>/dev/null || echo 0)"
last_success="$(cat "$cache_dir/last-success" 2>/dev/null || echo 0)"
printf '{"collection_success":%s,"last_success_timestamp_seconds":%s,"refresh_interval_seconds":%s,"disk_usage":' "$success" "$last_success" "$refresh_seconds"
if [[ -f "$cache_dir/disk-usage.json" ]]; then
  cat "$cache_dir/disk-usage.json"
else
  printf '{}'
fi
printf '}\n'
