from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import ray

from .adaptive_iter import ADAPTIVE_PREFIX_ITERABLE
from .sandbox_v2 import DevSandboxV2Base


@ray.remote
class DockerSandboxV2(DevSandboxV2Base):
    """Sandbox that runs shell commands inside a Docker container.

    File operations are still performed on the host filesystem but are scoped to the
    provided workspace root. `run_shell` is executed via `docker run --rm` with the
    workspace bind-mounted at `/workspace`.
    """

    def __init__(
        self,
        image: str,
        session_id: str = "",
        workspace: str = "",
        lsp_actor: Any = None,
        *,
        network: str = "none",
        runtime: str | None = None,
        docker_bin: str | None = None,
    ) -> None:
        super().__init__(image=image, session_id=session_id, workspace=workspace, lsp_actor=lsp_actor)
        self.network = str(network or "none")
        self.runtime = runtime or os.environ.get("RAY_DOCKER_RUNTIME")
        self.docker_bin = docker_bin or shutil.which("docker") or "docker"

    def _docker_prefix(self, *, env: Optional[Dict[str, str]] = None) -> List[str]:
        if shutil.which(self.docker_bin) is None and self.docker_bin != "docker":
            raise RuntimeError(f"docker binary not found: {self.docker_bin}")
        if shutil.which("docker") is None and self.docker_bin == "docker":
            raise RuntimeError("docker not available on this host")

        workspace = str(Path(self.workspace).resolve())
        args: List[str] = [
            self.docker_bin,
            "run",
            "--rm",
            "--volume",
            f"{workspace}:/workspace",
            "--workdir",
            "/workspace",
        ]
        if self.network:
            args.extend(["--network", self.network])
        if self.runtime:
            args.extend(["--runtime", self.runtime])
        for key, value in (env or {}).items():
            if not key:
                continue
            args.extend(["-e", f"{key}={value}"])
        args.append(self.image)
        return args

    def run_shell(
        self,
        command: str,
        timeout: int = 30,
        env: Optional[Dict[str, str]] = None,
        stream: bool = False,
        stdin_data: Optional[str] = None,
        shell: bool = True,
    ):
        # Execute inside the container via sh -lc.
        cmd = command or ""
        docker_cmd = [*self._docker_prefix(env=env), "sh", "-lc", cmd]
        try:
            result = subprocess.run(
                docker_cmd,
                timeout=timeout,
                input=stdin_data,
                capture_output=True,
                text=True,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            payload = {"exit": result.returncode, "stdout": stdout, "stderr": stderr}
            if not stream:
                return payload

            lines: List[Any] = [ADAPTIVE_PREFIX_ITERABLE]
            for line in (stdout.splitlines() or []):
                lines.append(line)
            if not stdout and stderr:
                for line in stderr.splitlines():
                    lines.append(line)
            lines.append(payload)
            return lines
        except subprocess.TimeoutExpired:
            payload = {"exit": 124, "stdout": "", "stderr": "Command timed out"}
            if not stream:
                return payload
            return [ADAPTIVE_PREFIX_ITERABLE, payload]
        except Exception as exc:  # noqa: BLE001
            payload = {"exit": 1, "stdout": "", "stderr": str(exc)}
            if not stream:
                return payload
            return [ADAPTIVE_PREFIX_ITERABLE, payload]

