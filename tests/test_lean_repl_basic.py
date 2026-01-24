"""Basic tests for Lean REPL service components.

These tests verify the schema, worker, router, and state store work correctly.
"""

import tempfile
from pathlib import Path

import pytest
import ray

from breadboard.lean_repl import (
    CheckLimits,
    CheckMode,
    CheckRequest,
    CheckResult,
    StateBlob,
    StateStore,
    LeanWorker,
    LeanRouter,
    create_lean_worker,
    create_lean_router,
    compute_header_hash,
    CANONICAL_MATHLIB_HEADER_HASH,
)


class TestSchema:
    """Test schema data structures."""

    def test_check_request_creation(self):
        """Test creating a CheckRequest."""
        req = CheckRequest(
            header_hash=CANONICAL_MATHLIB_HEADER_HASH,
            commands=["def foo := 2", "#check foo"],
        )

        assert req.header_hash == CANONICAL_MATHLIB_HEADER_HASH
        assert len(req.commands) == 2
        assert req.mode == CheckMode.COMMAND
        assert req.limits.timeout_s == 30.0
        assert "errors" in req.want

    def test_check_request_with_custom_limits(self):
        """Test CheckRequest with custom limits."""
        limits = CheckLimits(timeout_s=60.0, memory_mb=8192)
        req = CheckRequest(
            header_hash="test_hash",
            commands=["def bar := 3"],
            limits=limits,
        )

        assert req.limits.timeout_s == 60.0
        assert req.limits.memory_mb == 8192

    def test_check_result_creation(self):
        """Test creating a CheckResult."""
        result = CheckResult(
            request_id="test-123",
            success=True,
            messages=["foo : Nat"],
        )

        assert result.request_id == "test-123"
        assert result.success
        assert len(result.messages) == 1
        assert not result.crashed
        assert not result.timeout

    def test_state_blob_serialization(self):
        """Test StateBlob JSON serialization."""
        blob = StateBlob(
            format_version=1,
            lean_toolchain="leanprover/lean4:v4.14.0",
            mathlib_rev="v4.14.0",
            header_hash=CANONICAL_MATHLIB_HEADER_HASH,
            kind="env",
            pickle_bytes_b64="dGVzdA==",  # base64 of "test"
        )

        # Serialize
        json_str = blob.to_json()
        assert "lean_toolchain" in json_str
        assert "mathlib_rev" in json_str

        # Deserialize
        blob2 = StateBlob.from_json(json_str)
        assert blob2.lean_toolchain == blob.lean_toolchain
        assert blob2.mathlib_rev == blob.mathlib_rev
        assert blob2.header_hash == blob.header_hash
        assert blob2.kind == blob.kind

    def test_state_blob_compatibility(self):
        """Test StateBlob compatibility checking."""
        blob = StateBlob(
            format_version=1,
            lean_toolchain="leanprover/lean4:v4.14.0",
            mathlib_rev="v4.14.0",
            header_hash=CANONICAL_MATHLIB_HEADER_HASH,
            kind="env",
            pickle_bytes_b64="dGVzdA==",
        )

        # Same toolchain/mathlib/header -> compatible
        assert blob.is_compatible_with(
            "leanprover/lean4:v4.14.0",
            "v4.14.0",
            CANONICAL_MATHLIB_HEADER_HASH,
        )

        # Different toolchain -> incompatible
        assert not blob.is_compatible_with(
            "leanprover/lean4:v4.15.0",
            "v4.14.0",
            CANONICAL_MATHLIB_HEADER_HASH,
        )

        # Different mathlib -> incompatible
        assert not blob.is_compatible_with(
            "leanprover/lean4:v4.14.0",
            "v4.15.0",
            CANONICAL_MATHLIB_HEADER_HASH,
        )

    def test_compute_header_hash(self):
        """Test header hash computation."""
        imports = ["import Mathlib"]
        hash1 = compute_header_hash(imports)
        hash2 = compute_header_hash(imports)

        # Should be deterministic
        assert hash1 == hash2

        # Should be the canonical hash
        assert hash1 == CANONICAL_MATHLIB_HEADER_HASH

        # Different imports -> different hash
        hash3 = compute_header_hash(["import Std"])
        assert hash3 != hash1


class TestStateStore:
    """Test state store operations."""

    def test_state_store_put_get(self):
        """Test storing and retrieving state blobs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)

            # Create test data
            pickle_bytes = b"test pickle data"
            blob = StateBlob(
                format_version=1,
                lean_toolchain="leanprover/lean4:v4.14.0",
                mathlib_rev="v4.14.0",
                header_hash=CANONICAL_MATHLIB_HEADER_HASH,
                kind="env",
                pickle_bytes_b64="",  # Will be filled by put()
            )

            # Store
            ref = store.put(pickle_bytes, blob)
            assert ref.startswith("cas:sha256:")

            # Retrieve
            result = store.get(ref)
            assert result is not None

            retrieved_bytes, retrieved_blob = result
            assert retrieved_bytes == pickle_bytes
            assert retrieved_blob.lean_toolchain == blob.lean_toolchain
            assert retrieved_blob.mathlib_rev == blob.mathlib_rev

    def test_state_store_exists(self):
        """Test checking if blobs exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)

            # Non-existent blob
            assert not store.exists("cas:sha256:nonexistent")

            # Store a blob
            pickle_bytes = b"test"
            blob = StateBlob(
                format_version=1,
                lean_toolchain="leanprover/lean4:v4.14.0",
                mathlib_rev="v4.14.0",
                header_hash=CANONICAL_MATHLIB_HEADER_HASH,
                kind="env",
                pickle_bytes_b64="",
            )
            ref = store.put(pickle_bytes, blob)

            # Should exist now
            assert store.exists(ref)

    def test_state_store_delete(self):
        """Test deleting blobs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)

            # Store a blob
            pickle_bytes = b"test"
            blob = StateBlob(
                format_version=1,
                lean_toolchain="leanprover/lean4:v4.14.0",
                mathlib_rev="v4.14.0",
                header_hash=CANONICAL_MATHLIB_HEADER_HASH,
                kind="env",
                pickle_bytes_b64="",
            )
            ref = store.put(pickle_bytes, blob)
            assert store.exists(ref)

            # Delete
            assert store.delete(ref)
            assert not store.exists(ref)

            # Delete again -> should return False
            assert not store.delete(ref)

    def test_state_store_stats(self):
        """Test getting storage statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(tmpdir)

            stats = store.get_stats()
            assert stats["total_blobs"] == 0
            assert stats["total_size_bytes"] == 0

            # Store some blobs
            for i in range(3):
                pickle_bytes = f"test_{i}".encode()
                blob = StateBlob(
                    format_version=1,
                    lean_toolchain="leanprover/lean4:v4.14.0",
                    mathlib_rev="v4.14.0",
                    header_hash=CANONICAL_MATHLIB_HEADER_HASH,
                    kind="env",
                    pickle_bytes_b64="",
                )
                store.put(pickle_bytes, blob)

            stats = store.get_stats()
            assert stats["total_blobs"] == 3
            assert stats["total_size_bytes"] > 0


@pytest.mark.skipif(not ray.is_initialized(), reason="Ray not initialized")
class TestWorkerAndRouter:
    """Test LeanWorker and LeanRouter integration.

    These tests require Ray to be running.
    """

    @pytest.fixture(autouse=True)
    def setup_ray(self):
        """Initialize Ray if needed."""
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True)
        yield
        # Don't shut down Ray - other tests may need it

    def test_worker_creation(self):
        """Test creating a LeanWorker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = create_lean_worker(workspace=tmpdir, pool_size=2)

            # Get stats
            stats = ray.get(worker.get_stats.remote())
            assert stats["pool_size"] == 2
            assert stats["total_requests"] == 0

            # Cleanup
            ray.get(worker.shutdown.remote())

    def test_router_creation(self):
        """Test creating a LeanRouter."""
        router = create_lean_router()

        # Get stats
        stats = ray.get(router.get_stats.remote())
        assert stats["total_requests"] == 0
        assert stats["worker_count"] == 0

        # Cleanup
        ray.get(router.stop.remote())

    def test_router_with_worker(self):
        """Test router with registered worker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create worker and router
            worker = create_lean_worker(workspace=tmpdir, pool_size=2)
            router = create_lean_router()

            # Register worker
            ray.get(router.register_worker.remote(worker, "worker_1"))

            # Check stats
            stats = ray.get(router.get_stats.remote())
            assert stats["worker_count"] == 1
            assert "worker_1" in stats["workers"]

            # Cleanup
            ray.get(worker.shutdown.remote())
            ray.get(router.stop.remote())

    def test_submit_request_to_router(self):
        """Test submitting a check request through the router."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create worker and router
            worker = create_lean_worker(workspace=tmpdir, pool_size=2)
            router = create_lean_router()
            ray.get(router.register_worker.remote(worker, "worker_1"))

            # Create a request
            request = CheckRequest(
                header_hash=CANONICAL_MATHLIB_HEADER_HASH,
                commands=["def foo := 2"],
            )

            # Submit request (this will return a mock result for now)
            result = ray.get(router.submit_request.remote(request))

            assert isinstance(result, CheckResult)
            assert result.request_id == request.request_id

            # Cleanup
            ray.get(worker.shutdown.remote())
            ray.get(router.stop.remote())


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
