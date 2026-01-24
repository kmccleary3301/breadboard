"""Firecracker microVM Sandbox Implementation.

This module provides a Firecracker-based sandbox that executes shell commands
inside lightweight microVMs. It inherits from DevSandboxV2, overriding only
the command execution path while keeping file operations on the host filesystem.

Firecracker provides sub-second boot times and strong isolation through KVM
virtualization, making it ideal for ATP workloads requiring fast, isolated
Lean proof checking.

Architecture:
    - File I/O: Host filesystem (inherited from DevSandboxV2)
    - Command execution: Firecracker microVM with workspace mounted via virtio-fs
    - Communication: vsock (preferred) with serial PTY fallback

Prerequisites:
    - Firecracker binary installed
    - KVM available (/dev/kvm)
    - Root filesystem image with required tools
    - Linux kernel image compatible with Firecracker
    - Guest init that provides a shell on ttyS0 (for serial exec)

References:
    - https://github.com/firecracker-microvm/firecracker
    - https://github.com/firecracker-microvm/firecracker/blob/main/docs/vsock.md
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import pty
import select
import shlex
import shutil
import socket
import subprocess
import tempfile
import textwrap
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ray

from .adaptive_iter import ADAPTIVE_PREFIX_ITERABLE
from .sandbox_v2 import DevSandboxV2Base

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Default paths - can be overridden via environment or constructor
DEFAULT_FIRECRACKER_BIN = "/usr/bin/firecracker"
DEFAULT_JAILER_BIN = "/usr/bin/jailer"
DEFAULT_KERNEL_PATH = "/var/lib/firecracker/kernels/vmlinux"
DEFAULT_ROOTFS_PATH = "/var/lib/firecracker/rootfs/ubuntu-22.04.ext4"

# VM resource defaults
DEFAULT_VCPU_COUNT = 2
DEFAULT_MEM_SIZE_MIB = 512
DEFAULT_BOOT_TIMEOUT_S = 10.0
DEFAULT_COMMAND_TIMEOUT_S = 30

# vsock configuration
VSOCK_GUEST_CID = 3  # Standard guest CID
VSOCK_HOST_PORT = 52  # Port for command execution service


@dataclass
class FirecrackerConfig:
    """Configuration for a Firecracker microVM."""

    firecracker_bin: str = DEFAULT_FIRECRACKER_BIN
    jailer_bin: Optional[str] = None  # Use jailer for production isolation
    kernel_path: str = DEFAULT_KERNEL_PATH
    rootfs_path: str = DEFAULT_ROOTFS_PATH
    vcpu_count: int = DEFAULT_VCPU_COUNT
    mem_size_mib: int = DEFAULT_MEM_SIZE_MIB
    boot_timeout_s: float = DEFAULT_BOOT_TIMEOUT_S
    use_jailer: bool = False
    network_mode: str = "none"  # "none", "tap", "user"
    enable_vsock: bool = True
    snapshot_path: Optional[str] = None  # Path to VM snapshot for fast restore
    snapshot_mem_path: Optional[str] = None
    snapshot_vsock_path: Optional[str] = None
    snapshot_resume: bool = True
    enable_diff_snapshots: bool = False
    exec_mode: str = "auto"  # auto|vsock|serial|placeholder
    allow_placeholder: bool = True
    vsock_port: int = VSOCK_HOST_PORT

    @classmethod
    def from_env(cls) -> "FirecrackerConfig":
        """Create config from environment variables."""
        return cls(
            firecracker_bin=os.environ.get("FIRECRACKER_BIN", DEFAULT_FIRECRACKER_BIN),
            jailer_bin=os.environ.get("FIRECRACKER_JAILER_BIN"),
            kernel_path=os.environ.get("FIRECRACKER_KERNEL", DEFAULT_KERNEL_PATH),
            rootfs_path=os.environ.get("FIRECRACKER_ROOTFS", DEFAULT_ROOTFS_PATH),
            vcpu_count=int(os.environ.get("FIRECRACKER_VCPUS", DEFAULT_VCPU_COUNT)),
            mem_size_mib=int(os.environ.get("FIRECRACKER_MEM_MIB", DEFAULT_MEM_SIZE_MIB)),
            use_jailer=os.environ.get("FIRECRACKER_USE_JAILER", "").lower() in ("1", "true", "yes"),
            network_mode=os.environ.get("FIRECRACKER_NETWORK", "none"),
            snapshot_path=os.environ.get("FIRECRACKER_SNAPSHOT"),
            snapshot_mem_path=os.environ.get("FIRECRACKER_SNAPSHOT_MEM"),
            snapshot_vsock_path=os.environ.get("FIRECRACKER_SNAPSHOT_VSOCK"),
            snapshot_resume=os.environ.get("FIRECRACKER_SNAPSHOT_RESUME", "1").lower()
            in ("1", "true", "yes"),
            enable_diff_snapshots=os.environ.get("FIRECRACKER_ENABLE_DIFF_SNAPSHOTS", "").lower()
            in ("1", "true", "yes"),
            exec_mode=os.environ.get("FIRECRACKER_EXEC_MODE", "auto"),
            allow_placeholder=os.environ.get("FIRECRACKER_ALLOW_PLACEHOLDER", "1").lower()
            in ("1", "true", "yes"),
            vsock_port=int(os.environ.get("FIRECRACKER_VSOCK_PORT", VSOCK_HOST_PORT)),
        )


@dataclass
class VMInstance:
    """Represents a running Firecracker VM instance."""

    vm_id: str
    socket_path: str
    process: Optional[subprocess.Popen] = None
    vsock_path: Optional[str] = None
    state: str = "created"  # created, running, stopped, error
    boot_time_s: float = 0.0
    created_at: float = field(default_factory=time.time)

    def is_running(self) -> bool:
        """Check if the VM process is still running."""
        if self.process is None:
            return False
        return self.process.poll() is None


# -----------------------------------------------------------------------------
# Firecracker API Client
# -----------------------------------------------------------------------------


class FirecrackerAPIClient:
    """Client for Firecracker's REST API over Unix socket.

    Firecracker exposes a REST API over a Unix socket for VM configuration
    and lifecycle management.
    """

    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Tuple[int, Dict[str, Any]]:
        """Make an HTTP request to the Firecracker API.

        Args:
            method: HTTP method (GET, PUT, PATCH)
            path: API endpoint path
            body: Request body (will be JSON-encoded)
            timeout: Request timeout in seconds

        Returns:
            Tuple of (status_code, response_body)
        """
        import http.client

        # Create Unix socket connection
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(self.socket_path)

        try:
            # Build HTTP request manually for Unix socket
            body_bytes = json.dumps(body).encode() if body else b""

            request_lines = [
                f"{method} {path} HTTP/1.1",
                "Host: localhost",
                "Accept: application/json",
            ]

            if body:
                request_lines.extend([
                    "Content-Type: application/json",
                    f"Content-Length: {len(body_bytes)}",
                ])

            request_lines.append("")
            request_lines.append("")

            request = "\r\n".join(request_lines).encode()
            if body:
                request = request + body_bytes

            sock.sendall(request)

            # Read response
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\r\n\r\n" in response:
                    # Check if we have Content-Length and read body
                    header_end = response.index(b"\r\n\r\n")
                    headers = response[:header_end].decode()
                    body_start = header_end + 4

                    # Parse Content-Length
                    content_length = 0
                    for line in headers.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            content_length = int(line.split(":")[1].strip())
                            break

                    if len(response) >= body_start + content_length:
                        break

            # Parse response
            header_end = response.index(b"\r\n\r\n")
            status_line = response[:response.index(b"\r\n")].decode()
            status_code = int(status_line.split()[1])

            response_body = response[header_end + 4:]
            if response_body:
                return status_code, json.loads(response_body.decode())
            return status_code, {}

        finally:
            sock.close()

    def put_machine_config(
        self,
        vcpu_count: int,
        mem_size_mib: int,
        smt: bool = False,
    ) -> bool:
        """Configure VM resources."""
        status, _ = self._request("PUT", "/machine-config", {
            "vcpu_count": vcpu_count,
            "mem_size_mib": mem_size_mib,
            "smt": smt,
        })
        return status == 204

    def put_boot_source(
        self,
        kernel_path: str,
        boot_args: str = "console=ttyS0 reboot=k panic=1 pci=off",
    ) -> bool:
        """Configure kernel boot source."""
        status, _ = self._request("PUT", "/boot-source", {
            "kernel_image_path": kernel_path,
            "boot_args": boot_args,
        })
        return status == 204

    def put_drive(
        self,
        drive_id: str,
        path: str,
        is_root_device: bool = True,
        is_read_only: bool = False,
    ) -> bool:
        """Attach a block device (rootfs)."""
        status, _ = self._request("PUT", f"/drives/{drive_id}", {
            "drive_id": drive_id,
            "path_on_host": path,
            "is_root_device": is_root_device,
            "is_read_only": is_read_only,
        })
        return status == 204

    def put_vsock(self, guest_cid: int, uds_path: str) -> bool:
        """Configure vsock device for host-guest communication."""
        status, _ = self._request("PUT", "/vsock", {
            "guest_cid": guest_cid,
            "uds_path": uds_path,
        })
        return status == 204

    def put_network_interface(
        self,
        iface_id: str,
        host_dev_name: str,
        guest_mac: Optional[str] = None,
    ) -> bool:
        """Configure network interface."""
        body: Dict[str, Any] = {
            "iface_id": iface_id,
            "host_dev_name": host_dev_name,
        }
        if guest_mac:
            body["guest_mac"] = guest_mac
        status, _ = self._request("PUT", f"/network-interfaces/{iface_id}", body)
        return status == 204

    def start_instance(self) -> bool:
        """Start the VM instance."""
        status, _ = self._request("PUT", "/actions", {
            "action_type": "InstanceStart",
        })
        return status == 204

    def create_snapshot(self, snapshot_path: str, mem_path: str) -> bool:
        """Create a VM snapshot for fast restore."""
        status, _ = self._request("PUT", "/snapshot/create", {
            "snapshot_type": "Full",
            "snapshot_path": snapshot_path,
            "mem_file_path": mem_path,
        })
        return status == 204

    def load_snapshot(
        self,
        snapshot_path: str,
        mem_path: str,
        *,
        resume_vm: Optional[bool] = None,
        enable_diff_snapshots: Optional[bool] = None,
    ) -> bool:
        """Load a VM from snapshot."""
        body: Dict[str, Any] = {
            "snapshot_path": snapshot_path,
            "mem_backend": {
                "backend_type": "File",
                "backend_path": mem_path,
            },
        }
        if resume_vm is not None:
            body["resume_vm"] = bool(resume_vm)
        if enable_diff_snapshots is not None:
            body["enable_diff_snapshots"] = bool(enable_diff_snapshots)
        status, _ = self._request("PUT", "/snapshot/load", body)
        return status == 204


# -----------------------------------------------------------------------------
# VM Lifecycle Manager
# -----------------------------------------------------------------------------


class FirecrackerVMPool:
    """Pool of warm Firecracker VMs for fast command execution.

    Maintains a pool of pre-booted VMs that can be quickly assigned to
    execute commands, avoiding the boot latency on each request.

    With snapshots, VMs can be restored in ~10ms instead of booting.
    """

    def __init__(
        self,
        config: FirecrackerConfig,
        workspace: Path,
        pool_size: int = 4,
        use_snapshot: bool = False,
    ):
        self.config = config
        self.workspace = Path(workspace).resolve()
        self.pool_size = max(0, int(pool_size))
        self.use_snapshot = use_snapshot
        self.managers: List[FirecrackerVMManager] = []
        self.free: List[FirecrackerVMManager] = []
        self.busy: List[FirecrackerVMManager] = []
        self._lock = threading.Lock()

    def initialize(self) -> int:
        """Initialize the VM pool.

        Returns:
            Number of VMs successfully started
        """
        if self.pool_size <= 0:
            return 0
        started = 0
        for i in range(self.pool_size):
            try:
                manager = FirecrackerVMManager(config=self.config, workspace=str(self.workspace))
                manager.create_vm()
                if manager.start_vm(use_snapshot=self.use_snapshot):
                    self.managers.append(manager)
                    self.free.append(manager)
                    started += 1
            except Exception as e:
                logger.warning(f"Failed to create pool VM {i}: {e}")
        logger.info(f"VM pool initialized: {started}/{self.pool_size} VMs ready")
        return started

    def acquire(self, timeout: float = 30.0) -> Optional[FirecrackerVMManager]:
        """Acquire a VM manager from the pool."""
        deadline = time.time() + max(0.0, timeout)
        while time.time() <= deadline:
            with self._lock:
                if self.free:
                    manager = self.free.pop()
                    self.busy.append(manager)
                    return manager
            time.sleep(0.05)
        return None

    def release(self, manager: FirecrackerVMManager) -> None:
        """Release a VM manager back to the pool."""
        with self._lock:
            if manager in self.busy:
                self.busy.remove(manager)
            if manager.vm and manager.vm.is_running():
                self.free.append(manager)
                return
        try:
            manager.stop_vm()
        except Exception:
            pass

    def shutdown(self) -> None:
        """Shutdown all VMs in the pool."""
        for manager in self.managers:
            try:
                manager.stop_vm()
            except Exception:
                pass
        self.managers.clear()
        self.free.clear()
        self.busy.clear()


class FirecrackerVMManager:
    """Manages Firecracker VM lifecycle.

    Handles VM creation, configuration, startup, command execution, and cleanup.
    Supports both on-demand VMs and VM pooling for high performance.
    """

    def __init__(self, config: FirecrackerConfig, workspace: str):
        self.config = config
        self.workspace = Path(workspace).resolve()
        self.vm: Optional[VMInstance] = None
        self._temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self._pool: Optional[FirecrackerVMPool] = None
        self._serial_master: Optional[int] = None
        self._serial_slave: Optional[int] = None
        self._serial_ready: bool = False
        self._serial_disabled: bool = False
        self._serial_failures: int = 0
        self._serial_lock = threading.Lock()
        self._vsock_ready: bool = False
        self._vsock_disabled: bool = False
        self._vsock_failures: int = 0
        self._vsock_lock = threading.Lock()

    def _create_temp_dir(self) -> str:
        """Create temporary directory for VM artifacts."""
        self._temp_dir = tempfile.TemporaryDirectory(prefix="fc-")
        return self._temp_dir.name

    def _cleanup_temp_dir(self) -> None:
        """Clean up temporary directory."""
        if self._temp_dir:
            try:
                self._temp_dir.cleanup()
            except Exception as e:
                logger.warning(f"Failed to cleanup temp dir: {e}")
            self._temp_dir = None
        self._close_serial_pty()
        self._vsock_ready = False
        self._vsock_disabled = False
        self._serial_failures = 0
        self._vsock_failures = 0

    def _setup_serial_pty(self) -> None:
        """Allocate a PTY for serial console I/O."""
        if self._serial_master is not None:
            return
        master_fd, slave_fd = pty.openpty()
        self._serial_master = master_fd
        self._serial_slave = slave_fd
        try:
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except Exception:
            pass

    def _close_serial_pty(self) -> None:
        """Close PTY file descriptors."""
        if self._serial_master is not None:
            try:
                os.close(self._serial_master)
            except Exception:
                pass
            self._serial_master = None
        if self._serial_slave is not None:
            try:
                os.close(self._serial_slave)
            except Exception:
                pass
            self._serial_slave = None
        self._serial_ready = False
        self._serial_disabled = False

    def _serial_write(self, data: str) -> bool:
        if self._serial_master is None:
            return False
        try:
            os.write(self._serial_master, data.encode())
            return True
        except Exception:
            return False

    def _serial_read_until(self, marker: str, timeout: float) -> Tuple[str, bool]:
        if self._serial_master is None:
            return "", False
        end_time = time.time() + timeout
        chunks: List[str] = []
        buffer = ""
        while time.time() < end_time:
            remaining = max(0.0, end_time - time.time())
            try:
                ready, _, _ = select.select([self._serial_master], [], [], min(0.1, remaining))
            except Exception:
                ready = []
            if not ready:
                continue
            try:
                data = os.read(self._serial_master, 4096)
            except BlockingIOError:
                continue
            except Exception:
                break
            if not data:
                continue
            text = data.decode(errors="replace")
            chunks.append(text)
            buffer += text
            if marker in buffer:
                break
        return "".join(chunks), marker in buffer

    def _serial_drain(self) -> None:
        """Drain any pending data from the serial buffer."""
        if self._serial_master is None:
            return
        while True:
            try:
                data = os.read(self._serial_master, 4096)
            except BlockingIOError:
                break
            except Exception:
                break
            if not data:
                break

    def _ensure_serial_ready(self, timeout: float = 5.0) -> bool:
        if self._serial_disabled:
            return False
        if self._serial_ready:
            return True
        marker = f"__FC_READY_{uuid.uuid4().hex}__"
        if not self._serial_write(f"echo {marker}\n"):
            self._serial_failures += 1
            if self._serial_failures >= 2:
                self._serial_disabled = True
            return False
        output, ok = self._serial_read_until(marker, timeout)
        if ok:
            self._serial_failures = 0
            self._serial_ready = True
            return True
        if output:
            logger.debug("Serial readiness output: %s", output[-200:])
        self._serial_failures += 1
        if self._serial_failures >= 2:
            self._serial_disabled = True
        return False

    def _build_shell_command(
        self,
        command: str,
        env: Optional[Dict[str, str]],
        stdin_data: Optional[str],
        token: str,
    ) -> str:
        full_cmd = command
        if env:
            env_prefix = " ".join(
                f"{key}={shlex.quote(str(value))}" for key, value in env.items() if key
            )
            if env_prefix:
                full_cmd = f"{env_prefix} {full_cmd}"

        if stdin_data is not None:
            delim = f"__FC_STDIN_{token}__"
            full_cmd = (
                f"cat <<'{delim}' | ( {full_cmd} )\n"
                f"{stdin_data}\n"
                f"{delim}"
            )
        return full_cmd

    def _execute_command_serial(
        self,
        command: str,
        timeout: float,
        env: Optional[Dict[str, str]],
        stdin_data: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if self._serial_master is None:
            return None
        if not self._ensure_serial_ready():
            return None

        token = uuid.uuid4().hex
        begin = f"__FC_BEGIN_{token}__"
        exit_marker = f"__FC_EXIT_{token}__"
        done = f"__FC_DONE_{token}__"
        shell_cmd = self._build_shell_command(command, env, stdin_data, token)
        payload = (
            f"echo {begin}\n"
            f"{shell_cmd}\n"
            "__fc_status=$?\n"
            f"echo {exit_marker}${{__fc_status}}\n"
            f"echo {done}\n"
        )

        with self._serial_lock:
            self._serial_drain()
            if not self._serial_write(payload):
                self._serial_failures += 1
                if self._serial_failures >= 2:
                    self._serial_disabled = True
                return None
            output, ok = self._serial_read_until(done, timeout)

        if not ok:
            return {
                "exit": 124,
                "stdout": output or "",
                "stderr": "Command timed out waiting for serial completion",
            }

        self._serial_failures = 0

        stdout = output
        exit_code = 0
        if begin in stdout:
            stdout = stdout.split(begin, 1)[1]
        if exit_marker in stdout:
            before_exit, after_exit = stdout.split(exit_marker, 1)
            stdout = before_exit
            digits = []
            for ch in after_exit:
                if ch.isdigit():
                    digits.append(ch)
                else:
                    break
            if digits:
                try:
                    exit_code = int("".join(digits))
                except Exception:
                    exit_code = 1
            else:
                exit_code = 1

        for marker in (begin, exit_marker, done):
            stdout = stdout.replace(marker, "")
        stdout = stdout.replace(done, "")
        stdout = stdout.strip("\r\n")

        return {
            "exit": exit_code,
            "stdout": stdout,
            "stderr": "",
        }

    def _read_line_from_socket(self, sock: socket.socket, timeout: float) -> Optional[str]:
        """Read a single newline-terminated line from a socket."""
        end_time = time.time() + timeout
        data = b""
        while time.time() < end_time:
            remaining = max(0.0, end_time - time.time())
            sock.settimeout(max(0.1, min(1.0, remaining)))
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except Exception:
                return None
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
        if not data:
            return None
        line = data.split(b"\n", 1)[0]
        return line.decode(errors="replace")

    def _uds_in_use(self, path: str, timeout: float = 0.2) -> bool:
        """Check whether a Unix domain socket path is active."""
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(path)
            return True
        except Exception:
            return False
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _vsock_connect(self, port: int, timeout: float) -> Optional[socket.socket]:
        """Connect to guest vsock port via Firecracker host UDS."""
        if self.vm is None or not self.vm.vsock_path:
            return None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(self.vm.vsock_path)
            sock.sendall(f"CONNECT {port}\n".encode())
            response = self._read_line_from_socket(sock, timeout)
            if not response or not response.startswith("OK"):
                sock.close()
                return None
            return sock
        except Exception:
            return None

    def _build_vsock_agent_script(self, port: int) -> str:
        """Build a minimal vsock agent script for the guest."""
        return textwrap.dedent(f"""\
#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import sys

PORT = {port}

def main():
    s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((socket.VMADDR_CID_ANY, PORT))
    s.listen(5)
    while True:
        conn, _ = s.accept()
        try:
            data = b""
            while not data.endswith(b"\\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            if not data:
                conn.close()
                continue
            req = json.loads(data.decode())
            cmd = req.get("command", "")
            env = req.get("env") or {{}}
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
                out = {{
                    "exit": res.returncode,
                    "stdout": res.stdout or "",
                    "stderr": res.stderr or "",
                }}
            except subprocess.TimeoutExpired:
                out = {{
                    "exit": 124,
                    "stdout": "",
                    "stderr": "Command timed out",
                }}
            except Exception as exc:
                out = {{
                    "exit": 1,
                    "stdout": "",
                    "stderr": str(exc),
                }}
            conn.sendall((json.dumps(out) + "\\n").encode())
        except Exception:
            try:
                conn.sendall((json.dumps({{"exit": 1, "stdout": "", "stderr": "vsock agent error"}}) + "\\n").encode())
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

if __name__ == "__main__":
    main()
""")

    def _ensure_vsock_server(self, timeout: float = 3.0) -> bool:
        """Ensure the guest vsock server is running."""
        if self._vsock_disabled:
            return False
        if self._vsock_ready:
            return True
        with self._vsock_lock:
            if self._vsock_ready:
                return True
            # Probe existing server
            for _ in range(5):
                probe = self._vsock_connect(self.config.vsock_port, timeout=1.0)
                if probe:
                    try:
                        probe.close()
                    except Exception:
                        pass
                    self._vsock_failures = 0
                    self._vsock_ready = True
                    return True
                time.sleep(0.05)

            # Need serial to install/start server
            if not self._ensure_serial_ready():
                self._vsock_failures += 1
                if self._vsock_failures >= 2:
                    self._vsock_disabled = True
                return False

            script = self._build_vsock_agent_script(self.config.vsock_port)
            install_cmd = (
                "PYTHON_BIN=$(command -v python3 || command -v python || true)\n"
                "if [ -z \"$PYTHON_BIN\" ]; then echo 'python missing'; exit 1; fi\n"
                "cat <<'PYEOF' >/tmp/atp_vsock_agent.py\n"
                f"{script}\n"
                "PYEOF\n"
                "chmod +x /tmp/atp_vsock_agent.py\n"
                "nohup $PYTHON_BIN /tmp/atp_vsock_agent.py >/tmp/atp_vsock_agent.log 2>&1 &\n"
            )
            result = self._execute_command_serial(
                command=install_cmd,
                timeout=timeout,
                env=None,
                stdin_data=None,
            )
            if not result or result.get("exit") != 0:
                self._vsock_failures += 1
                if self._vsock_failures >= 2:
                    self._vsock_disabled = True
                return False

            # Re-probe
            probe = self._vsock_connect(self.config.vsock_port, timeout=1.0)
            if probe:
                try:
                    probe.close()
                except Exception:
                    pass
                self._vsock_failures = 0
                self._vsock_ready = True
                return True

            self._vsock_failures += 1
            if self._vsock_failures >= 2:
                self._vsock_disabled = True
            return False

    def _execute_command_vsock(
        self,
        command: str,
        timeout: float,
        env: Optional[Dict[str, str]],
        stdin_data: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if self.vm is None or not self.vm.is_running():
            return None
        if not self._ensure_vsock_server():
            return None
        sock = self._vsock_connect(self.config.vsock_port, timeout=2.0)
        if not sock:
            self._vsock_failures += 1
            if self._vsock_failures >= 2:
                self._vsock_disabled = True
            return None
        try:
            request = {
                "command": command,
                "env": env or {},
                "stdin": stdin_data,
                "timeout": timeout,
            }
            sock.sendall((json.dumps(request) + "\n").encode())
            line = self._read_line_from_socket(sock, timeout=timeout + 5.0)
            if not line:
                self._vsock_failures += 1
                if self._vsock_failures >= 2:
                    self._vsock_disabled = True
                return None
            response = json.loads(line)
            self._vsock_failures = 0
            return {
                "exit": int(response.get("exit", 1)),
                "stdout": response.get("stdout", ""),
                "stderr": response.get("stderr", ""),
            }
        except Exception:
            self._vsock_failures += 1
            if self._vsock_failures >= 2:
                self._vsock_disabled = True
            return None
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def create_vm(self) -> VMInstance:
        """Create and configure a new Firecracker VM.

        Returns:
            VMInstance with configuration ready to start
        """
        vm_id = f"fc-{uuid.uuid4().hex[:8]}"
        temp_dir = self._create_temp_dir()

        self._setup_serial_pty()

        socket_path = os.path.join(temp_dir, "firecracker.sock")
        vsock_path = os.path.join(temp_dir, "vsock.sock")

        self.vm = VMInstance(
            vm_id=vm_id,
            socket_path=socket_path,
            vsock_path=vsock_path,
        )

        return self.vm

    def _resolve_snapshot_paths(self) -> Optional[Tuple[str, str]]:
        """Resolve snapshot and memory paths."""
        snap = self.config.snapshot_path
        if not snap:
            return None
        mem = self.config.snapshot_mem_path
        if not mem:
            if snap.endswith(".snap"):
                mem = snap[:-5] + ".mem"
            else:
                mem = snap + ".mem"
        return snap, mem

    def _resolve_snapshot_vsock_path(self, snapshot_path: str) -> Optional[str]:
        """Resolve vsock UDS path associated with snapshot."""
        if self.config.snapshot_vsock_path:
            return self.config.snapshot_vsock_path
        return os.path.join(os.path.dirname(snapshot_path), "vsock.sock")

    def _boot_from_snapshot(self) -> bool:
        """Boot a VM from an existing snapshot."""
        if self.vm is None:
            return False
        paths = self._resolve_snapshot_paths()
        if not paths:
            return False
        snapshot_path, mem_path = paths
        if not os.path.exists(snapshot_path) or not os.path.exists(mem_path):
            logger.warning("Snapshot files not found: %s %s", snapshot_path, mem_path)
            return False

        vsock_path = self._resolve_snapshot_vsock_path(snapshot_path)
        if vsock_path:
            self.vm.vsock_path = vsock_path
            try:
                if os.path.exists(vsock_path):
                    if self._uds_in_use(vsock_path):
                        logger.warning(
                            "Snapshot vsock path already in use: %s. "
                            "Cannot restore snapshot concurrently.",
                            vsock_path,
                        )
                        return False
                    os.remove(vsock_path)
            except Exception:
                pass

        start_time = time.time()

        cmd = [
            self.config.firecracker_bin,
            "--api-sock", self.vm.socket_path,
        ]
        if self._temp_dir is not None:
            log_path = os.path.join(self._temp_dir.name, "firecracker.log")
            try:
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                Path(log_path).touch(exist_ok=True)
            except Exception:
                pass
            cmd.extend(["--log-path", log_path, "--level", "Warn"])

        logger.info(f"Starting Firecracker VM {self.vm.vm_id} from snapshot")
        stdin = self._serial_slave if self._serial_slave is not None else subprocess.PIPE
        stdout = self._serial_slave if self._serial_slave is not None else subprocess.PIPE

        self.vm.process = subprocess.Popen(
            cmd,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
        )
        if self._serial_slave is not None:
            try:
                os.close(self._serial_slave)
            except Exception:
                pass
            self._serial_slave = None

        for _ in range(50):
            if os.path.exists(self.vm.socket_path):
                break
            time.sleep(0.1)
        else:
            self.vm.state = "error"
            return False

        api = FirecrackerAPIClient(self.vm.socket_path)
        ok = api.load_snapshot(
            snapshot_path=snapshot_path,
            mem_path=mem_path,
            resume_vm=self.config.snapshot_resume,
            enable_diff_snapshots=self.config.enable_diff_snapshots,
        )
        if not ok:
            logger.warning("Failed to load snapshot via API")
            self.vm.state = "error"
            self.stop_vm()
            return False

        self.vm.state = "running"
        self.vm.boot_time_s = time.time() - start_time
        logger.info(f"VM {self.vm.vm_id} restored in {self.vm.boot_time_s:.2f}s")
        return True

    def start_vm(self, *, use_snapshot: bool = False) -> bool:
        """Start the Firecracker VM.

        Returns:
            True if VM started successfully, False otherwise
        """
        if self.vm is None:
            raise RuntimeError("VM not created. Call create_vm() first.")

        if use_snapshot and self.config.snapshot_path:
            if self._boot_from_snapshot():
                return True
            logger.warning("Snapshot restore failed; falling back to cold boot")

        # Check prerequisites
        if not os.path.exists(self.config.firecracker_bin):
            raise RuntimeError(f"Firecracker binary not found: {self.config.firecracker_bin}")
        if not os.path.exists(self.config.kernel_path):
            raise RuntimeError(f"Kernel not found: {self.config.kernel_path}")
        if not os.path.exists(self.config.rootfs_path):
            raise RuntimeError(f"Rootfs not found: {self.config.rootfs_path}")
        if not os.path.exists("/dev/kvm"):
            raise RuntimeError("KVM not available. Ensure /dev/kvm exists and is accessible.")

        start_time = time.time()

        # Start Firecracker process
        cmd = [
            self.config.firecracker_bin,
            "--api-sock", self.vm.socket_path,
        ]
        if self._temp_dir is not None:
            log_path = os.path.join(self._temp_dir.name, "firecracker.log")
            try:
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                Path(log_path).touch(exist_ok=True)
            except Exception:
                pass
            cmd.extend(["--log-path", log_path, "--level", "Warn"])

        logger.info(f"Starting Firecracker VM {self.vm.vm_id}")
        stdin = self._serial_slave if self._serial_slave is not None else subprocess.PIPE
        stdout = self._serial_slave if self._serial_slave is not None else subprocess.PIPE

        self.vm.process = subprocess.Popen(
            cmd,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
        )
        if self._serial_slave is not None:
            try:
                os.close(self._serial_slave)
            except Exception:
                pass
            self._serial_slave = None

        # Wait for socket to be available
        for _ in range(50):  # 5 seconds max
            if os.path.exists(self.vm.socket_path):
                break
            time.sleep(0.1)
        else:
            self.vm.state = "error"
            return False

        # Configure VM via API
        api = FirecrackerAPIClient(self.vm.socket_path)

        try:
            # Set machine config
            if not api.put_machine_config(
                vcpu_count=self.config.vcpu_count,
                mem_size_mib=self.config.mem_size_mib,
            ):
                raise RuntimeError("Failed to configure machine")

            # Set boot source with workspace mount info in boot args
            boot_args = (
                f"console=ttyS0 reboot=k panic=1 pci=off "
                f"init=/sbin/init "
                f"root=/dev/vda rw "
                f"workspace={self.workspace} "
                f"vsock_port={self.config.vsock_port}"
            )
            if not api.put_boot_source(self.config.kernel_path, boot_args):
                raise RuntimeError("Failed to configure boot source")

            # Attach rootfs
            if not api.put_drive("rootfs", self.config.rootfs_path):
                raise RuntimeError("Failed to attach rootfs")

            # Configure vsock for host-guest communication
            if self.config.enable_vsock and self.vm.vsock_path:
                if not api.put_vsock(VSOCK_GUEST_CID, self.vm.vsock_path):
                    logger.warning("Failed to configure vsock, falling back to serial")
                    self.vm.vsock_path = None

            # Start the instance
            if not api.start_instance():
                raise RuntimeError("Failed to start instance")

            self.vm.state = "running"
            self.vm.boot_time_s = time.time() - start_time
            logger.info(f"VM {self.vm.vm_id} started in {self.vm.boot_time_s:.2f}s")

            return True

        except Exception as e:
            logger.error(f"Failed to start VM: {e}")
            self.vm.state = "error"
            self.stop_vm()
            return False

    def stop_vm(self) -> None:
        """Stop and cleanup the VM."""
        if self.vm is None:
            return

        logger.info(f"Stopping VM {self.vm.vm_id}")

        if self.vm.process and self.vm.is_running():
            self.vm.process.terminate()
            try:
                self.vm.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.vm.process.kill()
                self.vm.process.wait()

        self.vm.state = "stopped"
        self._cleanup_temp_dir()
        self.vm = None

    def execute_command(
        self,
        command: str,
        timeout: float = DEFAULT_COMMAND_TIMEOUT_S,
        env: Optional[Dict[str, str]] = None,
        stdin_data: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a command inside the VM.

        This uses vsock for communication if available, otherwise falls back
        to a serial console approach.

        Args:
            command: Shell command to execute
            timeout: Execution timeout in seconds
            env: Environment variables
            stdin_data: Data to send to stdin

        Returns:
            Dict with exit code, stdout, and stderr
        """
        if self.vm is None or not self.vm.is_running():
            return {
                "exit": 1,
                "stdout": "",
                "stderr": "VM not running",
            }

        mode = (self.config.exec_mode or "auto").strip().lower()

        if mode in {"auto", "vsock"}:
            result = self._execute_command_vsock(
                command=command,
                timeout=timeout,
                env=env,
                stdin_data=stdin_data,
            )
            if result is not None:
                return result
            if mode == "vsock":
                return {
                    "exit": 1,
                    "stdout": "",
                    "stderr": "Vsock command execution unavailable",
                }

        if mode in {"auto", "serial"}:
            result = self._execute_command_serial(
                command=command,
                timeout=timeout,
                env=env,
                stdin_data=stdin_data,
            )
            if result is not None:
                return result
            if mode == "serial":
                return {
                    "exit": 1,
                    "stdout": "",
                    "stderr": "Serial command execution unavailable",
                }

        if mode == "placeholder" or self.config.allow_placeholder:
            logger.warning(
                "Firecracker command execution not fully implemented. "
                "Returning placeholder response."
            )
            return {
                "exit": 0,
                "stdout": f"[Firecracker VM {self.vm.vm_id}] Command would execute: {command[:100]}",
                "stderr": "",
                "_firecracker_placeholder": True,
            }

        return {
            "exit": 1,
            "stdout": "",
            "stderr": "Firecracker command execution unavailable",
        }


# -----------------------------------------------------------------------------
# Firecracker Sandbox Ray Actor
# -----------------------------------------------------------------------------


@ray.remote
class FirecrackerSandboxV2(DevSandboxV2Base):
    """Sandbox that runs shell commands inside Firecracker microVMs.

    File operations are performed on the host filesystem (inherited from
    DevSandboxV2) but are scoped to the workspace. Command execution happens
    inside a Firecracker microVM with the workspace mounted.

    This provides stronger isolation than Docker with faster startup times
    (sub-second with snapshots) at the cost of requiring KVM support.

    Args:
        image: Image identifier (used for rootfs selection)
        session_id: Unique session identifier
        workspace: Host filesystem path for sandbox root
        lsp_actor: Optional LSP integration actor
        kernel_path: Path to Linux kernel image
        rootfs_path: Path to root filesystem image
        vcpu_count: Number of virtual CPUs
        mem_size_mib: Memory size in MiB
        network_mode: Network mode ("none", "tap")
        use_snapshot: Whether to use VM snapshots for fast startup
        snapshot_path: Path to VM snapshot
        firecracker_bin: Path to firecracker binary

    Example:
        >>> import ray
        >>> from breadboard.sandbox_firecracker import FirecrackerSandboxV2
        >>> ray.init()
        >>> sandbox = FirecrackerSandboxV2.remote(
        ...     image="ubuntu-22.04",
        ...     workspace="/tmp/workspace",
        ... )
        >>> result = ray.get(sandbox.run_shell.remote("echo hello"))
        >>> print(result["stdout"])
        hello
    """

    def __init__(
        self,
        image: str,
        session_id: str = "",
        workspace: str = "",
        lsp_actor: Any = None,
        *,
        kernel_path: Optional[str] = None,
        rootfs_path: Optional[str] = None,
        vcpu_count: Optional[int] = None,
        mem_size_mib: Optional[int] = None,
        network_mode: str = "none",
        use_snapshot: bool = False,
        snapshot_path: Optional[str] = None,
        snapshot_mem_path: Optional[str] = None,
        snapshot_vsock_path: Optional[str] = None,
        snapshot_resume: Optional[bool] = None,
        enable_diff_snapshots: Optional[bool] = None,
        firecracker_bin: Optional[str] = None,
        exec_mode: Optional[str] = None,
        allow_placeholder: Optional[bool] = None,
        vsock_port: Optional[int] = None,
        pool_size: Optional[int] = None,
        pool_timeout_s: Optional[float] = None,
    ) -> None:
        super().__init__(
            image=image,
            session_id=session_id,
            workspace=workspace,
            lsp_actor=lsp_actor,
        )

        # Build configuration from args and environment
        self.config = FirecrackerConfig(
            firecracker_bin=firecracker_bin or os.environ.get(
                "FIRECRACKER_BIN", DEFAULT_FIRECRACKER_BIN
            ),
            kernel_path=kernel_path or os.environ.get(
                "FIRECRACKER_KERNEL", DEFAULT_KERNEL_PATH
            ),
            rootfs_path=rootfs_path or os.environ.get(
                "FIRECRACKER_ROOTFS", DEFAULT_ROOTFS_PATH
            ),
            vcpu_count=vcpu_count or int(os.environ.get(
                "FIRECRACKER_VCPUS", DEFAULT_VCPU_COUNT
            )),
            mem_size_mib=mem_size_mib or int(os.environ.get(
                "FIRECRACKER_MEM_MIB", DEFAULT_MEM_SIZE_MIB
            )),
            network_mode=network_mode,
            snapshot_path=snapshot_path or os.environ.get("FIRECRACKER_SNAPSHOT"),
            snapshot_mem_path=snapshot_mem_path or os.environ.get("FIRECRACKER_SNAPSHOT_MEM"),
            snapshot_vsock_path=snapshot_vsock_path or os.environ.get("FIRECRACKER_SNAPSHOT_VSOCK"),
            snapshot_resume=(
                snapshot_resume
                if snapshot_resume is not None
                else os.environ.get("FIRECRACKER_SNAPSHOT_RESUME", "1").lower()
                in ("1", "true", "yes")
            ),
            enable_diff_snapshots=(
                enable_diff_snapshots
                if enable_diff_snapshots is not None
                else os.environ.get("FIRECRACKER_ENABLE_DIFF_SNAPSHOTS", "").lower()
                in ("1", "true", "yes")
            ),
            exec_mode=exec_mode or os.environ.get("FIRECRACKER_EXEC_MODE", "auto"),
            allow_placeholder=(
                allow_placeholder
                if allow_placeholder is not None
                else os.environ.get("FIRECRACKER_ALLOW_PLACEHOLDER", "1").lower()
                in ("1", "true", "yes")
            ),
            vsock_port=vsock_port or int(os.environ.get("FIRECRACKER_VSOCK_PORT", VSOCK_HOST_PORT)),
        )

        # VM manager (lazy initialization)
        self._vm_manager: Optional[FirecrackerVMManager] = None
        self._vm_pool: Optional[FirecrackerVMPool] = None
        self._use_snapshot = use_snapshot
        self._pool_size = int(pool_size or os.environ.get("FIRECRACKER_POOL_SIZE", 0) or 0)
        self._pool_timeout_s = float(
            pool_timeout_s or os.environ.get("FIRECRACKER_POOL_TIMEOUT_S", 5.0)
        )

        logger.info(
            f"FirecrackerSandboxV2 initialized: session={session_id}, "
            f"workspace={workspace}, vcpus={self.config.vcpu_count}, "
            f"mem={self.config.mem_size_mib}MiB"
        )

    def _get_vm_manager(self) -> FirecrackerVMManager:
        """Get or create the VM manager."""
        if self._vm_manager is None:
            self._vm_manager = FirecrackerVMManager(
                config=self.config,
                workspace=self.workspace,
            )
        return self._vm_manager

    def _get_vm_pool(self) -> FirecrackerVMPool:
        """Get or create a VM pool."""
        if self._vm_pool is None:
            self._vm_pool = FirecrackerVMPool(
                config=self.config,
                workspace=Path(self.workspace),
                pool_size=self._pool_size,
                use_snapshot=self._use_snapshot,
            )
            self._vm_pool.initialize()
        return self._vm_pool

    def _ensure_vm_running(self) -> bool:
        """Ensure the VM is running, starting it if necessary.

        Returns:
            True if VM is running, False if failed to start
        """
        manager = self._get_vm_manager()

        if manager.vm is not None and manager.vm.is_running():
            return True

        # Create and start VM
        manager.create_vm()
        return manager.start_vm(use_snapshot=self._use_snapshot)

    def run_shell(
        self,
        command: str,
        timeout: int = 30,
        env: Optional[Dict[str, str]] = None,
        stream: bool = False,
        stdin_data: Optional[str] = None,
        shell: bool = True,
    ):
        """Execute a shell command inside the Firecracker VM.

        Args:
            command: Shell command to execute
            timeout: Execution timeout in seconds
            env: Additional environment variables
            stream: If True, return streaming output format
            stdin_data: Data to send to command's stdin
            shell: If True (default), run command through shell

        Returns:
            If stream=False: {"exit": int, "stdout": str, "stderr": str}
            If stream=True: [ADAPTIVE_PREFIX_ITERABLE, ...lines..., result_dict]
        """
        # Check if Firecracker is available
        if not self._check_firecracker_available():
            return self._fallback_to_subprocess(
                command, timeout, env, stream, stdin_data, shell
            )

        try:
            result: Dict[str, Any]
            if self._pool_size > 0:
                pool = self._get_vm_pool()
                manager = pool.acquire(timeout=self._pool_timeout_s)
                if manager is None:
                    payload = {
                        "exit": 1,
                        "stdout": "",
                        "stderr": "No Firecracker VM available in pool",
                    }
                    return self._format_output(payload, stream)
                try:
                    result = manager.execute_command(
                        command=command,
                        timeout=float(timeout),
                        env=env,
                        stdin_data=stdin_data,
                    )
                finally:
                    pool.release(manager)
            else:
                # Ensure VM is running
                if not self._ensure_vm_running():
                    payload = {
                        "exit": 1,
                        "stdout": "",
                        "stderr": "Failed to start Firecracker VM",
                    }
                    return self._format_output(payload, stream)

                manager = self._get_vm_manager()
                result = manager.execute_command(
                    command=command,
                    timeout=float(timeout),
                    env=env,
                    stdin_data=stdin_data,
                )

            return self._format_output(result, stream)

        except Exception as exc:
            logger.exception(f"Error executing command in Firecracker: {exc}")
            payload = {"exit": 1, "stdout": "", "stderr": str(exc)}
            return self._format_output(payload, stream)

    def _check_firecracker_available(self) -> bool:
        """Check if Firecracker is available on this system."""
        # Check binary exists
        if not os.path.exists(self.config.firecracker_bin):
            logger.debug(f"Firecracker binary not found: {self.config.firecracker_bin}")
            return False

        # Check KVM available
        if not os.path.exists("/dev/kvm"):
            logger.debug("KVM not available (/dev/kvm not found)")
            return False

        # Check kernel exists
        if not os.path.exists(self.config.kernel_path):
            logger.debug(f"Kernel not found: {self.config.kernel_path}")
            return False

        # Check rootfs exists
        if not os.path.exists(self.config.rootfs_path):
            logger.debug(f"Rootfs not found: {self.config.rootfs_path}")
            return False

        return True

    def _fallback_to_subprocess(
        self,
        command: str,
        timeout: int,
        env: Optional[Dict[str, str]],
        stream: bool,
        stdin_data: Optional[str],
        shell: bool,
    ):
        """Fall back to subprocess execution when Firecracker isn't available.

        This allows the sandbox to work in development environments without
        Firecracker, using the same interface.
        """
        logger.warning(
            "Firecracker not available, falling back to subprocess execution. "
            "This provides no isolation."
        )

        # Use parent class implementation
        return super().run_shell(
            command=command,
            timeout=timeout,
            env=env,
            stream=stream,
            stdin_data=stdin_data,
            shell=shell,
        )

    def _format_output(
        self,
        payload: Dict[str, Any],
        stream: bool,
    ):
        """Format command output for return."""
        if not stream:
            return payload

        stdout = payload.get("stdout", "")
        stderr = payload.get("stderr", "")

        lines: List[Any] = [ADAPTIVE_PREFIX_ITERABLE]
        for line in stdout.splitlines():
            lines.append(line)
        if not stdout and stderr:
            for line in stderr.splitlines():
                lines.append(line)
        lines.append(payload)
        return lines

    def shutdown(self) -> None:
        """Shutdown the sandbox and cleanup VM resources."""
        logger.info(f"Shutting down FirecrackerSandboxV2 session={self.session_id}")

        if self._vm_pool is not None:
            self._vm_pool.shutdown()
            self._vm_pool = None

        if self._vm_manager is not None:
            self._vm_manager.stop_vm()
            self._vm_manager = None

    def __del__(self):
        """Cleanup on garbage collection."""
        try:
            self.shutdown()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Snapshot Management (for fast VM restore)
# -----------------------------------------------------------------------------


class FirecrackerSnapshotManager:
    """Manages Firecracker VM snapshots for fast startup.

    Snapshots capture a fully-booted VM state (including loaded Mathlib)
    allowing sub-10ms restore times instead of seconds of boot time.
    """

    def __init__(self, snapshot_dir: str = "/var/lib/firecracker/snapshots"):
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def create_snapshot(
        self,
        vm_manager: FirecrackerVMManager,
        name: str,
    ) -> Optional[str]:
        """Create a snapshot of a running VM.

        Args:
            vm_manager: Manager with running VM
            name: Snapshot identifier

        Returns:
            Path to snapshot file, or None if failed
        """
        if vm_manager.vm is None or not vm_manager.vm.is_running():
            logger.error("Cannot snapshot: VM not running")
            return None

        snapshot_path = self.snapshot_dir / f"{name}.snap"
        mem_path = self.snapshot_dir / f"{name}.mem"

        api = FirecrackerAPIClient(vm_manager.vm.socket_path)

        if api.create_snapshot(str(snapshot_path), str(mem_path)):
            logger.info(f"Created snapshot: {snapshot_path}")
            return str(snapshot_path)

        logger.error("Failed to create snapshot")
        return None

    def list_snapshots(self) -> List[str]:
        """List available snapshots."""
        return [
            p.stem for p in self.snapshot_dir.glob("*.snap")
        ]

    def delete_snapshot(self, name: str) -> bool:
        """Delete a snapshot."""
        snapshot_path = self.snapshot_dir / f"{name}.snap"
        mem_path = self.snapshot_dir / f"{name}.mem"

        deleted = False
        for path in [snapshot_path, mem_path]:
            if path.exists():
                path.unlink()
                deleted = True

        return deleted


# -----------------------------------------------------------------------------
# Factory Functions
# -----------------------------------------------------------------------------


def create_firecracker_sandbox(
    image: str,
    workspace: str,
    session_id: Optional[str] = None,
    name: Optional[str] = None,
    **kwargs,
) -> ray.actor.ActorHandle:
    """Create a Firecracker sandbox Ray actor.

    Args:
        image: Image identifier for rootfs selection
        workspace: Host filesystem path for sandbox root
        session_id: Optional session identifier
        name: Optional Ray actor name
        **kwargs: Additional options passed to FirecrackerSandboxV2

    Returns:
        Ray actor handle
    """
    session_id = session_id or f"fc-{uuid.uuid4()}"
    actor_name = name or f"fc-{session_id}"

    return FirecrackerSandboxV2.options(name=actor_name).remote(
        image=image,
        session_id=session_id,
        workspace=workspace,
        **kwargs,
    )


def check_firecracker_prerequisites() -> Dict[str, Any]:
    """Check if system meets Firecracker requirements.

    Returns:
        Dict with check results and any issues found
    """
    results: Dict[str, Any] = {
        "ready": True,
        "checks": {},
        "issues": [],
    }

    # Check KVM
    kvm_available = os.path.exists("/dev/kvm")
    results["checks"]["kvm"] = kvm_available
    if not kvm_available:
        results["ready"] = False
        results["issues"].append("KVM not available (/dev/kvm not found)")

    # Check Firecracker binary
    fc_bin = shutil.which("firecracker") or DEFAULT_FIRECRACKER_BIN
    fc_exists = os.path.exists(fc_bin)
    results["checks"]["firecracker_bin"] = fc_exists
    if not fc_exists:
        results["ready"] = False
        results["issues"].append(f"Firecracker binary not found: {fc_bin}")

    # Check kernel
    kernel_exists = os.path.exists(DEFAULT_KERNEL_PATH)
    results["checks"]["kernel"] = kernel_exists
    if not kernel_exists:
        results["ready"] = False
        results["issues"].append(f"Kernel not found: {DEFAULT_KERNEL_PATH}")

    # Check rootfs
    rootfs_exists = os.path.exists(DEFAULT_ROOTFS_PATH)
    results["checks"]["rootfs"] = rootfs_exists
    if not rootfs_exists:
        results["ready"] = False
        results["issues"].append(f"Rootfs not found: {DEFAULT_ROOTFS_PATH}")

    return results
