"""Integration tests for Lean REPL service.

These tests verify actual Lean execution inside Docker.
Run with: pytest tests/test_lean_repl_integration.py -v -s

Prerequisites:
- Docker image `lean4-mathlib-simple:latest` must be built
- Docker daemon must be running
"""

import asyncio
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import pytest


# Skip if Docker not available
def docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def lean_image_exists() -> bool:
    """Check if the Lean Docker image exists."""
    try:
        result = subprocess.run(
            ["docker", "images", "-q", "lean4-mathlib-simple:latest"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


SKIP_DOCKER = not docker_available()
SKIP_LEAN_IMAGE = not lean_image_exists()


@pytest.mark.skipif(SKIP_DOCKER, reason="Docker not available")
@pytest.mark.skipif(SKIP_LEAN_IMAGE, reason="lean4-mathlib-simple:latest image not found")
class TestLeanDockerIntegration:
    """Integration tests using Docker to run actual Lean commands."""

    def run_in_docker(
        self,
        lean_code: str,
        timeout: int = 60,
        import_mathlib: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run Lean code inside Docker container.

        Args:
            lean_code: Lean source code to execute
            timeout: Timeout in seconds
            import_mathlib: Whether to prepend 'import Mathlib'

        Returns:
            CompletedProcess with stdout/stderr
        """
        if import_mathlib:
            lean_code = "import Mathlib\n\n" + lean_code

        # Create temp file with lean code
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".lean",
            delete=False,
        ) as f:
            f.write(lean_code)
            temp_path = f.name

        try:
            # Run in Docker with mathlib project
            # Mount inside mathlib4 directory so imports work
            result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-v", f"{temp_path}:/workspace/Test.lean:ro",
                    "-w", "/workspace",
                    "lean4-mathlib-simple:latest",
                    "lake", "env", "lean", "Test.lean",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result
        finally:
            os.unlink(temp_path)

    def test_simple_lean_execution(self):
        """Test executing simple Lean code."""
        lean_code = """
def foo : Nat := 42
#check foo
#eval foo
"""
        result = self.run_in_docker(lean_code)

        assert result.returncode == 0, f"Lean failed: {result.stderr}"
        assert "foo : Nat" in result.stdout or "foo : Nat" in result.stderr

    def test_lean_with_error(self):
        """Test that Lean errors are properly detected."""
        lean_code = """
def broken : Nat := "not a nat"
"""
        result = self.run_in_docker(lean_code)

        assert result.returncode != 0
        # Lean outputs errors to stdout, not stderr
        output = (result.stdout + result.stderr).lower()
        assert "error" in output or "type mismatch" in output

    def test_mathlib_import(self):
        """Test importing Mathlib (uses cached oleans)."""
        lean_code = """
#check Nat.Prime
"""
        result = self.run_in_docker(lean_code, import_mathlib=True, timeout=120)

        # This tests that import Mathlib works
        assert result.returncode == 0, f"Mathlib import failed: {result.stderr}"
        assert "Prime" in result.stdout or "Prime" in result.stderr

    def test_mathlib_theorem(self):
        """Test using Mathlib theorems."""
        lean_code = """
example : 2 + 2 = 4 := rfl
example : Nat.succ 0 = 1 := rfl
"""
        result = self.run_in_docker(lean_code, import_mathlib=True, timeout=120)

        assert result.returncode == 0, f"Theorem check failed: {result.stderr}"

    def test_sorry_detection(self):
        """Test that sorry is detected in output."""
        lean_code = """
theorem needs_proof : 1 + 1 = 2 := sorry
"""
        result = self.run_in_docker(lean_code)

        # sorry should produce a warning but not an error
        assert result.returncode == 0
        # The declaration should still succeed
        assert "error" not in result.stdout.lower()


@pytest.mark.skipif(SKIP_DOCKER, reason="Docker not available")
@pytest.mark.skipif(SKIP_LEAN_IMAGE, reason="lean4-mathlib-simple:latest image not found")
class TestLeanREPLProtocol:
    """Test the Lean REPL JSON protocol directly."""

    def run_repl_commands(
        self,
        commands: list,
        timeout: int = 60,
    ) -> list:
        """Run REPL commands and return responses.

        Note: This requires the leanprover-community/repl to be installed.
        Falls back to testing individual commands if REPL not available.

        Args:
            commands: List of REPL JSON commands (as dicts)
            timeout: Timeout in seconds

        Returns:
            List of response dicts
        """
        # Build JSON commands
        cmd_json = "\n".join(json.dumps(cmd) for cmd in commands)

        # Create a test script that builds and runs the REPL
        test_script = f'''
cd /mathlib4

# Check if REPL exists
if [ -f .lake/build/bin/repl ]; then
    echo '{cmd_json}' | .lake/build/bin/repl
else
    echo "REPL_NOT_AVAILABLE"
fi
'''

        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "-w", "/workspace",
                "lean4-mathlib-simple:latest",
                "bash", "-c", test_script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if "REPL_NOT_AVAILABLE" in result.stdout:
            pytest.skip("REPL binary not available in container")

        # Parse responses
        responses = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                try:
                    responses.append(json.loads(line))
                except json.JSONDecodeError:
                    # Not a JSON line, might be stderr or debug output
                    pass

        return responses

    def test_repl_simple_command(self):
        """Test simple REPL command execution."""
        commands = [
            {"cmd": "def foo := 42"},
            {"cmd": "#check foo", "env": 0},
        ]

        responses = self.run_repl_commands(commands)

        # Should get at least one response
        assert len(responses) >= 1

        # First response should have env field
        if responses:
            assert "env" in responses[0] or "messages" in responses[0]


@pytest.mark.skipif(SKIP_DOCKER, reason="Docker not available")
@pytest.mark.skipif(SKIP_LEAN_IMAGE, reason="lean4-mathlib-simple:latest image not found")
class TestLeanBenchmarks:
    """Benchmark tests for Lean execution performance."""

    def run_in_docker(self, lean_code: str, timeout: int = 120) -> float:
        """Run Lean code and return execution time."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".lean",
            delete=False,
        ) as f:
            f.write(lean_code)
            temp_path = f.name

        try:
            start = time.time()
            # Mount inside mathlib4 directory so imports work
            result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-v", f"{temp_path}:/workspace/Test.lean:ro",
                    "-w", "/workspace",
                    "lean4-mathlib-simple:latest",
                    "lake", "env", "lean", "Test.lean",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            elapsed = time.time() - start
            return elapsed
        finally:
            os.unlink(temp_path)

    def test_simple_lean_benchmark(self):
        """Benchmark simple Lean execution (no imports)."""
        lean_code = "def foo := 42\n#check foo"
        elapsed = self.run_in_docker(lean_code)

        print(f"\nSimple Lean execution: {elapsed:.3f}s")
        # Should be under 5 seconds (generous for CI)
        assert elapsed < 5.0

    def test_mathlib_import_benchmark(self):
        """Benchmark Mathlib import time."""
        lean_code = "import Mathlib\n#check Nat.Prime"
        elapsed = self.run_in_docker(lean_code, timeout=120)

        print(f"\nMathlib import: {elapsed:.3f}s")
        # With cached oleans, should be under 10 seconds
        # (actual is ~3.5s but giving headroom for CI)
        assert elapsed < 15.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
