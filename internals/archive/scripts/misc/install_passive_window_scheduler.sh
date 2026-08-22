#!/usr/bin/env bash
set -euo pipefail

mode="${1:-both}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
JOB_SCRIPT="${REPO_ROOT}/scripts/passive_window_daily_job.sh"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SYSTEMD_DIR}/breadboard-passive-window-daily.service"
TIMER_FILE="${SYSTEMD_DIR}/breadboard-passive-window-daily.timer"
CRON_TAG="# breadboard-passive-window-daily"

install_systemd() {
  mkdir -p "${SYSTEMD_DIR}"
  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Run BreadBoard passive-window daily

[Service]
Type=oneshot
WorkingDirectory=${REPO_ROOT}
ExecStart=/bin/bash ${JOB_SCRIPT}
EOF
  cat > "${TIMER_FILE}" <<EOF
[Unit]
Description=Run BreadBoard passive-window daily

[Timer]
OnCalendar=*-*-* 03:20:00
Persistent=true
RandomizedDelaySec=300
Unit=breadboard-passive-window-daily.service

[Install]
WantedBy=timers.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now breadboard-passive-window-daily.timer
  echo "installed systemd user timer"
}

install_cron() {
  local cron_line="20 3 * * * /bin/bash ${JOB_SCRIPT} ${CRON_TAG}"
  local existing
  existing="$(crontab -l 2>/dev/null || true)"
  if grep -Fq "${CRON_TAG}" <<<"${existing}"; then
    echo "cron entry already present"
    return
  fi
  {
    printf "%s\n" "${existing}"
    printf "%s\n" "${cron_line}"
  } | crontab -
  echo "installed cron fallback"
}

remove_scheduler() {
  systemctl --user disable --now breadboard-passive-window-daily.timer >/dev/null 2>&1 || true
  rm -f "${SERVICE_FILE}" "${TIMER_FILE}"
  systemctl --user daemon-reload
  (crontab -l 2>/dev/null || true) | grep -v "${CRON_TAG#\# }" | crontab - || true
  echo "removed scheduler configuration"
}

case "${mode}" in
  systemd)
    install_systemd
    ;;
  cron)
    install_cron
    ;;
  both)
    install_systemd
    install_cron
    ;;
  disable)
    remove_scheduler
    ;;
  *)
    echo "usage: $0 [systemd|cron|both|disable]" >&2
    exit 2
    ;;
esac
