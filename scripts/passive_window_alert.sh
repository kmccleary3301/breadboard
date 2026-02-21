#!/usr/bin/env bash
set -u -o pipefail

ALERT_LEVEL="${PASSIVE_WINDOW_ALERT_LEVEL:-error}"
ALERT_RC="${PASSIVE_WINDOW_ALERT_RC:-unknown}"
ALERT_MESSAGE="${PASSIVE_WINDOW_ALERT_MESSAGE:-passive-window alert}"
ALERT_LOG_FILE="${PASSIVE_WINDOW_ALERT_LOG_FILE:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALERT_AUDIT_DIR="${REPO_ROOT}/artifacts/nightly_archive/alerts"
ALERT_AUDIT_FILE="${ALERT_AUDIT_DIR}/passive_window_alerts.jsonl"

mkdir -p "${ALERT_AUDIT_DIR}"

timestamp_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

logger -t breadboard-passive-window-alert "level=${ALERT_LEVEL} rc=${ALERT_RC} msg=${ALERT_MESSAGE} log=${ALERT_LOG_FILE}" || true

cat >> "${ALERT_AUDIT_FILE}" <<EOF
{"timestamp":"${timestamp_utc}","level":"${ALERT_LEVEL}","rc":"${ALERT_RC}","message":"${ALERT_MESSAGE}","log_file":"${ALERT_LOG_FILE}"}
EOF

if command -v notify-send >/dev/null 2>&1; then
  title="BreadBoard passive-window (${ALERT_LEVEL})"
  body="rc=${ALERT_RC} ${ALERT_MESSAGE}"
  notify-send "${title}" "${body}" >/dev/null 2>&1 || true
fi

exit 0
