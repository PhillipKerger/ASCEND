from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..redaction import redact_text, sanitized_environment
from .base import CommandRequest, CommandResult, CommandTimeoutError

_READ_CHUNK_SIZE = 64 * 1024


async def _read_bounded(stream: asyncio.StreamReader | None, limit: int) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False

    captured = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        remaining = limit - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            truncated = True
    return bytes(captured), truncated


async def _write_stdin(stream: asyncio.StreamWriter | None, payload: str | bytes | None) -> None:
    if stream is None:
        return
    try:
        if payload is not None:
            encoded = payload.encode("utf-8") if isinstance(payload, str) else payload
            stream.write(encoded)
            await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        # A command may legitimately exit without consuming all input.
        pass
    finally:
        stream.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await stream.wait_closed()


class NativeBackend:
    """Execute argument arrays with bounded output and process-group timeouts."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        subprocess_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
        termination_grace_seconds: float = 2.0,
    ) -> None:
        if termination_grace_seconds < 0:
            raise ValueError("termination_grace_seconds must not be negative")
        self._clock = clock
        self._subprocess_factory = subprocess_factory
        self._termination_grace_seconds = termination_grace_seconds

    async def run(self, request: CommandRequest) -> CommandResult:
        cwd = _validated_cwd(request.cwd)
        started = self._clock()
        process_kwargs: dict[str, object] = {}
        if os.name == "posix":
            process_kwargs["start_new_session"] = True
        elif os.name == "nt":  # pragma: no cover - release-tested on Windows/WSL
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        process = await self._subprocess_factory(
            *request.argv,
            cwd=cwd,
            env=_sanitized_environment(request.argv),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **process_kwargs,
        )
        stdout_task = asyncio.create_task(_read_bounded(process.stdout, request.max_output_bytes))
        stderr_task = asyncio.create_task(_read_bounded(process.stderr, request.max_output_bytes))
        stdin_task = asyncio.create_task(_write_stdin(process.stdin, request.stdin))

        try:
            await asyncio.wait_for(process.wait(), timeout=request.timeout_seconds)
        except TimeoutError:
            await self._terminate_process_group(process)
            stdout, stderr, _ = await _collect_io(stdout_task, stderr_task, stdin_task)
            result = CommandResult(
                argv=request.argv,
                cwd=cwd,
                exit_code=process.returncode if process.returncode is not None else -1,
                stdout=redact_text(stdout[0].decode("utf-8", errors="replace")),
                stderr=redact_text(stderr[0].decode("utf-8", errors="replace")),
                duration_seconds=self._clock() - started,
                stdout_truncated=stdout[1],
                stderr_truncated=stderr[1],
                timed_out=True,
                executed_argv=request.argv,
            )
            raise CommandTimeoutError(result) from None
        except BaseException:
            await self._terminate_process_group(process)
            for task in (stdout_task, stderr_task, stdin_task):
                task.cancel()
            await asyncio.gather(stdout_task, stderr_task, stdin_task, return_exceptions=True)
            raise

        stdout, stderr, _ = await _collect_io(stdout_task, stderr_task, stdin_task)
        return CommandResult(
            argv=request.argv,
            cwd=cwd,
            exit_code=process.returncode,
            stdout=redact_text(stdout[0].decode("utf-8", errors="replace")),
            stderr=redact_text(stderr[0].decode("utf-8", errors="replace")),
            duration_seconds=self._clock() - started,
            stdout_truncated=stdout[1],
            stderr_truncated=stderr[1],
            executed_argv=request.argv,
        )

    async def _terminate_process_group(self, process: Any) -> None:
        if process.returncode is not None:
            await process.wait()
            return

        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGTERM)
        else:  # pragma: no cover - release-tested on Windows/WSL
            with contextlib.suppress(ProcessLookupError):
                process.terminate()

        try:
            await asyncio.wait_for(process.wait(), timeout=self._termination_grace_seconds)
            return
        except TimeoutError:
            pass

        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - release-tested on Windows/WSL
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        await process.wait()


async def _collect_io(
    stdout_task: asyncio.Task[tuple[bytes, bool]],
    stderr_task: asyncio.Task[tuple[bytes, bool]],
    stdin_task: asyncio.Task[None],
) -> tuple[tuple[bytes, bool], tuple[bytes, bool], None]:
    stdout, stderr, stdin_result = await asyncio.gather(stdout_task, stderr_task, stdin_task)
    return stdout, stderr, stdin_result


def _validated_cwd(cwd: Path) -> Path:
    try:
        resolved = cwd.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"command cwd does not exist: {cwd}") from exc
    if not resolved.is_dir():
        raise ValueError(f"command cwd is not a directory: {cwd}")
    return resolved


def _sanitized_environment(argv: tuple[str, ...]) -> dict[str, str]:
    """Retain normal tool configuration while withholding ambient credentials.

    Codex subprocesses reuse the official login stored and managed by Codex itself.
    In particular, ASCEND never injects ``OPENAI_API_KEY`` or ``CODEX_API_KEY`` into
    Codex (or any other child process), preventing an ambient Platform credential
    from silently changing the selected authentication and billing path.
    """

    del argv
    return sanitized_environment()
