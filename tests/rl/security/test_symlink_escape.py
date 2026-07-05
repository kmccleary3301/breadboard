from __future__ import annotations

from breadboard.rl.security import scan_symlink_escapes


def test_symlink_escape_is_detected(tmp_path) -> None:
    (tmp_path / "escape").symlink_to("/etc/passwd")

    findings = scan_symlink_escapes(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "symlink_escape"


def test_internal_symlink_is_allowed(tmp_path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("ok\n", encoding="utf-8")
    (tmp_path / "inside").symlink_to(target)

    assert scan_symlink_escapes(tmp_path) == []
