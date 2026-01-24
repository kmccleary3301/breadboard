# Lean REPL Service

High-performance, scalable service for executing Lean4 proof checking operations at scale. Part of the EvoLake ATP infrastructure.

## Overview

The Lean REPL service provides a distributed, stateless API for executing Lean commands and tactics. It's designed to support 10k-75k Lean checks per problem, matching the throughput of systems like Aleph Prover.

## Architecture

```
Client
  │
  ├─> CheckRequest(header_hash, commands, state_ref, limits)
  │
  └─> LeanRouter (Ray actor)
        │
        ├─> LeanWorker 1 (pool of REPL processes)
        ├─> LeanWorker 2
        └─> LeanWorker N
              │
              ├─> lean --stdin --json (process 1)
              ├─> lean --stdin --json (process 2)
              └─> lean --stdin --json (process K)
                    │
                    └─> StateStore (CAS)
```

## Quick Start

```python
import ray
from breadboard.lean_repl import (
    create_lean_worker,
    create_lean_router,
    CheckRequest,
    CANONICAL_MATHLIB_HEADER_HASH,
)

# Initialize Ray
ray.init()

# Create worker and router
worker = create_lean_worker(workspace="/tmp/lean_workspace", pool_size=4)
router = create_lean_router()
router.register_worker.remote(worker, "worker_1")

# Submit a check request
request = CheckRequest(
    header_hash=CANONICAL_MATHLIB_HEADER_HASH,
    commands=["def foo := 2", "#check foo"],
)

result = ray.get(router.submit_request.remote(request))
print(f"Success: {result.success}")
print(f"Messages: {result.messages}")
```

## Components

### Schema (`schema.py`)

Core data structures for the REPL service:

- **`CheckRequest`**: Request to execute Lean commands/tactics
- **`CheckResult`**: Response with results, errors, and new state
- **`StateBlob`**: Serialized Lean environment/proofState for CAS storage
- **`LeanError`**: Parsed error with signature for APOLLO repair

### Worker (`worker.py`)

Ray actor managing a pool of Lean REPL processes:

- Maintains warm environments with pre-loaded imports
- LRU cache for non-canonical headers
- Statistics tracking (cache hits, execution time)
- Graceful handling of process crashes

### Router (`router.py`)

Ray actor for routing requests to workers:

- Header-aware locality routing (prefer workers with warm header)
- Load balancing (least loaded worker)
- Pending queue for backpressure
- Background scheduler for queue processing

### State Store (`state_store.py`)

Content-addressable storage for state blobs:

- File-based CAS with sharded directories
- zstd compression for pickle bytes
- Deduplication via content hashing
- Version compatibility tracking

## Configuration

### Worker Configuration

```python
worker = create_lean_worker(
    workspace="/tmp/lean_workspace",
    pool_size=4,                           # CPU cores - 1
    canonical_header="import Mathlib",     # Always warm
    lru_cache_size=8,                      # Non-canonical headers
    lean_toolchain="leanprover/lean4:v4.14.0",
    mathlib_rev="v4.14.0",
)
```

### Check Limits

```python
from breadboard.lean_repl import CheckLimits

limits = CheckLimits(
    timeout_s=30.0,       # Wall time limit
    memory_mb=4096,       # Memory limit
    max_heartbeats=100_000,  # Lean elaboration limit
)
```

### State Store

```python
from breadboard.lean_repl import get_state_store

store = get_state_store(
    root="/tmp/lean_state_store",
    compression_level=3,  # zstd level (1-22)
)
```

## Performance Targets

| Metric | Target | Status |
|--------|--------|--------|
| Warm check latency (p50) | <50ms | TBD |
| Warm check latency (p95) | <200ms | TBD |
| Header cache hit rate | >95% | TBD |
| CPU utilization | >85% | TBD |
| Mathlib load time | <1s | 3.5s (needs Firecracker) |

## Testing

```bash
# Run all tests (schema, state store, Docker integration)
pytest tests/test_lean_repl_basic.py tests/test_lean_repl_integration.py -v

# Run schema tests only
pytest tests/test_lean_repl_basic.py::TestSchema -v

# Run Docker integration tests (requires Docker + lean4-mathlib-simple image)
pytest tests/test_lean_repl_integration.py -v -s

# Run with Ray initialized
RAY_INIT=1 pytest tests/test_lean_repl_basic.py::TestWorkerAndRouter -v

# Manual Docker test script
python scripts/test_lean_worker_docker.py
```

### Prerequisites for Docker Tests

Build the Lean4 + Mathlib Docker image:
```bash
# See Dockerfile in project root or use existing lean4-mathlib-simple:latest
docker images | grep lean4-mathlib-simple
```

## Implementation Status

### ✓ Complete

- Schema definitions (CheckRequest/Result, StateBlob)
- LeanWorker Ray actor with JSON protocol communication
- LeanRouter routing logic
- StateStore CAS implementation
- State blob pickling/unpickling (pickleTo/unpickleEnvFrom)
- Error parsing from Lean JSON output
- Test suite (basic + Docker integration)

### ✓ Recently Completed

- [x] Actual Lean JSON protocol communication
- [x] State blob pickling/unpickling
- [x] Error parsing from Lean output
- [x] Integration test with real Lean execution (Docker)

### TODO (Phase 2)

- [ ] LRU header cache eviction
- [ ] Resource limits enforcement (timeout, memory)
- [ ] Batch check API
- [ ] Metrics export

### TODO (Phase 3)

- [ ] Firecracker sandbox integration
- [ ] Snapshot pools for <1s mathlib load
- [ ] Autoscaling based on queue depth

## References

- **Master Spec**: `docs_tmp/related_projects/EVOLAKE_ATP_MASTER_SPEC.md`
- **Implementation Summary**: `docs_tmp/related_projects/LEAN_REPL_IMPLEMENTATION_SUMMARY.md`
- **Lean4 Docs**: https://lean-lang.org/lean4/doc/
- **Lean REPL**: https://github.com/leanprover-community/repl

## License

Same as BreadBoard project.
