"""R-0B2/R-0B3: version-aware dotted-module and repo-path resolution."""

from __future__ import annotations

import importlib

from agentic_coder_prototype.compat import legacy_names as ln


class TestDottedTranslation:
    def test_legacy_prefix_translates_to_canonical(self):
        assert (
            ln.canonical_dotted("agentic_coder_prototype.compilation.effective_config_graph")
            == f"{ln.CANONICAL_PACKAGE}.compilation.effective_config_graph"
        )

    def test_bare_package_name_translates(self):
        assert ln.canonical_dotted("agentic_coder_prototype") == ln.CANONICAL_PACKAGE

    def test_whole_segment_match_only(self):
        assert ln.canonical_dotted("agentic_coder_prototype_ext.x") == "agentic_coder_prototype_ext.x"

    def test_unknown_prefix_passes_through(self):
        assert ln.canonical_dotted("breadboard.rl.phase3") == "breadboard.rl.phase3"

    def test_idempotent(self):
        once = ln.canonical_dotted("agentic_coder_prototype.compat")
        assert ln.canonical_dotted(once) == once

    def test_round_trip(self):
        canonical = f"{ln.CANONICAL_PACKAGE}.compilation.primitive_records"
        assert ln.canonical_dotted(ln.legacy_dotted(canonical)) == canonical

    def test_non_string_passthrough(self):
        assert ln.canonical_dotted("") == ""
        assert ln.canonical_dotted(None) is None


class TestRepoPathTranslation:
    def test_legacy_repo_path_translates(self):
        assert (
            ln.canonical_repo_path("agentic_coder_prototype/compilation/effective_config_graph.py")
            == f"{ln.CANONICAL_PACKAGE}/compilation/effective_config_graph.py"
        )

    def test_whole_component_match_only(self):
        assert ln.canonical_repo_path("scripts/agentic_coder_prototype/x.py") == (
            "scripts/agentic_coder_prototype/x.py"
        )

    def test_windows_separators_normalized(self):
        assert ln.canonical_repo_path("agentic_coder_prototype\\compat\\legacy_names.py") == (
            f"{ln.CANONICAL_PACKAGE}/compat/legacy_names.py"
        )

    def test_round_trip(self):
        canonical = f"{ln.CANONICAL_PACKAGE}/run_logging/run_logger.py"
        assert ln.canonical_repo_path(ln.legacy_repo_path(canonical)) == canonical


class TestResolution:
    def test_resolve_module_identity(self):
        legacy = ln.resolve_module("agentic_coder_prototype.compilation.effective_config_graph")
        canonical = importlib.import_module(
            f"{ln.CANONICAL_PACKAGE}.compilation.effective_config_graph"
        )
        assert legacy is canonical

    def test_resolve_callable_colon_form(self):
        fn = ln.resolve_callable(
            "agentic_coder_prototype.compilation.effective_config_graph:graph_content_hash"
        )
        assert callable(fn)

    def test_resolve_callable_dot_form(self):
        fn = ln.resolve_callable(
            "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_capability_registry"
        )
        assert callable(fn)

    def test_resolve_callable_module_only(self):
        mod = ln.resolve_callable("agentic_coder_prototype.compat.legacy_names")
        assert mod is ln

    def test_missing_module_raises(self):
        try:
            ln.resolve_callable("agentic_coder_prototype.no_such_module_xyz.attr")
        except ModuleNotFoundError:
            pass
        else:
            raise AssertionError("expected ModuleNotFoundError")
