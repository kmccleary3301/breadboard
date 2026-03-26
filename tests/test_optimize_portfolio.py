from __future__ import annotations

from agentic_coder_prototype.optimize import (
    CandidateBundle,
    CandidateChange,
    CandidatePortfolio,
    MaterializedCandidate,
    ObjectiveVector,
    PortfolioEntry,
    build_codex_dossier_example,
)


def _entry(candidate_id: str, applied_loci: list[str], objective_vector: ObjectiveVector) -> PortfolioEntry:
    example = build_codex_dossier_example()
    target = example["target"]
    assert target.target_id == "target.codex_dossier.tool_render"

    candidate = CandidateBundle(
        candidate_id=candidate_id,
        source_target_id=target.target_id,
        applied_loci=applied_loci,
        changes=[
            CandidateChange(
                locus_id=locus_id,
                value={"text": f"overlay for {locus_id}"},
            )
            for locus_id in applied_loci
        ],
        provenance={"test": "portfolio"},
    )
    materialized = MaterializedCandidate(
        candidate_id=candidate_id,
        source_target_id=target.target_id,
        applied_loci=applied_loci,
        effective_artifact={"overlay_by_locus": {locus_id: {"text": locus_id} for locus_id in applied_loci}},
        effective_tool_surface={"tools": ["exec_command", "apply_patch", "spawn_agent"]},
        support_envelope=target.support_envelope,
        evaluation_input_compatibility={"replay": True},
    )
    return PortfolioEntry(
        candidate=candidate,
        materialized_candidate=materialized,
        objective_vector=objective_vector,
        evidence_lineage=["eval.test.001"],
        score_kind="predicted",
    )


def test_portfolio_replaces_dominated_entry() -> None:
    portfolio = CandidatePortfolio()
    dominated = _entry(
        "cand.portfolio.001",
        ["tool.render.exec_command"],
        ObjectiveVector(
            correctness_score=0.55,
            wrongness_penalty=0.60,
            mutation_cost=0.40,
            instability_penalty=0.10,
        ),
    )
    dominating = _entry(
        "cand.portfolio.002",
        ["tool.render.exec_command"],
        ObjectiveVector(
            correctness_score=0.70,
            wrongness_penalty=0.40,
            mutation_cost=0.30,
            instability_penalty=0.05,
        ),
    )

    portfolio = portfolio.retain_entry(dominated)
    portfolio = portfolio.retain_entry(dominating)

    assert [entry.candidate.candidate_id for entry in portfolio.entries] == ["cand.portfolio.002"]


def test_portfolio_retains_tradeoff_candidates() -> None:
    portfolio = CandidatePortfolio()
    narrow = _entry(
        "cand.portfolio.010",
        ["tool.render.exec_command"],
        ObjectiveVector(
            correctness_score=0.78,
            wrongness_penalty=0.22,
            mutation_cost=0.18,
            instability_penalty=0.08,
        ),
    )
    broader = _entry(
        "cand.portfolio.011",
        ["tool.render.exec_command", "prompt.section.optimization_guidance"],
        ObjectiveVector(
            correctness_score=0.86,
            wrongness_penalty=0.18,
            mutation_cost=0.42,
            instability_penalty=0.10,
        ),
    )

    portfolio = portfolio.retain_entry(narrow)
    portfolio = portfolio.retain_entry(broader)

    assert [entry.candidate.candidate_id for entry in portfolio.entries] == [
        "cand.portfolio.010",
        "cand.portfolio.011",
    ]


def test_portfolio_deduplicates_equal_overlay_stably() -> None:
    portfolio = CandidatePortfolio()
    left = _entry(
        "cand.portfolio.020",
        ["tool.render.exec_command"],
        ObjectiveVector(
            correctness_score=0.80,
            wrongness_penalty=0.20,
            mutation_cost=0.25,
            instability_penalty=0.05,
        ),
    )
    right = _entry(
        "cand.portfolio.021",
        ["tool.render.exec_command"],
        ObjectiveVector(
            correctness_score=0.80,
            wrongness_penalty=0.20,
            mutation_cost=0.25,
            instability_penalty=0.05,
        ),
    )

    portfolio = portfolio.retain_entry(right)
    portfolio = portfolio.retain_entry(left)

    assert [entry.candidate.candidate_id for entry in portfolio.entries] == ["cand.portfolio.020"]
