from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.rlm.primitives import (
    BlobStore,
    build_budget_limits,
    can_start_subcall,
    consume_subcall,
    extract_usage_metrics,
    init_budget_state,
    is_rlm_enabled,
)


def test_rlm_enabled_flag_default_false() -> None:
    assert is_rlm_enabled({}) is False
    assert is_rlm_enabled({"features": {}}) is False
    assert is_rlm_enabled({"features": {"rlm": {"enabled": False}}}) is False
    assert is_rlm_enabled({"features": {"rlm": {"enabled": True}}}) is True


def test_blob_store_put_get_search(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    source = workspace / "alpha.txt"
    source.write_text("line one\nline two needle\nline three needle\n", encoding="utf-8")

    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "blob_store": {
                    "root": ".breadboard/rlm_blobs",
                    "max_total_bytes": 10_000,
                    "max_blob_bytes": 2_000,
                    "mvi_excerpt_bytes": 32,
                },
            }
        }
    }
    store = BlobStore(str(workspace), cfg)

    one = store.put_content(content="hello needle world", metadata={"source": "unit"})
    assert str(one["blob_id"]).startswith("sha256:")

    two = store.put_file_slice(path="alpha.txt", start_line=2, end_line=3, branch_id="b.root")
    assert two["path"] == "alpha.txt"
    assert two["start_line"] == 2
    assert two["end_line"] == 3

    preview = store.get(one["blob_id"], preview_bytes=8)
    assert preview["blob_id"] == one["blob_id"]
    assert preview["truncated"] is True
    assert "hello ne" in preview["preview"]

    hits = store.search(blob_ids=[one["blob_id"], two["blob_id"]], query="needle", max_results=10)
    assert hits["query"] == "needle"
    assert len(hits["results"]) >= 2


def test_budget_limits_and_consumption() -> None:
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "budget": {
                    "max_depth": 2,
                    "max_subcalls": 2,
                    "max_total_tokens": 100,
                    "max_total_cost_usd": 1.0,
                    "max_wallclock_seconds": 10,
                    "per_branch": {
                        "max_subcalls": 1,
                        "max_total_tokens": 80,
                        "max_total_cost_usd": 0.8,
                    },
                }
            }
        }
    }
    limits = build_budget_limits(cfg)
    state = init_budget_state(None)

    ok, reason = can_start_subcall(state=state, limits=limits, branch_id="b.root", depth=1)
    assert ok is True
    assert reason is None

    tokens, cost = extract_usage_metrics({"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.2})
    state = consume_subcall(state=state, branch_id="b.root", depth=1, usage_tokens=tokens, usage_cost_usd=cost)

    # Per-branch max_subcalls is 1; next call on same branch is blocked.
    ok2, reason2 = can_start_subcall(state=state, limits=limits, branch_id="b.root", depth=1)
    assert ok2 is False
    assert reason2 == "branch_subcall_limit_exceeded"

