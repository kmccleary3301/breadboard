from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import breadboard_engine.optimize as live_optimize
from breadboard_engine.corpora import optimize as optimize_corpus


ROOT = Path(__file__).resolve().parents[1]
CORPORA_BUILDERS = set(optimize_corpus.__all__)


def test_live_optimize_import_is_non_eager() -> None:
    code = (
        "import sys\n"
        "import breadboard_engine.optimize\n"
        "assert not any(name.startswith('breadboard_engine.corpora') "
        "for name in sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_optimize_corpus_has_explicit_builder_boundary() -> None:
    assert len(CORPORA_BUILDERS) == 72
    assert all(callable(getattr(optimize_corpus, name)) for name in CORPORA_BUILDERS)
    assert CORPORA_BUILDERS.isdisjoint(live_optimize.__all__)
    assert all(not hasattr(live_optimize, name) for name in CORPORA_BUILDERS)
    assert importlib.util.find_spec("breadboard_engine.optimize.examples") is None


def test_representative_optimize_builders_execute() -> None:
    example = optimize_corpus.build_codex_dossier_example()
    staged = optimize_corpus.build_staged_backend_comparison_example()
    verifier = optimize_corpus.build_coding_overlay_verifier_experiment_example()
    assert example["target"].target_id == "target.codex_dossier.tool_render"
    assert staged["backend_comparison"]
    assert verifier["comparison_result"]


def test_live_sources_do_not_eagerly_import_corpora() -> None:
    package_root = ROOT / "breadboard_engine"
    allowed = {package_root / "search" / "cross_execution.py"}
    for path in package_root.rglob("*.py"):
        if "corpora" in path.parts or path in allowed:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and "breadboard_engine.corpora" in ast.unparse(node)
        ]
        assert not imports, f"{path} imports corpus: {imports}"