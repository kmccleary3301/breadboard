from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "prepush", ROOT / "scripts/quality/prepush.py"
)
prepush = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepush)


@contextmanager
def in_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class PrepushConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.scratch = Path(self.temp.name)

    def git(self, repo: Path, *args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=repo, text=True, stderr=subprocess.DEVNULL
        ).strip()

    def put(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def commit(self, repo: Path, message: str) -> str:
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", message)
        return self.git(repo, "rev-parse", "HEAD")

    def hook_repository(self) -> tuple[Path, Path, str, str]:
        repo = self.scratch / "repo"
        mirror = self.scratch / "mirror.git"
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.name", "Prepush Test")
        self.git(repo, "config", "user.email", "prepush@example.invalid")
        self.put(repo, "README.md", "base\n")
        base = self.commit(repo, "base")
        subprocess.run(
            ["git", "clone", "--bare", str(repo), str(mirror)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for relative in (
            ".githooks/pre-push",
            ".githooks/prepush-test-map.json",
            ".githooks/README.md",
            "scripts/quality/prepush.py",
        ):
            source = ROOT / relative
            destination = repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        head = self.commit(repo, "install local hook")
        return repo, mirror, base, head

    def run_hook(
        self,
        repo: Path,
        mirror: Path,
        local: str,
        remote: str,
        *,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        updates = input_text or (
            f"refs/heads/topic {local} refs/heads/topic {remote}\n"
        )
        return subprocess.run(
            [str(repo / ".githooks/pre-push"), "origin", str(mirror)],
            cwd=repo,
            input=updates,
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "BB_PREPUSH_PYTHON": sys.executable,
                **(env or {}),
            },
        )

    def test_hook_checks_changed_python_syntax_against_local_mirror(self):
        repo, mirror, base, _ = self.hook_repository()
        self.put(repo, "new_module.py", "answer = 42\n")
        head = self.commit(repo, "add valid Python")

        result = self.run_hook(repo, mirror, head, base)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("python syntax=", result.stdout)
        self.assertIn("no mapped focused tests", result.stdout)

    def test_hook_rejects_invalid_changed_python(self):
        repo, mirror, base, _ = self.hook_repository()
        self.put(repo, "broken.py", "def broken(\n")
        head = self.commit(repo, "add invalid Python")

        result = self.run_hook(repo, mirror, head, base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SyntaxError", result.stderr)

    def test_hook_rejects_dirty_changed_bytes_and_a_head_other_than_checkout(self):
        repo, mirror, base, _ = self.hook_repository()
        self.put(repo, "new_module.py", "answer = 42\n")
        head = self.commit(repo, "add module")
        self.put(repo, "new_module.py", "answer = 43\n")

        dirty = self.run_hook(repo, mirror, head, base)
        other_head = self.run_hook(repo, mirror, base, base)

        self.assertNotEqual(dirty.returncode, 0)
        self.assertIn("uncommitted edits", dirty.stderr)
        self.assertNotEqual(other_head.returncode, 0)
        self.assertIn("check out each pushed head", other_head.stderr)

    def test_skip_flag_bypasses_fast_checks_before_parsing_ref_updates(self):
        repo, mirror, _, head = self.hook_repository()

        result = self.run_hook(
            repo,
            mirror,
            head,
            "not-an-object-id",
            env={"BB_PREPUSH_SKIP": "1"},
            input_text="malformed update\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fast checks skipped (BB_PREPUSH_SKIP=1)", result.stdout)
        self.assertNotIn("security", result.stdout.lower())

    def test_focused_test_file_runs_directly_when_lock_is_unavailable(self):
        test_file = self.put(
            self.scratch,
            "test_probe.py",
            "from pathlib import Path\n"
            "def test_probe():\n"
            "    Path('probe-ran').write_text('yes')\n",
        )
        missing_lock = self.scratch / "missing-lock"

        with (
            in_directory(self.scratch),
            patch.object(prepush, "TEST_LOCK", str(missing_lock)),
            patch.dict(os.environ, {"BB_PREPUSH_PYTHON": sys.executable}),
        ):
            result = prepush.run_tests([test_file.name], 20.0)

        self.assertEqual(result, 0)
        self.assertEqual((self.scratch / "probe-ran").read_text(), "yes")

    def test_focused_test_failure_blocks(self):
        failure_ran = self.scratch / "failure-ran"
        test_file = self.put(
            self.scratch,
            "test_failure.py",
            "from pathlib import Path\n"
            "def test_failure():\n"
            f"    Path({str(failure_ran)!r}).write_text('ran')\n"
            "    assert False, 'focused failure'\n",
        )

        with (
            in_directory(self.scratch),
            patch.object(prepush, "TEST_LOCK", str(self.scratch / "missing-lock")),
            patch.dict(os.environ, {"BB_PREPUSH_PYTHON": sys.executable}),
        ):
            result = prepush.run_tests([test_file.name], 20.0)

        self.assertTrue(failure_ran.is_file())
        self.assertNotEqual(result, 0)

    def test_focused_admission_and_busy_lock_defer_to_ci(self):
        marker = self.scratch / "must-not-run"
        test_file = self.put(
            self.scratch,
            "test_probe.py",
            f"from pathlib import Path\n"
            f"def test_probe():\n"
            f"    Path({str(marker)!r}).write_text('ran')\n",
        )
        output = io.StringIO()
        with (
            in_directory(self.scratch),
            patch.object(prepush, "TEST_LOCK", str(self.scratch / "missing-lock")),
            patch.dict(os.environ, {"BB_PREPUSH_PYTHON": sys.executable}),
            redirect_stdout(output),
        ):
            self.assertEqual(prepush.run_tests([test_file.name], 4.0), 0)
        self.assertIn("FOCUSED-TESTS-NOT-RUN", output.getvalue())
        self.assertFalse(marker.exists())

        busy_lock = self.put(self.scratch, "busy-lock", "#!/bin/sh\nexit 75\n")
        busy_lock.chmod(0o755)
        output = io.StringIO()
        with (
            in_directory(self.scratch),
            patch.object(prepush, "TEST_LOCK", str(busy_lock)),
            patch.dict(os.environ, {"BB_PREPUSH_PYTHON": sys.executable}),
            redirect_stdout(output),
        ):
            self.assertEqual(prepush.run_tests([test_file.name], 20.0), 0)
        self.assertIn("FOCUSED-TESTS-NOT-RUN: lock busy", output.getvalue())
        self.assertFalse(marker.exists())

    def test_focused_spawn_failure_defers_to_ci(self):
        output = io.StringIO()
        missing_python = str(self.scratch / "missing-python")
        with (
            in_directory(self.scratch),
            patch.object(prepush, "TEST_LOCK", str(self.scratch / "missing-lock")),
            patch.dict(os.environ, {"BB_PREPUSH_PYTHON": missing_python}),
            redirect_stdout(output),
        ):
            result = prepush.run_tests(["tests/test_prepush.py"], 20.0)

        self.assertEqual(result, 0)
        self.assertIn("FOCUSED-TESTS-NOT-RUN", output.getvalue())
        self.assertIn(missing_python, output.getvalue())

    def test_focused_timeout_defers_to_ci(self):
        test_file = self.put(
            self.scratch,
            "test_probe.py",
            "def test_probe():\n"
            "    assert True\n",
        )
        slow_lock = self.put(self.scratch, "slow-lock", "#!/bin/sh\nsleep 20\n")
        slow_lock.chmod(0o755)
        output = io.StringIO()

        with (
            in_directory(self.scratch),
            patch.object(prepush, "TEST_LOCK", str(slow_lock)),
            patch.dict(os.environ, {"BB_PREPUSH_PYTHON": sys.executable}),
            redirect_stdout(output),
        ):
            result = prepush.run_tests([test_file.name], 5.1)

        self.assertEqual(result, 0)
        self.assertIn("FOCUSED-TESTS-NOT-RUN: budget expired", output.getvalue())

    def test_deadline_kills_the_owned_process_group(self):
        marker = self.scratch / "child-survived"
        child = (
            "import time; time.sleep(0.4); "
            f"open({str(marker)!r}, 'w').write('alive')"
        )
        parent = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', sys.argv[1]]); "
            "time.sleep(5)"
        )

        result = prepush.run(
            [sys.executable, "-c", parent, child], self.scratch, 0.1
        )
        time.sleep(0.6)

        self.assertIsNone(result)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
