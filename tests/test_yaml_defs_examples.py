from pathlib import Path
import yaml


def test_run_shell_yaml_fields_present():
    repo_root = Path(__file__).resolve().parents[1]
    p = repo_root / "implementations/tools/defs/run_shell.yaml"
    data = yaml.safe_load(p.read_text())
    assert data["id"] == "run_shell"
    assert data["name"] == "run_shell"
    assert "parameters" in data and any(prm["name"] == "command" for prm in data["parameters"])
    assert "provider_routing" in data


def test_apply_search_replace_yaml_fields_present():
    repo_root = Path(__file__).resolve().parents[1]
    p = repo_root / "implementations/tools/defs/apply_search_replace.yaml"
    data = yaml.safe_load(p.read_text())
    assert data["id"] == "apply_search_replace"
    names = [p["name"] for p in data["parameters"]]
    assert {"file_name", "search", "replace"}.issubset(set(names))


