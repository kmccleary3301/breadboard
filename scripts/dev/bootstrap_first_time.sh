#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PROFILE="full"
EFFECTIVE_PROFILE=""
RUN_SMOKE=0
RUN_DOCTOR=1
RUN_SDK_HELLO_LIVE=0
SKIP_PYTHON=0
SKIP_NODE=0
RECREATE_VENV=0
HAS_TUI_SOURCE=0
HAS_TS_SDK_SOURCE=0
REFRESH_PYTHON_DEPS=0

usage() {
  cat <<'EOF'
Usage: scripts/dev/bootstrap_first_time.sh [options]

Options:
  --profile <full|engine|tui>
                   full: python + sdk/ts + tui (default)
                   engine: python-only setup (skip node/tui)
                   tui: node/tui-only setup (skip python)
  --all-checks      Run both unit smoke and live SDK hello verification.
  --smoke           Run unit-only switcher smoke after setup.
  --sdk-hello-live  Run live python+ts SDK hello verification after setup.
  --no-doctor       Skip first-time doctor checks.
  --skip-python     Skip Python venv + dependency install.
  --skip-node       Skip Node deps + TUI build.
  --recreate-venv   Remove and recreate .venv.
  --refresh-python-deps
                   Force reinstall of Python dependencies even if unchanged.
  -h, --help        Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --smoke)
      RUN_SMOKE=1
      shift
      ;;
    --all-checks)
      RUN_SMOKE=1
      RUN_SDK_HELLO_LIVE=1
      shift
      ;;
    --no-doctor)
      RUN_DOCTOR=0
      shift
      ;;
    --sdk-hello-live)
      RUN_SDK_HELLO_LIVE=1
      shift
      ;;
    --skip-python)
      SKIP_PYTHON=1
      shift
      ;;
    --skip-node)
      SKIP_NODE=1
      shift
      ;;
    --recreate-venv)
      RECREATE_VENV=1
      shift
      ;;
    --refresh-python-deps)
      REFRESH_PYTHON_DEPS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[bootstrap] unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "${ROOT_DIR}/requirements.txt" || ! -f "${ROOT_DIR}/sdk/ts/package.json" ]]; then
  echo "[bootstrap] expected repository layout not found at ${ROOT_DIR}" >&2
  echo "[bootstrap] missing requirements.txt or sdk/ts/package.json" >&2
  echo "[bootstrap] run this script from inside the BreadBoard repository." >&2
  exit 2
fi

if [[ -f "${ROOT_DIR}/tui_skeleton/package.json" ]]; then
  HAS_TUI_SOURCE=1
fi
if [[ -f "${ROOT_DIR}/sdk/ts/package.json" ]]; then
  HAS_TS_SDK_SOURCE=1
fi

case "${PROFILE}" in
  full)
    ;;
  engine)
    SKIP_NODE=1
    ;;
  tui)
    SKIP_PYTHON=1
    ;;
  *)
    echo "[bootstrap] invalid --profile: ${PROFILE}" >&2
    echo "[bootstrap] expected one of: full, engine, tui" >&2
    exit 2
    ;;
esac

if [[ "${PROFILE}" == "full" && "${HAS_TUI_SOURCE}" == "0" ]]; then
  echo "[bootstrap] note: tui_skeleton/package.json not found; downgrading profile full -> engine"
  SKIP_NODE=1
  EFFECTIVE_PROFILE="engine"
elif [[ "${PROFILE}" == "tui" && "${HAS_TUI_SOURCE}" == "0" ]]; then
  echo "[bootstrap] tui profile requested, but tui_skeleton/package.json is missing." >&2
  echo "[bootstrap] if this is an engine-only checkout, use --profile engine." >&2
  exit 2
else
  EFFECTIVE_PROFILE="${PROFILE}"
fi

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "[bootstrap] required command missing: ${cmd}" >&2
    exit 2
  fi
}

check_python_version() {
  python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ required")
print(f"[bootstrap] python {sys.version.split()[0]}")
PY
}

check_node_version() {
  node - <<'JS'
const [major] = process.versions.node.split(".").map(Number);
if (major < 20) {
  console.error("[bootstrap] Node 20+ required");
  process.exit(1);
}
console.log(`[bootstrap] node ${process.versions.node}`);
JS
}

create_or_reuse_venv() {
  if [[ "${RECREATE_VENV}" == "1" && -d "${VENV_DIR}" ]]; then
    echo "[bootstrap] removing existing ${VENV_DIR}"
    rm -rf "${VENV_DIR}"
  fi

  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    echo "[bootstrap] reusing existing virtualenv: ${VENV_DIR}"
    return
  fi

  if command -v uv >/dev/null 2>&1; then
    echo "[bootstrap] creating virtualenv with uv"
    uv venv "${VENV_DIR}"
  else
    echo "[bootstrap] creating virtualenv with python -m venv"
    python3 -m venv "${VENV_DIR}"
  fi
}

install_python_deps() {
  local vpy="${VENV_DIR}/bin/python"
  local marker="${VENV_DIR}/.breadboard_requirements_lock"
  if [[ ! -x "${vpy}" ]]; then
    echo "[bootstrap] expected venv python missing: ${vpy}" >&2
    exit 2
  fi

  local req_hash
  req_hash="$(sha256_file "${ROOT_DIR}/requirements.txt")"
  local py_version
  py_version="$("${vpy}" - <<'PY'
import sys
print(sys.version.split()[0])
PY
)"
  local resolver="pip"
  if command -v uv >/dev/null 2>&1; then
    resolver="uv"
  fi
  local expected
  expected="requirements=${req_hash}|python=${py_version}|resolver=${resolver}"

  if [[ "${REFRESH_PYTHON_DEPS}" == "0" && -f "${marker}" ]]; then
    local current
    current="$(cat "${marker}")"
    if [[ "${current}" == "${expected}" ]]; then
      echo "[bootstrap] python deps skipped (requirements+python+resolver unchanged)"
      return
    fi
  fi

  if [[ "${resolver}" == "uv" ]]; then
    echo "[bootstrap] installing python deps via uv pip"
    uv pip install --python "${vpy}" -r "${ROOT_DIR}/requirements.txt"
  else
    echo "[bootstrap] installing python deps via pip"
    "${vpy}" -m pip install --upgrade pip
    "${vpy}" -m pip install -r "${ROOT_DIR}/requirements.txt"
  fi
  printf '%s' "${expected}" >"${marker}"
}

sha256_file() {
  local path="$1"
  python3 - "$path" <<'PY'
import hashlib
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
h = hashlib.sha256()
h.update(target.read_bytes())
print(h.hexdigest())
PY
}

npm_ci_if_needed() {
  local pkg_dir="$1"
  local label="$2"
  local lock_file="${pkg_dir}/package-lock.json"
  local node_modules="${pkg_dir}/node_modules"
  local marker="${node_modules}/.breadboard_bootstrap_lock"
  local node_version
  node_version="$(node -p 'process.versions.node')"

  if [[ ! -f "${lock_file}" ]]; then
    echo "[bootstrap] npm ci (${label})"
    npm -C "${pkg_dir}" ci --no-audit --no-fund
    return
  fi

  local lock_hash
  lock_hash="$(sha256_file "${lock_file}")"
  local expected
  expected="${lock_hash}|node=${node_version}"

  if [[ -d "${node_modules}" && -f "${marker}" ]]; then
    local current
    current="$(cat "${marker}")"
    if [[ "${current}" == "${expected}" ]]; then
      echo "[bootstrap] npm ci (${label}) skipped (lock+node unchanged)"
      return
    fi
  fi

  echo "[bootstrap] npm ci (${label})"
  npm -C "${pkg_dir}" ci --no-audit --no-fund
  mkdir -p "${node_modules}"
  printf '%s' "${expected}" >"${marker}"
}

build_needed_from_mtime() {
  local dist_probe="$1"
  shift
  python3 - "${dist_probe}" "$@" <<'PY'
import os
import pathlib
import sys

dist_probe = pathlib.Path(sys.argv[1])
sources = [pathlib.Path(p) for p in sys.argv[2:]]

if not dist_probe.exists():
    print("1")
    raise SystemExit(0)

def newest(paths: list[pathlib.Path], treat_as_source: bool) -> float:
    latest = 0.0
    for root in paths:
        if not root.exists():
            continue
        if root.is_file():
            try:
                latest = max(latest, root.stat().st_mtime)
            except OSError:
                pass
            continue
        for cur_root, dirnames, filenames in os.walk(root):
            if treat_as_source:
                dirnames[:] = [d for d in dirnames if d not in {"node_modules", "dist", ".git"}]
            for name in filenames:
                p = pathlib.Path(cur_root) / name
                try:
                    latest = max(latest, p.stat().st_mtime)
                except OSError:
                    continue
    return latest

src_latest = newest(sources, treat_as_source=True)
dist_latest = newest([dist_probe], treat_as_source=False)

# Build is needed when sources are newer than outputs.
print("1" if src_latest > dist_latest else "0")
PY
}

npm_build_if_needed() {
  local pkg_dir="$1"
  local label="$2"
  local dist_probe="$3"
  shift 3
  local src_paths=("$@")

  local build_needed
  build_needed="$(build_needed_from_mtime "${dist_probe}" "${src_paths[@]}")"
  if [[ "${build_needed}" == "0" ]]; then
    echo "[bootstrap] npm build (${label}) skipped (dist newer than sources)"
    return
  fi

  echo "[bootstrap] npm build (${label})"
  npm -C "${pkg_dir}" run build
}

install_node_deps_and_build() {
  require_cmd npm
  check_node_version
  if [[ "${HAS_TS_SDK_SOURCE}" == "1" ]]; then
    npm_ci_if_needed "${ROOT_DIR}/sdk/ts" "sdk/ts"
    npm_build_if_needed \
      "${ROOT_DIR}/sdk/ts" \
      "sdk/ts" \
      "${ROOT_DIR}/sdk/ts/dist/index.js" \
      "${ROOT_DIR}/sdk/ts/src" \
      "${ROOT_DIR}/sdk/ts/package.json" \
      "${ROOT_DIR}/sdk/ts/package-lock.json" \
      "${ROOT_DIR}/sdk/ts/tsconfig.json"
  fi
  if [[ "${HAS_TUI_SOURCE}" == "1" ]]; then
    npm_ci_if_needed "${ROOT_DIR}/tui_skeleton" "tui_skeleton"
    npm_build_if_needed \
      "${ROOT_DIR}/tui_skeleton" \
      "tui_skeleton" \
      "${ROOT_DIR}/tui_skeleton/dist/main.js" \
      "${ROOT_DIR}/tui_skeleton/src" \
      "${ROOT_DIR}/tui_skeleton/scripts/copyAsciiHeader.ts" \
      "${ROOT_DIR}/tui_skeleton/scripts/installLocalBin.ts" \
      "${ROOT_DIR}/tui_skeleton/package.json" \
      "${ROOT_DIR}/tui_skeleton/package-lock.json" \
      "${ROOT_DIR}/tui_skeleton/tsconfig.json"
  else
    echo "[bootstrap] skipping tui_skeleton build (source not present)"
  fi
}

run_doctor() {
  local py_bin="python3"
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    py_bin="${VENV_DIR}/bin/python"
  fi
  echo "[bootstrap] running first-time doctor"
  "${py_bin}" "${ROOT_DIR}/scripts/dev/first_time_doctor.py" --profile "${EFFECTIVE_PROFILE}" --strict
}

run_smoke() {
  echo "[bootstrap] running unit-only switcher smoke"
  (cd "${ROOT_DIR}" && ./scripts/smoke_switcher_fancy_use_cases.sh --no-live)
}

run_sdk_hello_live() {
  echo "[bootstrap] running live SDK hello verification"
  local args=()
  if [[ "${SKIP_NODE}" == "1" ]]; then
    args+=(--no-ts)
  fi
  (cd "${ROOT_DIR}" && ./scripts/dev/sdk_hello_live_smoke.sh "${args[@]}")
}

breadboard_cli_usable() {
  if ! command -v breadboard >/dev/null 2>&1; then
    return 1
  fi
  breadboard --help >/dev/null 2>&1
}

echo "[bootstrap] repo=${ROOT_DIR}"
echo "[bootstrap] profile=${PROFILE} (effective=${EFFECTIVE_PROFILE})"

if [[ "${SKIP_PYTHON}" == "0" ]]; then
  require_cmd python3
  check_python_version
  create_or_reuse_venv
  install_python_deps
else
  echo "[bootstrap] skipping python setup"
fi

if [[ "${SKIP_NODE}" == "0" ]]; then
  install_node_deps_and_build
else
  echo "[bootstrap] skipping node/tui setup"
fi

if [[ "${RUN_DOCTOR}" == "1" ]]; then
  run_doctor
fi

if [[ "${RUN_SMOKE}" == "1" ]]; then
  run_smoke
fi

if [[ "${RUN_SDK_HELLO_LIVE}" == "1" ]]; then
  run_sdk_hello_live
fi

echo "[bootstrap] done."
echo ""
echo "Next steps:"
if [[ "${SKIP_PYTHON}" == "0" ]]; then
  echo "  1) Activate Python env:"
  echo "     source ${VENV_DIR}/bin/activate"
else
  echo "  1) Python setup was skipped (--profile tui or --skip-python)."
  echo "     If needed later: bash scripts/dev/bootstrap_first_time.sh --profile engine"
fi
if breadboard_cli_usable; then
  echo "  2) Verify CLI:"
  echo "     breadboard doctor --config agent_configs/opencode_mock_c_fs.yaml"
else
  echo "  2) CLI wrapper is unavailable in the current state."
  echo "     Use quickstart helper: python scripts/dev/quickstart_first_time.py --include-advanced"
  if [[ "${HAS_TUI_SOURCE}" == "1" ]]; then
    echo "     Then rebuild wrapper: bash scripts/dev/repair_cli_wrapper.sh"
  fi
fi
if [[ "${SKIP_PYTHON}" == "0" ]]; then
  echo "  3) Run SDK hello smokes (engine must be running):"
  echo "     python scripts/dev/python_sdk_hello.py"
  if [[ "${SKIP_NODE}" == "0" ]]; then
    echo "     node scripts/dev/ts_sdk_hello.mjs"
  fi
fi
if [[ "${SKIP_NODE}" == "0" ]]; then
  if [[ "${HAS_TUI_SOURCE}" == "1" ]]; then
    echo "  4) Run UI:"
    echo "     breadboard ui --config agent_configs/opencode_mock_c_fs.yaml"
  else
    echo "  4) TUI source not present in this checkout; engine/sdk setup completed."
  fi
else
  echo "  4) TUI setup was skipped (--profile engine or --skip-node)."
  echo "     If needed later: bash scripts/dev/bootstrap_first_time.sh --profile tui"
fi
