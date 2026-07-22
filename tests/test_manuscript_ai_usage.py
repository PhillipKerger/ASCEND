from __future__ import annotations

from pathlib import Path

import pytest

from matek_theorem_agent.verification import validate_matek_ai_usage

PROJECT = Path(__file__).resolve().parents[1]

# These impossible-future records are deliberately test-only. Production code contains no
# invented MATEK owner or arXiv identifier and relies on the independent source gate.
FIXTURE_REPOSITORY = "https://github.com/matek-test-fixtures/matek-theorem-agent"
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
        "@misc{matekSoftwareFixture,\n"
        "  author = {MATEK test-fixture contributors},\n"
        "  title = {MATEK: Multi-Agent Theorem Exploration through Knowledge-Graph "
        "Memory},\n"
        "  year = {2099},\n"
        "  howpublished = {Software repository},\n"
        f"  url = {{{repository_url}}}\n"
        "}\n"
        "@misc{matekWhitepaperFixture,\n"
        "  author = {MATEK test-fixture contributors},\n"
        "  title = {MATEK: Multi-Agent Theorem Exploration through Knowledge-Graph "
        "Memory},\n"
        "  year = {2099},\n"
        "  howpublished = {arXiv preprint},\n"
        f"  eprint = {{{FIXTURE_ARXIV_ID}}},\n"
        "  archiveprefix = {arXiv}\n"
        "}\n"
    )


def test_ai_usage_statement_requires_disclosure_and_both_distinct_matek_citations() -> None:
    report = validate_matek_ai_usage(
        _paper(
            "The MATEK system with GPT 5.6 was used in this work "
            "\\cite{matekSoftwareFixture,matekWhitepaperFixture}."
        ),
        _bibliography(),
    )

    assert report.passed
    assert report.has_statement_section
    assert report.discloses_matek_with_gpt_5_6
    assert report.repository_citation_key == "matekSoftwareFixture"
    assert report.whitepaper_citation_key == "matekWhitepaperFixture"


@pytest.mark.parametrize(
    ("paper", "expected_code"),
    [
        (
            "\\documentclass{article}\\begin{document}No disclosure.\\end{document}",
            "missing_ai_usage_statement",
        ),
        (
            _paper(
                "The MATEK system with GPT 4.1 was used in this work "
                "\\cite{matekSoftwareFixture,matekWhitepaperFixture}."
            ),
            "incomplete_ai_usage_statement",
        ),
        (
            _paper(
                "The MATEK system with GPT 5.6 was used in this work \\cite{matekSoftwareFixture}."
            ),
            "missing_matek_whitepaper_citation",
        ),
    ],
)
def test_ai_usage_statement_rejects_missing_required_content(
    paper: str,
    expected_code: str,
) -> None:
    report = validate_matek_ai_usage(paper, _bibliography())

    assert not report.passed
    assert expected_code in {issue.code for issue in report.issues}


def test_ai_usage_statement_rejects_placeholder_repository_metadata() -> None:
    report = validate_matek_ai_usage(
        _paper(
            "The MATEK system with GPT 5.6 was used in this work "
            "\\cite{matekSoftwareFixture,matekWhitepaperFixture}."
        ),
        _bibliography(repository_url="https://github.com/OWNER/matek-theorem-agent"),
    )

    assert not report.passed
    assert "missing_matek_repository_citation" in {issue.code for issue in report.issues}


def test_ai_usage_statement_requires_two_distinct_bibliography_entries() -> None:
    combined = (
        "@misc{matekCombinedFixture, "
        "author={MATEK test-fixture contributors}, "
        "title={MATEK: Multi-Agent Theorem Exploration through Knowledge-Graph Memory}, "
        "year={2099}, howpublished={Software repository and arXiv preprint}, "
        f"url={{{FIXTURE_REPOSITORY}}}, eprint={{{FIXTURE_ARXIV_ID}}}}}\n"
    )
    report = validate_matek_ai_usage(
        _paper("The MATEK system with GPT 5.6 was used in this work \\cite{matekCombinedFixture}."),
        combined,
    )

    assert not report.passed
    assert "matek_citations_not_distinct" in {issue.code for issue in report.issues}


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
        assert "MATEK GitHub" in prompt
        assert "MATEK whitepaper" in prompt
