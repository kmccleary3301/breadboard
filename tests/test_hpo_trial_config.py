from __future__ import annotations

import json
from pathlib import Path

from scripts.run_hpo import load_trials_config


def test_load_trials_config_explicit(tmp_path: Path) -> None:
    payload = {
        "trials": [
            {"name": "t1", "overrides": {"foo.bar": 1}},
            {"name": "t2", "overrides": {"foo.bar": 2}},
        ]
    }
    path = tmp_path / "trials.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    trials = load_trials_config(Path("config.yaml"), path)
    assert len(trials) == 2
    assert trials[0].name == "t1"
    assert trials[1].overrides["foo.bar"] == 2


def test_load_trials_config_random(tmp_path: Path) -> None:
    payload = {
        "random": {
            "count": 3,
            "space": {
                "alpha": {"min": 0.0, "max": 1.0},
                "beta": {"choices": [1, 2]},
            },
        }
    }
    path = tmp_path / "trials.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    trials = load_trials_config(Path("config.yaml"), path)
    assert len(trials) == 3
    assert "alpha" in trials[0].overrides
    assert "beta" in trials[0].overrides
