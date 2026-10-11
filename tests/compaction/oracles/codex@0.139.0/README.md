# Codex 0.139.0 recordings

`recordings/` contains the earlier unknown-model-fallback captures. Keep them as evidence of that configuration; they are not the GPT-5.5 catalog baseline.

`recordings-gpt55/` contains fresh requests from the real pinned Codex 0.139.0 binary with `model = "gpt-5.5"`. Source commit: `a7dff904308535e965aee87680c1fc5ef1d19eec`. The normative model instructions and capabilities come from `codex-rs/models-manager/models.json`, with the pragmatic personality template. Each JSONL record retains raw request bytes and parsed JSON.

Capture all three scenarios with:

```sh
BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. /Users/kylemccleary/projects/breadboard-compaction-ref-20261009/.venv/bin/python -m scripts.compaction_lanes.codex_lane --out /tmp/codex-e4-gpt55-lane --window 16000 --stock-recordings tests/compaction/oracles/codex@0.139.0/recordings-gpt55
```

The same 16,000-token window and derived 14,400-token compaction limit apply to both sides. Stock `exec`/`resume` and fresh BreadBoard conductor instances share workspace, native environment, installation file and stock thread root. Process IDs are injected from stock's successful PTY allocation; allocation mismatches fail. Build the explicit native helper prerequisites with `scripts/build_codex_image.py` and `scripts/build_codex_patch.py` before running the lane.

Comparison uses canonical JSON member ordering only. Approved terminal chunk-ID and wall-time guards apply only to `exec_command`/`write_stdin` result headers, validate each side separately and count each substitution. `lane_report.json` reports any remaining difference; these recordings alone do not establish BreadBoard acceptance.
