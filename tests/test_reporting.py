from __future__ import annotations

import json
from pathlib import Path

import pytest

from matek_theorem_agent.models import new_run_state
from matek_theorem_agent.reporting import (
    ReportNarrative,
    assert_report_certificate_inventory,
    write_final_report,
)
from matek_theorem_agent.state import ArtifactIntegrityError
from matek_theorem_agent.workspace import create_run_root


def test_report_links_every_existing_non_report_artifact(tmp_path: Path) -> None:
    run_root = create_run_root(
        tmp_path,
        run_id="20260719T120000Z-report-abcdef",
    )
    (run_root / "input" / "problem.md").write_text("Prove P.\n", encoding="utf-8")
    (run_root / "research" / "verdict.json").write_text("{}\n", encoding="utf-8")
    state = new_run_state("20260719T120000Z-report-abcdef", tmp_path, run_root)
    state.metadata.update(
        {
            "research_status": "RESEARCH_REJECTED",
            "strongest_result": "A proper special case was proved.",
            "unresolved_obligations": ["The main quantifier remains unresolved."],
        }
    )

    result = write_final_report(state)

    payload = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert payload["scientific_status"] == "RESEARCH_REJECTED"
    assert "input/problem.md" in payload["artifacts"]
    assert "research/verdict.json" in payload["artifacts"]
    markdown = result.report_markdown.read_text(encoding="utf-8")
    assert "../research/verdict.json" in markdown
    assert "LEAN_VERIFIED" not in markdown
    assert result.verification_certificate.is_file()
    assert_report_certificate_inventory(run_root)

    (run_root / "research" / "late-artifact.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="uncertified"):
        assert_report_certificate_inventory(run_root)


def test_optional_narrative_cannot_replace_authoritative_report_fields(tmp_path: Path) -> None:
    run_root = create_run_root(
        tmp_path,
        run_id="20260719T120000Z-narrative-abcdef",
    )
    (run_root / "input" / "problem.md").write_text("Prove P.\n", encoding="utf-8")
    state = new_run_state("20260719T120000Z-narrative-abcdef", tmp_path, run_root)
    state.metadata["research_status"] = "RESEARCH_REJECTED"
    narrative = ReportNarrative(
        executive_summary="The audit rejected the proposed proof.",
        methodology_summary="Independent routes were compared and audited.",
        limitations=["The main claim remains unresolved."],
    )

    result = write_final_report(state, narrative=narrative)

    assert result.report.scientific_status == "RESEARCH_REJECTED"
    markdown = result.report_markdown.read_text(encoding="utf-8")
    assert "Optional model-assisted narrative" in markdown
    assert "deterministic status table" in markdown
    assert "[`input/problem.md`]" in markdown


def test_report_exposes_literature_and_problem_clarification_outcomes(tmp_path: Path) -> None:
    run_root = create_run_root(
        tmp_path,
        run_id="20260720T120000Z-clarify-abcdef",
    )
    (run_root / "input" / "problem.md").write_text(
        "Solve the extension problem.\n", encoding="utf-8"
    )
    state = new_run_state("20260720T120000Z-clarify-abcdef", tmp_path, run_root)
    state.metadata.update(
        {
            "research_status": "NEEDS_PROBLEM_CLARIFICATION",
            "literature_status": "unknown",
            "problem_clarification": {
                "required": True,
                "reason": "The domain and intended conclusion were not specified.",
                "questions": ["Which objects should be extended?"],
                "next_action": "Revise the problem file and start a new MATEK run.",
            },
        }
    )

    result = write_final_report(state)

    assert result.report.problem_clarification["required"] is True
    assert result.report.literature_status == "unknown"
    markdown = result.report_markdown.read_text(encoding="utf-8")
    assert "Problem clarification required" in markdown
    assert "Which objects should be extended?" in markdown
    assert "Prior literature assessment" in markdown


def test_report_separates_retriable_workflow_from_candidate_scientific_state(
    tmp_path: Path,
) -> None:
    run_root = create_run_root(tmp_path, run_id="20260722T120000Z-paused-abcdef")
    (run_root / "input" / "problem.md").write_text("Prove P.\n", encoding="utf-8")
    coordinator = run_root / "research" / "coordinator"
    coordinator.mkdir(parents=True)
    (coordinator / "state.json").write_text(
        json.dumps(
            {
                "phase": "awaiting_audits",
                "next_event_sequence": 12,
                "decisions": [{"decision": {}}],
                "assignments": [
                    {"status": "completed"},
                    {"status": "completed"},
                    {"status": "queued"},
                ],
                "active_candidate_attempt": {
                    "attempt_name": "event-7-attempt-1",
                    "mandatory_audits": ["foundational", "domain", "hostile"],
                    "audit_sha256": {"foundational": "a" * 64, "domain": "b" * 64},
                },
            }
        ),
        encoding="utf-8",
    )
    candidate_dir = run_root / "research" / "candidate"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "package.json").write_text(
        json.dumps({"exact_theorem": "The strongest frozen candidate theorem."}),
        encoding="utf-8",
    )
    state = new_run_state("20260722T120000Z-paused-abcdef", tmp_path, run_root)
    state.metadata.update(
        {
            "research_status": "CANDIDATE_AWAITING_AUDIT",
            "workflow_status": "PAUSED_RETRIABLE",
            "resume_action": "Resume only the missing hostile audit.",
            "execution_issues": [
                {
                    "category": "execution",
                    "message": "Hostile audit provider crashed.",
                    "trace_paths": ["research/issues/issue-00000001.json"],
                    "recovery_obligations": ["Retry the missing hostile audit."],
                }
            ],
        }
    )

    result = write_final_report(state)

    assert result.report.scientific_status == "CANDIDATE_AWAITING_AUDIT"
    assert result.report.workflow_status == "PAUSED_RETRIABLE"
    assert result.report.strongest_result == "The strongest frozen candidate theorem."
    assert result.report.research_checkpoint["assignments"]["completed"] == 2
    assert result.report.research_checkpoint["missing_audits"] == ["hostile"]
    markdown = result.report_markdown.read_text(encoding="utf-8")
    assert "Workflow | `PAUSED_RETRIABLE`" in markdown
    assert "Missing mandatory audits: hostile" in markdown
    assert "Trace: research/issues/issue-00000001.json" in markdown
    assert "Recovery: Retry the missing hostile audit." in markdown


def test_report_separates_research_manuscript_publication_and_lean_statuses(
    tmp_path: Path,
) -> None:
    run_root = create_run_root(tmp_path, run_id="20260722T130000Z-manuscript-warning-abcdef")
    (run_root / "input" / "problem.md").write_text("Prove P.\n", encoding="utf-8")
    state = new_run_state("20260722T130000Z-manuscript-warning-abcdef", tmp_path, run_root)
    state.metadata.update(
        {
            "research_status": "RESEARCH_ACCEPTED_FOR_MANUSCRIPT",
            "workflow_status": "COMPLETE_WITH_WARNINGS",
            "manuscript_status": "DRAFT_WITH_WARNINGS",
            "publication_status": "BLOCKED_METADATA",
            "lean_status": "LEAN_VERIFIED",
            "manuscript_findings": [
                {
                    "code": "matek_whitepaper_citation_pending",
                    "severity": "publication_warning",
                    "message": "Canonical whitepaper metadata is pending.",
                    "repair": None,
                }
            ],
        }
    )

    result = write_final_report(state)

    assert result.report.scientific_status == "RESEARCH_ACCEPTED_FOR_MANUSCRIPT"
    assert result.report.manuscript_status == "DRAFT_WITH_WARNINGS"
    assert result.report.publication_status == "BLOCKED_METADATA"
    assert result.report.lean_status == "LEAN_VERIFIED"
    markdown = result.report_markdown.read_text(encoding="utf-8")
    assert "Workflow | `COMPLETE_WITH_WARNINGS`" in markdown
    assert "Publication | `BLOCKED_METADATA`" in markdown
