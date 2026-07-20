from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ascend_math_agent.config import AppConfig
from ascend_math_agent.intake import IntakeError, _version, ingest_problem
from ascend_math_agent.models import StageName, StageStatus


def test_intake_preserves_original_and_normalizes_copy(tmp_path: Path) -> None:
    problem = tmp_path / "problem.txt"
    original = "  Prove \N{GREEK SMALL LETTER ALPHA}.\r\n\r\n"
    problem.write_bytes(original.encode("utf-8"))

    result = ingest_problem(
        problem_file=problem,
        project_root=tmp_path,
        config=AppConfig(),
        invocation={"no_lean": True, "api_key": "must-not-leak"},
        run_id="20260719T120000Z-intake-abcdef",
        snapshot={"fixture": True},
    )

    assert (result.run_root / "input" / "problem.original").read_bytes() == original.encode()
    assert result.problem_text == "Prove \N{GREEK SMALL LETTER ALPHA}.\n"
    invocation = json.loads(
        (result.run_root / "input" / "invocation.json").read_text(encoding="utf-8")
    )
    assert invocation["arguments"]["api_key"] == "[REDACTED]"
    assert "must-not-leak" not in (result.run_root / "state.json").read_text(encoding="utf-8")
    assert result.state.stages[StageName.INTAKE].status is StageStatus.SUCCEEDED


def test_intake_rejects_empty_problem_before_creating_run(tmp_path: Path) -> None:
    problem = tmp_path / "problem.md"
    problem.write_text("  \n", encoding="utf-8")
    with pytest.raises(IntakeError, match="empty"):
        ingest_problem(
            problem_file=problem,
            project_root=tmp_path,
            config=AppConfig(),
            invocation={},
        )
    assert not (tmp_path / ".ascend").exists()


def test_intake_redacts_credentials_embedded_in_problem_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = "fixture-credential-value-12345"
    monkeypatch.setenv("OPENAI_API_KEY", credential)
    problem = tmp_path / "problem.md"
    problem.write_text(f"Prove P.\nAPI key: {credential}\n", encoding="utf-8")

    result = ingest_problem(
        problem_file=problem,
        project_root=tmp_path,
        config=AppConfig(),
        invocation={},
        run_id="20260719T120000Z-redaction-abcdef",
        snapshot={},
    )

    artifact_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in result.run_root.rglob("*")
        if path.is_file()
    )
    assert credential not in artifact_text
    assert "[REDACTED]" in result.problem_text
    assert result.state.metadata["input_redactions"]["replacements"] >= 1


def test_environment_tool_versions_do_not_receive_or_persist_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fixture-access-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")
    observed_environment: dict[str, str] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args
        environment = kwargs.get("env")
        assert isinstance(environment, dict)
        observed_environment.update(environment)
        return subprocess.CompletedProcess(
            args=["tool"],
            returncode=0,
            stdout="Authorization: Bearer sk-test-secret-value\n",
            stderr="",
        )

    monkeypatch.setattr("ascend_math_agent.intake.shutil.which", lambda command: command)
    monkeypatch.setattr(subprocess, "run", fake_run)

    version = _version(("tool", "--version"))

    assert "AWS_ACCESS_KEY_ID" not in observed_environment
    assert "OPENAI_API_KEY" not in observed_environment
    assert version is not None
    assert "sk-test-secret-value" not in version
    assert "[REDACTED]" in version
