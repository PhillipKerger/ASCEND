"""Model-free reproducibility checks for a persisted ASCEND run.

The public :func:`verify_run` entry point deliberately does not update ``state.json`` and
does not call a model.  Compiler commands run against temporary copies of generated source
where practical, so re-verification does not replace the workflow's immutable artifacts.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from .config import AppConfig, ConfigError, load_config
from .execution.base import (
    CommandRequest,
    CommandResult,
    CommandTimeoutError,
    ExecutionBackend,
)
from .models import RunState, StageName, StageStatus
from .redaction import SecretRedactor
from .state import StateCorruptionError, load_state, verify_recorded_artifacts
from .verification import (
    VerificationCertificate,
    classify_latex_result,
    extract_theorem_statements,
    validate_ascend_ai_usage,
    validate_bibliography_files,
    verify_build,
)
from .workspace import ensure_path_confined, sha256_file


class ReproductionComponent(StrEnum):
    STATE = "state"
    ARTIFACT_INTEGRITY = "artifact_integrity"
    CONFIGURATION = "configuration"
    BIBLIOGRAPHY = "bibliography"
    LATEX = "latex"
    LEAN = "lean"


class ReproductionCheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReproductionCheck(BaseModel):
    """One deterministic check and its safe, user-facing evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    component: ReproductionComponent
    status: ReproductionCheckStatus
    summary: str
    diagnostics: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def name(self) -> str:
        """Concise display name retained for CLI-oriented callers."""

        return self.component.value

    @property
    def passed(self) -> bool:
        """Skipped checks are neutral; only an explicit failure fails the run."""

        return self.status is not ReproductionCheckStatus.FAILED


class RunVerificationResult(BaseModel):
    """Aggregate result returned to the CLI and other offline callers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    passed: bool
    checks: list[ReproductionCheck]

    @property
    def failed_checks(self) -> tuple[ReproductionCheck, ...]:
        return tuple(
            check for check in self.checks if check.status is ReproductionCheckStatus.FAILED
        )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class _LeanMetadata(TypedDict):
    statement_hash: str
    theorem_name: str | None


async def verify_run(
    run_root: Path,
    backend: ExecutionBackend,
    *,
    config: AppConfig | None = None,
) -> RunVerificationResult:
    """Re-run every applicable deterministic check for an existing run.

    ``config`` is an injection seam for callers that already loaded the frozen snapshot.
    Normal callers should leave it unset; current environment variables and the project's
    mutable ``ascend.toml`` are intentionally ignored in favor of
    ``input/config.resolved.toml``.
    """

    redactor = SecretRedactor()
    root, state, state_check = _load_run_state(run_root, redactor)
    checks = [state_check]
    if state is None or root is None:
        checks.extend(
            _skipped_after_state_failure(component)
            for component in (
                ReproductionComponent.ARTIFACT_INTEGRITY,
                ReproductionComponent.CONFIGURATION,
                ReproductionComponent.BIBLIOGRAPHY,
                ReproductionComponent.LATEX,
                ReproductionComponent.LEAN,
            )
        )
        return _aggregate(run_root.name, checks)

    integrity_check, integrity_problems = _check_artifact_integrity(state, redactor)
    checks.append(integrity_check)

    loaded_config, configuration_check = _load_frozen_config(
        state,
        config,
        integrity_problems,
        redactor,
    )
    checks.append(configuration_check)
    checks.append(_check_bibliography(root, state, redactor))

    if loaded_config is None:
        checks.extend(
            (
                ReproductionCheck(
                    component=ReproductionComponent.LATEX,
                    status=ReproductionCheckStatus.SKIPPED,
                    summary="LaTeX verification requires a valid frozen configuration.",
                ),
                ReproductionCheck(
                    component=ReproductionComponent.LEAN,
                    status=ReproductionCheckStatus.SKIPPED,
                    summary="Lean verification requires a valid frozen configuration.",
                ),
            )
        )
        return _aggregate(state.run_id, checks)

    checks.append(await _check_latex(root, loaded_config, backend, redactor))
    checks.append(await _check_lean(root, state, loaded_config, backend, redactor))
    return _aggregate(state.run_id, checks)


def _aggregate(run_id: str, checks: list[ReproductionCheck]) -> RunVerificationResult:
    return RunVerificationResult(
        run_id=run_id,
        passed=all(check.passed for check in checks),
        checks=checks,
    )


def _load_run_state(
    run_root: Path,
    redactor: SecretRedactor,
) -> tuple[Path | None, RunState | None, ReproductionCheck]:
    try:
        root = run_root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"run root is not a directory: {run_root}")
        state = load_state(root / "state.json", recover=False)
        if state.run_root.expanduser().resolve(strict=False) != root:
            raise StateCorruptionError(
                "persisted run_root does not match the selected run directory"
            )
        if state.run_id != root.name:
            raise StateCorruptionError("persisted run_id does not match the run directory name")
    except (OSError, ValueError, StateCorruptionError) as exc:
        diagnostic = redactor.redact_text(str(exc))
        return (
            None,
            None,
            ReproductionCheck(
                component=ReproductionComponent.STATE,
                status=ReproductionCheckStatus.FAILED,
                summary="The persisted run state could not be loaded safely.",
                diagnostics=[diagnostic],
            ),
        )
    return (
        root,
        state,
        ReproductionCheck(
            component=ReproductionComponent.STATE,
            status=ReproductionCheckStatus.PASSED,
            summary="The persisted run state is valid and matches the selected run.",
            details={"schema_version": state.schema_version},
        ),
    )


def _skipped_after_state_failure(component: ReproductionComponent) -> ReproductionCheck:
    return ReproductionCheck(
        component=component,
        status=ReproductionCheckStatus.SKIPPED,
        summary="Skipped because the persisted run state is unavailable.",
    )


def _check_artifact_integrity(
    state: RunState,
    redactor: SecretRedactor,
) -> tuple[ReproductionCheck, dict[str, str]]:
    problems = verify_recorded_artifacts(state)
    for stage_name, stage in state.stages.items():
        for relative, stage_digest in stage.artifacts.items():
            run_digest = state.artifact_hashes.get(relative)
            if run_digest is None:
                problems.setdefault(
                    relative,
                    f"recorded by stage {stage_name.value} but absent from the run hash index",
                )
            elif run_digest != stage_digest:
                problems.setdefault(
                    relative,
                    f"stage {stage_name.value} records {stage_digest}, "
                    f"run index records {run_digest}",
                )

    config_relative = "input/config.resolved.toml"
    if config_relative not in state.artifact_hashes:
        problems.setdefault(config_relative, "frozen configuration has no recorded hash")

    certificate_path = state.run_root / "report" / "verification_certificate.json"
    report_succeeded = (
        state.stages.get(StageName.REPORT) is not None
        and state.stages[StageName.REPORT].status is StageStatus.SUCCEEDED
    )
    if report_succeeded or certificate_path.exists():
        try:
            certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
            inventory = certificate.get("artifact_hashes")
            if not isinstance(inventory, dict) or not all(
                isinstance(path, str) and isinstance(digest, str)
                for path, digest in inventory.items()
            ):
                raise ValueError("certificate artifact_hashes must be a string mapping")
            expected_inventory = {str(path): str(digest) for path, digest in inventory.items()}
            current_inventory = _current_nonreport_inventory(state.run_root)
            for relative, expected in expected_inventory.items():
                actual = current_inventory.get(relative)
                if actual is None:
                    problems.setdefault(relative, "missing from current report inventory")
                elif actual != expected:
                    problems.setdefault(relative, f"expected {expected}, got {actual}")
            for relative in sorted(current_inventory.keys() - expected_inventory.keys()):
                problems.setdefault(relative, "not recorded by the verification certificate")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            problems.setdefault(
                "report/verification_certificate.json",
                f"invalid verification certificate: {exc}",
            )

    if problems:
        diagnostics = [
            redactor.redact_text(f"{path}: {problem}") for path, problem in sorted(problems.items())
        ]
        safe_problems = redactor.redact_data(dict(sorted(problems.items())))
        if not isinstance(safe_problems, dict):  # pragma: no cover - mapping stays a mapping
            safe_problems = {}
        return (
            ReproductionCheck(
                component=ReproductionComponent.ARTIFACT_INTEGRITY,
                status=ReproductionCheckStatus.FAILED,
                summary=f"{len(problems)} recorded artifact integrity check(s) failed.",
                diagnostics=diagnostics,
                details={"problems": safe_problems},
            ),
            problems,
        )
    return (
        ReproductionCheck(
            component=ReproductionComponent.ARTIFACT_INTEGRITY,
            status=ReproductionCheckStatus.PASSED,
            summary=f"Verified {len(state.artifact_hashes)} immutable artifact hash(es).",
            details={"artifact_count": len(state.artifact_hashes)},
        ),
        {},
    )


def _current_nonreport_inventory(run_root: Path) -> dict[str, str]:
    inventory: dict[str, str] = {}
    for unresolved in sorted(run_root.rglob("*")):
        relative = unresolved.relative_to(run_root).as_posix()
        if (
            relative.startswith("report/")
            or relative in {"state.json", "state.json.bak", "state.json.tmp"}
            or relative.endswith(".tmp")
        ):
            continue
        if unresolved.is_symlink():
            raise ValueError(f"artifact inventory contains a symlink: {relative}")
        if unresolved.is_file():
            path = ensure_path_confined(run_root, unresolved)
            inventory[relative] = sha256_file(path)
    return inventory


def _load_frozen_config(
    state: RunState,
    injected: AppConfig | None,
    integrity_problems: dict[str, str],
    redactor: SecretRedactor,
) -> tuple[AppConfig | None, ReproductionCheck]:
    relative = "input/config.resolved.toml"
    if injected is not None:
        return (
            injected,
            ReproductionCheck(
                component=ReproductionComponent.CONFIGURATION,
                status=ReproductionCheckStatus.PASSED,
                summary="Using the caller-provided resolved configuration.",
            ),
        )
    if relative in integrity_problems:
        return (
            None,
            ReproductionCheck(
                component=ReproductionComponent.CONFIGURATION,
                status=ReproductionCheckStatus.FAILED,
                summary="The frozen configuration failed integrity verification.",
                diagnostics=[redactor.redact_text(integrity_problems[relative])],
            ),
        )
    try:
        path = _regular_artifact(state.run_root, relative)
        loaded = load_config(path, project_root=state.project_root, env={})
    except (OSError, ValueError, ConfigError) as exc:
        return (
            None,
            ReproductionCheck(
                component=ReproductionComponent.CONFIGURATION,
                status=ReproductionCheckStatus.FAILED,
                summary="The frozen configuration could not be loaded.",
                diagnostics=[redactor.redact_text(str(exc))],
            ),
        )
    return (
        loaded,
        ReproductionCheck(
            component=ReproductionComponent.CONFIGURATION,
            status=ReproductionCheckStatus.PASSED,
            summary="Loaded the immutable resolved configuration without environment overrides.",
        ),
    )


def _check_bibliography(
    root: Path,
    state: RunState,
    redactor: SecretRedactor,
) -> ReproductionCheck:
    tex = root / "manuscript" / "paper.tex"
    bib = root / "manuscript" / "references.bib"
    if not tex.exists() and not bib.exists():
        return ReproductionCheck(
            component=ReproductionComponent.BIBLIOGRAPHY,
            status=ReproductionCheckStatus.SKIPPED,
            summary="No final manuscript bibliography exists for this run.",
        )
    missing = [path.name for path in (tex, bib) if not path.is_file()]
    if missing:
        return ReproductionCheck(
            component=ReproductionComponent.BIBLIOGRAPHY,
            status=ReproductionCheckStatus.FAILED,
            summary="The final manuscript bibliography is incomplete.",
            diagnostics=[f"missing required artifact: manuscript/{name}" for name in missing],
        )

    audit = root / "manuscript" / "bibliography_audit.json"
    audit_path: Path | None = audit if audit.is_file() else None
    bibliography_stage = state.stages.get(StageName.BIBLIOGRAPHY)
    if (
        audit_path is None
        and bibliography_stage is not None
        and bibliography_stage.status is StageStatus.SUCCEEDED
    ):
        return ReproductionCheck(
            component=ReproductionComponent.BIBLIOGRAPHY,
            status=ReproductionCheckStatus.FAILED,
            summary="A successful bibliography stage has no final audit artifact.",
            diagnostics=["missing required artifact: manuscript/bibliography_audit.json"],
        )
    try:
        report = validate_bibliography_files(
            _regular_artifact(root, "manuscript/paper.tex"),
            _regular_artifact(root, "manuscript/references.bib"),
            _regular_artifact(root, "manuscript/bibliography_audit.json")
            if audit_path is not None
            else None,
        )
        ai_usage = validate_ascend_ai_usage(
            _regular_artifact(root, "manuscript/paper.tex").read_text(encoding="utf-8"),
            _regular_artifact(root, "manuscript/references.bib").read_text(encoding="utf-8"),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return ReproductionCheck(
            component=ReproductionComponent.BIBLIOGRAPHY,
            status=ReproductionCheckStatus.FAILED,
            summary="Bibliography consistency verification could not complete.",
            diagnostics=[redactor.redact_text(str(exc))],
        )

    diagnostics = [redactor.redact_text(issue.message) for issue in report.issues]
    diagnostics.extend(redactor.redact_text(issue.message) for issue in ai_usage.issues)
    diagnostics.extend(
        redactor.redact_text(f"warning: {warning.message}") for warning in report.warnings
    )
    passed = report.passed and ai_usage.passed
    status = ReproductionCheckStatus.PASSED if passed else ReproductionCheckStatus.FAILED
    details = report.to_dict()
    details["ai_usage"] = ai_usage.to_dict()
    return ReproductionCheck(
        component=ReproductionComponent.BIBLIOGRAPHY,
        status=status,
        summary=(
            f"Bibliography consistency passed for {len(report.cited_keys)} cited key(s)."
            if passed
            else "Bibliography and attribution consistency found "
            f"{len(report.issues) + len(ai_usage.issues)} blocking issue(s)."
        ),
        diagnostics=diagnostics,
        details=_safe_details(details, redactor),
    )


async def _check_latex(
    root: Path,
    config: AppConfig,
    backend: ExecutionBackend,
    redactor: SecretRedactor,
) -> ReproductionCheck:
    manuscript = root / "manuscript"
    paper = manuscript / "paper.tex"
    if not paper.is_file():
        return ReproductionCheck(
            component=ReproductionComponent.LATEX,
            status=ReproductionCheckStatus.SKIPPED,
            summary="No final paper.tex exists for this run.",
        )

    try:
        report_directory = _verified_report_directory(root)
        with tempfile.TemporaryDirectory(
            prefix=".ascend-latex-verify-", dir=report_directory
        ) as temporary_name:
            temporary = Path(temporary_name)
            _copy_manuscript_inputs(manuscript, temporary)
            request = CommandRequest(
                argv=tuple(config.manuscript.latex_command),
                cwd=temporary,
                timeout_seconds=600,
            )
            result, command_error = await _execute(backend, request, redactor)
            if result is None:
                return ReproductionCheck(
                    component=ReproductionComponent.LATEX,
                    status=ReproductionCheckStatus.FAILED,
                    summary="The configured LaTeX command could not be executed.",
                    diagnostics=[command_error or "LaTeX backend returned no result."],
                )
            report = classify_latex_result(result)
            pdf = temporary / "paper.pdf"
            pdf_exists = pdf.is_file() and pdf.stat().st_size > 0
            diagnostics = [redactor.redact_text(issue.message) for issue in report.issues]
            if not pdf_exists:
                diagnostics.append("LaTeX did not produce a nonempty paper.pdf.")
            passed = report.passed and pdf_exists
            details = report.to_dict()
            details.update(
                {
                    "command": _safe_argv(result.argv, redactor),
                    "pdf_nonempty": pdf_exists,
                    "timed_out": result.timed_out,
                }
            )
            details = _safe_details(details, redactor)
    except (OSError, ValueError) as exc:
        return ReproductionCheck(
            component=ReproductionComponent.LATEX,
            status=ReproductionCheckStatus.FAILED,
            summary="The isolated LaTeX verification workspace could not be prepared.",
            diagnostics=[redactor.redact_text(str(exc))],
        )

    return ReproductionCheck(
        component=ReproductionComponent.LATEX,
        status=(ReproductionCheckStatus.PASSED if passed else ReproductionCheckStatus.FAILED),
        summary=(
            "The configured LaTeX command reproduced a nonempty paper.pdf."
            if passed
            else "The configured LaTeX command did not reproduce a valid paper.pdf."
        ),
        diagnostics=diagnostics,
        details=details,
    )


async def _check_lean(
    root: Path,
    state: RunState,
    config: AppConfig,
    backend: ExecutionBackend,
    redactor: SecretRedactor,
) -> ReproductionCheck:
    lean_dir = root / "lean"
    final_sources = sorted(
        path for path in lean_dir.glob("*.lean") if path.name != "_AscendAxiomCheck.lean"
    )
    if not final_sources:
        return ReproductionCheck(
            component=ReproductionComponent.LEAN,
            status=ReproductionCheckStatus.SKIPPED,
            summary="No final run-local Lean source exists for this run.",
        )

    required = {"challenge.lean", "Main.lean"}
    present = {path.name for path in final_sources if path.is_file()}
    missing = sorted(required - present)
    if missing:
        return ReproductionCheck(
            component=ReproductionComponent.LEAN,
            status=ReproductionCheckStatus.FAILED,
            summary="The final Lean source set is incomplete.",
            diagnostics=[f"missing required artifact: lean/{name}" for name in missing],
        )

    try:
        metadata = _lean_metadata(lean_dir)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return ReproductionCheck(
            component=ReproductionComponent.LEAN,
            status=ReproductionCheckStatus.FAILED,
            summary="Lean verification metadata is missing or invalid.",
            diagnostics=[redactor.redact_text(str(exc))],
        )

    approved_hash = metadata["statement_hash"]
    theorem_name = metadata["theorem_name"]
    project_root = state.project_root.expanduser().resolve(strict=False)
    if not project_root.is_dir():
        return ReproductionCheck(
            component=ReproductionComponent.LEAN,
            status=ReproductionCheckStatus.FAILED,
            summary="The recorded Lean project root is unavailable.",
            diagnostics=[redactor.redact_text(f"missing project root: {project_root}")],
        )

    try:
        report_directory = _verified_report_directory(root)
        with tempfile.TemporaryDirectory(
            prefix=".ascend-lean-verify-", dir=report_directory
        ) as temporary_name:
            temporary = Path(temporary_name)
            for source in final_sources:
                if source.is_symlink() or not source.is_file():
                    raise ValueError(f"Lean source must be a regular non-symlink file: {source}")
                shutil.copy2(source, temporary / source.name)

            challenge = temporary / "challenge.lean"
            theorem_name = _resolve_theorem_name(challenge, approved_hash, theorem_name)
            challenge_argument = _project_argument(project_root, challenge)
            build_request = CommandRequest(
                argv=(
                    "lake",
                    "lean",
                    challenge_argument,
                ),
                cwd=project_root,
                timeout_seconds=600,
            )
            build, build_error = await _execute(backend, build_request, redactor)
            if build is None:
                return ReproductionCheck(
                    component=ReproductionComponent.LEAN,
                    status=ReproductionCheckStatus.FAILED,
                    summary="The deterministic Lean build command could not be executed.",
                    diagnostics=[build_error or "Lean backend returned no result."],
                )

            axiom_exit_code: int | None = None
            axiom_output = ""
            axiom_error: str | None = None
            axiom_result: CommandResult | None = None
            if build.exit_code == 0 and not build.timed_out:
                axiom_source = temporary / "_AscendAxiomCheck.lean"
                axiom_source.write_text(
                    challenge.read_text(encoding="utf-8").rstrip()
                    + f"\n\n#print axioms {theorem_name}\n",
                    encoding="utf-8",
                )
                axiom_argument = _project_argument(project_root, axiom_source)
                axiom_request = CommandRequest(
                    argv=(
                        "lake",
                        "lean",
                        axiom_argument,
                    ),
                    cwd=project_root,
                    timeout_seconds=600,
                )
                axiom_result, axiom_error = await _execute(backend, axiom_request, redactor)
                if axiom_result is not None:
                    axiom_exit_code = axiom_result.exit_code
                    axiom_output = f"{axiom_result.stdout}\n{axiom_result.stderr}"
                axiom_source.unlink(missing_ok=True)

            certificate = verify_build(
                temporary,
                approved_hash,
                build,
                axiom_output,
                config.lean.approved_axioms,
                theorem_name=theorem_name,
                statement_file=challenge,
            )
            passed, diagnostics = _lean_outcome(
                certificate,
                axiom_result,
                axiom_error,
            )
            diagnostics = [redactor.redact_text(item) for item in diagnostics]
            details = _lean_details(certificate, build, axiom_exit_code, redactor)
    except (OSError, UnicodeError, ValueError) as exc:
        return ReproductionCheck(
            component=ReproductionComponent.LEAN,
            status=ReproductionCheckStatus.FAILED,
            summary="The isolated Lean verification workspace could not be prepared.",
            diagnostics=[redactor.redact_text(str(exc))],
        )

    return ReproductionCheck(
        component=ReproductionComponent.LEAN,
        status=(ReproductionCheckStatus.PASSED if passed else ReproductionCheckStatus.FAILED),
        summary=(
            f"Lean reproducibility passed with status {certificate.status.value}."
            if passed
            else "Lean deterministic verification failed."
        ),
        diagnostics=diagnostics,
        details=details,
    )


async def _execute(
    backend: ExecutionBackend,
    request: CommandRequest,
    redactor: SecretRedactor,
) -> tuple[CommandResult | None, str | None]:
    try:
        return await backend.run(request), None
    except CommandTimeoutError as exc:
        return exc.result, redactor.redact_text(str(exc))
    except Exception as exc:  # an adapter failure is a check failure, not a CLI traceback
        return None, redactor.redact_text(f"{type(exc).__name__}: {exc}")


def _regular_artifact(root: Path, relative: str) -> Path:
    unresolved = root / relative
    if unresolved.is_symlink():
        raise ValueError(f"artifact must not be a symlink: {relative}")
    path = ensure_path_confined(root, unresolved)
    if not path.is_file():
        raise ValueError(f"artifact is not a regular file: {relative}")
    return path


def _verified_report_directory(root: Path) -> Path:
    unresolved = root / "report"
    if unresolved.is_symlink():
        raise ValueError("run report directory is unavailable or unsafe")
    report = ensure_path_confined(root, unresolved)
    if not report.is_dir():
        raise ValueError("run report directory is unavailable or unsafe")
    return report


def _copy_manuscript_inputs(source: Path, destination: Path) -> None:
    """Copy manuscript inputs without following links or copying prior build products."""

    excluded_directories = {"drafts"}
    excluded_files = {"paper.pdf", "build.log", "result.json"}
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in sorted(directories):
            candidate = current_path / name
            if candidate.is_symlink():
                raise ValueError(f"manuscript input directory is a symlink: {candidate}")
            if candidate.relative_to(source).parts[0] not in excluded_directories:
                safe_directories.append(name)
        directories[:] = safe_directories
        relative_directory = current_path.relative_to(source)
        target_directory = destination / relative_directory
        target_directory.mkdir(parents=True, exist_ok=True)
        for name in sorted(files):
            if name in excluded_files:
                continue
            candidate = current_path / name
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"manuscript input is not a regular file: {candidate}")
            shutil.copy2(candidate, target_directory / name)


def _lean_metadata(lean_dir: Path) -> _LeanMetadata:
    result_path = lean_dir / "result.json"
    statement_hash: str | None = None
    theorem_name: str | None = None
    if result_path.is_symlink():
        raise ValueError("lean/result.json must not be a symlink")
    if result_path.is_file():
        value = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("lean/result.json must contain a JSON object")
        raw_hash = value.get("approved_statement_hash")
        if isinstance(raw_hash, str):
            statement_hash = raw_hash.strip().lower()
        statement_draft = value.get("statement_draft")
        if isinstance(statement_draft, dict):
            raw_name = statement_draft.get("theorem_name")
            if isinstance(raw_name, str) and raw_name.strip():
                theorem_name = raw_name.strip()

    formalization_path = lean_dir / "formalization.yaml"
    if formalization_path.is_symlink():
        raise ValueError("lean/formalization.yaml must not be a symlink")
    if formalization_path.is_file() and (statement_hash is None or theorem_name is None):
        formalization = formalization_path.read_text(encoding="utf-8")
        if statement_hash is None:
            statement_hash = _simple_yaml_string(formalization, "statement_hash")
            if statement_hash is not None:
                statement_hash = statement_hash.lower()
        if theorem_name is None:
            theorem_name = _simple_yaml_string(formalization, "main_theorem_name")

    if statement_hash is None or re.fullmatch(r"[0-9a-f]{64}", statement_hash) is None:
        raise ValueError("Lean approved statement hash is missing or invalid")
    return {"statement_hash": statement_hash, "theorem_name": theorem_name}


def _simple_yaml_string(source: str, key: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", source)
    if match is None:
        return None
    value = match.group(1).strip()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = value
    if not isinstance(decoded, str) or not decoded.strip():
        return None
    return decoded.strip()


def _resolve_theorem_name(
    challenge: Path,
    approved_hash: str,
    candidate: str | None,
) -> str:
    statements = extract_theorem_statements(challenge.read_text(encoding="utf-8"))
    if candidate is not None:
        named = [statement for statement in statements if statement.name == candidate]
        if len(named) != 1:
            raise ValueError(f"approved theorem {candidate!r} is not declared exactly once")
        return candidate
    matching = [statement.name for statement in statements if statement.sha256 == approved_hash]
    if len(matching) != 1:
        raise ValueError("cannot identify exactly one theorem with the approved statement hash")
    return matching[0]


def _lean_outcome(
    certificate: VerificationCertificate,
    axiom_result: CommandResult | None,
    axiom_error: str | None,
) -> tuple[bool, list[str]]:
    diagnostics = [issue.message for issue in certificate.issues]
    axiom_passed = (
        axiom_result is not None
        and axiom_result.exit_code == 0
        and not axiom_result.timed_out
        and not axiom_result.stdout_truncated
        and not axiom_result.stderr_truncated
    )
    if axiom_result is None:
        diagnostics.append(axiom_error or "The #print axioms command did not run.")
    elif axiom_result.exit_code != 0 or axiom_result.timed_out:
        diagnostics.append(f"The #print axioms command exited with code {axiom_result.exit_code}.")
    elif axiom_result.stdout_truncated or axiom_result.stderr_truncated:
        diagnostics.append("The #print axioms output was truncated.")
    return certificate.passed and axiom_passed, list(dict.fromkeys(diagnostics))


def _lean_details(
    certificate: VerificationCertificate,
    build: CommandResult,
    axiom_exit_code: int | None,
    redactor: SecretRedactor,
) -> dict[str, Any]:
    details = {
        "status": certificate.status.value,
        "checks": dict(sorted(certificate.checks.items())),
        "issues": [issue.to_dict() for issue in certificate.issues],
        "theorem_name": certificate.theorem_name,
        "approved_statement_hash": certificate.approved_statement_hash,
        "actual_statement_hash": certificate.actual_statement_hash,
        "lean_file_hashes": dict(sorted(certificate.lean_file_hashes.items())),
        "used_axioms": list(certificate.used_axioms),
        "approved_axioms": list(certificate.approved_axioms),
        "unapproved_axioms": list(certificate.unapproved_axioms),
        "build_exit_code": build.exit_code,
        "axiom_exit_code": axiom_exit_code,
        "build_command": _safe_argv(build.argv, redactor),
    }
    return _safe_details(details, redactor)


def _project_argument(project_root: Path, path: Path) -> str:
    """Prefer a project-relative compiler argument so restricted backends can map it."""

    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return str(path)


def _safe_details(details: dict[str, Any], redactor: SecretRedactor) -> dict[str, Any]:
    safe = redactor.redact_data(details)
    if not isinstance(safe, dict):  # pragma: no cover - mapping stays a mapping
        return {}
    return {str(key): value for key, value in safe.items()}


def _safe_argv(argv: tuple[str, ...], redactor: SecretRedactor) -> list[str]:
    return [redactor.redact_text(argument) for argument in argv]


__all__ = [
    "ReproductionCheck",
    "ReproductionCheckStatus",
    "ReproductionComponent",
    "RunVerificationResult",
    "verify_run",
]
