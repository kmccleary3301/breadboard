import json

from agentic_coder_prototype.api.cli_bridge.session_runner import project_ctree_snapshot_event


def test_ctree_snapshot_event_projection_is_bounded_and_keeps_summary_fields() -> None:
    oversized_prompt_planes = {f"plane-{index}": "x" * 4096 for index in range(64)}
    payload = {
        "snapshot": {
            "schema_version": "0.2",
            "node_count": 37,
            "event_count": 41,
            "last_node": oversized_prompt_planes,
        },
        "compiler": {
            "kind": "htsg_r_preview",
            "schema_version": "0.2",
            "node_count": 37,
            "event_count": 41,
            "prompt_planes": oversized_prompt_planes,
            "hashes": {
                **{f"hash-{index:03d}": "a" * 40 for index in range(64)},
                7: "non-string-key",
            },
        },
        "collapse": {
            "kind": "tranche1_collapse",
            "schema_version": "ctree_collapse_v1",
            "collapsed": True,
            "stage": "FROZEN",
            "rehydration_bundle": oversized_prompt_planes,
        },
    }

    projection = project_ctree_snapshot_event(payload)

    assert len(json.dumps(projection).encode("utf-8")) < 64 * 1024
    assert projection["projection"] == "summary"
    assert projection["snapshot"] == {
        "schema_version": "0.2",
        "node_count": 37,
        "event_count": 41,
    }
    assert projection["compiler"]["kind"] == "htsg_r_preview"
    assert len(projection["compiler"]["hashes"]) == 32
    assert projection["collapse"]["collapsed"] is True
    assert "prompt_planes" not in projection["compiler"]
    assert "rehydration_bundle" not in projection["collapse"]
    assert payload["compiler"]["prompt_planes"] is oversized_prompt_planes


def test_ctree_snapshot_event_projection_accepts_a_direct_snapshot() -> None:
    projection = project_ctree_snapshot_event(
        {
            "schema_version": "0.2",
            "node_count": 3,
            "event_count": 5,
            "last_node": {"private": "detail"},
        }
    )

    assert projection == {
        "projection": "summary",
        "snapshot": {
            "schema_version": "0.2",
            "node_count": 3,
            "event_count": 5,
        },
    }
