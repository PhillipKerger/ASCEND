from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CommandRequest:
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: int = 600
    stdin: str | bytes | None = None
    max_output_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("argv must contain at least one argument")
        if any("\x00" in argument for argument in self.argv):
            raise ValueError("argv must not contain NUL bytes")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    cwd: Path
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    executed_argv: tuple[str, ...] | None = None


class CommandTimeoutError(TimeoutError):
    """A timed-out process group was terminated; bounded output is retained."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        super().__init__(
            f"command timed out after {result.duration_seconds:.3f}s: {result.argv[0]}"
        )


class ExecutionBackend(Protocol):
    async def run(self, request: CommandRequest) -> CommandResult: ...
