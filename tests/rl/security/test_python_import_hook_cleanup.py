from __future__ import annotations

from breadboard.rl.security import scan_python_import_hooks


def test_python_import_hook_files_are_detected(tmp_path) -> None:
    (tmp_path / "sitecustomize.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "usercustomize.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "poison.pth").write_text("import x\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text("pytest_plugins=[]\n", encoding="utf-8")

    finding_ids = {item.finding_id for item in scan_python_import_hooks(tmp_path)}

    assert {"sitecustomize", "usercustomize", "pth_injection", "conftest_outside_tests"} <= finding_ids


def test_conftest_under_tests_is_allowed(tmp_path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "conftest.py").write_text("pass\n", encoding="utf-8")

    assert scan_python_import_hooks(tmp_path) == []
