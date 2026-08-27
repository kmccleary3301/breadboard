from breadboard_engine.compat.request_body_contract import (
    _normalize_compat_workspace_paths,
)


def test_compat_workspace_paths_use_declared_root() -> None:
    payload = {
        "instructions": "run in /private/tmp/bb_compat/opencode",
        "nested": ["/private/tmp/bb_compat/opencode/file.txt", 3],
    }

    assert _normalize_compat_workspace_paths(
        payload,
        resolved_root="/private/tmp/bb_compat",
        declared_root="/tmp/bb_compat",
    ) == {
        "instructions": "run in /tmp/bb_compat/opencode",
        "nested": ["/tmp/bb_compat/opencode/file.txt", 3],
    }
