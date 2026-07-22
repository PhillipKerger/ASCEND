"""Core persisted domain models for workflow state."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class _StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class StageName(StrEnum):
    INTAKE = "intake"
    PROMPT_COMPILATION = "prompt_compilation"
    RESEARCH = "research"
    RESEARCH_AUDIT = "research_audit"
    MANUSCRIPT = "manuscript"
    BIBLIOGRAPHY = "bibliography"
    LEAN_FEASIBILITY = "lean_feasibility"
    LEAN_ALIGNMENT = "lean_alignment"
    LEAN_FORMALIZATION = "lean_formalization"
    LEAN_VERIFICATION = "lean_verification"
    REPORT = "report"


STAGE_ORDER: tuple[StageName, ...] = tuple(StageName)


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    INTERRUPTED = "interrupted"


class ScientificStatus(StrEnum):
    """Truthful user-facing statuses required by the product contract."""

    RECEIVED = "RECEIVED"
    NEEDS_PROBLEM_CLARIFICATION = "NEEDS_PROBLEM_CLARIFICATION"
    PROMPT_COMPILED = "PROMPT_COMPILED"
    RESEARCH_RUNNING = "RESEARCH_RUNNING"
    RESEARCH_PARTIAL = "RESEARCH_PARTIAL"
    RESEARCH_REJECTED = "RESEARCH_REJECTED"
    RESEARCH_ACCEPTED_FOR_MANUSCRIPT = "RESEARCH_ACCEPTED_FOR_MANUSCRIPT"
    MANUSCRIPT_FAILED = "MANUSCRIPT_FAILED"
    MANUSCRIPT_COMPILED = "MANUSCRIPT_COMPILED"
    BIBLIOGRAPHY_REJECTED = "BIBLIOGRAPHY_REJECTED"
    BIBLIOGRAPHY_VERIFIED = "BIBLIOGRAPHY_VERIFIED"
    LEAN_NOT_REQUESTED = "LEAN_NOT_REQUESTED"
    LEAN_INFEASIBLE = "LEAN_INFEASIBLE"
    LEAN_STATEMENT_ONLY = "LEAN_STATEMENT_ONLY"
    LEAN_PARTIAL = "LEAN_PARTIAL"
    LEAN_FAILED = "LEAN_FAILED"
    LEAN_VERIFIED_WITH_APPROVED_AXIOMS = "LEAN_VERIFIED_WITH_APPROVED_AXIOMS"
    LEAN_VERIFIED = "LEAN_VERIFIED"
    REPORT_COMPLETE = "REPORT_COMPLETE"


class FailureInfo(_StateModel):
    kind: str
    message: str
    occurred_at: datetime = Field(default_factory=utc_now)
    retriable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind", "message")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("failure kind and message must not be blank")
        return value.strip()


class StageRecord(_StateModel):
    name: StageName
    status: StageStatus = StageStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    # Keys are run-relative artifact paths, values are lowercase SHA-256 hashes.
    artifacts: dict[str, str] = Field(default_factory=dict)
    paid_call_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)
    failure: FailureInfo | None = None
    # Kept for backwards compatibility with the initial scaffold. New code should
    # prefer the structured ``failure`` field.
    error: str | None = None
    invalidated_reason: str | None = None

    @field_validator("artifacts")
    @classmethod
    def _artifact_hashes_are_sha256(cls, value: dict[str, str]) -> dict[str, str]:
        for path, digest in value.items():
            if (
                path in {"", "."}
                or "\x00" in path
                or "\\" in path
                or Path(path).is_absolute()
                or ".." in Path(path).parts
            ):
                raise ValueError(f"artifact path must be run-relative and confined: {path!r}")
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"artifact hash is not lowercase SHA-256: {digest!r}")
        return value

    @field_validator("paid_call_ids")
    @classmethod
    def _paid_call_ids_are_unique(cls, value: list[str]) -> list[str]:
        if any(not identifier.strip() for identifier in value):
            raise ValueError("paid call IDs must not be blank")
        if len(value) != len(set(value)):
            raise ValueError("paid call IDs must be unique")
        return value


class RunState(_StateModel):
    schema_version: int = Field(default=1, ge=1)
    checkpoint_generation: int = Field(default=0, ge=0)
    run_id: str
    project_root: Path
    run_root: Path
    stages: dict[StageName, StageRecord]
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    paid_call_ids: list[str] = Field(default_factory=list)
    scientific_status: ScientificStatus = ScientificStatus.RECEIVED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("stages")
    @classmethod
    def _stage_keys_match_records(
        cls, value: dict[StageName, StageRecord]
    ) -> dict[StageName, StageRecord]:
        for stage_name, record in value.items():
            if stage_name != record.name:
                raise ValueError(
                    f"stage mapping key {stage_name.value!r} does not match record "
                    f"{record.name.value!r}"
                )
        return value

    @field_validator("artifact_hashes")
    @classmethod
    def _run_artifact_hashes_are_sha256(cls, value: dict[str, str]) -> dict[str, str]:
        # Reuse exactly the stage-level path/hash contract.
        StageRecord(name=StageName.INTAKE, artifacts=value)
        return value

    @field_validator("paid_call_ids")
    @classmethod
    def _run_paid_call_ids_are_unique(cls, value: list[str]) -> list[str]:
        if any(not identifier.strip() for identifier in value):
            raise ValueError("paid call IDs must not be blank")
        if len(value) != len(set(value)):
            raise ValueError("paid call IDs must be unique")
        return value


def initial_stage_records() -> dict[StageName, StageRecord]:
    return {name: StageRecord(name=name) for name in STAGE_ORDER}


def new_run_state(
    run_id: str,
    project_root: Path,
    run_root: Path,
    *,
    now: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> RunState:
    timestamp = now or utc_now()
    return RunState(
        run_id=run_id,
        project_root=project_root.resolve(),
        run_root=run_root.resolve(),
        stages=initial_stage_records(),
        created_at=timestamp,
        updated_at=timestamp,
        metadata=metadata or {},
    )


__all__ = [
    "STAGE_ORDER",
    "FailureInfo",
    "RunState",
    "ScientificStatus",
    "StageName",
    "StageRecord",
    "StageStatus",
    "initial_stage_records",
    "new_run_state",
    "utc_now",
]
