from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import breadboard_engine.optimize as live_optimize
import breadboard_engine.rl as live_rl
import breadboard_engine.search as live_search
from breadboard_engine.corpora import optimize as optimize_corpus
from breadboard_engine.corpora import rl as rl_corpus
from breadboard_engine.corpora import search as search_corpus


ROOT = Path(__file__).resolve().parents[1]
CORPORA_BUILDERS = set(optimize_corpus.__all__)
RL_BUILDERS = set(rl_corpus.__all__)
SEARCH_BUILDERS = set(search_corpus.__all__)
SEARCH_SURFACE = {
    name
    for name, value in vars(search_corpus).items()
    if not name.startswith("_") and callable(value)
}
RETIRED_SEARCH_MODULES = (
    "atp_production",
    "build_cache",
    "consumer_kits",
    "consumerization",
    "cross_execution",
    "deployment_readiness",
    "domain_pilots",
    "examples",
    "kits",
    "offline_convergence",
    "offline_stochasticity",
    "operator_views",
    "platform_publication",
    "research_controls",
    "study",
)


def _is_corpus_import(node: ast.Import | ast.ImportFrom) -> bool:
    if isinstance(node, ast.Import):
        modules = (alias.name for alias in node.names)
    else:
        modules = (node.module or "",)
    return any("corpora" in module.split(".") for module in modules)



def test_live_optimize_import_is_non_eager() -> None:
    code = (
        "import sys\n"
        "import breadboard_engine.optimize\n"
        "assert not any(name.startswith('breadboard_engine.corpora') "
        "for name in sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_live_search_import_is_non_eager() -> None:
    code = (
        "import sys\n"
        "import breadboard_engine.search\n"
        "assert not any(name.startswith('breadboard_engine.corpora') "
        "for name in sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_live_rl_import_is_non_eager() -> None:
    code = (
        "import sys\n"
        "import breadboard_engine.rl\n"
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
    assert importlib.util.find_spec("breadboard_engine.corpora.optimize.examples") is not None


def test_rl_corpus_has_explicit_builder_boundary() -> None:
    assert len(RL_BUILDERS) == 60
    assert all(callable(getattr(rl_corpus, name)) for name in RL_BUILDERS)
    assert RL_BUILDERS.isdisjoint(live_rl.__all__)
    assert all(not hasattr(live_rl, name) for name in RL_BUILDERS)
    assert importlib.util.find_spec("breadboard_engine.rl.examples") is None
    assert importlib.util.find_spec("breadboard_engine.corpora.rl.examples") is not None


def test_search_corpus_has_explicit_builder_boundary() -> None:
    assert len(SEARCH_BUILDERS) == 373
    assert len(SEARCH_SURFACE) == 397
    assert all(callable(getattr(search_corpus, name)) for name in SEARCH_BUILDERS)
    assert SEARCH_BUILDERS.isdisjoint(live_search.__all__)
    assert all(not hasattr(live_search, name) for name in SEARCH_SURFACE)
    assert all(
        importlib.util.find_spec(f"breadboard_engine.search.{module}") is None
        for module in RETIRED_SEARCH_MODULES
    )
    assert importlib.util.find_spec("breadboard_engine.corpora.search.examples") is not None


def test_representative_optimize_builders_execute() -> None:
    example = optimize_corpus.build_codex_dossier_example()
    staged = optimize_corpus.build_staged_backend_comparison_example()
    verifier = optimize_corpus.build_coding_overlay_verifier_experiment_example()
    assert example["target"].target_id == "target.codex_dossier.tool_render"
    assert staged["backend_comparison"]
    assert verifier["comparison_result"]


def test_representative_rl_builder_executes() -> None:
    example = rl_corpus.build_rl_v1_contract_pack_example()
    assert example["run"].search_id == "search.pacore_mvp.v1"


def test_representative_search_builders_execute() -> None:
    example = search_corpus.build_dag_v4_tot_v2_packet()
    pilot = search_corpus.build_search_atp_domain_pilot()
    matrix = search_corpus.build_search_offline_convergence_matrix_packet()
    assert example["run"].search_id == "search.dag_v4.tot_v2"
    assert pilot["pilot_packet"].pilot_id == "search.domain.atp.pilot.v1"
    assert matrix.packet_id == "search.platform.phase8.offline_convergence_matrix.v1"


def test_live_sources_do_not_import_corpora() -> None:
    package_root = ROOT / "breadboard_engine"

    for path in package_root.rglob("*.py"):
        if "corpora" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and _is_corpus_import(node)
        ]
        assert not imports, f"{path} imports corpus: {imports}"