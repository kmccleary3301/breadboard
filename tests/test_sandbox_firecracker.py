"""Tests for Firecracker sandbox implementation.

These tests verify the FirecrackerSandboxV2 interface matches the existing
sandbox implementations and falls back gracefully when Firecracker isn't available.
"""

import os
import tempfile
import uuid
from pathlib import Path

import pytest
import ray

from breadboard.sandbox_firecracker import (
    FirecrackerSandboxV2,
    FirecrackerConfig,
    FirecrackerVMManager,
    check_firecracker_prerequisites,
    create_firecracker_sandbox,
)
from breadboard.sandbox_driver import create_sandbox, SandboxLaunchSpec


# Check if Ray is available
def ray_available() -> bool:
    try:
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True)
        return True
    except Exception:
        return False


SKIP_RAY = not ray_available()


class TestFirecrackerConfig:
    """Test FirecrackerConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = FirecrackerConfig()

        assert config.vcpu_count == 2
        assert config.mem_size_mib == 512
        assert config.network_mode == "none"
        assert config.enable_vsock is True

    def test_config_from_env(self, monkeypatch):
        """Test configuration from environment variables."""
        monkeypatch.setenv("FIRECRACKER_VCPUS", "4")
        monkeypatch.setenv("FIRECRACKER_MEM_MIB", "1024")
        monkeypatch.setenv("FIRECRACKER_NETWORK", "tap")

        config = FirecrackerConfig.from_env()

        assert config.vcpu_count == 4
        assert config.mem_size_mib == 1024
        assert config.network_mode == "tap"

    def test_custom_config(self):
        """Test custom configuration."""
        config = FirecrackerConfig(
            vcpu_count=8,
            mem_size_mib=2048,
            use_jailer=True,
        )

        assert config.vcpu_count == 8
        assert config.mem_size_mib == 2048
        assert config.use_jailer is True


class TestPrerequisiteChecks:
    """Test Firecracker prerequisite checking."""

    def test_check_prerequisites(self):
        """Test prerequisite checking function."""
        results = check_firecracker_prerequisites()

        assert "ready" in results
        assert "checks" in results
        assert "issues" in results

        # Verify all expected checks are present
        assert "kvm" in results["checks"]
        assert "firecracker_bin" in results["checks"]
        assert "kernel" in results["checks"]
        assert "rootfs" in results["checks"]

    def test_prerequisites_report_issues(self):
        """Test that missing prerequisites are reported."""
        results = check_firecracker_prerequisites()

        # In most test environments, Firecracker won't be fully set up
        if not results["ready"]:
            assert len(results["issues"]) > 0


class TestVMManager:
    """Test FirecrackerVMManager."""

    def test_manager_creation(self):
        """Test creating a VM manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = FirecrackerConfig()
            manager = FirecrackerVMManager(config, tmpdir)

            assert manager.workspace == Path(tmpdir).resolve()
            assert manager.vm is None

    def test_vm_creation(self):
        """Test creating a VM instance (without starting)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = FirecrackerConfig()
            manager = FirecrackerVMManager(config, tmpdir)

            vm = manager.create_vm()

            assert vm is not None
            assert vm.vm_id.startswith("fc-")
            assert vm.state == "created"
            assert vm.socket_path is not None


@pytest.mark.skipif(SKIP_RAY, reason="Ray not available")
class TestFirecrackerSandboxV2:
    """Test FirecrackerSandboxV2 Ray actor."""

    @pytest.fixture(autouse=True)
    def setup_ray(self):
        """Ensure Ray is initialized."""
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True)
        yield

    def test_sandbox_creation(self):
        """Test creating a Firecracker sandbox."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = FirecrackerSandboxV2.remote(
                image="ubuntu-22.04",
                session_id=f"test-{uuid.uuid4()}",
                workspace=tmpdir,
            )

            # Verify basic methods work
            session_id = ray.get(sandbox.get_session_id.remote())
            workspace = ray.get(sandbox.get_workspace.remote())

            assert session_id.startswith("test-")
            assert workspace == tmpdir

    def test_sandbox_file_operations(self):
        """Test that file operations work (inherited from DevSandboxV2)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = FirecrackerSandboxV2.remote(
                image="ubuntu-22.04",
                session_id=f"test-{uuid.uuid4()}",
                workspace=tmpdir,
            )

            # Write a file
            result = ray.get(sandbox.write_text.remote("test.txt", "hello world"))
            assert result["ok"] is True

            # Read it back
            result = ray.get(sandbox.read_text.remote("test.txt"))
            assert result["content"] == "hello world"

            # Check exists
            exists = ray.get(sandbox.exists.remote("test.txt"))
            assert exists is True

            # Stat
            stat = ray.get(sandbox.stat.remote("test.txt"))
            assert stat["exists"] is True
            assert stat["type"] == "file"

    def test_sandbox_run_shell_fallback(self):
        """Test that run_shell falls back to subprocess when Firecracker unavailable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = FirecrackerSandboxV2.remote(
                image="ubuntu-22.04",
                session_id=f"test-{uuid.uuid4()}",
                workspace=tmpdir,
            )

            # This should fall back to subprocess since Firecracker isn't set up
            result = ray.get(sandbox.run_shell.remote("echo hello", stream=False))

            # Should have standard output format
            assert "exit" in result
            assert "stdout" in result
            assert "stderr" in result

            # If it fell back, should execute successfully
            if result.get("_firecracker_placeholder"):
                # Firecracker placeholder response
                assert result["exit"] == 0
            else:
                # Subprocess fallback
                assert result["exit"] == 0
                assert "hello" in result["stdout"]

    def test_sandbox_streaming_output(self):
        """Test streaming output format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = FirecrackerSandboxV2.remote(
                image="ubuntu-22.04",
                session_id=f"test-{uuid.uuid4()}",
                workspace=tmpdir,
            )

            result = ray.get(sandbox.run_shell.remote("echo line1; echo line2", stream=True))

            # Should be a list with adaptive prefix
            assert isinstance(result, list)
            assert len(result) >= 2  # At least prefix and final payload

            # Last item should be the result dict
            assert isinstance(result[-1], dict)
            assert "exit" in result[-1]


@pytest.mark.skipif(SKIP_RAY, reason="Ray not available")
class TestSandboxDriverIntegration:
    """Test Firecracker sandbox integration with sandbox_driver."""

    @pytest.fixture(autouse=True)
    def setup_ray(self):
        """Ensure Ray is initialized."""
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True)
        yield

    def test_create_via_driver(self):
        """Test creating Firecracker sandbox via driver factory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spec = SandboxLaunchSpec(
                driver="firecracker",
                image="ubuntu-22.04",
                workspace=tmpdir,
                session_id=f"test-{uuid.uuid4()}",
            )

            sandbox = create_sandbox(spec)

            # Verify it's a valid sandbox actor
            session_id = ray.get(sandbox.get_session_id.remote())
            assert session_id == spec.session_id

    def test_driver_options_passed(self):
        """Test that driver options are passed correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spec = SandboxLaunchSpec(
                driver="firecracker",
                image="ubuntu-22.04",
                workspace=tmpdir,
                session_id=f"test-{uuid.uuid4()}",
                driver_options={
                    "vcpu_count": 4,
                    "mem_size_mib": 1024,
                },
            )

            sandbox = create_sandbox(spec)

            # Should create successfully
            session_id = ray.get(sandbox.get_session_id.remote())
            assert session_id == spec.session_id


class TestInterfaceCompatibility:
    """Test that FirecrackerSandboxV2 has the same interface as DockerSandboxV2."""

    def test_common_methods_exist(self):
        """Verify all expected methods exist on the class."""
        expected_methods = [
            # Session
            "get_session_id",
            "get_workspace",
            # File I/O
            "put",
            "get",
            "read_text",
            "write_text",
            "exists",
            "stat",
            # Directory
            "ls",
            "glob",
            # Search
            "grep",
            # Execution
            "run",
            "run_shell",
            # Editing
            "edit_replace",
            "multiedit",
            # VCS
            "vcs",
        ]

        for method in expected_methods:
            assert hasattr(FirecrackerSandboxV2, method), f"Missing method: {method}"

    def test_constructor_signature(self):
        """Verify constructor accepts standard arguments."""
        import inspect

        # Ray actors wrap the class, so we need to check the _original class
        # or just verify the actor can be instantiated with standard args
        # The @ray.remote decorator changes the signature to (*args, **kwargs)

        # Instead, verify that the base class has the expected signature
        from breadboard.sandbox_v2 import DevSandboxV2Base

        sig = inspect.signature(DevSandboxV2Base.__init__)
        params = list(sig.parameters.keys())

        # Standard parameters (matching Docker sandbox)
        assert "image" in params
        assert "session_id" in params
        assert "workspace" in params
        assert "lsp_actor" in params


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
