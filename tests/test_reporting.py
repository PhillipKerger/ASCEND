from __future__ import annotations

import json
from pathlib import Path

import pytest

from ascend_math_agent.models import new_run_state
from ascend_math_agent.reporting import (
    ReportNarrative,
    assert_report_certificate_inventory,
    write_final_report,
)
from ascend_math_agent.state import ArtifactIntegrityError
from ascend_math_agent.workspace import create_run_root


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
