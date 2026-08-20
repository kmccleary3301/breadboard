"""Minimal crawl frontier primitives (Hermes-derived).

The interface is intentionally small and forward-compatible with Redis-backed
frontiers for QueryLake-scale crawling.
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Optional, Tuple


class BaseFrontier:
    async def enqueue(self, url: str, depth: int) -> None:
        raise NotImplementedError

    async def dequeue(self, timeout: Optional[float] = None) -> Tuple[str, int]:
        raise NotImplementedError

    async def mark_done(self, url: str) -> None:
        raise NotImplementedError

    def size(self) -> int:
        raise NotImplementedError

    def seen(self, url: str) -> bool:
        raise NotImplementedError

    def inflight_size(self) -> int:
        raise NotImplementedError


class MemoryFrontier(BaseFrontier):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Tuple[str, int]] = asyncio.Queue()
        self._seen: set[str] = set()
        self._inflight: set[str] = set()

    async def enqueue(self, url: str, depth: int) -> None:
        if url in self._seen or url in self._inflight:
            return
        await self._queue.put((url, depth))
        self._seen.add(url)

    async def dequeue(self, timeout: Optional[float] = None) -> Tuple[str, int]:
        if timeout is None:
            item = await self._queue.get()
        else:
            item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        url, depth = item
        self._inflight.add(url)
        return url, depth

    async def mark_done(self, url: str) -> None:
        self._inflight.discard(url)
        try:
            self._queue.task_done()
        except ValueError:
            # task_done called too many times; ignore.
            pass

    def size(self) -> int:
        return self._queue.qsize()

    def seen(self, url: str) -> bool:
        return url in self._seen or url in self._inflight

    def inflight_size(self) -> int:
        return len(self._inflight)


@dataclass(frozen=True)
class RedisFrontierConfig:
    redis_url: str
    namespace: str = "kc_web_frontier"


class RedisFrontier(BaseFrontier):
    """A simple Redis-backed frontier (no leases yet).

    This is intentionally minimal: it provides dedupe and a shared queue. QueryLake
    deployments should add leases/heartbeats for lost-task requeue.
    """

    def __init__(self, config: RedisFrontierConfig):
        try:
            import redis  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"RedisFrontier requires redis-py: {exc}") from exc
        self._redis = redis.from_url(config.redis_url, decode_responses=True)
        self._ns = config.namespace.strip(":") or "kc_web_frontier"
        self._queue_key = f"{self._ns}:queue"
        self._seen_key = f"{self._ns}:seen"
        self._inflight_key = f"{self._ns}:inflight"

    async def enqueue(self, url: str, depth: int) -> None:
        payload = json.dumps({"url": url, "depth": int(depth)}, ensure_ascii=False, sort_keys=True)
        pipe = self._redis.pipeline()
        pipe.sadd(self._seen_key, url)
        pipe.rpush(self._queue_key, payload)
        added, _ = pipe.execute()
        # If `sadd` returns 0, the URL was already seen; undo the rpush.
        if int(added or 0) == 0:
            try:
                self._redis.lrem(self._queue_key, 1, payload)
            except Exception:
                pass

    async def dequeue(self, timeout: Optional[float] = None) -> Tuple[str, int]:
        # BLPOP returns (key, value). Redis timeouts are integer seconds.
        blpop_timeout = 0
        if timeout is not None:
            try:
                blpop_timeout = max(1, int(math.ceil(float(timeout))))
            except Exception:
                blpop_timeout = 1
        item = self._redis.blpop(self._queue_key, timeout=blpop_timeout)
        if not item:
            raise asyncio.TimeoutError("frontier dequeue timeout")
        _, raw = item
        data = json.loads(raw)
        url = str(data.get("url") or "")
        depth = int(data.get("depth") or 0)
        try:
            self._redis.sadd(self._inflight_key, url)
        except Exception:
            pass
        return url, depth

    async def mark_done(self, url: str) -> None:
        try:
            self._redis.srem(self._inflight_key, url)
        except Exception:
            pass

    def size(self) -> int:
        try:
            return int(self._redis.llen(self._queue_key) or 0)
        except Exception:
            return 0

    def seen(self, url: str) -> bool:
        try:
            return bool(self._redis.sismember(self._seen_key, url) or self._redis.sismember(self._inflight_key, url))
        except Exception:
            return False

    def inflight_size(self) -> int:
        try:
            return int(self._redis.scard(self._inflight_key) or 0)
        except Exception:
            return 0

