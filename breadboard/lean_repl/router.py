"""Lean REPL Router Implementation.

This module provides the LeanRouter Ray actor, which routes CheckRequests to
appropriate LeanWorker actors based on header cache locality and load balancing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import ray

from .schema import CheckRequest, CheckResult

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Worker Metadata
# -----------------------------------------------------------------------------


@dataclass
class WorkerMetadata:
    """Metadata about a LeanWorker for routing decisions."""

    actor_handle: ray.actor.ActorHandle
    worker_id: str
    free_slots: int = 0
    warmed_headers: set = None
    last_update_ts: float = 0.0

    def __post_init__(self):
        if self.warmed_headers is None:
            self.warmed_headers = set()


# -----------------------------------------------------------------------------
# LeanRouter Ray Actor
# -----------------------------------------------------------------------------


@ray.remote
class LeanRouter:
    """Ray actor for routing Lean check requests to workers.

    The router implements the scheduling algorithm from the master spec:
    1. Prefer workers with the required header already warm (cache hit)
    2. Fall back to any worker with free slots (cache miss)
    3. Queue the request if no workers available

    Attributes:
        workers: Map of worker_id -> WorkerMetadata
        pending_queue: Queue of pending requests
        stats: Routing statistics
    """

    def __init__(self):
        self.workers: Dict[str, WorkerMetadata] = {}
        self.pending_queue: asyncio.Queue = asyncio.Queue()
        self.stats = {
            "total_requests": 0,
            "routed_requests": 0,
            "queued_requests": 0,
            "header_hits": 0,
            "header_misses": 0,
            "worker_count": 0,
        }
        self.running = False

        logger.info("LeanRouter initialized")

    async def start(self) -> None:
        """Start the router's background scheduler loop."""
        if self.running:
            logger.warning("Router already running")
            return

        self.running = True
        asyncio.create_task(self._scheduler_loop())
        logger.info("LeanRouter started")

    async def stop(self) -> None:
        """Stop the router."""
        self.running = False
        logger.info("LeanRouter stopped")

    def register_worker(self, worker_handle: ray.actor.ActorHandle, worker_id: str) -> None:
        """Register a worker with the router.

        Args:
            worker_handle: Ray actor handle for the worker
            worker_id: Unique identifier for this worker
        """
        self.workers[worker_id] = WorkerMetadata(
            actor_handle=worker_handle,
            worker_id=worker_id,
            free_slots=0,
            warmed_headers=set(),
            last_update_ts=time.time(),
        )
        self.stats["worker_count"] = len(self.workers)
        logger.info(f"Registered worker: {worker_id}")

    def unregister_worker(self, worker_id: str) -> None:
        """Unregister a worker from the router.

        Args:
            worker_id: Worker to unregister
        """
        if worker_id in self.workers:
            del self.workers[worker_id]
            self.stats["worker_count"] = len(self.workers)
            logger.info(f"Unregistered worker: {worker_id}")

    async def submit_request(self, request: CheckRequest) -> CheckResult:
        """Submit a check request for execution.

        This is the primary entry point for executing Lean checks.
        The request will be routed to an appropriate worker.

        Args:
            request: The check request to execute

        Returns:
            CheckResult from the worker
        """
        self.stats["total_requests"] += 1

        # Try immediate routing
        worker = self._find_suitable_worker(request.header_hash)

        if worker is not None:
            # Direct route to worker
            self.stats["routed_requests"] += 1
            return await self._execute_on_worker(worker, request)

        # No worker available - queue the request
        self.stats["queued_requests"] += 1
        logger.debug(f"Queueing request {request.request_id}")

        # Create a future for the result
        result_future = asyncio.Future()
        await self.pending_queue.put((request, result_future))

        # Wait for the scheduler to process it
        return await result_future

    def _find_suitable_worker(self, header_hash: str) -> Optional[WorkerMetadata]:
        """Find the best worker for a request.

        Routing logic (from master spec):
        1. Prefer workers with warm header + free slots (cache hit)
        2. Fall back to any worker with free slots (cache miss)
        3. Return None if no capacity

        Args:
            header_hash: Required header hash

        Returns:
            WorkerMetadata if found, None otherwise
        """
        # Refresh worker stats first
        self._refresh_worker_stats()

        # Strategy 1: Workers with matching header and free slots
        candidates = [
            w for w in self.workers.values()
            if w.free_slots > 0 and header_hash in w.warmed_headers
        ]

        if candidates:
            self.stats["header_hits"] += 1
            # Choose least loaded
            return min(candidates, key=lambda w: -w.free_slots)

        # Strategy 2: Any worker with free slots
        candidates = [w for w in self.workers.values() if w.free_slots > 0]

        if candidates:
            self.stats["header_misses"] += 1
            # Choose least loaded
            return min(candidates, key=lambda w: -w.free_slots)

        # No capacity available
        return None

    def _refresh_worker_stats(self) -> None:
        """Refresh cached worker statistics.

        In production, this would query workers asynchronously.
        For now, we'll use cached values.
        """
        # TODO: Implement async stat refresh
        # For now, assume stats are fresh
        pass

    async def _execute_on_worker(
        self, worker: WorkerMetadata, request: CheckRequest
    ) -> CheckResult:
        """Execute a request on a specific worker.

        Args:
            worker: Worker to execute on
            request: Request to execute

        Returns:
            CheckResult from the worker
        """
        try:
            result = await worker.actor_handle.execute_check.remote(request)
            return result
        except Exception as e:
            logger.exception(f"Error executing on worker {worker.worker_id}: {e}")
            return CheckResult(
                request_id=request.request_id,
                success=False,
                messages=[f"Router error: {str(e)}"],
                crashed=True,
            )

    async def _scheduler_loop(self) -> None:
        """Background loop for processing the pending queue.

        This loop continuously checks for pending requests and routes them
        when workers become available.
        """
        logger.info("Scheduler loop started")

        while self.running:
            try:
                # Check if there are pending requests
                if self.pending_queue.empty():
                    await asyncio.sleep(0.01)  # 10ms poll interval
                    continue

                # Get next request (non-blocking peek)
                try:
                    request, result_future = self.pending_queue.get_nowait()
                except asyncio.QueueEmpty:
                    continue

                # Try to find a worker
                worker = self._find_suitable_worker(request.header_hash)

                if worker is None:
                    # No capacity - put back in queue with backoff
                    await self.pending_queue.put((request, result_future))
                    await asyncio.sleep(0.1)  # Backoff
                    continue

                # Route to worker
                logger.debug(f"Routing queued request {request.request_id} to {worker.worker_id}")
                result = await self._execute_on_worker(worker, request)
                result_future.set_result(result)

            except Exception as e:
                logger.exception(f"Error in scheduler loop: {e}")
                await asyncio.sleep(1.0)  # Error backoff

        logger.info("Scheduler loop stopped")

    def get_stats(self) -> Dict:
        """Get router statistics.

        Returns:
            Dictionary of statistics
        """
        queue_depth = self.pending_queue.qsize()

        return {
            **self.stats,
            "queue_depth": queue_depth,
            "workers": {
                wid: {
                    "free_slots": w.free_slots,
                    "warmed_headers": list(w.warmed_headers),
                    "last_update_ts": w.last_update_ts,
                }
                for wid, w in self.workers.items()
            },
        }


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def create_lean_router() -> ray.actor.ActorHandle:
    """Create a LeanRouter Ray actor.

    Returns:
        Ray actor handle
    """
    actor = LeanRouter.options(
        name="lean_router",
        max_concurrency=100,  # Allow high concurrency
    ).remote()

    # Start the scheduler
    ray.get(actor.start.remote())

    return actor
