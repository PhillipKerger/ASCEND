from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from matek_theorem_agent.codex_client import (
    CodexExecClient,
    CodexJSONLError,
    CodexRequest,
)
from matek_theorem_agent.execution.base import CommandRequest, CommandResult, CommandTimeoutError
from matek_theorem_agent.execution.docker import DockerBackend
from matek_theorem_agent.execution.native import NativeBackend


class FakeBackend:
    def __init__(self, exec_stdout: str = '{"type":"turn.completed"}\n') -> None:
        self.requests: list[CommandRequest] = []
        self.exec_stdout = exec_stdout

    async def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        if request.argv[-1] == "--help":
            stdout = (
                "Usage: codex [OPTIONS]\n  --ask-for-approval POLICY\n  --config KEY=VALUE\n"
                "  --model MODEL\n"
                if request.argv == ("codex", "--help")
                else (
                    "Usage: codex exec [OPTIONS]\n  --json\n  --sandbox MODE\n"
                    "  -C DIR\n  --add-dir DIR\n  --ephemeral\n"
                    "  --ignore-user-config\n  --ignore-rules\n  --config KEY=VALUE\n"
                    "  --model MODEL\n"
                )
            )
        else:
            stdout = self.exec_stdout
        return CommandResult(request.argv, request.cwd, 0, stdout, "", 0.1)


@pytest.mark.asyncio
async def test_native_backend_stdin_and_bounded_output(tmp_path: Path) -> None:
    backend = NativeBackend()
    result = await backend.run(
        CommandRequest(
            argv=(
                sys.executable,
                "-c",
                "import sys; data=sys.stdin.read(); print(data); print('x'*100)",
            ),
            cwd=tmp_path,
            stdin="hello",
            max_output_bytes=16,
        )
    )
    assert result.exit_code == 0
    assert result.stdout.startswith("hello")
    assert result.stdout_truncated
    assert len(result.stdout.encode()) <= 16


@pytest.mark.asyncio
async def test_native_backend_timeout_retains_bounded_result(tmp_path: Path) -> None:
    backend = NativeBackend(termination_grace_seconds=0.1)
    with pytest.raises(CommandTimeoutError) as caught:
        await backend.run(
            CommandRequest(
                argv=(
                    sys.executable,
                    "-c",
                    "import time; print('start', flush=True); time.sleep(5)",
                ),
                cwd=tmp_path,
                timeout_seconds=1,
            )
        )
    assert caught.value.result.timed_out
    assert "start" in caught.value.result.stdout


@pytest.mark.asyncio
async def test_native_backend_redacts_command_output_before_returning_it(tmp_path: Path) -> None:
    result = await NativeBackend().run(
        CommandRequest(
            argv=(sys.executable, "-c", "print('Authorization: Bearer sk-secretsecret')"),
            cwd=tmp_path,
        )
    )

    assert "sk-secretsecret" not in result.stdout
    assert "[REDACTED]" in result.stdout


@pytest.mark.asyncio
async def test_native_backend_does_not_inherit_ambient_secret_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MATEK_TEST_SECRET_TOKEN", "do-not-inherit")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-platform-must-not-reach-codex")
    monkeypatch.setenv("CODEX_API_KEY", "codex-key-must-not-reach-child")

    result = await NativeBackend().run(
        CommandRequest(
            argv=(
                sys.executable,
                "-c",
                (
                    "import os; print(any(k in os.environ for k in "
                    "['MATEK_TEST_SECRET_TOKEN','OPENAI_API_KEY','CODEX_API_KEY']))"
                ),
            ),
            cwd=tmp_path,
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "False"


@pytest.mark.asyncio
async def test_codex_feature_detection_exact_args_stdin_and_jsonl(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    capture = tmp_path / "codex.jsonl"
    backend = FakeBackend('{"type":"item.completed","secret":"sk-secretsecret"}\n')
    client = CodexExecClient(backend)

    result = await client.execute(
        CodexRequest(
            prompt="prove it",
            cwd=tmp_path,
            writable_paths=(tmp_path, allowed),
            timeout_seconds=30,
            jsonl_path=capture,
        )
    )

    assert len(backend.requests) == 3
    command = backend.requests[2]
    assert command.argv == (
        "codex",
        "--ask-for-approval",
        "never",
        "--config",
        'model_reasoning_effort="xhigh"',
        "exec",
        "--json",
        "--sandbox",
        "workspace-write",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "-C",
        str(tmp_path.resolve()),
        "--add-dir",
        str(tmp_path.resolve()),
        "--add-dir",
        str(allowed.resolve()),
        "-",
    )
    assert command.stdin == "prove it"
    assert result.json_events[0]["secret"] == "[REDACTED]"
    assert json.loads(capture.read_text().strip())["secret"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_codex_rejects_invalid_jsonl_and_broad_write_path(tmp_path: Path) -> None:
    client = CodexExecClient(FakeBackend("not-json\n"))
    with pytest.raises(CodexJSONLError):
        await client.execute(CodexRequest("p", tmp_path, (tmp_path,), 30))

    child = tmp_path / "child"
    child.mkdir()
    client = CodexExecClient(FakeBackend())
    with pytest.raises(ValueError, match="broader than Codex cwd"):
        await client.execute(CodexRequest("p", child, (tmp_path,), 30))


@pytest.mark.asyncio
async def test_codex_allows_broader_project_path_only_with_explicit_opt_in(
    tmp_path: Path,
) -> None:
    child = tmp_path / "run" / "lean"
    child.mkdir(parents=True)
    backend = FakeBackend()
    client = CodexExecClient(backend)

    with pytest.raises(ValueError, match="broader than Codex cwd"):
        await client.execute(CodexRequest("p", child, (child, tmp_path), 30))

    result = await client.execute(
        CodexRequest(
            "p",
            child,
            (child, tmp_path),
            30,
            allow_broader_writes=True,
        )
    )

    assert result.exit_code == 0
    assert ("--add-dir", str(tmp_path.resolve())) in tuple(
        zip(result.command, result.command[1:], strict=False)
    )


@pytest.mark.asyncio
async def test_codex_preserves_nonzero_exit_with_empty_jsonl(tmp_path: Path) -> None:
    class FailureBackend(FakeBackend):
        async def run(self, request: CommandRequest) -> CommandResult:
            if request.argv[-1] == "--help":
                return await super().run(request)
            self.requests.append(request)
            return CommandResult(request.argv, request.cwd, 4, "", "authentication failed", 0.1)

    result = await CodexExecClient(FailureBackend()).execute(
        CodexRequest("p", tmp_path, (tmp_path,), 30)
    )
    assert result.exit_code == 4
    assert result.json_events == ()


@pytest.mark.asyncio
async def test_docker_backend_builds_restricted_argument_array(tmp_path: Path) -> None:
    native = FakeBackend()
    backend = DockerBackend("lean:test", native_backend=native)  # type: ignore[arg-type]
    result = await backend.run(CommandRequest(("lake", "build"), tmp_path))
    executed = native.requests[0]
    assert executed.argv[:3] == ("docker", "run", "--rm")
    assert "--read-only" in executed.argv
    network_index = executed.argv.index("--network")
    assert executed.argv[network_index + 1] == "none"
    mount_index = executed.argv.index("--mount")
    assert executed.argv[mount_index + 1].endswith(",readonly")
    assert executed.argv[-2:] == ("lake", "build")
    assert result.argv == ("lake", "build")

    stage = tmp_path / ".matek" / "runs" / "run-123" / "manuscript"
    stage.mkdir(parents=True)
    await backend.run(CommandRequest(("latexmk", "paper.tex"), stage))
    stage_command = native.requests[1]
    stage_mount_index = stage_command.argv.index("--mount")
    assert not stage_command.argv[stage_mount_index + 1].endswith(",readonly")
