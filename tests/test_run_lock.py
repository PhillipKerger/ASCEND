from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from matek_theorem_agent.application import (
    WorkflowDependencies,
    WorkflowOptions,
    WorkflowRunner,
)
from matek_theorem_agent.codex_client import CodexRequest
from matek_theorem_agent.config import AppConfig
from matek_theorem_agent.execution.base import CommandRequest
from matek_theorem_agent.openai_client import ModelRequest
from matek_theorem_agent.reporting import assert_report_certificate_inventory
from matek_theorem_agent.workspace import RunLock, RunLockHeldError


class BlockingModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self._release = asyncio.Event()

    async def generate_structured(
        self,
        request: ModelRequest,
        output_type: type[BaseModel],
    ) -> Any:
        del request, output_type
        self.started.set()
        await self._release.wait()
        raise AssertionError("blocking model was unexpectedly released")


class ForbiddenBackend:
    async def run(self, request: CommandRequest) -> Any:
        del request
        raise AssertionError("run-lock test reached an execution backend")


class ForbiddenCodex:
    async def execute(self, request: CodexRequest) -> Any:
        del request
        raise AssertionError("run-lock test reached Codex")


@pytest.mark.asyncio
async def test_active_execution_blocks_resume_and_both_report_writers(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    problem = project / "problem.md"
    problem.write_text("# Problem\n\nProve the fixture statement.\n", encoding="utf-8")
    model = BlockingModel()
    runner = WorkflowRunner(
        AppConfig(project_root=project),
        WorkflowDependencies(
            model_client=model,
            execution_backend=ForbiddenBackend(),
            codex_client=ForbiddenCodex(),
        ),
    )

    running = asyncio.create_task(
        runner.run_new(
            problem,
            project,
            options=WorkflowOptions(research_only=True),
            environment_snapshot={"fixture": "offline"},
        )
    )
    await asyncio.wait_for(model.started.wait(), timeout=2)
    [run_root] = (project / ".matek" / "runs").iterdir()

    with pytest.raises(RunLockHeldError, match="already active"):
        await runner.resume(project, run_id=run_root.name)
    with pytest.raises(RunLockHeldError, match="already active"):
        await runner.rewrite_report(project, run_id=run_root.name)
    with pytest.raises(RunLockHeldError, match="already active"):
        runner.regenerate_report(project, run_id=run_root.name)

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    # Cancellation releases the kernel lock, and the external lock file does not
    # destabilize the run's deterministic report certificate.
    with RunLock(run_root):
        pass
    assert_report_certificate_inventory(run_root)
