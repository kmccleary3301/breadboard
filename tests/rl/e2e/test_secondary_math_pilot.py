from __future__ import annotations

from pathlib import Path

from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.export import build_projection_manifest
from breadboard.rl.replay import compare_replay_parity
from breadboard.rl.session import create_local_session
from breadboard.rl.trace import build_graph_from_session_events, validate_graph_invariants


REPO_ROOT = Path(__file__).resolve().parents[3]
MATH_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "math_console_toy" / "env_package.yaml"


def test_math_console_toy_env_package_validates() -> None:
    package = load_env_package(MATH_TOY)

    assert package.package_id == "bb.math_console_toy.v1alpha"
    assert package.runtime.backend == "local_process"


def test_math_console_toy_lifecycle_replay_and_projection_proof() -> None:
    package = load_env_package(MATH_TOY)
    session = create_local_session(package, "math_toy_001")
    session.reset()
    session.step({"tool": "submit_answer", "answer": "42"})
    evaluation = session.evaluate()
    graph = build_graph_from_session_events(
        graph_id="math_toy_001.graph",
        session_id=session.session_id,
        events=session.events,
    )
    projection = build_projection_manifest(
        graph=graph,
        target_format="math_console_jsonl_probe",
        preserved_fields=["task_id", "reward", "event_id"],
        lost_fields=["full_python_console_transcript"],
        included_node_kinds={"step", "evaluate"},
    )
    replay = compare_replay_parity(graph, graph)

    assert evaluation.reward == 1.0
    assert validate_graph_invariants(graph) == []
    assert replay.passed is True
    assert projection.lost_fields == ["full_python_console_transcript"]
    assert projection.metadata["canonical_truth"] == "breadboard_graph_replay_runtime"
