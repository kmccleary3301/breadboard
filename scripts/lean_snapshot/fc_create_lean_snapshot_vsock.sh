#!/usr/bin/env bash
set -euo pipefail

KERNEL=${KERNEL:-/var/lib/firecracker/kernels/vmlinux}
ROOTFS=${ROOTFS:-/var/lib/firecracker/rootfs/lean-mathlib.ext4}
VCPU_COUNT=${VCPU_COUNT:-4}
MEM_MIB=${MEM_MIB:-8192}
VSOCK_PORT=${VSOCK_PORT:-52}
DATA_DISK_MB=${DATA_DISK_MB:-24576}
ROOTFS_GROW_GB=${ROOTFS_GROW_GB:-8}
REQUIRE_WARMUP=${REQUIRE_WARMUP:-0}
SCRIPT_REV=${SCRIPT_REV:-20260210-r18}
SNAP_WAIT_TIMEOUT_S=${SNAP_WAIT_TIMEOUT_S:-7200}
EAGER_MATHLIB_IMPORT=${EAGER_MATHLIB_IMPORT:-0}
MATHLIB_IMPORT_TIMEOUT_S=${MATHLIB_IMPORT_TIMEOUT_S:-5400}
WARMUP_PROBE_TIMEOUT_S=${WARMUP_PROBE_TIMEOUT_S:-120}
REPL_WARM_RETRIES=${REPL_WARM_RETRIES:-2}
CACHE_GET_TIMEOUT_S=${CACHE_GET_TIMEOUT_S:-1800}
LAKE_BUILD_TIMEOUT_S=${LAKE_BUILD_TIMEOUT_S:-3600}
SKIP_CACHE_GET_IF_PRESENT=${SKIP_CACHE_GET_IF_PRESENT:-1}
SKIP_LAKE_BUILD_IF_PRESENT=${SKIP_LAKE_BUILD_IF_PRESENT:-1}
REPL_BUILD_JOBS=${REPL_BUILD_JOBS:-0}
SERIAL_POLL_INTERVAL_S=${SERIAL_POLL_INTERVAL_S:-15}
SERIAL_HEARTBEAT_S=${SERIAL_HEARTBEAT_S:-120}
FAILURE_SERIAL_TAIL_LINES=${FAILURE_SERIAL_TAIL_LINES:-1200}

if [ ! -f "$KERNEL" ]; then
  echo "Kernel not found: $KERNEL" >&2
  exit 1
fi
if [ ! -f "$ROOTFS" ]; then
  echo "Rootfs not found: $ROOTFS" >&2
  exit 1
fi

TS=$(date +%Y%m%d-%H%M%S)
SNAP_DIR=${SNAP_DIR:-/tmp/fc_lean_snap_ready-$TS}
mkdir -p "$SNAP_DIR"

ROOTFS_COPY="$SNAP_DIR/rootfs.ext4"
DATA_DISK="$SNAP_DIR/data.ext4"
SNAP_FILE="$SNAP_DIR/lean.snap"
MEM_FILE="$SNAP_DIR/lean.mem"
API_SOCK="$SNAP_DIR/firecracker.sock"
VSOCK_SOCK="$SNAP_DIR/vsock.sock"
LOG_FILE="$SNAP_DIR/firecracker.log"
SERIAL_LOG="$SNAP_DIR/serial.log"
MNT_DIR="/tmp/lean-snap-mnt-$TS"

# Avoid stale artifacts when reusing an existing snapshot directory.
rm -f "$SNAP_FILE" "$MEM_FILE" "$API_SOCK" "$VSOCK_SOCK" "$LOG_FILE" "$SERIAL_LOG"

cleanup() {
  set +e
  if mountpoint -q "$MNT_DIR/proc"; then
    sudo umount "$MNT_DIR/proc" || sudo umount -l "$MNT_DIR/proc"
  fi
  if mountpoint -q "$MNT_DIR/sys"; then
    sudo umount "$MNT_DIR/sys" || sudo umount -l "$MNT_DIR/sys"
  fi
  if mountpoint -q "$MNT_DIR/dev"; then
    sudo umount "$MNT_DIR/dev" || sudo umount -l "$MNT_DIR/dev"
  fi
  if mountpoint -q "$MNT_DIR"; then
    sudo umount "$MNT_DIR" || sudo umount -l "$MNT_DIR"
  fi
  rmdir "$MNT_DIR" 2>/dev/null || true
  if [ -n "${FC_PID:-}" ]; then
    kill "$FC_PID" 2>/dev/null || true
    sleep 1
    kill -9 "$FC_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

HOST_DEBUG_LOG="$SNAP_DIR/host_debug.log"
: > "$HOST_DEBUG_LOG"

log_host() {
  local msg="$*"
  local line
  line="[$(date +'%Y-%m-%d %H:%M:%S')] $msg"
  echo "$line" | tee -a "$HOST_DEBUG_LOG"
}

write_failure_bundle() {
  local reason="$1"
  local report="$SNAP_DIR/host_failure_report.txt"
  {
    echo "reason: $reason"
    echo "timestamp: $(date -Is)"
    echo "script_revision: $SCRIPT_REV"
    echo "config: vcpu=$VCPU_COUNT mem_mib=$MEM_MIB vsock_port=$VSOCK_PORT"
    echo "config: eager_mathlib=$EAGER_MATHLIB_IMPORT import_timeout_s=$MATHLIB_IMPORT_TIMEOUT_S probe_timeout_s=$WARMUP_PROBE_TIMEOUT_S warm_retries=$REPL_WARM_RETRIES"
    echo "config: cache_get_timeout_s=$CACHE_GET_TIMEOUT_S lake_build_timeout_s=$LAKE_BUILD_TIMEOUT_S skip_cache_if_present=$SKIP_CACHE_GET_IF_PRESENT skip_build_if_present=$SKIP_LAKE_BUILD_IF_PRESENT"
    echo
    echo "--- df -h ---"
    df -h || true
    echo
    echo "--- free -h ---"
    free -h || true
    echo
    echo "--- firecracker.log (tail -200) ---"
    tail -n 200 "$LOG_FILE" 2>/dev/null || true
    echo
    echo "--- serial.log (tail -${FAILURE_SERIAL_TAIL_LINES}) ---"
    tail -n "$FAILURE_SERIAL_TAIL_LINES" "$SERIAL_LOG" 2>/dev/null || true
  } > "$report" 2>&1 || true
  log_host "Wrote failure bundle: $report"
}

echo "=== Creating Mathlib REPL snapshot (vsock enabled) ==="
echo "Script revision: $SCRIPT_REV"
echo "Snapshot dir: $SNAP_DIR"
echo "VM config: vcpu=${VCPU_COUNT} mem_mib=${MEM_MIB} vsock_port=${VSOCK_PORT}"
echo "Warm config: eager_mathlib=${EAGER_MATHLIB_IMPORT} import_timeout_s=${MATHLIB_IMPORT_TIMEOUT_S}"
echo "Warm config: probe_timeout_s=${WARMUP_PROBE_TIMEOUT_S} warm_retries=${REPL_WARM_RETRIES}"
echo "Build config: cache_timeout_s=${CACHE_GET_TIMEOUT_S} build_timeout_s=${LAKE_BUILD_TIMEOUT_S} repl_build_jobs=${REPL_BUILD_JOBS}"
echo "Build config: skip_cache_if_present=${SKIP_CACHE_GET_IF_PRESENT} skip_build_if_present=${SKIP_LAKE_BUILD_IF_PRESENT}"
log_host "Snapshot build started"

echo "[1/8] Copying rootfs (this is large; may take a bit)..."
if cp --reflink=auto "$ROOTFS" "$ROOTFS_COPY" 2>/dev/null; then
  :
else
  cp "$ROOTFS" "$ROOTFS_COPY"
fi
chmod 666 "$ROOTFS_COPY"

if [ "$ROOTFS_GROW_GB" -gt 0 ]; then
  echo "[1/8] Expanding rootfs copy by ${ROOTFS_GROW_GB}G for repl build..."
  sudo truncate -s +${ROOTFS_GROW_GB}G "$ROOTFS_COPY"
  sudo e2fsck -p -f "$ROOTFS_COPY" >/dev/null || true
  sudo resize2fs "$ROOTFS_COPY" >/dev/null
fi

if [ ! -f "$DATA_DISK" ]; then
  echo "[2/8] Creating data disk for .lake (${DATA_DISK_MB}MB)..."
  dd if=/dev/zero of="$DATA_DISK" bs=1M count=$DATA_DISK_MB status=progress
  mkfs.ext4 -F "$DATA_DISK" >/dev/null
  chmod 666 "$DATA_DISK"
fi

mkdir -p "$MNT_DIR"
sudo mount "$ROOTFS_COPY" "$MNT_DIR"

# Ensure repl binary is available inside rootfs copy
sudo mount -t proc proc "$MNT_DIR/proc"
sudo mount -t sysfs sysfs "$MNT_DIR/sys"
sudo mount -t devtmpfs devtmpfs "$MNT_DIR/dev"

echo "[3/8] Installing lean REPL binary inside rootfs copy..."
sudo REPL_BUILD_JOBS="$REPL_BUILD_JOBS" chroot "$MNT_DIR" /bin/bash << 'CHROOTEOF'
set -e
export HOME=/root
export PATH="/root/.elan/bin:$PATH"

MATHLIB_TOOLCHAIN="$(cat /root/mathlib4/lean-toolchain 2>/dev/null || true)"
if [ -z "$MATHLIB_TOOLCHAIN" ]; then
  echo "ERROR: missing /root/mathlib4/lean-toolchain" >&2
  exit 1
fi

REBUILD_REPL=1
if [ -x /root/repl/.lake/build/bin/repl ] && [ -f /root/repl/lean-toolchain ]; then
  CURRENT_REPL_TOOLCHAIN="$(cat /root/repl/lean-toolchain 2>/dev/null || true)"
  if [ "$CURRENT_REPL_TOOLCHAIN" = "$MATHLIB_TOOLCHAIN" ]; then
    REBUILD_REPL=0
  fi
fi

REPL_FRONTEND_PATH=/root/repl/REPL/Frontend.lean
REPL_INFOTREE_PATCH_NEEDED=1
if [ -f "$REPL_FRONTEND_PATH" ]; then
  if grep -q 'infoState.enabled := false' "$REPL_FRONTEND_PATH"; then
    REPL_INFOTREE_PATCH_NEEDED=0
  fi
fi
if [ "$REPL_INFOTREE_PATCH_NEEDED" -eq 1 ]; then
  REBUILD_REPL=1
fi

if [ "$REBUILD_REPL" -eq 1 ]; then
  rm -rf /root/repl
  git clone --depth 1 https://github.com/leanprover-community/repl.git /root/repl
  printf '%s\n' "$MATHLIB_TOOLCHAIN" > /root/repl/lean-toolchain
  if grep -q 'infoState.enabled := true' /root/repl/REPL/Frontend.lean; then
    sed -i 's/infoState.enabled := true/infoState.enabled := false/' /root/repl/REPL/Frontend.lean
  fi
  cd /root/repl
  REPL_BUILD_JOBS="${REPL_BUILD_JOBS:-0}"
  LAKE_BUILD_HELP="$(lake build --help 2>/dev/null || true)"
  if printf '%s\n' "$LAKE_BUILD_HELP" | grep -q -- '--jobs'; then
    if [ "${REPL_BUILD_JOBS}" -gt 0 ] 2>/dev/null; then
      lake build --jobs "$REPL_BUILD_JOBS"
    else
      lake build
    fi
  else
    lake build
  fi
fi

if [ -x /root/repl/.lake/build/bin/repl ]; then
  mkdir -p /usr/local/bin
  cp /root/repl/.lake/build/bin/repl /usr/local/bin/leanrepl
  chmod +x /usr/local/bin/leanrepl
fi
CHROOTEOF

sudo umount "$MNT_DIR/proc" "$MNT_DIR/sys" "$MNT_DIR/dev"


echo "[4/8] Writing snapshot init script (REPL warm)..."
sudo tee "$MNT_DIR/init" >/dev/null <<'INIT_EOF'
#!/bin/bash
set -euo pipefail

log() {
  local line
  line="[$(date +'%Y-%m-%d %H:%M:%S')] $*"
  echo "$line" >> /tmp/atp_guest_init.log
  echo "$line" > /dev/ttyS0
}

karg_value() {
  local key="$1"
  local default="$2"
  local value
  value="$(tr ' ' '\n' < /proc/cmdline | grep "^${key}=" | sed "s/^${key}=//" | tail -1 || true)"
  if [ -z "$value" ]; then
    echo "$default"
  else
    echo "$value"
  fi
}

run_timed() {
  local label="$1"
  local timeout_s="$2"
  shift 2
  local start end dur rc
  start=$(date +%s)
  log "STAGE_BEGIN ${label}: timeout=${timeout_s}s cmd=$*"
  set +e
  timeout --foreground "$timeout_s" "$@"
  rc=$?
  set -e
  end=$(date +%s)
  dur=$((end - start))
  log "STAGE_END ${label}: rc=${rc} duration_s=${dur}"
  return $rc
}

mount_if_needed() {
  local fstype="$1"
  local source="$2"
  local target="$3"
  if mountpoint -q "$target"; then
    log "Mount skip: ${target} already mounted"
    return 0
  fi
  mkdir -p "$target"
  mount -t "$fstype" "$source" "$target"
  log "Mounted ${source} on ${target} (${fstype})"
}

mount_if_needed proc proc /proc
mount_if_needed sysfs sysfs /sys
mount_if_needed devtmpfs devtmpfs /dev
mkdir -p /dev/pts /dev/shm
mount_if_needed devpts devpts /dev/pts
mount_if_needed tmpfs tmpfs /dev/shm
mount_if_needed tmpfs tmpfs /tmp

hostname lean-sandbox
export HOME=/root
export PATH="/root/.elan/bin:$PATH"
export ELAN_HOME="/root/.elan"

EAGER_MATHLIB_IMPORT="$(karg_value eager_mathlib 0)"
MATHLIB_IMPORT_TIMEOUT_S="$(karg_value mathlib_import_timeout_s 5400)"
WARMUP_PROBE_TIMEOUT_S="$(karg_value warmup_probe_timeout_s 120)"
REPL_WARM_RETRIES="$(karg_value repl_warm_retries 2)"
USE_MATHLIB_PICKLE="$(karg_value use_mathlib_pickle 1)"
MATHLIB_PICKLE_PATH="$(karg_value mathlib_pickle_path /mnt/data/mathlib_env.olean)"
PICKLE_AFTER_IMPORT="$(karg_value pickle_after_import 1)"
CACHE_GET_TIMEOUT_S="$(karg_value cache_get_timeout_s 1800)"
LAKE_BUILD_TIMEOUT_S="$(karg_value lake_build_timeout_s 3600)"
SKIP_CACHE_GET_IF_PRESENT="$(karg_value skip_cache_get_if_present 1)"
SKIP_LAKE_BUILD_IF_PRESENT="$(karg_value skip_lake_build_if_present 1)"
export EAGER_MATHLIB_IMPORT MATHLIB_IMPORT_TIMEOUT_S WARMUP_PROBE_TIMEOUT_S REPL_WARM_RETRIES
export USE_MATHLIB_PICKLE MATHLIB_PICKLE_PATH PICKLE_AFTER_IMPORT
log "Guest boot params: eager_mathlib=${EAGER_MATHLIB_IMPORT} import_timeout_s=${MATHLIB_IMPORT_TIMEOUT_S} probe_timeout_s=${WARMUP_PROBE_TIMEOUT_S} warm_retries=${REPL_WARM_RETRIES}"
log "Guest warm cache params: use_mathlib_pickle=${USE_MATHLIB_PICKLE} pickle_after_import=${PICKLE_AFTER_IMPORT} pickle_path=${MATHLIB_PICKLE_PATH}"
log "Guest build params: cache_timeout_s=${CACHE_GET_TIMEOUT_S} build_timeout_s=${LAKE_BUILD_TIMEOUT_S} skip_cache_if_present=${SKIP_CACHE_GET_IF_PRESENT} skip_build_if_present=${SKIP_LAKE_BUILD_IF_PRESENT}"

# Prepare data disk for .lake (extra build space)
mkdir -p /mnt/data
if ! mountpoint -q /mnt/data && ! mount -t ext4 /dev/vdb /mnt/data; then
  log "First mount /dev/vdb failed; retrying once after 2s"
  sleep 2
  if ! mountpoint -q /mnt/data; then
    mount -t ext4 /dev/vdb /mnt/data
  fi
fi
mkdir -p /mnt/data/.lake

# If data disk is empty, seed it from rootfs .lake (so no network fetch needed)
if [ ! -d /mnt/data/.lake/packages ] && [ -d /root/mathlib4/.lake ]; then
  log "Seeding /mnt/data/.lake from rootfs copy"
  cp -a /root/mathlib4/.lake/. /mnt/data/.lake/ || true
fi

# Bind .lake to data disk
rm -rf /root/mathlib4/.lake
mkdir -p /root/mathlib4/.lake
mount --bind /mnt/data/.lake /root/mathlib4/.lake

cd /root/mathlib4 2>/dev/null || cd /root

CACHE_PRESENT=0
if [ -d /root/mathlib4/.lake/packages ] && [ "$(find /root/mathlib4/.lake/packages -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)" -gt 0 ]; then
  CACHE_PRESENT=1
fi
BUILD_PRESENT=0
if [ -f /root/mathlib4/.lake/build/lib/Mathlib.olean ]; then
  BUILD_PRESENT=1
fi

if [ "$SKIP_CACHE_GET_IF_PRESENT" = "1" ] && [ "$CACHE_PRESENT" = "1" ]; then
  log "Skipping cache get (packages already present)"
else
  run_timed "cache_get" "$CACHE_GET_TIMEOUT_S" lake exe cache get || log "cache_get returned non-zero"
fi

if [ "$SKIP_LAKE_BUILD_IF_PRESENT" = "1" ] && [ "$BUILD_PRESENT" = "1" ]; then
  log "Skipping lake build (Mathlib.olean already present)"
else
  run_timed "lake_build" "$LAKE_BUILD_TIMEOUT_S" lake build || log "lake_build returned non-zero"
fi

# Start persistent Lean REPL and vsock server
cat >/tmp/atp_vsock_repl_agent.py <<'PYEOF'
#!/usr/bin/env python3
import base64
import json
import os
import socket
import subprocess
import sys
import time
import select

PORT = 52
PROTOCOL_VERSION = 1
CAPABILITIES = {"repl": True, "envelope": True, "file_io_b64": True}
if len(sys.argv) > 1:
    try:
        PORT = int(sys.argv[1])
    except Exception:
        PORT = 52

REPL_BIN = "/usr/local/bin/leanrepl"
if not os.path.exists(REPL_BIN):
    REPL_BIN = "/root/repl/.lake/build/bin/repl"
LAST_TIMEOUT_DIAG = ""

def _clip(text):
    if not text:
        return ""
    return str(text)[:600]

def _log(message):
    try:
        print(message, flush=True)
    except Exception:
        pass

def _run_cmd(cmd, timeout_s=3.0):
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return f"$ {cmd}\nrc={proc.returncode}\n{out}"
    except Exception as exc:
        return f"$ {cmd}\nerror={exc}"

def _proc_diag(proc):
    if proc is None:
        return ""
    try:
        pid = int(proc.pid)
    except Exception:
        return ""
    status_path = f"/proc/{pid}/status"
    if not os.path.exists(status_path):
        return ""
    state = ""
    vmrss = ""
    vmhwm = ""
    wchan = ""
    try:
        with open(status_path, "r", errors="ignore") as f:
            for line in f:
                if line.startswith("State:"):
                    state = line.strip()
                elif line.startswith("VmRSS:"):
                    vmrss = line.strip()
                elif line.startswith("VmHWM:"):
                    vmhwm = line.strip()
        wchan_path = f"/proc/{pid}/wchan"
        if os.path.exists(wchan_path):
            with open(wchan_path, "r", errors="ignore") as f:
                wchan_val = f.read().strip()
            if wchan_val:
                wchan = f"wchan={wchan_val}"
        parts = [p for p in [f"pid={pid}", state, vmrss, vmhwm, wchan] if p]
        return ", ".join(parts)
    except Exception:
        return ""

def _collect_timeout_diag(proc, reason, buffer_tail):
    global LAST_TIMEOUT_DIAG
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = f"/tmp/atp_repl_timeout_{ts}.log"
    lines = []
    lines.append(f"timestamp={time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"reason={reason}")
    lines.append(f"repl_bin={REPL_BIN}")
    lines.append(f"buffer_tail={_clip(buffer_tail)}")
    pid = None
    try:
        if proc is not None:
            pid = int(proc.pid)
    except Exception:
        pid = None
    def _list_children(ppid):
        try:
            proc_children = subprocess.run(
                ["ps", "-o", "pid=", "--ppid", str(int(ppid))],
                text=True,
                capture_output=True,
                timeout=2.0,
            )
            if proc_children.returncode != 0:
                return []
            out = []
            for token in (proc_children.stdout or "").split():
                try:
                    child_pid = int(token.strip())
                    if child_pid > 0:
                        out.append(child_pid)
                except Exception:
                    continue
            return out
        except Exception:
            return []

    def _append_pid_dump(out_lines, target_pid, label):
        out_lines.append(f"--- proc dump: {label} pid={target_pid} ---")
        out_lines.append(_run_cmd(f"ps -o pid,ppid,stat,pcpu,pmem,rss,vsz,etime,wchan,cmd -p {target_pid}", timeout_s=2.0))
        out_lines.append(_run_cmd(f"cat /proc/{target_pid}/status", timeout_s=2.0))
        out_lines.append(_run_cmd(f"cat /proc/{target_pid}/wchan", timeout_s=2.0))
        out_lines.append(_run_cmd(f"cat /proc/{target_pid}/stack", timeout_s=2.0))
        out_lines.append(_run_cmd(f"cat /proc/{target_pid}/io", timeout_s=2.0))
        out_lines.append(_run_cmd(f"ls -l /proc/{target_pid}/fd | sed -n '1,120p'", timeout_s=2.0))

    if pid is not None:
        _append_pid_dump(lines, pid, "repl_driver")
        children = _list_children(pid)
        lines.append(f"repl_driver_children={children}")
        max_children = 4
        max_grandchildren = 4
        for child_index, child_pid in enumerate(children[:max_children], start=1):
            _append_pid_dump(lines, child_pid, f"child[{child_index}]")
            grandchildren = _list_children(child_pid)
            lines.append(f"child[{child_index}]_children={grandchildren}")
            for grandchild_index, grandchild_pid in enumerate(grandchildren[:max_grandchildren], start=1):
                _append_pid_dump(lines, grandchild_pid, f"grandchild[{child_index}.{grandchild_index}]")
    lines.append(_run_cmd("ps -eo pid,ppid,stat,pcpu,pmem,rss,etime,wchan,cmd | sed -n '1,160p'", timeout_s=3.0))
    lines.append(_run_cmd("df -h", timeout_s=2.0))
    lines.append(_run_cmd("free -m", timeout_s=2.0))
    lines.append(_run_cmd("ls -la /usr/local/bin/leanrepl /root/repl/.lake/build/bin/repl /root/.elan/bin/lake 2>/dev/null || true", timeout_s=2.0))
    lines.append(_run_cmd("file /usr/local/bin/leanrepl 2>/dev/null || true", timeout_s=2.0))
    lines.append(_run_cmd("ldd /usr/local/bin/leanrepl 2>/dev/null || true", timeout_s=2.0))
    lines.append(_run_cmd("cd /root/mathlib4 && /root/.elan/bin/lake env --help 2>&1 | head -n 80", timeout_s=3.0))
    lines.append(_run_cmd("tail -n 200 /tmp/atp_vsock_repl.log", timeout_s=2.0))
    try:
        with open(path, "w", encoding="utf-8", errors="ignore") as f:
            f.write("\n\n".join(lines))
        LAST_TIMEOUT_DIAG = path
    except Exception:
        LAST_TIMEOUT_DIAG = ""
        path = ""
    return path

def _read_diag_excerpt(path, max_lines=160, max_chars=24000):
    if not path:
        return ""
    try:
        with open(path, "r", errors="ignore") as f:
            lines = f.readlines()
        if not lines:
            return ""
        # Include both the beginning (often contains reason/buffer_tail) and the end (process dumps).
        head_n = max(0, min(60, len(lines)))
        tail_n = max(0, min(int(max_lines), len(lines)))
        selected = []
        selected.extend(lines[:head_n])
        if tail_n > 0:
            if head_n > 0:
                selected.append("\n--- diag tail ---\n")
            selected.extend(lines[-tail_n:])
        text = "".join(selected)
        if len(text) > int(max_chars):
            text = text[-int(max_chars):]
        return text
    except Exception:
        return ""

def _extract_json(buffer):
    text = buffer
    start = text.find("{")
    if start == -1:
        return None, text[-65536:]
    if start > 0:
        text = text[start:]
    depth = 0
    in_string = False
    escape = False
    for idx, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[: idx + 1]
                rest = text[idx + 1 :]
                try:
                    parsed = json.loads(candidate)
                    return parsed, rest[-65536:]
                except json.JSONDecodeError:
                    return None, rest[-65536:]
    return None, text[-65536:]

def _has_error_message(resp):
    if not isinstance(resp, dict):
        return False
    # The REPL encodes errors as `{ "message": "..." }` in some cases (e.g. unknown environment).
    if isinstance(resp.get("message"), str) and resp.get("message").strip():
        return True
    messages = resp.get("messages")
    if not isinstance(messages, list):
        return False
    for msg in messages:
        if isinstance(msg, dict) and str(msg.get("severity", "")).lower() == "error":
            return True
    return False

def _looks_like_request_payload(resp):
    if not isinstance(resp, dict):
        return False
    # Only treat objects that actually look like a request envelope as "not a response".
    # Responses can legitimately be as small as {"env": 0}, so the older "subset of keys"
    # heuristic caused us to drop real responses and hang waiting for more output.
    if "cmd" in resp and isinstance(resp.get("cmd"), str):
        return True
    if "command" in resp and isinstance(resp.get("command"), str):
        return True
    if "payload" in resp and isinstance(resp.get("payload"), dict) and (
        "cmd" in (resp.get("payload") or {}) or "command" in (resp.get("payload") or {})
    ):
        return True
    return False

class PersistentLeanRepl:
    def __init__(self):
        self.proc = None
        self.stdin_fd = None
        self.stdout_fd = None
        self.buffer = ""
        self.env_id = None
        self._pgid = None

    def _spawn(self):
        self.close()
        cmd = ["/root/.elan/bin/lake", "env", REPL_BIN]
        _log(f"spawning repl: {' '.join(cmd)} (cwd=/root/mathlib4)")
        self.proc = subprocess.Popen(
            cmd,
            cwd="/root/mathlib4",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=True,
            bufsize=0,
            start_new_session=True,
        )
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("repl pipes unavailable")
        self.stdin_fd = self.proc.stdin.fileno()
        self.stdout_fd = self.proc.stdout.fileno()
        self.buffer = ""
        try:
            self._pgid = os.getpgid(self.proc.pid)
        except Exception:
            self._pgid = None

    def _ensure(self):
        if self.proc is None or self.proc.poll() is not None or self.stdin_fd is None or self.stdout_fd is None:
            self._spawn()

    def _read_response(self, timeout_s):
        deadline = time.time() + float(timeout_s)
        start = time.time()
        next_heartbeat = start + 20.0
        while time.time() < deadline:
            parsed, self.buffer = _extract_json(self.buffer)
            if parsed is not None:
                if _looks_like_request_payload(parsed):
                    continue
                return parsed, None
            if self.stdout_fd is None:
                return None, "repl fd unavailable"
            wait_s = max(0.0, min(0.25, deadline - time.time()))
            if wait_s == 0:
                break
            now = time.time()
            if now >= next_heartbeat:
                _log(f"repl wait heartbeat: elapsed={int(now - start)}s timeout_s={int(timeout_s)}")
                next_heartbeat = now + 20.0
            ready, _, _ = select.select([self.stdout_fd], [], [], wait_s)
            if not ready:
                continue
            try:
                chunk = os.read(self.stdout_fd, 4096)
            except OSError as exc:
                return None, f"repl read error: {_clip(str(exc))}"
            if not chunk:
                if self.proc is not None and self.proc.poll() is not None:
                    rc = self.proc.returncode
                    snippet = _clip(self.buffer.strip()[-4000:])
                    # Even on immediate failures, `lake env` often emits a plain-text error message
                    # (not JSON). Include it and write a diag bundle so we can see *why* it exited.
                    diag_path = _collect_timeout_diag(self.proc, f"repl_exited_rc={rc}", snippet)
                    diag_suffix = f"; diag={diag_path}" if diag_path else ""
                    if snippet:
                        return None, f"repl exited rc={rc} tail={snippet!r}{diag_suffix}"
                    return None, f"repl exited rc={rc}{diag_suffix}"
                continue
            self.buffer += chunk.decode("utf-8", errors="ignore").replace("\r", "")
        snippet = _clip(self.buffer.strip()[-200:])
        proc_diag = _proc_diag(self.proc)
        proc_suffix = f"; {proc_diag}" if proc_diag else ""
        diag_path = _collect_timeout_diag(self.proc, "repl_read_timeout", snippet)
        diag_suffix = f"; diag={diag_path}" if diag_path else ""
        if snippet:
            return None, f"repl timeout (tail={snippet!r}{proc_suffix}{diag_suffix})"
        return None, f"repl timeout{proc_suffix}{diag_suffix}"

    def send(self, command, timeout_s=120.0, allow_restart=True):
        try:
            self._ensure()
        except Exception as exc:
            return None, f"repl spawn failed: {_clip(str(exc))}"
        def _is_import_cmd(text):
            try:
                s = str(text or "").lstrip()
            except Exception:
                return False
            return s.startswith("import ")

        is_payload = isinstance(command, dict)
        if is_payload:
            payload = dict(command)
            cmd_text = payload.get("cmd")
        else:
            payload = {"cmd": command}
            cmd_text = command

        is_import = _is_import_cmd(cmd_text)
        can_auto_env = (not is_payload) or ("cmd" in payload)
        if is_import:
            # `import ...` commands must be sent without `env` per upstream REPL protocol.
            payload.pop("env", None)
        else:
            # Preserve any explicit `env` field on non-import requests (needed for pickling),
            # otherwise default to the most recent environment when sending `cmd` requests.
            if "env" not in payload and can_auto_env and isinstance(self.env_id, int) and self.env_id >= 0:
                payload["env"] = self.env_id
        # REPL/Main.lean's getLines reads until a blank line.
        # Requests must end with two newlines, not one.
        request_bytes = (json.dumps(payload) + "\n\n").encode()
        try:
            prefix = ""
            try:
                prefix = str(cmd_text or "").strip().replace("\n", " ")[:80]
            except Exception:
                prefix = ""
            _log(
                f"repl send: is_import={is_import} has_env={'env' in payload} env={payload.get('env')} cmd_prefix={prefix!r} bytes={len(request_bytes)}"
            )
            os.write(self.stdin_fd, request_bytes)
        except OSError as exc:
            if allow_restart:
                try:
                    self._spawn()
                except Exception:
                    return None, f"repl write error: {_clip(str(exc))}"
                return self.send(command, timeout_s=timeout_s, allow_restart=False)
            return None, f"repl write error: {_clip(str(exc))}"
        response, err = self._read_response(timeout_s)
        should_restart = (
            response is None
            and allow_restart
            and ("timeout" not in str(err or "").lower())
        )
        if should_restart:
            try:
                self._spawn()
            except Exception:
                return None, err or "repl restart failed"
            is_payload = isinstance(command, dict)
            if is_payload:
                payload = dict(command)
                cmd_text = payload.get("cmd")
            else:
                payload = {"cmd": command}
                cmd_text = command
            is_import = _is_import_cmd(cmd_text)
            can_auto_env = (not is_payload) or ("cmd" in payload)
            if (not is_import) and can_auto_env and isinstance(self.env_id, int) and self.env_id >= 0:
                payload["env"] = self.env_id
            else:
                payload.pop("env", None)
            try:
                os.write(self.stdin_fd, (json.dumps(payload) + "\n\n").encode())
            except OSError as exc:
                return None, f"repl write error after restart: {_clip(str(exc))}"
            response, err = self._read_response(timeout_s)
        if isinstance(response, dict):
            env = response.get("env")
            if isinstance(env, int) and env >= 0:
                self.env_id = env
                _log(f"repl recv: env={env}")
        return response, err

    def close(self):
        self.stdin_fd = None
        self.stdout_fd = None
        if self.proc is not None and self.proc.poll() is None:
            # Best-effort: kill the whole process group so `lake env` doesn't leave orphaned `leanrepl`.
            try:
                if isinstance(self._pgid, int) and self._pgid > 0:
                    os.killpg(self._pgid, 15)
                else:
                    self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    if isinstance(self._pgid, int) and self._pgid > 0:
                        os.killpg(self._pgid, 9)
                    else:
                        self.proc.kill()
                except Exception:
                    pass
        if self.proc is not None:
            try:
                if self.proc.stdin is not None:
                    self.proc.stdin.close()
            except Exception:
                pass
            try:
                if self.proc.stdout is not None:
                    self.proc.stdout.close()
            except Exception:
                pass
        self.proc = None
        self.buffer = ""
        self._pgid = None

repl = PersistentLeanRepl()

warm_timeout_s = 5400.0
probe_timeout_s = 120.0
warm_retries = 2
use_mathlib_pickle = True
pickle_after_import = True
mathlib_pickle_path = "/mnt/data/mathlib_env.olean"
try:
    eager_import = str(os.environ.get("EAGER_MATHLIB_IMPORT", "0")).strip().lower() in {"1", "true", "yes", "on"}
except Exception:
    eager_import = False
try:
    warm_timeout_s = float(os.environ.get("MATHLIB_IMPORT_TIMEOUT_S", "5400") or "5400")
except Exception:
    warm_timeout_s = 5400.0
try:
    probe_timeout_s = float(os.environ.get("WARMUP_PROBE_TIMEOUT_S", "120") or "120")
except Exception:
    probe_timeout_s = 120.0
try:
    warm_retries = int(float(os.environ.get("REPL_WARM_RETRIES", "2") or "2"))
except Exception:
    warm_retries = 2
try:
    use_mathlib_pickle = str(os.environ.get("USE_MATHLIB_PICKLE", "1")).strip().lower() in {"1", "true", "yes", "on"}
except Exception:
    use_mathlib_pickle = True
try:
    pickle_after_import = str(os.environ.get("PICKLE_AFTER_IMPORT", "1")).strip().lower() in {"1", "true", "yes", "on"}
except Exception:
    pickle_after_import = True
try:
    mathlib_pickle_path = str(os.environ.get("MATHLIB_PICKLE_PATH", "/mnt/data/mathlib_env.olean") or "/mnt/data/mathlib_env.olean").strip()
except Exception:
    mathlib_pickle_path = "/mnt/data/mathlib_env.olean"
if not mathlib_pickle_path:
    mathlib_pickle_path = "/mnt/data/mathlib_env.olean"
if warm_timeout_s < 30.0:
    warm_timeout_s = 30.0
if probe_timeout_s < 5.0:
    probe_timeout_s = 5.0
if warm_retries < 1:
    warm_retries = 1

def _cmd_succeeded(resp):
    return (resp is not None) and (not _has_error_message(resp))

def _is_mathlib_import_command(command):
    try:
        text = str(command or "").strip()
    except Exception:
        return False
    if not text:
        return False
    tokens = text.replace(",", " ").split()
    if len(tokens) < 2:
        return False
    if tokens[0] != "import":
        return False
    for token in tokens[1:]:
        normalized = token.strip()
        if normalized == "Mathlib" or normalized.startswith("Mathlib."):
            return True
    return False

def _send_with_retries(command, timeout_s, retries, tag):
    retries = max(1, int(retries))
    last_err = None
    for attempt in range(1, retries + 1):
        resp, err = repl.send(command, timeout_s=timeout_s, allow_restart=(attempt < retries))
        ok = _cmd_succeeded(resp)
        _log(f"{tag} attempt {attempt}/{retries}: ok={ok} err={err!r}")
        if ok:
            return resp, None
        last_err = err or "repl command failed"
        if attempt < retries:
            repl.close()
            time.sleep(1.0)
    return None, last_err

def _send_payload_with_retries(payload, timeout_s, retries, tag):
    retries = max(1, int(retries))
    last_err = None
    for attempt in range(1, retries + 1):
        resp, err = repl.send(payload, timeout_s=timeout_s, allow_restart=(attempt < retries))
        ok = _cmd_succeeded(resp)
        _log(f"{tag} attempt {attempt}/{retries}: ok={ok} err={err!r}")
        if ok:
            return resp, None
        last_err = err or "repl payload failed"
        if attempt < retries:
            repl.close()
            time.sleep(1.0)
    return None, last_err

probe, probe_err = _send_with_retries("#check Nat", timeout_s=probe_timeout_s, retries=min(2, warm_retries), tag="warmup_probe")
probe_ok = (
    _cmd_succeeded(probe)
)
_log(f"warmup probe finished: ok={probe_ok} err={probe_err!r}")

mathlib_ready = False
warm, warm_err = (None, None)
mathlib_probe_ok = False
mathlib_probe_err = None
unpickle, unpickle_err = (None, None)
pickle_resp, pickle_err = (None, None)

if eager_import and probe_ok:
    if use_mathlib_pickle and mathlib_pickle_path and os.path.exists(mathlib_pickle_path):
        unpickle, unpickle_err = _send_payload_with_retries(
            {"unpickleEnvFrom": mathlib_pickle_path},
            timeout_s=min(600.0, warm_timeout_s),
            retries=1,
            tag="warmup_unpickle",
        )
        mathlib_ready = _cmd_succeeded(unpickle)
        _log(f"warmup unpickle finished: ok={mathlib_ready} err={unpickle_err!r} path={mathlib_pickle_path!r}")

    if not mathlib_ready:
        warm, warm_err = _send_with_retries("import Mathlib", timeout_s=warm_timeout_s, retries=warm_retries, tag="warmup_import")
        mathlib_ready = _cmd_succeeded(warm)
        _log(f"warmup import finished: ok={mathlib_ready} err={warm_err!r}")

        if mathlib_ready and pickle_after_import and mathlib_pickle_path:
            env_for_pickle = repl.env_id if isinstance(repl.env_id, int) and repl.env_id >= 0 else None
            if env_for_pickle is None and isinstance(warm, dict):
                env_val = warm.get("env")
                if isinstance(env_val, int) and env_val >= 0:
                    env_for_pickle = env_val
            if env_for_pickle is not None:
                pickle_payload = {"pickleTo": mathlib_pickle_path, "env": int(env_for_pickle)}
                pickle_resp, pickle_err = _send_payload_with_retries(
                    pickle_payload,
                    timeout_s=min(300.0, warm_timeout_s),
                    retries=1,
                    tag="warmup_pickle",
                )
                _log(f"warmup pickle finished: ok={_cmd_succeeded(pickle_resp)} err={pickle_err!r} path={mathlib_pickle_path!r}")
            else:
                _log("warmup pickle skipped: env id unavailable")

    if mathlib_ready:
        # Verify that a Mathlib-only symbol is available in the current env.
        _, mathlib_probe_err = _send_with_retries(
            "#check CategoryTheory.Category",
            timeout_s=min(180.0, probe_timeout_s),
            retries=1,
            tag="mathlib_probe",
        )
        mathlib_probe_ok = mathlib_probe_err is None
        _log(f"mathlib probe finished: ok={mathlib_probe_ok} err={mathlib_probe_err!r}")

warm_ok = probe_ok and ((not eager_import) or (mathlib_ready and mathlib_probe_ok))
warm_reason = None
warm_diag_excerpt = ""
if not warm_ok:
    warm_reason = warm_err or unpickle_err or mathlib_probe_err or probe_err or "repl warmup probe failed"
    if LAST_TIMEOUT_DIAG and "diag=" not in str(warm_reason):
        warm_reason = f"{warm_reason}; diag={LAST_TIMEOUT_DIAG}"
    if LAST_TIMEOUT_DIAG:
        warm_diag_excerpt = _read_diag_excerpt(LAST_TIMEOUT_DIAG)
    if "timeout" in str(warm_reason).lower():
        try:
            with open("/tmp/atp_vsock_repl.log", "r", errors="ignore") as f:
                tail = f.read()[-4000:]
            tail = tail.strip().replace("\n", "\\n")
            if tail:
                warm_reason = f"{warm_reason}; agent_log_tail={_clip(tail)}"
        except Exception:
            pass

# Signal readiness for snapshot loop.
try:
    with open("/dev/ttyS0", "w") as f:
        if warm_ok:
            if eager_import:
                f.write("Mathlib ready for snapshot\n")
            else:
                f.write("Mathlib ready for snapshot (lazy import)\n")
        else:
            if warm_diag_excerpt:
                f.write("Mathlib warmup diag begin\n")
                f.write(warm_diag_excerpt)
                if not warm_diag_excerpt.endswith("\n"):
                    f.write("\n")
                f.write("Mathlib warmup diag end\n")
            f.write(f"Mathlib warmup failed: {warm_reason}\n")
        f.flush()
except Exception:
    pass

# Vsock server for host requests.
server = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((socket.VMADDR_CID_ANY, PORT))
server.listen(5)

def _send_json(conn, payload):
    conn.sendall((json.dumps(payload) + "\n").encode())

def _parse_repl_request(req):
    if req.get("type") == "repl":
        payload = req.get("payload") or {}
        command = payload.get("command") or payload.get("cmd")
        timeout = payload.get("timeout", 120)
        return command, timeout, "envelope"
    if req.get("mode") == "repl":
        command = req.get("command") or req.get("cmd")
        timeout = req.get("timeout", 120)
        return command, timeout, "mode"
    if "cmd" in req:
        return req.get("cmd"), req.get("timeout", 120), "cmd"
    if "code" in req:
        return req.get("code"), req.get("timeout", 120), "code"
    return None, None, None

while True:
    conn, _ = server.accept()
    try:
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        if not data:
            conn.close()
            continue
        req = json.loads(data.decode())

        if req.get("type") == "hello":
            _send_json(
                conn,
                {
                    "type": "hello_ack",
                    "version": PROTOCOL_VERSION,
                    "capabilities": CAPABILITIES,
                },
            )
            continue

        if req.get("type") == "file_io":
            op = req.get("op")
            path = req.get("path")
            try:
                if not isinstance(path, str) or not path.strip():
                    _send_json(conn, {"type": "file_io_response", "ok": False, "error": "path required"})
                    continue
                if op == "write_b64":
                    data_b64 = req.get("data_b64") or ""
                    if not isinstance(data_b64, str):
                        _send_json(conn, {"type": "file_io_response", "ok": False, "error": "data_b64 must be str"})
                        continue
                    parent = os.path.dirname(path) or "/tmp"
                    os.makedirs(parent, exist_ok=True)
                    payload = base64.b64decode(data_b64.encode("ascii")) if data_b64 else b""
                    with open(path, "wb") as f:
                        f.write(payload)
                    _send_json(conn, {"type": "file_io_response", "ok": True, "bytes": len(payload)})
                    continue
                if op == "read_b64":
                    max_bytes = req.get("max_bytes")
                    try:
                        max_bytes = int(max_bytes) if max_bytes is not None else 0
                    except Exception:
                        max_bytes = 0
                    if max_bytes <= 0:
                        max_bytes = 64 * 1024 * 1024
                    size = os.path.getsize(path)
                    if size > max_bytes:
                        _send_json(
                            conn,
                            {
                                "type": "file_io_response",
                                "ok": False,
                                "error": f"file too large: {size} bytes (max {max_bytes})",
                                "bytes": size,
                            },
                        )
                        continue
                    with open(path, "rb") as f:
                        data = f.read()
                    _send_json(
                        conn,
                        {
                            "type": "file_io_response",
                            "ok": True,
                            "bytes": len(data),
                            "data_b64": base64.b64encode(data).decode("ascii") if data else "",
                        },
                    )
                    continue
                _send_json(conn, {"type": "file_io_response", "ok": False, "error": f"unknown op: {op!r}"})
                continue
            except Exception as exc:
                _send_json(conn, {"type": "file_io_response", "ok": False, "error": str(exc)})
                continue

        command, timeout, req_kind = _parse_repl_request(req)
        if command:
            if (not mathlib_ready) and (not str(command).lstrip().startswith("import ")):
                lazy_timeout = warm_timeout_s
                lazy_warm, lazy_err = _send_with_retries("import Mathlib", timeout_s=lazy_timeout, retries=warm_retries, tag="lazy_import")
                if not _cmd_succeeded(lazy_warm):
                    repl_err_msg = lazy_err or "lazy import Mathlib failed"
                    if req_kind == "envelope":
                        _send_json(
                            conn,
                            {
                                "type": "repl_response",
                                "version": PROTOCOL_VERSION,
                                "error": {
                                    "code": "repl_error",
                                    "message": repl_err_msg,
                                },
                            },
                        )
                    else:
                        _send_json(conn, {"exit": 1, "response": None, "stderr": repl_err_msg})
                    continue
                mathlib_ready = True
            timeout_value = float(timeout if timeout is not None else 120.0)
            resp, repl_err = repl.send(command, timeout_s=timeout_value)
            if _cmd_succeeded(resp) and _is_mathlib_import_command(command):
                mathlib_ready = True
            if req_kind == "envelope":
                if resp is None:
                    _send_json(
                        conn,
                        {
                            "type": "repl_response",
                            "version": PROTOCOL_VERSION,
                            "error": {
                                "code": "repl_error",
                                "message": repl_err or "repl failure",
                            },
                        },
                    )
                else:
                    _send_json(
                        conn,
                        {
                            "type": "repl_response",
                            "version": PROTOCOL_VERSION,
                            "response": resp,
                        },
                    )
            else:
                out = {"exit": 0 if resp is not None else 1, "response": resp}
                if resp is None:
                    out["stderr"] = repl_err or "repl failure"
                _send_json(conn, out)
            continue

        # Shell execution fallback
        cmd = req.get("command", "")
        env = req.get("env") or {}
        stdin_data = req.get("stdin")
        timeout = req.get("timeout")
        cwd = req.get("cwd") or "/root"
        merged_env = os.environ.copy()
        for k, v in env.items():
            if k:
                merged_env[str(k)] = str(v)
        try:
            res = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                env=merged_env,
                input=stdin_data,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            out = {
                "exit": res.returncode,
                "stdout": res.stdout or "",
                "stderr": res.stderr or "",
            }
        except subprocess.TimeoutExpired:
            out = {"exit": 124, "stdout": "", "stderr": "Command timed out"}
        except Exception as exc:
            out = {"exit": 1, "stdout": "", "stderr": str(exc)}
        _send_json(conn, out)
    except Exception as exc:
        try:
            _send_json(conn, {"exit": 1, "stderr": f"vsock agent error: {exc}"})
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
PYEOF
chmod +x /tmp/atp_vsock_repl_agent.py

# Start REPL agent (captures Mathlib in memory)
VSOCK_PORT=$(cat /proc/cmdline | tr ' ' '\n' | grep 'vsock_port=' | sed 's/vsock_port=//' | tail -1)
if [ -z "$VSOCK_PORT" ]; then
  VSOCK_PORT=52
fi
log "Launching vsock REPL agent on port ${VSOCK_PORT}"
nohup python3 /tmp/atp_vsock_repl_agent.py "$VSOCK_PORT" >/tmp/atp_vsock_repl.log 2>&1 &
AGENT_PID=$!
sleep 1
if ! kill -0 "$AGENT_PID" 2>/dev/null; then
  log "Vsock REPL agent failed to start"
  tail -n 120 /tmp/atp_vsock_repl.log > /dev/ttyS0 || true
  echo "Mathlib warmup failed: vsock agent crashed during startup" > /dev/ttyS0
else
  log "Vsock REPL agent running with pid=${AGENT_PID}"
fi

while true; do sleep 1; done
INIT_EOF
sudo chmod +x "$MNT_DIR/init"
sudo ln -sf /init "$MNT_DIR/sbin/init"

sudo umount "$MNT_DIR"

FC_BIN=$(command -v firecracker || true)
if [ -z "$FC_BIN" ]; then
  for p in /usr/local/bin/firecracker /var/lib/firecracker/bin/firecracker /usr/bin/firecracker; do
    if [ -x "$p" ]; then
      FC_BIN="$p"
      break
    fi
  done
fi
if [ -z "$FC_BIN" ]; then
  echo "Firecracker binary not found in PATH or standard locations" >&2
  exit 1
fi

rm -f "$API_SOCK" "$VSOCK_SOCK"
: > "$LOG_FILE"
: > "$SERIAL_LOG"

echo "[5/8] Starting Firecracker..."
"$FC_BIN" --api-sock "$API_SOCK" --log-path "$LOG_FILE" --level Warn > "$SERIAL_LOG" 2>&1 &
FC_PID=$!

for i in {1..100}; do
  if [ -S "$API_SOCK" ]; then
    break
  fi
  sleep 0.1
done

configure_vm() {
  local endpoint="$1"
  local data="$2"
  local body_file status
  body_file="$(mktemp)"
  status="$(curl --unix-socket "$API_SOCK" -sS -o "$body_file" -w '%{http_code}' -X PUT "http://localhost$endpoint" \
    -H 'Content-Type: application/json' \
    -d "$data" || echo "000")"
  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    echo "Firecracker API PUT failed: endpoint=$endpoint status=$status" >&2
    echo "Payload: $data" >&2
    echo "Response body:" >&2
    cat "$body_file" >&2 || true
    rm -f "$body_file"
    write_failure_bundle "firecracker API PUT failure: $endpoint status=$status"
    exit 1
  fi
  rm -f "$body_file"
}

patch_vm() {
  local endpoint="$1"
  local data="$2"
  local body_file status
  body_file="$(mktemp)"
  status="$(curl --unix-socket "$API_SOCK" -sS -o "$body_file" -w '%{http_code}' -X PATCH "http://localhost$endpoint" \
    -H 'Content-Type: application/json' \
    -d "$data" || echo "000")"
  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    echo "Firecracker API PATCH failed: endpoint=$endpoint status=$status" >&2
    echo "Payload: $data" >&2
    echo "Response body:" >&2
    cat "$body_file" >&2 || true
    rm -f "$body_file"
    write_failure_bundle "firecracker API PATCH failure: $endpoint status=$status"
    exit 1
  fi
  rm -f "$body_file"
}

configure_vm "/machine-config" "{\"vcpu_count\": $VCPU_COUNT, \"mem_size_mib\": $MEM_MIB}"
configure_vm "/boot-source" "{\"kernel_image_path\": \"$KERNEL\", \"boot_args\": \"console=ttyS0 reboot=k panic=1 pci=off vsock_port=$VSOCK_PORT eager_mathlib=$EAGER_MATHLIB_IMPORT mathlib_import_timeout_s=$MATHLIB_IMPORT_TIMEOUT_S warmup_probe_timeout_s=$WARMUP_PROBE_TIMEOUT_S repl_warm_retries=$REPL_WARM_RETRIES use_mathlib_pickle=${USE_MATHLIB_PICKLE:-1} mathlib_pickle_path=${MATHLIB_PICKLE_PATH:-/mnt/data/mathlib_env.olean} pickle_after_import=${PICKLE_AFTER_IMPORT:-1} cache_get_timeout_s=$CACHE_GET_TIMEOUT_S lake_build_timeout_s=$LAKE_BUILD_TIMEOUT_S skip_cache_get_if_present=$SKIP_CACHE_GET_IF_PRESENT skip_lake_build_if_present=$SKIP_LAKE_BUILD_IF_PRESENT\"}"
configure_vm "/drives/rootfs" "{\"drive_id\": \"rootfs\", \"path_on_host\": \"$ROOTFS_COPY\", \"is_root_device\": true, \"is_read_only\": false}"
configure_vm "/drives/data" "{\"drive_id\": \"data\", \"path_on_host\": \"$DATA_DISK\", \"is_root_device\": false, \"is_read_only\": false}"
configure_vm "/vsock" "{\"guest_cid\": 3, \"uds_path\": \"$VSOCK_SOCK\"}"
configure_vm "/actions" "{\"action_type\": \"InstanceStart\"}"

TIMEOUT=${SNAP_WAIT_TIMEOUT_S}
echo "[6/8] Waiting for Mathlib REPL warmup (up to $((TIMEOUT / 60)) minutes)..."
START=$(date +%s)
LAST_HEARTBEAT=0
POLL_INTERVAL="$SERIAL_POLL_INTERVAL_S"
if [ "$POLL_INTERVAL" -lt 1 ] 2>/dev/null; then
  POLL_INTERVAL=1
fi
HEARTBEAT_INTERVAL="$SERIAL_HEARTBEAT_S"
if [ "$HEARTBEAT_INTERVAL" -lt 1 ] 2>/dev/null; then
  HEARTBEAT_INTERVAL=120
fi
while true; do
  ELAPSED=$(( $(date +%s) - START ))
  if [ $ELAPSED -gt $TIMEOUT ]; then
    echo "Timeout waiting for Mathlib" >&2
    write_failure_bundle "timeout waiting for Mathlib readiness marker"
    exit 1
  fi
  if grep -q "Mathlib warmup failed" "$SERIAL_LOG" 2>/dev/null; then
    echo "Mathlib warmup failed inside guest" >&2
    REASON_LINE=$(grep "Mathlib warmup failed" "$SERIAL_LOG" 2>/dev/null | tail -n1 || true)
    if [ -n "${REASON_LINE:-}" ]; then
      echo "Guest reason: ${REASON_LINE}" >&2
    fi
    write_failure_bundle "guest warmup failure marker"
    if [ "$REQUIRE_WARMUP" = "1" ]; then
      exit 1
    fi
    echo "Continuing with snapshot even though warmup failed (REQUIRE_WARMUP=0)." >&2
    break
  fi
  if grep -q "Mathlib ready for snapshot" "$SERIAL_LOG" 2>/dev/null; then
    echo "Mathlib loaded!"
    log_host "Guest reported readiness in ${ELAPSED}s"
    break
  fi
  if [ $((ELAPSED - LAST_HEARTBEAT)) -ge "$HEARTBEAT_INTERVAL" ]; then
    LAST_HEARTBEAT=$ELAPSED
    echo "  Elapsed: ${ELAPSED}s (guest serial tail below)"
    tail -n 8 "$SERIAL_LOG" 2>/dev/null | sed 's/^/    guest> /'
  else
    echo "  Elapsed: ${ELAPSED}s"
  fi
  sleep "$POLL_INTERVAL"
done

sleep 2

echo "[7/8] Pausing VM and creating snapshot..."
patch_vm "/vm" "{\"state\": \"Paused\"}"
sleep 1
configure_vm "/snapshot/create" "{\"snapshot_type\": \"Full\", \"snapshot_path\": \"$SNAP_FILE\", \"mem_file_path\": \"$MEM_FILE\"}"

sleep 1

echo "[8/8] Stopping Firecracker..."
kill "$FC_PID" 2>/dev/null || true
sleep 1
kill -9 "$FC_PID" 2>/dev/null || true

# Auto-fix ownership for convenience
TARGET_USER=${SUDO_USER:-$USER}
TARGET_GROUP=$(id -gn "$TARGET_USER" 2>/dev/null || echo "$TARGET_USER")
chown -R "$TARGET_USER:$TARGET_GROUP" "$SNAP_DIR" || true

echo
if [ -f "$SNAP_FILE" ] && [ -f "$MEM_FILE" ]; then
  log_host "Snapshot creation completed successfully"
  echo "=== Snapshot Created ==="
  echo "Snapshot: $SNAP_FILE"
  echo "Memory:   $MEM_FILE"
  echo "Data:     $DATA_DISK"
  echo "Vsock:    $VSOCK_SOCK"
  echo "Serial:   $SERIAL_LOG"
  echo "HostLog:  $HOST_DEBUG_LOG"
  echo "Owner:    $TARGET_USER:$TARGET_GROUP"
else
  write_failure_bundle "snapshot files missing at completion"
  echo "ERROR: Snapshot files not created" >&2
  exit 1
fi
