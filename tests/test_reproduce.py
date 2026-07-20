from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ascend_math_agent.config import AppConfig
from ascend_math_agent.execution.base import CommandRequest, CommandResult
from ascend_math_agent.execution.docker import DockerBackend
from ascend_math_agent.intake import ingest_problem
from ascend_math_agent.models import RunState, StageName
from ascend_math_agent.reporting import write_final_report
from ascend_math_agent.reproduce import (
    ReproductionCheck,
    ReproductionCheckStatus,
    ReproductionComponent,
    RunVerificationResult,
    verify_run,
)
from ascend_math_agent.state import (
    record_artifact_file,
    save_state_atomic,
    start_stage,
    succeed_stage,
)
from ascend_math_agent.verification import canonical_theorem_hash


class SuccessfulBackend:
    def __init__(self) -> None:
        self.requests: list[CommandRequest] = []

    async def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        executable = request.argv[0]
        if executable == "latexmk":
            (request.cwd / "paper.pdf").write_bytes(b"%PDF-1.7\nreproduced\n")
            stdout = "Latexmk: All targets are up-to-date"
        elif request.argv[-1].endswith("_AscendAxiomCheck.lean"):
            stdout = "'result' does not depend on any axioms"
        else:
            stdout = ""
        return CommandResult(
            argv=request.argv,
            cwd=request.cwd,
            exit_code=0,
            stdout=stdout,
            stderr="",
            duration_seconds=0.01,
        )


class FailingBackend:
    async def run(self, request: CommandRequest) -> CommandResult:
        if request.argv[0] == "latexmk":
            output = "LaTeX Warning: Citation `missing' on page 1 undefined."
        else:
            output = "Lean compilation failed"
        return CommandResult(
            argv=request.argv,
            cwd=request.cwd,
            exit_code=1,
            stdout=output,
            stderr="",
            duration_seconds=0.01,
        )


class SuccessfulDockerHost:
    def __init__(self) -> None:
        self.requests: list[CommandRequest] = []

    async def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        if "latexmk" in request.argv:
            (request.cwd / "paper.pdf").write_bytes(b"%PDF-1.7\nreproduced in Docker\n")
            stdout = "Latexmk: All targets are up-to-date"
        elif request.argv[-1].endswith("_AscendAxiomCheck.lean"):
            stdout = "'result' does not depend on any axioms"
        else:
            stdout = ""
        return CommandResult(
            argv=request.argv,
            cwd=request.cwd,
            exit_code=0,
            stdout=stdout,
            stderr="",
            duration_seconds=0.01,
        )


def _component(result: RunVerificationResult, name: ReproductionComponent) -> ReproductionCheck:
    return next(check for check in result.checks if check.component is name)


def _create_run(tmp_path: Path, *, with_artifacts: bool = True) -> tuple[Path, RunState]:
    problem = tmp_path / "problem.md"
    problem.write_text("Prove a theorem.\n", encoding="utf-8")
    intake = ingest_problem(
        problem_file=problem,
        project_root=tmp_path,
        config=AppConfig(project_root=tmp_path),
        invocation={"test": True},
        run_id="20260719T123456Z-reproduce-abcdef",
        snapshot={},
    )
    state = intake.state
    if not with_artifacts:
        return intake.run_root, state

    manuscript = intake.run_root / "manuscript"
    (manuscript / "paper.tex").write_text(
        """\\documentclass{article}
\\begin{document}
\\section{Related Work}
The relevant result is established in \\cite{good}.
\\section*{Statement of AI Usage}
The ASCEND system with GPT 5.6 was used in this work
\\cite{ascendSoftwareFixture,ascendWhitepaperFixture}.
\\bibliographystyle{plain}
\\bibliography{references}
\\end{document}
""",
        encoding="utf-8",
    )
    (manuscript / "references.bib").write_text(
        """@article{good,
  author = {Ada Lovelace},
  title = {An Exact Result},
  year = {2024},
  journal = {Journal of Exact Results},
  doi = {10.1000/example}
}
@misc{ascendSoftwareFixture,
  author = {ASCEND test-fixture contributors},
  title = {ASCEND: Autonomous System for Conjecture Exploration and Verified Deduction},
  year = {2099},
  howpublished = {Software repository},
  url = {https://github.com/ascend-test-fixtures/ascend-math-agent}
}
@misc{ascendWhitepaperFixture,
  author = {ASCEND test-fixture contributors},
  title = {ASCEND: Autonomous System for Conjecture Exploration and Verified Deduction},
  year = {2099},
  howpublished = {arXiv preprint},
  eprint = {2099.99999},
  archiveprefix = {arXiv}
}
""",
        encoding="utf-8",
    )
    (manuscript / "bibliography_audit.json").write_text(
        json.dumps(
            {
                "status": "verified",
                "entries": [
                    {"citation_key": "good", "status": "verified"},
                    {"citation_key": "ascendSoftwareFixture", "status": "verified"},
                    {"citation_key": "ascendWhitepaperFixture", "status": "verified"},
                ],
                "claim_checks": [],
                "blocking_issues": [],
            }
        ),
        encoding="utf-8",
    )

    lean = intake.run_root / "lean"
    challenge = "theorem result : True := by\n  trivial\n"
    (lean / "challenge.lean").write_text(challenge, encoding="utf-8")
    (lean / "Main.lean").write_text("import challenge\n", encoding="utf-8")
    (lean / "result.json").write_text(
        json.dumps(
            {
                "approved_statement_hash": canonical_theorem_hash(challenge, "result"),
                "statement_draft": {"theorem_name": "result"},
            }
        ),
        encoding="utf-8",
    )

    for path in (
        manuscript / "paper.tex",
        manuscript / "references.bib",
        manuscript / "bibliography_audit.json",
    ):
        record_artifact_file(state, StageName.MANUSCRIPT, path)
    for path in (lean / "challenge.lean", lean / "Main.lean", lean / "result.json"):
        record_artifact_file(state, StageName.LEAN_VERIFICATION, path)
    save_state_atomic(state, intake.run_root / "state.json")
    return intake.run_root, state


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.asyncio
async def test_verify_run_reproduces_all_checks_without_mutating_run(tmp_path: Path) -> None:
    run_root, _ = _create_run(tmp_path)
    before = _tree_hashes(run_root)
    backend = SuccessfulBackend()

    result = await verify_run(run_root, backend)

    assert result.passed
    assert all(check.status is not ReproductionCheckStatus.FAILED for check in result.checks)
    assert _component(result, ReproductionComponent.BIBLIOGRAPHY).status == "passed"
    assert _component(result, ReproductionComponent.LATEX).status == "passed"
    assert _component(result, ReproductionComponent.LEAN).status == "passed"
    assert len(backend.requests) == 3
    assert _tree_hashes(run_root) == before


@pytest.mark.asyncio
async def test_docker_latex_reproduction_uses_writable_active_run_workspace(
    tmp_path: Path,
) -> None:
    run_root, _ = _create_run(tmp_path)
    before = _tree_hashes(run_root)
    host = SuccessfulDockerHost()
    backend = DockerBackend("ascend:test", native_backend=host)  # type: ignore[arg-type]

    result = await verify_run(run_root, backend)

    assert result.passed
    latex_request = next(request for request in host.requests if "latexmk" in request.argv)
    assert latex_request.cwd.is_relative_to(run_root / "report")
    assert latex_request.cwd.name.startswith(".ascend-latex-verify-")
    mount_index = latex_request.argv.index("--mount")
    assert not latex_request.argv[mount_index + 1].endswith(",readonly")
    lean_requests = [request for request in host.requests if "lake" in request.argv]
    assert lean_requests
    for request in lean_requests:
        project_mount_index = request.argv.index("--mount")
        assert request.argv[project_mount_index + 1].endswith(",readonly")
    assert _tree_hashes(run_root) == before


@pytest.mark.asyncio
async def test_verify_run_reports_tampered_artifact(tmp_path: Path) -> None:
    run_root, _ = _create_run(tmp_path)
    (run_root / "manuscript" / "references.bib").write_text(
        "@article{tampered, title={Tampered}}\n", encoding="utf-8"
    )

    result = await verify_run(run_root, SuccessfulBackend())

    assert not result.passed
    integrity = _component(result, ReproductionComponent.ARTIFACT_INTEGRITY)
    assert integrity.status == "failed"
    assert any("manuscript/references.bib" in item for item in integrity.diagnostics)


@pytest.mark.asyncio
async def test_verify_run_rechecks_statement_of_ai_usage(tmp_path: Path) -> None:
    run_root, state = _create_run(tmp_path)
    paper = run_root / "manuscript" / "paper.tex"
    paper.write_text(
        paper.read_text(encoding="utf-8").replace("GPT 5.6", "GPT 4.1"),
        encoding="utf-8",
    )
    # Construct a self-consistent legacy fixture whose original recorded manuscript lacks the
    # new disclosure. The public state API correctly refuses to mutate an immutable artifact.
    relative = paper.relative_to(run_root).as_posix()
    digest = hashlib.sha256(paper.read_bytes()).hexdigest()
    state.stages[StageName.MANUSCRIPT].artifacts[relative] = digest
    state.artifact_hashes[relative] = digest
    save_state_atomic(state, run_root / "state.json")

    result = await verify_run(run_root, SuccessfulBackend())

    assert not result.passed
    bibliography = _component(result, ReproductionComponent.BIBLIOGRAPHY)
    assert bibliography.status == "failed"
    assert any("GPT 5.6" in item for item in bibliography.diagnostics)


@pytest.mark.asyncio
async def test_verify_run_checks_complete_report_certificate_inventory(tmp_path: Path) -> None:
    run_root, state = _create_run(tmp_path)
    start_stage(state, StageName.REPORT)
    report = write_final_report(state)
    for path in (report.report_json, report.report_markdown, report.verification_certificate):
        record_artifact_file(state, StageName.REPORT, path)
    succeed_stage(state, StageName.REPORT)
    save_state_atomic(state, run_root / "state.json")
    (run_root / "research" / "untracked-after-report.txt").write_text(
        "unexpected\n", encoding="utf-8"
    )

    result = await verify_run(run_root, SuccessfulBackend())

    assert not result.passed
    integrity = _component(result, ReproductionComponent.ARTIFACT_INTEGRITY)
    assert any("untracked-after-report.txt" in item for item in integrity.diagnostics)


@pytest.mark.asyncio
async def test_verify_run_truthfully_reports_compiler_failures(tmp_path: Path) -> None:
    run_root, _ = _create_run(tmp_path)

    result = await verify_run(run_root, FailingBackend())

    assert not result.passed
    assert _component(result, ReproductionComponent.LATEX).status == "failed"
    lean = _component(result, ReproductionComponent.LEAN)
    assert lean.status == "failed"
    assert any("Lean build exited with code 1" in item for item in lean.diagnostics)


@pytest.mark.asyncio
async def test_verify_run_skips_absent_optional_outputs(tmp_path: Path) -> None:
    run_root, _ = _create_run(tmp_path, with_artifacts=False)

    result = await verify_run(run_root, SuccessfulBackend())

    assert result.passed
    for component in (
        ReproductionComponent.BIBLIOGRAPHY,
        ReproductionComponent.LATEX,
        ReproductionComponent.LEAN,
    ):
        assert _component(result, component).status == "skipped"


@pytest.mark.asyncio
async def test_verify_run_returns_typed_failure_for_corrupt_state(tmp_path: Path) -> None:
    run_root = tmp_path / "broken-run"
    run_root.mkdir()
    (run_root / "state.json").write_text('{"truncated":', encoding="utf-8")

    result = await verify_run(run_root, SuccessfulBackend())

    assert not result.passed
    assert result.checks[0].component is ReproductionComponent.STATE
    assert result.checks[0].status is ReproductionCheckStatus.FAILED
    assert all(check.status is ReproductionCheckStatus.SKIPPED for check in result.checks[1:])
