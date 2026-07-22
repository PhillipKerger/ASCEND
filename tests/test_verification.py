from __future__ import annotations

import os
from pathlib import Path

import pytest

from matek_theorem_agent.execution.base import CommandResult
from matek_theorem_agent.verification import (
    LatexClassification,
    LeanVerificationStatus,
    canonical_theorem_hash,
    check_axiom_allowlist,
    classify_latex_result,
    parse_print_axioms,
    scan_lean_tree,
    validate_bibliography,
    verify_build,
)


def command_result(tmp_path: Path, *, exit_code: int = 0, log: str = "") -> CommandResult:
    return CommandResult(("lake", "build"), tmp_path, exit_code, log, "", 0.1)


def test_latex_classification_requires_resolved_citations(tmp_path: Path) -> None:
    report = classify_latex_result(
        CommandResult(
            ("latexmk", "-pdf", "paper.tex"),
            tmp_path,
            0,
            "LaTeX Warning: Citation `missing' on page 1 undefined.",
            "",
            0.1,
        )
    )
    assert not report.passed
    assert report.classification == LatexClassification.UNDEFINED_CITATIONS


def test_bibliography_consistency_and_metadata() -> None:
    tex = r"Result \cite{good,missing}."
    bib = """@article{good,
      author = {Ada Lovelace}, title = {Exact title}, year = {2024},
      journal = {Journal}, doi = {10.1/example}
    }"""
    report = validate_bibliography(tex, bib)
    assert not report.passed
    assert {issue.code for issue in report.issues} == {"missing_bibliography_entry"}


def test_lean_scan_ignores_comments_but_rejects_todo_axiom_and_placeholder(
    tmp_path: Path,
) -> None:
    (tmp_path / "Good.lean").write_text(
        "-- sorry in historical prose\ntheorem ok : True := by trivial\n"
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "Bad.lean").write_text("TODO\naxiom target : False\ntheorem result : True := by?\n")
    report = scan_lean_tree(tmp_path, "target")
    codes = {finding.code for finding in report.findings}
    assert "placeholder_sorry" not in codes
    assert {"placeholder_todo", "suspicious_axiom_declaration", "placeholder_by_question"} <= codes


def test_lean_scan_blocks_compile_time_escapes_without_comment_or_string_false_positives(
    tmp_path: Path,
) -> None:
    (tmp_path / "Escapes.lean").write_text(
        '-- #eval 1; run_cmd IO.println "not code"; unsafe def hidden := 1\n'
        'def prose := "run_cmd #eval unsafe elab include_str"\n'
        "#eval 1\n"
        'run_cmd IO.println "executed"\n'
        'elab "escape" : command => pure ()\n'
        "theorem target : True := by trivial\n",
        encoding="utf-8",
    )

    report = scan_lean_tree(tmp_path, "target")
    codes = [finding.code for finding in report.findings]
    assert codes.count("compile_time_execution") == 2
    assert codes.count("custom_elaborator") == 1
    assert "unsafe_declaration" not in codes
    assert "compile_time_file_read" not in codes


def test_lean_scan_rejects_nonlean_symlink_without_following_it(tmp_path: Path) -> None:
    challenge = tmp_path / "challenge.lean"
    challenge.write_text("theorem target : True := by trivial\n", encoding="utf-8")
    (tmp_path / "build.log").symlink_to(challenge.name)

    report = scan_lean_tree(tmp_path, "target")

    assert any(
        finding.code == "symlink_not_scanned" and finding.path == "build.log"
        for finding in report.findings
    )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is POSIX-only")
def test_lean_scan_rejects_non_regular_entries_without_opening_them(tmp_path: Path) -> None:
    (tmp_path / "challenge.lean").write_text(
        "theorem target : True := by trivial\n", encoding="utf-8"
    )
    os.mkfifo(tmp_path / "compiler.log")

    report = scan_lean_tree(tmp_path, "target")

    assert any(
        finding.code == "non_regular_tree_entry" and finding.path == "compiler.log"
        for finding in report.findings
    )


def test_canonical_theorem_hash_ignores_only_lexical_trivia() -> None:
    first = "theorem result (n : Nat) : n = n := by rfl"
    second = "-- comment\ntheorem   result ( n:Nat ) : n=n := by\n  rfl"
    changed = "theorem result (n : Nat) : n + 0 = n := by simp"
    assert canonical_theorem_hash(first, "result") == canonical_theorem_hash(second, "result")
    assert canonical_theorem_hash(first, "result") != canonical_theorem_hash(changed, "result")


def test_print_axioms_parser_and_allowlist() -> None:
    output = "'result' depends on axioms: [propext, Classical.choice, Bad.axiom]"
    assert parse_print_axioms(output) == frozenset({"propext", "Classical.choice", "Bad.axiom"})
    report = check_axiom_allowlist(output, ["propext", "Classical.choice"])
    assert not report.passed
    assert report.unapproved_axioms == ("Bad.axiom",)


def test_verify_build_truthful_status_and_certificate(tmp_path: Path) -> None:
    source = "theorem result (n : Nat) : n = n := by\n  rfl\n"
    (tmp_path / "Main.lean").write_text(source)
    approved_hash = canonical_theorem_hash(source, "result")
    report = verify_build(
        tmp_path,
        approved_hash,
        command_result(tmp_path),
        "'result' depends on axioms: [propext]",
        ["propext"],
        theorem_name="result",
    )
    assert report.passed
    assert report.status == LeanVerificationStatus.LEAN_VERIFIED_WITH_APPROVED_AXIOMS
    assert report.to_dict()["checks"]["theorem_statement_unchanged"] is True


def test_verify_build_rejects_changed_statement_and_sorry(tmp_path: Path) -> None:
    approved = "theorem result (n : Nat) : n = n := by rfl"
    changed = "theorem result (n : Nat) : n + 0 = n := by sorry\n"
    (tmp_path / "Main.lean").write_text(changed)
    report = verify_build(
        tmp_path,
        canonical_theorem_hash(approved, "result"),
        command_result(tmp_path),
        "'result' does not depend on any axioms",
        [],
        theorem_name="result",
    )
    assert not report.passed
    assert report.status == LeanVerificationStatus.LEAN_FAILED
    codes = {issue.code for issue in report.issues}
    assert {"theorem_statement_mismatch", "placeholder_sorry"} <= codes
