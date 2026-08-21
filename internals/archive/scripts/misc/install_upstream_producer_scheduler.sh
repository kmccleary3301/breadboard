#!/usr/bin/env bash
set -euo pipefail

mode="${1:-enable}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SYSTEMD_DIR}/breadboard-upstream-producers.service"
TIMER_FILE="${SYSTEMD_DIR}/breadboard-upstream-producers.timer"
RUN_SCRIPT="${REPO_ROOT}/scripts/run_upstream_producers_to_bridge.sh"

install_units() {
  mkdir -p "${SYSTEMD_DIR}"
  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Run BreadBoard upstream ATP/EvoLake producer bridge

[Service]
Type=oneshot
WorkingDirectory=${REPO_ROOT}
ExecStart=/bin/bash ${RUN_SCRIPT}
EOF

  cat > "${TIMER_FILE}" <<EOF
[Unit]
Description=Run BreadBoard upstream ATP/EvoLake producer bridge

[Timer]
OnCalendar=*-*-* 03:05:00
Persistent=true
RandomizedDelaySec=180
Unit=breadboard-upstream-producers.service

[Install]
WantedBy=timers.target
EOF
}

enable_units() {
  install_units
  systemctl --user daemon-reload
  systemctl --user enable --now breadboard-upstream-producers.timer
  systemctl --user start breadboard-upstream-producers.service
}

disable_units() {
  systemctl --user disable --now breadboard-upstream-producers.timer >/dev/null 2>&1 || true
  rm -f "${SERVICE_FILE}" "${TIMER_FILE}"
  systemctl --user daemon-reload
}

case "${mode}" in
  enable)
    enable_units
    ;;
  disable)
    disable_units
    ;;
  *)
    echo "usage: $0 [enable|disable]" >&2
    exit 2
    ;;
esac
