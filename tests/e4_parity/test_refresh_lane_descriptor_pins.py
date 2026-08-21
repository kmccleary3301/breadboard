from __future__ import annotations

import json
from pathlib import Path

from scripts.e4_parity import refresh_lane_descriptor_pins


def test_refresh_pins_updates_v4_catalog_segment_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lane_hash = "sha256:" + "1" * 64
    shared_hash = "sha256:" + "2" * 64
    catalog_hash = "sha256:" + "3" * 64
    catalog_path = tmp_path / "docs/conformance/e4_artifact_catalog.json"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_text(
        json.dumps(
            {
                "integrity": {"stable_entries_hash": catalog_hash},
                "revision": 14,
                "segments": [
                    {
                        "segment_id": "example_lane",
                        "stable_entries_hash": lane_hash,
                    },
                    {
                        "segment_id": "shared",
                        "stable_entries_hash": shared_hash,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    descriptor = tmp_path / "example_lane.yaml"
    descriptor.write_text(
        """lane_id: example_lane
packet:
  \"catalog_binding\": {
    \"catalog_hash\": \"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",
    \"catalog_revision\": 12,
    \"segment_hash\": \"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",
    \"shared_segment_hash\": \"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc\"
  }
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(refresh_lane_descriptor_pins, "ROOT", tmp_path)

    stale = refresh_lane_descriptor_pins.refresh_pins_once(descriptor, ())

    text = descriptor.read_text(encoding="utf-8")
    assert f'"catalog_hash": "{catalog_hash}"' in text
    assert '"catalog_revision": 14' in text
    assert f'"segment_hash": "{lane_hash}"' in text
    assert f'"shared_segment_hash": "{shared_hash}"' in text
    assert stale == [
        "catalog_binding.catalog_hash",
        "catalog_binding.catalog_revision",
        "catalog_binding.segment_hash",
        "catalog_binding.shared_segment_hash",
    ]


def test_workspace_evidence_wins_over_checkout_local_shadow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    checkout_evidence = repo_root / "docs_tmp/phase_15/ledger.json"
    workspace_evidence = tmp_path / "docs_tmp/phase_15/ledger.json"
    checkout_evidence.parent.mkdir(parents=True)
    workspace_evidence.parent.mkdir(parents=True)
    checkout_evidence.write_text("stale", encoding="utf-8")
    workspace_evidence.write_text("governed", encoding="utf-8")
    monkeypatch.setattr(refresh_lane_descriptor_pins, "ROOT", repo_root)

    resolved = refresh_lane_descriptor_pins._resolve_pin_path(
        "docs_tmp/phase_15/ledger.json"
    )

    assert resolved == workspace_evidence


def test_refresh_uses_migrated_payload_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lane_id = "migrated_lane"
    lane_dir = tmp_path / "config/e4_lanes"
    lane_dir.mkdir(parents=True)
    manifest_path = lane_dir / f"{lane_id}.manifest.yaml"
    payload_path = lane_dir / f"{lane_id}.payloads.yaml"
    manifest_path.write_text("manifest", encoding="utf-8")
    payload_path.write_text("payload", encoding="utf-8")
    refreshed: list[Path] = []
    compiled: list[Path] = []
    monkeypatch.setattr(refresh_lane_descriptor_pins, "ROOT", tmp_path)
    monkeypatch.setattr(
        refresh_lane_descriptor_pins,
        "refresh_pins_once",
        lambda path, prefixes: refreshed.append(path) or [],
    )
    monkeypatch.setattr(
        refresh_lane_descriptor_pins,
        "refresh_migrated_lane",
        lambda path: compiled.append(path) or 0,
    )
    monkeypatch.setattr(
        refresh_lane_descriptor_pins.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Completed", (), {"stdout": '{"ok": true}', "stderr": ""}
        )(),
    )

    report = refresh_lane_descriptor_pins.refresh(lane_id, max_iterations=1)

    assert refreshed == [payload_path]
    assert compiled == [manifest_path]
    assert report["yaml"] == f"config/e4_lanes/{lane_id}.payloads.yaml"
    assert report["history"] == [
        {
            "iteration": 0,
            "stale_pins": [],
            "compile_ok": True,
            "capture_ok": True,
        }
    ]
