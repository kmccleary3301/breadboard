import json
import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_tmux_capture_run.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("validate_tmux_capture_run", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def _build_minimal_run(
    tmp_path,
    *,
    render_lock_payload: dict[str, object] | None = None,
) -> Path:
    """Create the run directory layout that validate_run_dir expects.

    tmp_path structure:
    tmp_path/
        run/
            meta.json
            index.jsonl
            initial.txt
            initial.ansi
            frames/
                frame0.txt
                frame0.ansi
                frame0.png
                frame0.render_lock.json    # when render_lock_payload is provided
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    frames_dir = run_dir / "frames"
    frames_dir.mkdir()

    _write_json(run_dir / "meta.json", {"render_png": True})
    (run_dir / "initial.txt").write_text("initial text", encoding="utf-8")
    (run_dir / "initial.ansi").write_text("initial ansi", encoding="utf-8")

    text_path = frames_dir / "frame0.txt"
    ansi_path = frames_dir / "frame0.ansi"
    png_path = frames_dir / "frame0.png"
    text_path.write_text("frame text", encoding="utf-8")
    ansi_path.write_text("frame ansi", encoding="utf-8")
    png_path.write_text("", encoding="utf-8")

    record = {
        "frame": 0,
        "text": str(text_path.relative_to(run_dir)),
        "ansi": str(ansi_path.relative_to(run_dir)),
        "png": str(png_path.relative_to(run_dir)),
    }

    if render_lock_payload is not None:
        render_lock_rel = str(png_path.relative_to(run_dir).with_suffix(".render_lock.json"))
        record["render_lock"] = render_lock_rel
        _write_json(run_dir / render_lock_rel, render_lock_payload)

    _write_jsonl(run_dir / "index.jsonl", [record])
    return run_dir


def test_missing_render_lock_sidecar_strict_vs_lenient(tmp_path):
    module = _load_module()
    run_dir = _build_minimal_run(tmp_path)

    # Non-strict mode should pass despite the missing render_lock file.
    result = module.validate_run_dir(run_dir, strict=False, expect_png=True, max_missing_frames=0)
    assert result.ok
    assert result.render_lock_missing_count == 1
    assert result.render_parity_violation_count == 0
    assert not result.errors
    assert any("missing render_lock" in warning for warning in result.warnings)

    # Strict mode turns the missing render_lock into an error.
    strict_result = module.validate_run_dir(run_dir, strict=True, expect_png=True, max_missing_frames=0)
    assert not strict_result.ok
    assert strict_result.render_lock_missing_count == 1
    assert any("missing render_lock" in error for error in strict_result.errors)


def test_render_parity_bounds_warn_vs_error(tmp_path):
    module = _load_module()
    render_lock_payload = {
        "schema_version": "tmux_render_lock_frame_v1",
        "row_occupancy": {
            "text_sha256_normalized": "deadbeef",
            "missing_count": 5,
            "extra_count": 3,
            "row_span_delta": 4,
        },
    }
    run_dir = _build_minimal_run(tmp_path, render_lock_payload=render_lock_payload)

    lenient = module.validate_run_dir(run_dir, strict=False, expect_png=True, max_missing_frames=0)
    assert lenient.ok
    assert lenient.render_lock_missing_count == 0
    assert lenient.render_parity_violation_count == 1
    assert not lenient.errors
    assert any("render parity out of bounds" in warning for warning in lenient.warnings)

    strict = module.validate_run_dir(run_dir, strict=True, expect_png=True, max_missing_frames=0)
    assert not strict.ok
    assert strict.render_parity_violation_count == 1
    assert strict.errors


def test_row_parity_summary_mismatch_is_detected(tmp_path):
    module = _load_module()
    render_lock_payload = {
        "schema_version": "tmux_render_lock_frame_v1",
        "row_occupancy": {
            "text_sha256_normalized": "deadbeef",
            "missing_count": 0,
            "extra_count": 0,
            "row_span_delta": 0,
        },
    }
    run_dir = _build_minimal_run(tmp_path, render_lock_payload=render_lock_payload)
    row_parity_path = run_dir / "frames" / "frame0.row_parity.json"
    row_parity_path.write_text(
        json.dumps(
            {
                "schema_version": "tmux_row_parity_summary_v1",
                "parity": {
                    "text_sha256_normalized": "deadbeef",
                    "missing_count": 2,
                    "extra_count": 0,
                    "row_span_delta": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    # Keep quick index metrics aligned with lock but not row parity summary.
    idx_path = run_dir / "index.jsonl"
    rows = [json.loads(line) for line in idx_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[0]["render_parity_summary"] = "frames/frame0.row_parity.json"
    rows[0]["render_parity"] = {"missing_count": 0, "extra_count": 0, "row_span_delta": 0}
    _write_jsonl(idx_path, rows)

    strict = module.validate_run_dir(run_dir, strict=True, expect_png=True, max_missing_frames=0)
    assert not strict.ok
    assert strict.render_parity_violation_count >= 1
    assert any("render_lock vs row parity summary mismatch" in e for e in strict.errors) or any(
        "index render_parity mismatch vs row parity summary" in e for e in strict.errors
    )
