"""Legal workflow transitions and crash-safe state persistence."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import (
    STAGE_ORDER,
    FailureCategory,
    FailureInfo,
    RunState,
    StageName,
    StageRecord,
    StageStatus,
    utc_now,
)
from .redaction import redact_data, redact_text
from .workspace import (
    atomic_write_bytes,
    ensure_path_confined,
    relative_artifact_path,
    sha256_file,
)


class StateError(RuntimeError):
    """Base error for workflow-state operations."""


class IllegalStageTransition(StateError):
    """Raised when a stage status change violates the state machine."""


class StateCorruptionError(StateError):
    """Raised when no complete, valid state snapshot can be recovered."""


class ArtifactIntegrityError(StateError):
    """Raised when an immutable artifact hash changes or fails verification."""


LEGAL_STAGE_TRANSITIONS: dict[StageStatus, frozenset[StageStatus]] = {
    StageStatus.PENDING: frozenset({StageStatus.RUNNING, StageStatus.SKIPPED}),
    StageStatus.RUNNING: frozenset(
        {StageStatus.SUCCEEDED, StageStatus.FAILED, StageStatus.INTERRUPTED}
    ),
    StageStatus.FAILED: frozenset({StageStatus.RUNNING}),
    StageStatus.INTERRUPTED: frozenset({StageStatus.RUNNING}),
    StageStatus.SUCCEEDED: frozenset(),
    StageStatus.SKIPPED: frozenset(),
}


def is_legal_transition(current: StageStatus, target: StageStatus) -> bool:
    return target in LEGAL_STAGE_TRANSITIONS[current]


def _timestamp(now: datetime | None) -> datetime:
    return now or utc_now()


def _get_or_create_stage(state: RunState, stage: StageName) -> StageRecord:
    record = state.stages.get(stage)
    if record is not None:
        return record
    record = StageRecord(name=stage)
    stages = dict(state.stages)
    stages[stage] = record
    state.stages = stages
    return record


def transition_stage(
    state: RunState,
    stage: StageName,
    target: StageStatus,
    *,
    now: datetime | None = None,
) -> StageRecord:
    """Perform one legal status transition and return the updated record."""

    record = _get_or_create_stage(state, stage)
    if not is_legal_transition(record.status, target):
        raise IllegalStageTransition(
            f"cannot transition {stage.value} from {record.status.value} to {target.value}"
        )
    timestamp = _timestamp(now)
    record.status = target
    record.updated_at = timestamp
    state.updated_at = timestamp
    if target is StageStatus.RUNNING:
        record.attempts += 1
        record.started_at = timestamp
        record.completed_at = None
        record.failure = None
        record.error = None
        record.invalidated_reason = None
    elif target in {
        StageStatus.SUCCEEDED,
        StageStatus.FAILED,
        StageStatus.INTERRUPTED,
        StageStatus.SKIPPED,
    }:
        record.completed_at = timestamp
    return record


def start_stage(state: RunState, stage: StageName, *, now: datetime | None = None) -> StageRecord:
    return transition_stage(state, stage, StageStatus.RUNNING, now=now)


def succeed_stage(state: RunState, stage: StageName, *, now: datetime | None = None) -> StageRecord:
    return transition_stage(state, stage, StageStatus.SUCCEEDED, now=now)


# A conventional alias used by stage/application code.
complete_stage = succeed_stage


def fail_stage(
    state: RunState,
    stage: StageName,
    message: str,
    *,
    kind: str = "stage_failure",
    category: FailureCategory = FailureCategory.EXECUTION,
    retriable: bool = False,
    details: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> StageRecord:
    timestamp = _timestamp(now)
    record = transition_stage(state, stage, StageStatus.FAILED, now=timestamp)
    safe_message = redact_text(message)
    safe_details = redact_data(details or {})
    if not isinstance(safe_details, dict):  # pragma: no cover - mapping input remains a mapping
        safe_details = {}
    failure = FailureInfo(
        kind=kind,
        message=safe_message,
        category=category,
        occurred_at=timestamp,
        retriable=retriable,
        details=safe_details,
    )
    record.failure = failure
    record.error = safe_message

    history_value = state.metadata.get("failure_history", [])
    history = list(history_value) if isinstance(history_value, list) else []
    history.append(
        {
            "stage": stage.value,
            **failure.model_dump(mode="json"),
        }
    )
    state.metadata = {**state.metadata, "failure_history": history}
    return record


def interrupt_stage(
    state: RunState,
    stage: StageName,
    message: str = "interrupted",
    *,
    now: datetime | None = None,
) -> StageRecord:
    timestamp = _timestamp(now)
    record = transition_stage(state, stage, StageStatus.INTERRUPTED, now=timestamp)
    safe_message = redact_text(message)
    record.failure = FailureInfo(
        kind="interrupted",
        message=safe_message,
        category=FailureCategory.EXECUTION,
        occurred_at=timestamp,
        retriable=True,
    )
    record.error = safe_message
    return record


def interrupt_running_stages(
    state: RunState,
    message: str = "interrupted",
    *,
    now: datetime | None = None,
) -> tuple[StageName, ...]:
    """Mark all in-flight stages interrupted so the snapshot can be resumed."""

    interrupted: list[StageName] = []
    for stage in STAGE_ORDER:
        record = state.stages.get(stage)
        if record is not None and record.status is StageStatus.RUNNING:
            interrupt_stage(state, stage, message, now=now)
            interrupted.append(stage)
    return tuple(interrupted)


def skip_stage(
    state: RunState,
    stage: StageName,
    reason: str,
    *,
    now: datetime | None = None,
) -> StageRecord:
    if not reason.strip():
        raise ValueError("skip reason must not be blank")
    record = transition_stage(state, stage, StageStatus.SKIPPED, now=now)
    record.error = reason.strip()
    return record


def first_incomplete_stage(state: RunState) -> StageName | None:
    """Return the first stage that is neither successful nor intentionally skipped."""

    for stage in STAGE_ORDER:
        record = state.stages.get(stage)
        if record is None or record.status not in {StageStatus.SUCCEEDED, StageStatus.SKIPPED}:
            return stage
    return None


def prepare_for_resume(
    state: RunState,
    *,
    now: datetime | None = None,
) -> StageName | None:
    """Normalize stale RUNNING records after a crash and locate the resume boundary."""

    interrupt_running_stages(state, "process ended before a stage checkpoint", now=now)
    return first_incomplete_stage(state)


# A concise alias for callers presenting a status screen.
next_resumable_stage = first_incomplete_stage


def invalidate_from(
    state: RunState,
    stage: StageName,
    reason: str,
    *,
    now: datetime | None = None,
) -> tuple[StageName, ...]:
    """Explicitly invalidate ``stage`` and every downstream stage.

    Attempts and paid-call IDs remain as audit history. Artifact hashes are removed
    only through this explicit operation, allowing a forced rerun to record new
    immutable outputs without weakening ordinary integrity checks.
    """

    if not reason.strip():
        raise ValueError("invalidation reason must not be blank")
    start_index = STAGE_ORDER.index(stage)
    timestamp = _timestamp(now)
    invalidated: list[StageName] = []
    global_hashes = dict(state.artifact_hashes)
    stages = dict(state.stages)
    for name in STAGE_ORDER[start_index:]:
        existing = stages.get(name)
        if existing is None:
            stages[name] = StageRecord(
                name=name, updated_at=timestamp, invalidated_reason=reason.strip()
            )
            invalidated.append(name)
            continue
        for artifact_path in existing.artifacts:
            global_hashes.pop(artifact_path, None)
        existing.status = StageStatus.PENDING
        existing.artifacts = {}
        existing.started_at = None
        existing.completed_at = None
        existing.failure = None
        existing.error = None
        existing.invalidated_reason = reason.strip()
        existing.updated_at = timestamp
        invalidated.append(name)
    state.stages = stages
    state.artifact_hashes = global_hashes
    state.updated_at = timestamp
    return tuple(invalidated)


# Alternate phrasing kept explicit for discoverability.
invalidate_stage_and_downstream = invalidate_from


def _validate_sha256(digest: str) -> None:
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ArtifactIntegrityError(f"not a lowercase SHA-256 digest: {digest!r}")


def record_artifact_hash(
    state: RunState,
    stage: StageName,
    artifact_path: str,
    digest: str,
    *,
    now: datetime | None = None,
) -> None:
    """Record a hash once; a later different value is always corruption."""

    _validate_sha256(digest)
    normalized_path = Path(artifact_path).as_posix()
    normalized = Path(normalized_path)
    if (
        normalized_path in {"", "."}
        or "\x00" in normalized_path
        or "\\" in normalized_path
        or normalized.is_absolute()
        or ".." in normalized.parts
    ):
        raise ArtifactIntegrityError(f"artifact path is not run-relative: {artifact_path!r}")

    record = _get_or_create_stage(state, stage)
    existing_stage_hash = record.artifacts.get(normalized_path)
    existing_run_hash = state.artifact_hashes.get(normalized_path)
    for existing in (existing_stage_hash, existing_run_hash):
        if existing is not None and existing != digest:
            raise ArtifactIntegrityError(
                f"immutable artifact changed: {normalized_path} was {existing}, now {digest}"
            )

    stage_hashes = dict(record.artifacts)
    stage_hashes[normalized_path] = digest
    record.artifacts = stage_hashes
    run_hashes = dict(state.artifact_hashes)
    run_hashes[normalized_path] = digest
    state.artifact_hashes = run_hashes
    timestamp = _timestamp(now)
    record.updated_at = timestamp
    state.updated_at = timestamp


def record_artifact_file(
    state: RunState,
    stage: StageName,
    path: Path,
    *,
    now: datetime | None = None,
) -> str:
    """Confine, hash, and record an existing run artifact."""

    resolved = ensure_path_confined(state.run_root, path)
    if not resolved.is_file():
        raise ArtifactIntegrityError(f"artifact is not a regular file: {path}")
    relative = relative_artifact_path(state.run_root, resolved)
    digest = sha256_file(resolved)
    record_artifact_hash(state, stage, relative, digest, now=now)
    return digest


def verify_recorded_artifacts(state: RunState) -> dict[str, str]:
    """Return path-to-problem details for missing or modified recorded artifacts."""

    problems: dict[str, str] = {}
    for relative, expected in state.artifact_hashes.items():
        try:
            path = ensure_path_confined(state.run_root, state.run_root / relative)
        except ValueError as exc:
            problems[relative] = str(exc)
            continue
        if not path.is_file():
            problems[relative] = "missing"
            continue
        actual = sha256_file(path)
        if actual != expected:
            problems[relative] = f"expected {expected}, got {actual}"
    return problems


def assert_recorded_artifacts(state: RunState) -> None:
    problems = verify_recorded_artifacts(state)
    if problems:
        details = "; ".join(f"{path}: {problem}" for path, problem in sorted(problems.items()))
        raise ArtifactIntegrityError(f"artifact verification failed: {details}")


def record_paid_call(
    state: RunState,
    stage: StageName,
    call_id: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Checkpoint a paid call ID, returning false when already checkpointed."""

    identifier = call_id.strip()
    if not identifier:
        raise ValueError("paid call ID must not be blank")
    record = _get_or_create_stage(state, stage)
    if identifier in state.paid_call_ids:
        if identifier not in record.paid_call_ids:
            raise StateError(f"paid call ID already belongs to another stage: {identifier}")
        return False
    record.paid_call_ids = [*record.paid_call_ids, identifier]
    state.paid_call_ids = [*state.paid_call_ids, identifier]
    timestamp = _timestamp(now)
    record.updated_at = timestamp
    state.updated_at = timestamp
    return True


def _temporary_state_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".tmp")


def _backup_state_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _parse_state_bytes(contents: bytes, source: Path) -> RunState:
    try:
        return RunState.model_validate_json(contents)
    except (ValidationError, ValueError) as exc:
        raise StateCorruptionError(f"invalid state snapshot {source}: {exc}") from exc


def save_state_atomic(state: RunState, path: Path) -> None:
    """Persist ``state`` using fsync + atomic rename and a last-good backup.

    The stable ``.tmp`` filename is intentional: if a process dies after writing a
    complete temporary snapshot but before rename, :func:`load_state` can validate and
    promote it. A truncated temporary never replaces a valid primary snapshot.
    """

    target = path.expanduser().resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_state_path(target)
    backup = _backup_state_path(target)

    # Persist a monotonic generation so recovery can distinguish a newer complete
    # temporary checkpoint from a valid but stale primary snapshot.
    state.checkpoint_generation += 1
    safe_state = redact_data(state.model_dump(mode="json"))
    serialized = f"{json.dumps(safe_state, ensure_ascii=False, indent=2)}\n".encode()
    # Preserve the previous snapshot only when it is actually valid. This prevents a
    # truncated primary from overwriting an older recoverable backup.
    if target.is_file():
        previous = target.read_bytes()
        try:
            _parse_state_bytes(previous, target)
        except StateCorruptionError:
            pass
        else:
            atomic_write_bytes(backup, previous)

    atomic_write_bytes(temporary, serialized)
    os.replace(temporary, target)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(target.parent, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def load_state(path: Path, *, recover: bool = True) -> RunState:
    """Load state and optionally recover a valid temporary/backup checkpoint."""

    target = path.expanduser().resolve(strict=False)
    temporary = _temporary_state_path(target)
    backup = _backup_state_path(target)
    errors: list[str] = []
    saw_file = False
    valid: list[tuple[Path, RunState]] = []
    for candidate in (target, temporary) if recover else (target,):
        if not candidate.is_file():
            continue
        saw_file = True
        try:
            state = _parse_state_bytes(candidate.read_bytes(), candidate)
        except (OSError, StateCorruptionError) as exc:
            errors.append(str(exc))
            continue
        valid.append((candidate, state))

    if valid:
        # On equal generations the primary has completed the atomic rename and wins.
        candidate, state = max(
            valid,
            key=lambda item: (
                item[1].checkpoint_generation,
                1 if item[0] == target else 0,
            ),
        )
        if candidate != target and recover:
            save_state_atomic(state, target)
        return state

    if recover and backup.is_file():
        saw_file = True
        try:
            state = _parse_state_bytes(backup.read_bytes(), backup)
        except (OSError, StateCorruptionError) as exc:
            errors.append(str(exc))
        else:
            save_state_atomic(state, target)
            return state
    if not saw_file:
        raise FileNotFoundError(target)
    raise StateCorruptionError("; ".join(errors) or f"unable to read state: {target}")


class StateStore:
    """Run-confined convenience wrapper for application services."""

    def __init__(self, run_root: Path, filename: str = "state.json") -> None:
        self.run_root = run_root.expanduser().resolve(strict=True)
        self.path = ensure_path_confined(self.run_root, self.run_root / filename)

    def save(self, state: RunState) -> None:
        if state.run_root.resolve(strict=False) != self.run_root:
            raise StateError(
                f"state run root {state.run_root} does not match store root {self.run_root}"
            )
        save_state_atomic(state, self.path)

    def load(self, *, recover: bool = True) -> RunState:
        state = load_state(self.path, recover=recover)
        if state.run_root.resolve(strict=False) != self.run_root:
            raise StateCorruptionError(
                f"persisted run root {state.run_root} does not match {self.run_root}"
            )
        return state


__all__ = [
    "LEGAL_STAGE_TRANSITIONS",
    "ArtifactIntegrityError",
    "IllegalStageTransition",
    "StateCorruptionError",
    "StateError",
    "StateStore",
    "assert_recorded_artifacts",
    "complete_stage",
    "fail_stage",
    "first_incomplete_stage",
    "interrupt_running_stages",
    "interrupt_stage",
    "invalidate_from",
    "invalidate_stage_and_downstream",
    "is_legal_transition",
    "load_state",
    "next_resumable_stage",
    "prepare_for_resume",
    "record_artifact_file",
    "record_artifact_hash",
    "record_paid_call",
    "save_state_atomic",
    "skip_stage",
    "start_stage",
    "succeed_stage",
    "transition_stage",
    "verify_recorded_artifacts",
]
