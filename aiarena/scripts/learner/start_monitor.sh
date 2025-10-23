#!/usr/bin/env bash
set -euo pipefail

log(){ echo "[$(date '+%F %T')] $*"; }
log "Start service..."

# dev 场景：若明确不需要监控，才退出
if [[ -n "${KAIWU_DEV:-}" ]]; then
  log "KAIWU_DEV set; skip starting monitor stack"
  exit 0
fi

INFLUXDB_HOST="${INFLUXDB_HOST:-localhost}"
INFLUXDB_PORT="${INFLUXDB_PORT:-8086}"
INFLUXDB_DB="${INFLUXDB_DB:-monitordb}"

# 确保 8086 是 influxd（HTTP API），而不是 exporter
ensure_influxd() {
  # 端口已被占用时，确认是 influxd
  if ss -lnt | awk '{print $4}' | grep -q ":${INFLUXDB_PORT}$"; then
    if pgrep -x influxd >/dev/null 2>&1; then
      log "influxd already listening on :${INFLUXDB_PORT}"
      return 0
    else
      log "ERROR: :${INFLUXDB_PORT} is NOT influxd; please free it."; exit 1
    fi
  fi
  if command -v influxd >/dev/null 2>&1; then
    log "Starting influxd on :${INFLUXDB_PORT} ..."
    nohup influxd >/dev/null 2>&1 &
  else
    log "ERROR: influxd not found in PATH"; exit 1
  fi
  # 等待就绪
  for i in $(seq 1 60); do
    if curl -s "http://${INFLUXDB_HOST}:${INFLUXDB_PORT}/ping" -o /dev/null -w "%{http_code}" | grep -q 204; then
      log "influxd is up."; break
    fi; sleep 1
  done
}

create_db() {
  log "Ensure database '${INFLUXDB_DB}' ..."
  curl -s "http://${INFLUXDB_HOST}:${INFLUXDB_PORT}/query" \
    --data-urlencode "q=CREATE DATABASE ${INFLUXDB_DB}" >/dev/null
  log "Database ready."
}

start_grafana() {
  # 尝试用 systemd 管理；没有也不报错
  if command -v systemctl >/dev/null 2>&1; then
    log "Starting grafana-server ..."
    sudo systemctl start grafana-server || true
  fi
}

main() {
  ensure_influxd
  create_db
  start_grafana
  log "Monitor ready: InfluxDB http://${INFLUXDB_HOST}:${INFLUXDB_PORT} DB=${INFLUXDB_DB}"
  log "NOTE: keep NOT_USE_INFLUXDB_EXPORTER=\"\" in start_learner.sh to push metrics."
}

main "$@"
