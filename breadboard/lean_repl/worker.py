"""Lean REPL Worker Implementation.

This module provides the LeanWorker Ray actor, which manages a pool of Lean
REPL processes for executing proof checking operations.

Each worker maintains warm environments with pre-loaded imports to minimize
startup overhead.

The worker communicates with Lean using the JSON protocol via `lean --stdin --json`.
For state persistence across commands, we use Lean's built-in pickle commands
(pickleTo/unpickleEnvFrom).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import ray

from .schema import (
    CheckLimits,
    CheckMode,
    CheckRequest,
    CheckResult,
    ErrorSeverity,
    LeanError,
    REPLCommand,
    REPLResponse,
    ResourceUsage,
    Sorry,
    CANONICAL_MATHLIB_HEADER,
    CANONICAL_MATHLIB_HEADER_HASH,
)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

# Default header imports for various configurations
DEFAULT_HEADERS: Dict[str, List[str]] = {
    CANONICAL_MATHLIB_HEADER_HASH: CANONICAL_MATHLIB_HEADER,
}


# -----------------------------------------------------------------------------
# REPL Process Management
# -----------------------------------------------------------------------------


@dataclass
class REPLProcess:
    """A single Lean REPL subprocess with state tracking.

    For the JSON REPL mode, we maintain a persistent subprocess and track
    environment IDs for state continuity.
    """

    process: Optional[asyncio.subprocess.Process] = None
    env_id: int = 0
    header_hash: str = ""
    idle: bool = True
    created_ts: float = 0.0
    last_used_ts: float = 0.0
    # For tracking pending I/O
    _pending_response: bool = False

    def is_alive(self) -> bool:
        """Check if the subprocess is still running."""
        if self.process is None:
            return False
        return self.process.returncode is None

    def kill(self) -> None:
        """Terminate the subprocess."""
        if self.process is None:
            return
        try:
            self.process.terminate()
        except ProcessLookupError:
            pass


# -----------------------------------------------------------------------------
# JSON Protocol Helpers
# -----------------------------------------------------------------------------


def parse_lean_message(msg: Dict[str, Any]) -> Tuple[str, Optional[LeanError]]:
    """Parse a Lean message from JSON response.

    Returns:
        Tuple of (message_text, LeanError if it's an error/warning else None)
    """
    severity_str = msg.get("severity", "info")
    message_text = msg.get("data", msg.get("message", str(msg)))

    # Map severity
    if severity_str == "error":
        severity = ErrorSeverity.ERROR
    elif severity_str == "warning":
        severity = ErrorSeverity.WARNING
    else:
        severity = ErrorSeverity.INFO

    # Extract position information
    pos = msg.get("pos", {})
    end_pos = msg.get("endPos", {})

    pos_line = pos.get("line", 1)
    pos_col = pos.get("column", 0)
    end_line = end_pos.get("line") if end_pos else None
    end_col = end_pos.get("column") if end_pos else None

    # Create LeanError for errors and warnings
    if severity in (ErrorSeverity.ERROR, ErrorSeverity.WARNING):
        signature = LeanError.compute_signature(severity_str, message_text)
        lean_error = LeanError(
            severity=severity,
            message=message_text,
            pos_line=pos_line,
            pos_col=pos_col,
            end_line=end_line,
            end_col=end_col,
            signature=signature,
        )
        return message_text, lean_error

    return message_text, None


def parse_lean_sorry(sorry: Dict[str, Any]) -> Sorry:
    """Parse a sorry from JSON response."""
    pos = sorry.get("pos", {})
    return Sorry(
        pos_line=pos.get("line", 1),
        pos_col=pos.get("column", 0),
        goal=sorry.get("goal"),
    )


@dataclass
class WorkerStats:
    """Statistics for monitoring worker health."""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    timeout_requests: int = 0
    crash_count: int = 0
    header_cache_hits: int = 0
    header_cache_misses: int = 0
    total_execution_time_s: float = 0.0


# -----------------------------------------------------------------------------
# LeanWorker Ray Actor
# -----------------------------------------------------------------------------


@ray.remote
class LeanWorker:
    """Ray actor managing a pool of Lean REPL processes.

    This worker maintains warm environments with pre-loaded imports to minimize
    latency. The canonical header (import Mathlib) is always kept warm.

    Attributes:
        workspace: Working directory for Lean operations
        pool_size: Number of concurrent REPL processes
        canonical_header: Standard imports to pre-load
        canonical_header_hash: Hash of canonical header
        lru_cache_size: Number of non-canonical headers to cache
        lean_toolchain: Lean version string
        mathlib_rev: Mathlib version/commit
    """

    def __init__(
        self,
        workspace: str,
        pool_size: int = 4,
        canonical_header: str = "import Mathlib",
        lru_cache_size: int = 8,
        lean_toolchain: str = "leanprover/lean4:v4.14.0",
        mathlib_rev: str = "v4.14.0",
    ):
        self.workspace = Path(workspace)
        self.pool_size = pool_size
        self.canonical_header = canonical_header
        self.canonical_header_hash = CANONICAL_MATHLIB_HEADER_HASH
        self.lru_cache_size = lru_cache_size
        self.lean_toolchain = lean_toolchain
        self.mathlib_rev = mathlib_rev

        # REPL process pool
        self.processes: List[REPLProcess] = []

        # Header hash -> set of process indices with that header loaded
        self.header_cache: Dict[str, Set[int]] = {}

        # Statistics
        self.stats = WorkerStats()

        # Free slots (indices of idle processes)
        self.free_slots: Set[int] = set()

        # Warmed up flag
        self.warmed_up = False

        # Pickle directory (initialized in initialize())
        self.pickle_dir: Optional[Path] = None

        logger.info(
            f"LeanWorker initialized: workspace={workspace}, pool_size={pool_size}, "
            f"canonical_header={canonical_header}"
        )

    async def initialize(self) -> None:
        """Initialize the worker pool and warm up canonical header."""
        # Ensure workspace exists
        self.workspace.mkdir(parents=True, exist_ok=True)

        # Create temp directory for pickles
        self.pickle_dir = self.workspace / "pickles"
        self.pickle_dir.mkdir(parents=True, exist_ok=True)

        # Start process pool
        for i in range(self.pool_size):
            proc = await self._spawn_repl_process()
            self.processes.append(REPLProcess(
                process=proc,
                env_id=0,
                header_hash="",
                idle=True,
                created_ts=time.time(),
                last_used_ts=time.time(),
            ))
            self.free_slots.add(i)

        # Warm up canonical header
        await self._warmup_canonical_header()

        self.warmed_up = True
        logger.info(f"LeanWorker initialized with {self.pool_size} processes")

    async def _spawn_repl_process(self) -> asyncio.subprocess.Process:
        """Spawn a new Lean REPL subprocess.

        Uses leanprover-community/repl JSON protocol for stateful communication.
        Falls back to `lake env lean -Dpp.all` for one-shot execution.
        """
        # Try to use the REPL if available, otherwise fall back to lean directly
        # The REPL binary is typically at .lake/build/bin/repl in a Mathlib project
        repl_path = self.workspace / ".lake" / "build" / "bin" / "repl"

        if repl_path.exists():
            cmd = [str(repl_path)]
            logger.info(f"Using REPL binary: {repl_path}")
        else:
            # Fall back to lean --run for one-shot execution
            # Note: This doesn't support stateful REPL, but works for single checks
            cmd = ["lake", "env", "lean", "--stdin"]
            logger.info("REPL not found, using lake env lean --stdin (one-shot mode)")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace),
            )
            return proc
        except FileNotFoundError as e:
            logger.error(f"Failed to spawn Lean process: {e}")
            raise RuntimeError(f"Lean not found. Ensure lake/lean is installed: {e}")

    async def _warmup_canonical_header(self) -> None:
        """Pre-load the canonical header on all processes.

        This sends the import commands to each process to pre-load Mathlib.
        """
        logger.info("Warming up canonical header on all processes")

        # Get header imports
        header_imports = DEFAULT_HEADERS.get(
            self.canonical_header_hash,
            [self.canonical_header]
        )

        # Warm up each process
        warmed = 0
        for i in range(self.pool_size):
            proc = self.processes[i]
            if not proc.is_alive():
                logger.warning(f"Process {i} is not alive, skipping warmup")
                continue

            try:
                # Send header command to REPL
                cmd = REPLCommand(cmd="\n".join(header_imports))
                response = await self._send_repl_command(i, cmd, timeout_s=120.0)

                if response is not None:
                    proc.env_id = response.env or 0
                    proc.header_hash = self.canonical_header_hash
                    warmed += 1
                    logger.debug(f"Process {i} warmed up with env_id={proc.env_id}")
                else:
                    logger.warning(f"Failed to warm up process {i}")
            except Exception as e:
                logger.warning(f"Error warming up process {i}: {e}")

        # Register in cache
        if warmed > 0:
            self.header_cache[self.canonical_header_hash] = {
                i for i in range(self.pool_size)
                if self.processes[i].header_hash == self.canonical_header_hash
            }

        logger.info(f"Canonical header loaded on {warmed}/{self.pool_size} processes")

    async def _send_repl_command(
        self,
        proc_idx: int,
        cmd: REPLCommand,
        timeout_s: float = 30.0,
    ) -> Optional[REPLResponse]:
        """Send a command to the REPL and wait for response.

        Args:
            proc_idx: Index of the process to use
            cmd: REPL command to send
            timeout_s: Timeout in seconds

        Returns:
            REPLResponse if successful, None if failed/timeout
        """
        proc = self.processes[proc_idx]
        if not proc.is_alive() or proc.process is None:
            logger.error(f"Process {proc_idx} is not alive")
            return None

        stdin = proc.process.stdin
        stdout = proc.process.stdout

        if stdin is None or stdout is None:
            logger.error(f"Process {proc_idx} has no stdin/stdout")
            return None

        # Serialize command
        cmd_json = cmd.to_json()
        logger.debug(f"Sending to process {proc_idx}: {cmd_json[:200]}...")

        try:
            # Send command with newline
            stdin.write((cmd_json + "\n").encode())
            await stdin.drain()

            # Read response with timeout
            try:
                response_line = await asyncio.wait_for(
                    stdout.readline(),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Timeout reading from process {proc_idx}")
                return None

            if not response_line:
                logger.warning(f"Empty response from process {proc_idx}")
                return None

            # Parse response
            response_str = response_line.decode().strip()
            logger.debug(f"Response from process {proc_idx}: {response_str[:200]}...")

            return REPLResponse.from_json(response_str)

        except Exception as e:
            logger.exception(f"Error communicating with process {proc_idx}: {e}")
            return None

    async def _respawn_process(self, proc_idx: int) -> bool:
        """Respawn a crashed or dead process.

        Returns:
            True if respawn successful, False otherwise
        """
        old_proc = self.processes[proc_idx]

        # Kill old process if still exists
        old_proc.kill()

        try:
            new_proc = await self._spawn_repl_process()
            self.processes[proc_idx] = REPLProcess(
                process=new_proc,
                env_id=0,
                header_hash="",
                idle=True,
                created_ts=time.time(),
                last_used_ts=time.time(),
            )
            logger.info(f"Respawned process {proc_idx}")
            return True
        except Exception as e:
            logger.error(f"Failed to respawn process {proc_idx}: {e}")
            return False

    def get_stats(self) -> Dict:
        """Get worker statistics."""
        return {
            "total_requests": self.stats.total_requests,
            "successful_requests": self.stats.successful_requests,
            "failed_requests": self.stats.failed_requests,
            "timeout_requests": self.stats.timeout_requests,
            "crash_count": self.stats.crash_count,
            "header_cache_hits": self.stats.header_cache_hits,
            "header_cache_misses": self.stats.header_cache_misses,
            "avg_execution_time_s": (
                self.stats.total_execution_time_s / max(1, self.stats.total_requests)
            ),
            "pool_size": self.pool_size,
            "free_slots": len(self.free_slots),
            "warmed_headers": list(self.header_cache.keys()),
        }

    def get_free_slots(self) -> int:
        """Get number of free slots."""
        return len(self.free_slots)

    def has_header(self, header_hash: str) -> bool:
        """Check if this worker has the header loaded."""
        return header_hash in self.header_cache and len(self.header_cache[header_hash]) > 0

    async def execute_check(self, request: CheckRequest) -> CheckResult:
        """Execute a Lean check request.

        Args:
            request: The check request to execute

        Returns:
            CheckResult with execution results
        """
        start_time = time.time()
        self.stats.total_requests += 1

        try:
            # Find appropriate process
            proc_idx = self._find_process(request.header_hash)
            if proc_idx is None:
                # No free process available - this shouldn't happen in normal operation
                logger.warning("No free process available, request will fail")
                self.stats.failed_requests += 1
                return CheckResult(
                    request_id=request.request_id,
                    success=False,
                    messages=["No free worker process available"],
                    crashed=True,
                )

            # Mark process as busy
            self.free_slots.discard(proc_idx)
            proc = self.processes[proc_idx]
            proc.idle = False
            proc.last_used_ts = time.time()

            # Execute the check
            result = await self._execute_on_process(proc_idx, request)

            # Mark process as free
            proc.idle = True
            self.free_slots.add(proc_idx)

            # Update stats
            if result.success:
                self.stats.successful_requests += 1
            elif result.timeout:
                self.stats.timeout_requests += 1
            else:
                self.stats.failed_requests += 1

            execution_time = time.time() - start_time
            self.stats.total_execution_time_s += execution_time

            # Add resource usage
            result.resource_usage = ResourceUsage(
                wall_time_s=execution_time,
                cpu_time_s=None,  # TODO: Track via cgroups
                memory_peak_mb=None,
                heartbeats=None,
            )

            return result

        except Exception as e:
            logger.exception(f"Error executing check: {e}")
            self.stats.failed_requests += 1
            return CheckResult(
                request_id=request.request_id,
                success=False,
                messages=[f"Worker exception: {str(e)}"],
                crashed=True,
            )

    def _find_process(self, header_hash: str) -> Optional[int]:
        """Find an appropriate process for the request.

        Prefers processes with the header already loaded (cache hit),
        falls back to any idle process (cache miss).

        Returns:
            Process index, or None if no process available
        """
        # Try to find idle process with matching header
        if header_hash in self.header_cache:
            for proc_idx in self.header_cache[header_hash]:
                if proc_idx in self.free_slots:
                    self.stats.header_cache_hits += 1
                    return proc_idx

        # Fall back to any idle process
        if self.free_slots:
            self.stats.header_cache_misses += 1
            return next(iter(self.free_slots))

        return None

    async def _execute_on_process(
        self, proc_idx: int, request: CheckRequest
    ) -> CheckResult:
        """Execute a check request on a specific process.

        Steps:
        1. Check if process is alive, respawn if needed
        2. Check if header needs to be loaded
        3. Load state_ref if provided (unpickle)
        4. Execute commands sequentially
        5. Pickle new state if requested
        6. Parse Lean output and extract errors/messages
        """
        proc = self.processes[proc_idx]

        logger.info(
            f"Executing check on process {proc_idx}: "
            f"header={request.header_hash[:8]}..., commands={len(request.commands)}"
        )

        # Check if process is alive
        if not proc.is_alive():
            logger.warning(f"Process {proc_idx} is dead, attempting respawn")
            if not await self._respawn_process(proc_idx):
                return CheckResult(
                    request_id=request.request_id,
                    success=False,
                    messages=["Failed to respawn Lean process"],
                    crashed=True,
                )
            proc = self.processes[proc_idx]
            self.stats.crash_count += 1

        # Collect results
        all_messages: List[str] = []
        all_errors: List[LeanError] = []
        all_sorries: List[Sorry] = []
        current_env_id = proc.env_id
        success = True

        # Check if we need to load a different header
        if proc.header_hash != request.header_hash:
            logger.debug(f"Loading header {request.header_hash[:8]}... on process {proc_idx}")
            header_imports = DEFAULT_HEADERS.get(request.header_hash)
            if header_imports:
                # Need to restart process with new header since we can't unload
                # For now, just send the imports (this may not work perfectly)
                cmd = REPLCommand(cmd="\n".join(header_imports))
                response = await self._send_repl_command(
                    proc_idx, cmd, timeout_s=request.limits.timeout_s
                )
                if response is None:
                    return CheckResult(
                        request_id=request.request_id,
                        success=False,
                        messages=["Failed to load header imports"],
                        timeout=True,
                    )
                proc.header_hash = request.header_hash
                proc.env_id = response.env or 0
                current_env_id = proc.env_id

        # Handle state_ref (unpickle existing state)
        if request.state_ref:
            unpickle_path = self._get_pickle_path(request.state_ref)
            if unpickle_path.exists():
                cmd = REPLCommand(
                    unpickleEnvFrom=str(unpickle_path),
                    env=0,  # Base env
                )
                response = await self._send_repl_command(
                    proc_idx, cmd, timeout_s=request.limits.timeout_s
                )
                if response is None:
                    return CheckResult(
                        request_id=request.request_id,
                        success=False,
                        messages=[f"Failed to unpickle state: {request.state_ref}"],
                        crashed=True,
                    )
                current_env_id = response.env or current_env_id
                logger.debug(f"Unpickled state, new env_id={current_env_id}")
            else:
                logger.warning(f"State ref not found: {request.state_ref}")

        # Execute commands
        for i, command in enumerate(request.commands):
            if request.mode == CheckMode.TACTIC:
                # Tactic mode - use proofState
                cmd = REPLCommand(
                    tactic=command,
                    proofState=current_env_id,
                )
            else:
                # Command mode - use env
                cmd = REPLCommand(
                    cmd=command,
                    env=current_env_id,
                )

            response = await self._send_repl_command(
                proc_idx, cmd, timeout_s=request.limits.timeout_s
            )

            if response is None:
                # Timeout or crash
                logger.warning(f"No response for command {i}: {command[:50]}...")
                return CheckResult(
                    request_id=request.request_id,
                    success=False,
                    messages=all_messages + [f"Timeout/crash on command {i}"],
                    errors=all_errors,
                    sorries=all_sorries,
                    timeout=True,
                    new_env_id=current_env_id,
                )

            # Update env_id
            if response.env is not None:
                current_env_id = response.env

            # Parse messages
            for msg in response.messages:
                msg_text, lean_error = parse_lean_message(msg)
                all_messages.append(msg_text)
                if lean_error:
                    all_errors.append(lean_error)
                    if lean_error.severity == ErrorSeverity.ERROR:
                        success = False

            # Parse sorries
            for sorry in response.sorries:
                all_sorries.append(parse_lean_sorry(sorry))

        # Pickle new state if requested
        new_state_ref = None
        if "state" in request.want and success:
            pickle_path = self._get_pickle_path(f"state_{request.request_id}")
            cmd = REPLCommand(
                env=current_env_id,
                pickleTo=str(pickle_path),
            )
            response = await self._send_repl_command(
                proc_idx, cmd, timeout_s=10.0
            )
            if response is not None:
                new_state_ref = f"state_{request.request_id}"
                logger.debug(f"Pickled state to {pickle_path}")

        # Update process state
        proc.env_id = current_env_id
        proc.last_used_ts = time.time()

        return CheckResult(
            request_id=request.request_id,
            success=success and len(all_errors) == 0,
            messages=all_messages,
            errors=all_errors,
            sorries=all_sorries,
            new_state_ref=new_state_ref,
            new_env_id=current_env_id,
        )

    def _get_pickle_path(self, ref: str) -> Path:
        """Get filesystem path for a pickle reference."""
        # Sanitize ref to prevent path traversal
        safe_ref = re.sub(r"[^\w\-]", "_", ref)
        return self.pickle_dir / f"{safe_ref}.olean"

    async def shutdown(self) -> None:
        """Shutdown all REPL processes."""
        logger.info("Shutting down LeanWorker")

        for proc in self.processes:
            if proc.is_alive():
                proc.kill()
                # Wait for process to terminate
                if proc.process is not None:
                    try:
                        await asyncio.wait_for(proc.process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        # Force kill
                        if proc.process.returncode is None:
                            proc.process.kill()

        self.processes.clear()
        self.free_slots.clear()
        self.header_cache.clear()

        logger.info("LeanWorker shutdown complete")


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def create_lean_worker(
    workspace: str,
    pool_size: Optional[int] = None,
    **kwargs,
) -> ray.actor.ActorHandle:
    """Create a LeanWorker Ray actor.

    Args:
        workspace: Working directory for Lean operations
        pool_size: Number of REPL processes (defaults to CPU count - 1)
        **kwargs: Additional arguments for LeanWorker

    Returns:
        Ray actor handle
    """
    if pool_size is None:
        pool_size = max(1, os.cpu_count() - 1)

    actor = LeanWorker.options(
        name=f"lean_worker_{id(workspace)}",
        num_cpus=pool_size,
    ).remote(
        workspace=workspace,
        pool_size=pool_size,
        **kwargs,
    )

    # Initialize asynchronously
    ray.get(actor.initialize.remote())

    return actor
