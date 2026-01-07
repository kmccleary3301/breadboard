from __future__ import annotations

import json
from pathlib import Path

from agentic_coder_prototype.skills.registry import (
    apply_skill_selection,
    build_skill_catalog,
    load_skills,
    normalize_skill_selection,
)


def test_load_skills_from_json_paths(tmp_path: Path) -> None:
    workspace = tmp_path
    skills_path = workspace / "skills.json"
    skills_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "id": "repo.search",
                        "type": "prompt",
                        "version": "1.0.0",
                        "label": "Repo Search",
                        "group": "repo",
                        "description": "Search the repo quickly.",
                        "slot": "system",
                        "blocks": ["Use ripgrep."],
                    },
                    {
                        "id": "graph.ctree",
                        "type": "graph",
                        "version": "0.1.0",
                        "label": "CTree Graph",
                        "description": "Deterministic graph skill.",
                        "steps": [{"tool": "mcp.echo", "arguments": {"text": "$inputs.text"}}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    cfg = {"skills": {"enabled": True, "paths": ["skills.json"]}}
    prompt_skills, graph_skills = load_skills(cfg, str(workspace))
    assert [s.skill_id for s in prompt_skills] == ["repo.search"]
    assert [s.skill_id for s in graph_skills] == ["graph.ctree"]

    selection = normalize_skill_selection(cfg, {"mode": "blocklist", "blocklist": ["repo.search@1.0.0"]})
    selected_prompts, selected_graphs, enabled_map = apply_skill_selection(prompt_skills, graph_skills, selection)
    assert [s.skill_id for s in selected_prompts] == []
    assert [s.skill_id for s in selected_graphs] == ["graph.ctree"]
    catalog = build_skill_catalog(prompt_skills, graph_skills, selection=selection, enabled_map=enabled_map)
    enabled_by_id = {f"{s['id']}@{s['version']}": s.get("enabled") for s in catalog.get("skills", [])}
    assert enabled_by_id["repo.search@1.0.0"] is False
    assert enabled_by_id["graph.ctree@0.1.0"] is True
