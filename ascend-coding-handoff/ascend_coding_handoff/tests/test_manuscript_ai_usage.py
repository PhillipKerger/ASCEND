from __future__ import annotations

from pathlib import Path

import pytest

from ascend_math_agent.verification import validate_ascend_ai_usage

PROJECT = Path(__file__).resolve().parents[1]

# These impossible-future records are deliberately test-only. Production code contains no
# invented ASCEND owner or arXiv identifier and relies on the independent source gate.
FIXTURE_REPOSITORY = "https://github.com/ascend-test-fixtures/ascend-math-agent"
FIXTURE_ARXIV_ID = "2099.99999"


def _paper(statement: str) -> str:
    return (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section*{Statement of AI Usage}\n"
        f"{statement}\n"
        "\\bibliography{references}\n"
        "\\end{document}\n"
    )


def _bibliography(*, repository_url: str = FIXTURE_REPOSITORY) -> str:
    return (
        "@misc{ascendSoftwareFixture,\n"
        "  author = {ASCEND test-fixture contributors},\n"
        "  title = {ASCEND: Autonomous System for Conjecture Exploration and Verified "
        "Deduction},\n"
        "  year = {2099},\n"
        "  howpublished = {Software repository},\n"
        f"  url = {{{repository_url}}}\n"
        "}\n"
        "@misc{ascendWhitepaperFixture,\n"
        "  author = {ASCEND test-fixture contributors},\n"
        "  title = {ASCEND: Autonomous System for Conjecture Exploration and Verified "
        "Deduction},\n"
        "  year = {2099},\n"
        "  howpublished = {arXiv preprint},\n"
        f"  eprint = {{{FIXTURE_ARXIV_ID}}},\n"
        "  archiveprefix = {arXiv}\n"
        "}\n"
    )


def test_ai_usage_statement_requires_disclosure_and_both_distinct_ascend_citations() -> None:
    report = validate_ascend_ai_usage(
        _paper(
            "The ASCEND system with GPT 5.6 was used in this work "
            "\\cite{ascendSoftwareFixture,ascendWhitepaperFixture}."
        ),
        _bibliography(),
    )

    assert report.passed
    assert report.has_statement_section
    assert report.discloses_ascend_with_gpt_5_6
    assert report.repository_citation_key == "ascendSoftwareFixture"
    assert report.whitepaper_citation_key == "ascendWhitepaperFixture"


@pytest.mark.parametrize(
    ("paper", "expected_code"),
    [
        (
            "\\documentclass{article}\\begin{document}No disclosure.\\end{document}",
            "missing_ai_usage_statement",
        ),
        (
            _paper(
                "The ASCEND system with GPT 4.1 was used in this work "
                "\\cite{ascendSoftwareFixture,ascendWhitepaperFixture}."
            ),
            "incomplete_ai_usage_statement",
        ),
        (
            _paper(
                "The ASCEND system with GPT 5.6 was used in this work "
                "\\cite{ascendSoftwareFixture}."
            ),
            "missing_ascend_whitepaper_citation",
        ),
    ],
)
def test_ai_usage_statement_rejects_missing_required_content(
    paper: str,
    expected_code: str,
) -> None:
    report = validate_ascend_ai_usage(paper, _bibliography())

    assert not report.passed
    assert expected_code in {issue.code for issue in report.issues}


def test_ai_usage_statement_rejects_placeholder_repository_metadata() -> None:
    report = validate_ascend_ai_usage(
        _paper(
            "The ASCEND system with GPT 5.6 was used in this work "
            "\\cite{ascendSoftwareFixture,ascendWhitepaperFixture}."
        ),
        _bibliography(repository_url="https://github.com/OWNER/ascend-math-agent"),
    )

    assert not report.passed
    assert "missing_ascend_repository_citation" in {issue.code for issue in report.issues}


def test_ai_usage_statement_requires_two_distinct_bibliography_entries() -> None:
    combined = (
        "@misc{ascendCombinedFixture, "
        "author={ASCEND test-fixture contributors}, "
        "title={ASCEND: Autonomous System for Conjecture Exploration and Verified Deduction}, "
        "year={2099}, howpublished={Software repository and arXiv preprint}, "
        f"url={{{FIXTURE_REPOSITORY}}}, eprint={{{FIXTURE_ARXIV_ID}}}}}\n"
    )
    report = validate_ascend_ai_usage(
        _paper(
            "The ASCEND system with GPT 5.6 was used in this work \\cite{ascendCombinedFixture}."
        ),
        combined,
    )

    assert not report.passed
    assert "ascend_citations_not_distinct" in {issue.code for issue in report.issues}


def test_manuscript_prompts_make_ai_usage_and_citation_requirements_mandatory() -> None:
    writer = (PROJECT / "resources" / "prompts" / "manuscript_writer.md").read_text(
        encoding="utf-8"
    )
    verifier = (PROJECT / "resources" / "prompts" / "bibliography_verifier.md").read_text(
        encoding="utf-8"
    )

    for prompt in (writer, verifier):
        assert "Statement of AI Usage" in prompt
        assert "GPT 5.6" in prompt
        assert "ASCEND GitHub" in prompt
        assert "ASCEND whitepaper" in prompt
