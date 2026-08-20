"""R-0B4/R-0B5: frozen P3/pinned/preserve-byte artifacts hold their recorded bytes.

Fixtures are generated once by ``scripts/dev/make_rename_compat_fixtures.py``
on the pre-rename tree. These tests assert against those *recorded* values, so
any rewrite of a preserved artifact - before or after the rename - fails here.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from agentic_coder_prototype.compat import legacy_names as ln

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(REPO_ROOT))
from scripts.e4_parity import lane_runtime  # noqa: E402

P3 = json.loads((FIXTURES / "recorded_p3_provenance.json").read_text(encoding="utf-8"))
PINNED = json.loads((FIXTURES / "recorded_pinned_artifacts.json").read_text(encoding="utf-8"))
PRESERVE = json.loads(
    (FIXTURES / "recorded_preserve_byte_digests.json").read_text(encoding="utf-8")
)


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class TestP3Configs:
    def test_exactly_eight_configs(self):
        assert len(P3) == 8

    @pytest.mark.parametrize("config_id", sorted(P3))
    def test_config_bytes_identical(self, config_id):
        record = P3[config_id]
        path = REPO_ROOT / ln.canonical_repo_path(record["config_path"])
        assert sha256_file(path) == record["config_sha256"]

    @pytest.mark.parametrize("config_id", sorted(P3))
    def test_freeze_row_hash_unchanged(self, config_id):
        manifest = yaml.safe_load(
            (REPO_ROOT / "config/e4_target_freeze_manifest.yaml").read_text(encoding="utf-8")
        )
        row = manifest["e4_configs"][config_id]
        recomputed = lane_runtime.sha256_text(
            lane_runtime.canonical_json(
                {"row_id": config_id, "row": row}, separators_style="compact"
            )
        )
        assert recomputed == P3[config_id]["freeze_row_hash"]

    @pytest.mark.parametrize("config_id", sorted(P3))
    def test_dotted_ref_resolution_matches_recorded_baseline(self, config_id):
        """No drift: refs that resolved pre-rename must still resolve to the
        canonical package; refs already dangling (p3_7) must not silently
        start resolving to something else."""
        record = P3[config_id]
        try:
            target = ln.resolve_callable(record["dotted_ref"])
        except (ModuleNotFoundError, AttributeError):
            assert record["dotted_ref_resolves"] is False
            return
        assert record["dotted_ref_resolves"] is True
        if record["ref_kind"] == "callable":
            assert callable(target)
        else:
            assert hasattr(target, "__name__")
        # The resolved object must live in the canonical package.
        module_name = getattr(target, "__module__", getattr(target, "__name__", ""))
        assert module_name.split(".")[0] == ln.CANONICAL_PACKAGE


class TestPinnedArtifacts:
    @pytest.mark.parametrize(
        "path", sorted(p for p, v in PINNED.items() if isinstance(v, str))
    )
    def test_pinned_file_unchanged(self, path):
        assert sha256_file(REPO_ROOT / path) == PINNED[path]

    def test_immutable_zip_unchanged(self):
        archive_path, record = next(
            (p, v) for p, v in PINNED.items() if isinstance(v, dict)
        )
        assert sha256_file(REPO_ROOT / archive_path) == record["sha256"]
        with zipfile.ZipFile(REPO_ROOT / archive_path) as zf:
            names = sorted(zf.namelist())
            assert names == sorted(record["members"])
            for name in names:
                digest = "sha256:" + hashlib.sha256(zf.read(name)).hexdigest()
                assert digest == record["members"][name], name


class TestPreserveByteFiles:
    def test_digest_corpus_nonempty(self):
        assert len(PRESERVE) >= 20

    @pytest.mark.parametrize("path", sorted(PRESERVE))
    def test_preserved_file_unchanged(self, path):
        assert sha256_file(REPO_ROOT / path) == PRESERVE[path]
