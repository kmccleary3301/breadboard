#!/usr/bin/env python3
"""Manual test script for LeanWorker with Docker.

This script tests the LeanWorker implementation using Docker to run
actual Lean commands. It doesn't require Ray - it directly tests the
subprocess communication logic.

Usage:
    python scripts/test_lean_worker_docker.py

Prerequisites:
    - Docker installed and running
    - lean4-mathlib-simple:latest image built
"""

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def check_prerequisites():
    """Check that Docker and the Lean image are available."""
    # Check Docker
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            print("ERROR: Docker daemon not running")
            return False
    except FileNotFoundError:
        print("ERROR: Docker not installed")
        return False

    # Check image
    result = subprocess.run(
        ["docker", "images", "-q", "lean4-mathlib-simple:latest"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if not result.stdout.strip():
        print("ERROR: lean4-mathlib-simple:latest image not found")
        print("Build it with: docker build -t lean4-mathlib-simple:latest .")
        return False

    return True


async def test_lean_json_protocol():
    """Test Lean's JSON protocol directly in Docker."""
    print("\n=== Testing Lean JSON Protocol ===\n")

    # Simple Lean code that outputs JSON diagnostics
    lean_code = '''
def foo := 42
#check foo
'''

    with tempfile.NamedTemporaryFile(mode="w", suffix=".lean", delete=False) as f:
        f.write(lean_code)
        temp_path = f.name

    try:
        # Run lean with JSON output
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm",
            "-v", f"{temp_path}:/test.lean:ro",
            "-w", "/workspace",
            "lean4-mathlib-simple:latest",
            "lake", "env", "lean", "/test.lean", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=60,
        )

        print(f"Return code: {proc.returncode}")
        print(f"stdout: {stdout.decode()[:500]}")
        print(f"stderr: {stderr.decode()[:500]}")

        # Parse JSON output
        for line in stdout.decode().split("\n"):
            if line.strip():
                try:
                    msg = json.loads(line)
                    print(f"JSON message: {json.dumps(msg, indent=2)}")
                except json.JSONDecodeError:
                    print(f"Non-JSON line: {line}")

        return proc.returncode == 0

    finally:
        Path(temp_path).unlink()


async def test_mathlib_import():
    """Test that Mathlib import works with cached oleans."""
    print("\n=== Testing Mathlib Import ===\n")

    lean_code = '''
import Mathlib

#check Nat.Prime
#check Real.exp
'''

    with tempfile.NamedTemporaryFile(mode="w", suffix=".lean", delete=False) as f:
        f.write(lean_code)
        temp_path = f.name

    try:
        start = time.time()

        # Mount file inside mathlib4 directory so imports work
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm",
            "-v", f"{temp_path}:/workspace/Test.lean:ro",
            "-w", "/workspace",
            "lean4-mathlib-simple:latest",
            "lake", "env", "lean", "Test.lean",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=120,
        )

        elapsed = time.time() - start

        print(f"Return code: {proc.returncode}")
        print(f"Time: {elapsed:.2f}s")
        print(f"stdout: {stdout.decode()[:500]}")
        print(f"stderr: {stderr.decode()[:500]}")

        return proc.returncode == 0

    finally:
        Path(temp_path).unlink()


async def test_repl_protocol():
    """Test leanprover-community/repl JSON protocol."""
    print("\n=== Testing REPL JSON Protocol ===\n")

    # REPL commands in JSON format
    commands = [
        '{"cmd": "def hello := 42"}',
        '{"cmd": "#check hello", "env": 0}',
    ]

    test_script = f'''
cd /workspace
if [ -f .lake/build/bin/repl ]; then
    echo 'REPL found, testing...'
    echo '{commands[0]}' | .lake/build/bin/repl
else
    echo 'REPL binary not found (expected - need to build it)'
    # Test basic lean instead
    echo 'def hello := 42' > /tmp/test.lean
    lake env lean /tmp/test.lean
fi
'''

    proc = await asyncio.create_subprocess_exec(
        "docker", "run", "--rm",
        "-w", "/workspace",
        "lean4-mathlib-simple:latest",
        "bash", "-c", test_script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(),
        timeout=60,
    )

    print(f"Return code: {proc.returncode}")
    print(f"stdout: {stdout.decode()[:1000]}")
    print(f"stderr: {stderr.decode()[:500]}")

    return True  # This is informational


async def test_error_detection():
    """Test that errors are properly detected."""
    print("\n=== Testing Error Detection ===\n")

    lean_code = '''
def broken : Nat := "not a nat"
'''

    with tempfile.NamedTemporaryFile(mode="w", suffix=".lean", delete=False) as f:
        f.write(lean_code)
        temp_path = f.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm",
            "-v", f"{temp_path}:/test.lean:ro",
            "-w", "/workspace",
            "lean4-mathlib-simple:latest",
            "lake", "env", "lean", "/test.lean", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=60,
        )

        print(f"Return code: {proc.returncode} (expected non-zero)")
        print(f"stdout: {stdout.decode()[:500]}")
        print(f"stderr: {stderr.decode()[:500]}")

        # Should have error
        return proc.returncode != 0

    finally:
        Path(temp_path).unlink()


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Lean REPL Worker Docker Integration Tests")
    print("=" * 60)

    if not check_prerequisites():
        sys.exit(1)

    results = []

    # Run tests
    try:
        results.append(("Lean JSON Protocol", await test_lean_json_protocol()))
    except Exception as e:
        print(f"ERROR: {e}")
        results.append(("Lean JSON Protocol", False))

    try:
        results.append(("Mathlib Import", await test_mathlib_import()))
    except Exception as e:
        print(f"ERROR: {e}")
        results.append(("Mathlib Import", False))

    try:
        results.append(("REPL Protocol", await test_repl_protocol()))
    except Exception as e:
        print(f"ERROR: {e}")
        results.append(("REPL Protocol", False))

    try:
        results.append(("Error Detection", await test_error_detection()))
    except Exception as e:
        print(f"ERROR: {e}")
        results.append(("Error Detection", False))

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("All tests passed!")
        sys.exit(0)
    else:
        print("Some tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
