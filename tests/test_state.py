from __future__ import annotations

from pathlib import Path

import pytest

from matek_theorem_agent.models import RunState, StageName, StageStatus, new_run_state
from matek_theorem_agent.state import (
    ArtifactIntegrityError,
    IllegalStageTransition,
    fail_stage,
    first_incomplete_stage,
    invalidate_from,
    load_state,
    prepare_for_resume,
    record_artifact_file,
    save_state_atomic,
    start_stage,
    succeed_stage,
)
from matek_theorem_agent.workspace import create_run_root


def _state(tmp_path: Path) -> RunState:
    run_root = create_run_root(tmp_path, run_id="20260719T123456Z-state-abcdef")
    return new_run_state(run_root.name, tmp_path, run_root)


def test_state_round_trip(tmp_path: Path) -> None:
    state = RunState(run_id="test", project_root=tmp_path, run_root=tmp_path / "run", stages={})
    path = tmp_path / "state.json"
    save_state_atomic(state, path)
    assert load_state(path).run_id == "test"


def test_legal_transitions_increment_attempts_and_allow_retry(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert start_stage(state, StageName.INTAKE).attempts == 1
    failed = fail_stage(state, StageName.INTAKE, "temporary", retriable=True)
    assert failed.failure is not None and failed.failure.retriable
    assert start_stage(state, StageName.INTAKE).attempts == 2
    assert succeed_stage(state, StageName.INTAKE).status is StageStatus.SUCCEEDED


def test_illegal_transition_is_rejected(tmp_path: Path) -> None:
    state = _state(tmp_path)
    with pytest.raises(IllegalStageTransition):
        succeed_stage(state, StageName.INTAKE)
    start_stage(state, StageName.INTAKE)
    succeed_stage(state, StageName.INTAKE)
    with pytest.raises(IllegalStageTransition):
        start_stage(state, StageName.INTAKE)


def test_resume_marks_stale_running_stage_interrupted(tmp_path: Path) -> None:
    state = _state(tmp_path)
    start_stage(state, StageName.INTAKE)
    assert prepare_for_resume(state) is StageName.INTAKE
    assert state.stages[StageName.INTAKE].status is StageStatus.INTERRUPTED


def test_load_ignores_truncated_temporary_when_primary_is_valid(tmp_path: Path) -> None:
    state = _state(tmp_path)
    path = state.run_root / "state.json"
    save_state_atomic(state, path)
    path.with_suffix(".json.tmp").write_text('{"truncated":', encoding="utf-8")
    assert load_state(path).run_id == state.run_id


def test_load_promotes_complete_temporary_when_primary_is_missing(tmp_path: Path) -> None:
    state = _state(tmp_path)
    path = state.run_root / "state.json"
    path.with_suffix(".json.tmp").write_text(state.model_dump_json(), encoding="utf-8")
    assert load_state(path).run_id == state.run_id
    assert path.is_file()


def test_load_prefers_newer_complete_temporary_over_stale_valid_primary(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    path = state.run_root / "state.json"
    save_state_atomic(state, path)
    stale_generation = state.checkpoint_generation
    newer = state.model_copy(deep=True)
    newer.checkpoint_generation = stale_generation + 1
    newer.metadata["checkpoint"] = "newer"
    path.with_suffix(".json.tmp").write_text(
        newer.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    loaded = load_state(path)

    assert loaded.metadata["checkpoint"] == "newer"
    assert loaded.checkpoint_generation > stale_generation
    assert load_state(path).metadata["checkpoint"] == "newer"


def test_load_recovers_last_good_backup(tmp_path: Path) -> None:
    state = _state(tmp_path)
    path = state.run_root / "state.json"
    save_state_atomic(state, path)
    start_stage(state, StageName.INTAKE)
    save_state_atomic(state, path)  # first snapshot is now state.json.bak
    path.write_text('{"truncated":', encoding="utf-8")
    recovered = load_state(path)
    assert recovered.stages[StageName.INTAKE].status is StageStatus.PENDING
    assert load_state(path, recover=False).run_id == state.run_id


def test_state_persistence_redacts_token_shaped_failure_text(tmp_path: Path) -> None:
    state = _state(tmp_path)
    start_stage(state, StageName.INTAKE)
    secret = "sk-proj-super-secret-token"
    fail_stage(state, StageName.INTAKE, f"Authorization: Bearer {secret}")
    path = state.run_root / "state.json"
    save_state_atomic(state, path)
    assert secret not in path.read_text(encoding="utf-8")


def test_artifact_hash_is_immutable_until_explicit_invalidation(tmp_path: Path) -> None:
    state = _state(tmp_path)
    artifact = state.run_root / "input" / "problem.md"
    artifact.write_text("first", encoding="utf-8")
    first_digest = record_artifact_file(state, StageName.INTAKE, artifact)
    artifact.write_text("second", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="immutable artifact changed"):
        record_artifact_file(state, StageName.INTAKE, artifact)

    invalidate_from(state, StageName.INTAKE, "explicit force")
    second_digest = record_artifact_file(state, StageName.INTAKE, artifact)
    assert first_digest != second_digest


def test_invalidation_clears_target_and_downstream_not_upstream(tmp_path: Path) -> None:
    state = _state(tmp_path)
    start_stage(state, StageName.INTAKE)
    succeed_stage(state, StageName.INTAKE)
    start_stage(state, StageName.PROMPT_COMPILATION)
    succeed_stage(state, StageName.PROMPT_COMPILATION)
    invalidated = invalidate_from(state, StageName.PROMPT_COMPILATION, "new framework")
    assert StageName.PROMPT_COMPILATION in invalidated
    assert state.stages[StageName.INTAKE].status is StageStatus.SUCCEEDED
    assert state.stages[StageName.PROMPT_COMPILATION].status is StageStatus.PENDING
    assert first_incomplete_stage(state) is StageName.PROMPT_COMPILATION
