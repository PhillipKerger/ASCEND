from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

import ascend_math_agent.cli as cli_module
from ascend_math_agent.accounting import AccountingModelClient
from ascend_math_agent.application import (
    WorkflowDependencies,
    WorkflowRunner,
    _model_cache_namespace,
)
from ascend_math_agent.budget import BudgetExceeded, BudgetTracker
from ascend_math_agent.cli import app
from ascend_math_agent.config import AppConfig, BackendSettings, Limits, ModelSettings
from ascend_math_agent.intake import ingest_problem
from ascend_math_agent.logging import RunLogger
from ascend_math_agent.models import new_run_state
from ascend_math_agent.openai_client import ModelRequest, ModelResult
from ascend_math_agent.reporting import build_final_report, write_final_report
from ascend_math_agent.state import StateStore
from ascend_math_agent.workspace import create_run_root


class _UnusedModel:
    async def generate_structured(self, request: Any, output_type: type[BaseModel]) -> Any:
        del request, output_type
        raise AssertionError("model client should not be called")


class _UnusedExecution:
    async def run(self, request: Any) -> Any:
        del request
        raise AssertionError("execution backend should not be called")


class _UnusedCodex:
    async def execute(self, request: Any) -> Any:
        del request
        raise AssertionError("formalization Codex client should not be called")


def _dependencies() -> WorkflowDependencies:
    return WorkflowDependencies(
        model_client=_UnusedModel(),  # type: ignore[arg-type]
        execution_backend=_UnusedExecution(),  # type: ignore[arg-type]
        codex_client=_UnusedCodex(),  # type: ignore[arg-type]
    )


def _problem(project: Path) -> Path:
    path = project / "problem.md"
    path.write_text("Prove P.\n", encoding="utf-8")
    return path


def test_live_runner_uses_codex_by_default_and_api_only_when_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[tuple[str, dict[str, Any]]] = []

    class FakeCodexModel:
        def __init__(self, workspace: Path, **kwargs: Any) -> None:
            constructed.append(("codex", {"workspace": workspace, **kwargs}))

    class FakeApiModel:
        def __init__(self, **kwargs: Any) -> None:
            constructed.append(("api", kwargs))

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-ambient-key-must-not-select-api")
    monkeypatch.setattr(cli_module, "CodexCliModelClient", FakeCodexModel)
    monkeypatch.setattr(cli_module, "OpenAIResponsesClient", FakeApiModel)
    monkeypatch.setattr(cli_module, "CodexExecClient", lambda *args, **kwargs: _UnusedCodex())
    monkeypatch.setattr(cli_module, "_execution_backend", lambda config: _UnusedExecution())

    default_runner = cli_module._live_runner(AppConfig(project_root=tmp_path))

    assert isinstance(default_runner.dependencies.model_client, FakeCodexModel)
    assert [provider for provider, _ in constructed] == ["codex"]

    explicit_api = AppConfig(
        project_root=tmp_path,
        backend=BackendSettings(provider="api"),
    )
    api_runner = cli_module._live_runner(explicit_api)

    assert isinstance(api_runner.dependencies.model_client, FakeApiModel)
    assert [provider for provider, _ in constructed] == ["codex", "api"]


def test_codex_construction_failure_never_falls_back_to_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_constructions = 0

    def failed_codex(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("Codex unavailable")

    def forbidden_api(**kwargs: Any) -> None:
        nonlocal api_constructions
        del kwargs
        api_constructions += 1
        raise AssertionError("API fallback was attempted")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-ambient-key-must-not-select-api")
    monkeypatch.setattr(cli_module, "CodexCliModelClient", failed_codex)
    monkeypatch.setattr(cli_module, "OpenAIResponsesClient", forbidden_api)
    monkeypatch.setattr(cli_module, "_execution_backend", lambda config: _UnusedExecution())

    with pytest.raises(RuntimeError, match="Codex unavailable"):
        cli_module._live_runner(AppConfig(project_root=tmp_path))

    assert api_constructions == 0


def test_cli_backend_flag_overrides_environment_and_project_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "ascend.toml").write_text(
        'config_version = 2\n[backend]\nprovider = "codex"\n',
        encoding="utf-8",
    )
    problem = _problem(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASCEND_BACKEND", "codex")

    result = CliRunner().invoke(
        app,
        ["run", str(problem), "--backend", "api", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "model backend" in result.output
    assert "api" in result.output
    assert not (tmp_path / ".ascend").exists()


@pytest.mark.asyncio
async def test_resume_uses_frozen_backend_even_when_runner_started_with_another_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    frozen = AppConfig(
        project_root=project,
        backend=BackendSettings(provider="api"),
    )
    intake = ingest_problem(
        problem_file=_problem(project),
        project_root=project,
        config=frozen,
        invocation={},
        run_id="20260719T120000Z-frozen-abcdef",
        snapshot={},
    )
    runner = WorkflowRunner(AppConfig(project_root=project), _dependencies())
    observed: list[str] = []

    async def stop_before_work(state: Any, options: Any) -> str:
        del state, options
        observed.append(runner.config.backend.provider)
        return "stopped"

    monkeypatch.setattr(runner, "_execute", stop_before_work)

    result = await runner.resume(project, run_id=intake.state.run_id)

    assert result == "stopped"
    assert observed == ["api"]
    persisted = StateStore(intake.run_root).load()
    assert persisted.metadata["backend"]["provider"] == "api"
    assert persisted.metadata["backend_history"] == []


@pytest.mark.asyncio
async def test_explicit_resume_backend_migration_records_provenance_and_new_cache_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    intake = ingest_problem(
        problem_file=_problem(project),
        project_root=project,
        config=AppConfig(
            project_root=project,
            backend=BackendSettings(provider="api"),
        ),
        invocation={},
        run_id="20260719T120000Z-migration-abcdef",
        snapshot={},
    )
    runner = WorkflowRunner(AppConfig(project_root=project), _dependencies())

    async def stop_before_work(state: Any, options: Any) -> str:
        del state, options
        return "stopped"

    monkeypatch.setattr(runner, "_execute", stop_before_work)

    await runner.resume(
        project,
        run_id=intake.state.run_id,
        config_overrides={"backend": "codex"},
    )

    persisted = StateStore(intake.run_root).load()
    assert persisted.metadata["backend"]["provider"] == "codex"
    assert persisted.metadata["backend"]["authentication_class"] == "unverified"
    assert persisted.metadata["model_cache_generation"] == 1
    assert _model_cache_namespace(persisted) == "codex-generation-1"
    assert persisted.metadata["backend_history"] == [
        {
            "from": "api",
            "to": "codex",
            "changed_at": persisted.metadata["backend_history"][0]["changed_at"],
            "reason": "explicit resume backend migration",
            "provenance_warning": (
                "Model behavior and provider provenance differ after this checkpoint."
            ),
            "usage_at_switch": {},
        }
    ]
    effective = (intake.run_root / "config" / "effective_config.toml").read_text(encoding="utf-8")
    assert '[backend]\nprovider = "codex"' in effective
    assert (
        json.loads(
            (intake.run_root / "config" / "backend_manifest.json").read_text(encoding="utf-8")
        )["provider"]
        == "codex"
    )
    assert build_final_report(persisted).backend_history == persisted.metadata["backend_history"]


def test_model_cache_namespace_is_provider_scoped_but_legacy_runs_remain_readable(
    tmp_path: Path,
) -> None:
    run_root = create_run_root(
        tmp_path,
        run_id="20260719T120000Z-cache-abcdef",
    )
    state = new_run_state(run_root.name, tmp_path, run_root)
    state.metadata.update(
        {
            "model_cache_schema_version": 2,
            "model_cache_generation": 3,
            "backend": {"provider": "codex"},
        }
    )

    assert _model_cache_namespace(state) == "codex-generation-3"
    state.metadata["backend"] = {"provider": "api"}
    assert _model_cache_namespace(state) == "api-generation-3"
    state.metadata.pop("model_cache_schema_version")
    assert _model_cache_namespace(state) == "generation-3"


class _Answer(BaseModel):
    value: str


class _BlockingUnknownCostClient:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_structured(
        self,
        request: ModelRequest,
        output_type: type[_Answer],
    ) -> ModelResult[_Answer]:
        del request
        self.calls += 1
        call = self.calls
        self.started.set()
        await self.release.wait()
        return ModelResult(
            parsed=output_type(value="ok"),
            response_id=f"codex-thread-{call}",
            input_tokens=4,
            output_tokens=2,
            total_tokens=6,
            estimated_cost_usd=None,
            request_metadata={"backend": "codex"},
        )


@pytest.mark.asyncio
async def test_codex_unknown_cost_is_allowed_but_concurrent_call_limit_is_atomic(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    (run_root / "logs").mkdir(parents=True)
    tracker = BudgetTracker(
        Limits(maximum_cost_usd=0.0),
        maximum_calls=1,
        enforce_cost_budget=False,
    )
    delegate = _BlockingUnknownCostClient()
    client = AccountingModelClient(
        delegate,  # type: ignore[arg-type]
        stage="research",
        budget=tracker,
        logger=RunLogger(run_root, model_cache_namespace="codex-generation-0"),
    )
    first = asyncio.create_task(
        client.generate_structured(
            ModelRequest("solve", "route one", ModelSettings()),
            _Answer,
        )
    )
    await delegate.started.wait()

    try:
        with pytest.raises(BudgetExceeded) as raised:
            await client.generate_structured(
                ModelRequest("solve", "route two", ModelSettings()),
                _Answer,
            )
    finally:
        delegate.release.set()
    await first

    snapshot = tracker.snapshot()
    assert raised.value.dimension == "calls"
    assert delegate.calls == 1
    assert snapshot.calls == 1
    assert snapshot.unknown_cost_calls == 1
    assert snapshot.cost_usd == 0.0


def test_status_and_report_describe_codex_allowance_without_inventing_dollar_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    intake = ingest_problem(
        problem_file=_problem(tmp_path),
        project_root=tmp_path,
        config=AppConfig(project_root=tmp_path),
        invocation={},
        run_id="20260719T120000Z-wording-abcdef",
        snapshot={},
    )
    state = intake.state
    state.metadata["backend"].update(
        {
            "provider": "codex",
            "display_name": "Codex CLI",
            "authentication_class": "chatgpt",
            "backend_version": "codex-cli 1.2.3",
            "model_requested": None,
            "reasoning_effort_requested": "xhigh",
            "web_search_enabled": True,
        }
    )
    state.metadata["usage"] = {
        "calls": 2,
        "total_tokens": 42,
        "unknown_cost_calls": 2,
        # Even malformed/legacy metadata must not be rendered as a Codex price.
        "cost_usd": 123.45,
    }
    StateStore(intake.run_root).save(state)
    monkeypatch.chdir(tmp_path)

    status = CliRunner().invoke(app, ["status", state.run_id])

    assert status.exit_code == 0, status.output
    assert "ChatGPT subscription" in status.output
    assert "reasoning effort xhigh" in status.output
    assert "Codex allowance/credits (no dollar estimate)" in status.output
    assert "$123" not in status.output

    report = write_final_report(state)
    markdown = report.report_markdown.read_text(encoding="utf-8")
    assert "Codex CLI using saved ChatGPT authentication" in markdown
    assert "Authentication class: ChatGPT subscription (`chatgpt`)" in markdown
    assert "Requested reasoning effort: `xhigh`" in markdown
    assert "ASCEND does not convert ChatGPT/Codex allowance" in markdown


def test_report_labels_explicit_api_mode_as_platform_billing(tmp_path: Path) -> None:
    run_root = create_run_root(
        tmp_path,
        run_id="20260719T120000Z-api-report-abcdef",
    )
    (run_root / "input" / "problem.md").write_text("Prove P.\n", encoding="utf-8")
    state = new_run_state(run_root.name, tmp_path, run_root)
    state.metadata["backend"] = {
        "provider": "api",
        "display_name": "OpenAI Responses API",
        "authentication_class": "platform_api_key",
        "backend_version": "openai-python fixture",
        "model_requested": "gpt-5.6-sol",
        "automatic_fallback": False,
    }

    markdown = write_final_report(state).report_markdown.read_text(encoding="utf-8")

    assert "OpenAI Responses API using Platform API billing" in markdown
    assert "OpenAI Platform API key (`platform_api_key`)" in markdown
    assert "Codex allowance/credits" not in markdown
