from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .execution.base import (
    CommandRequest,
    CommandResult,
    CommandTimeoutError,
    ExecutionBackend,
)
from .execution.native import NativeBackend
from .redaction import redact_data as _shared_redact_data
from .redaction import redact_text as _shared_redact_text

_CODEX_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"(?i)(\b(?:authorization|(?:openai[_-]?)?api[_-]?key|"
        r"access[_-]?token|secret)\b"
        r"\s*[:=]\s*)([^\s,;]+)"
    ),
)


@dataclass(frozen=True)
class CodexRequest:
    prompt: str
    cwd: Path
    writable_paths: tuple[Path, ...]
    timeout_seconds: int
    jsonl_path: Path | None = None
    max_output_bytes: int = 8 * 1024 * 1024
    allow_broader_writes: bool = False

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("Codex prompt must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")


@dataclass(frozen=True)
class CodexResult:
    exit_code: int
    stdout: str
    stderr: str
    jsonl_path: Path | None = None
    json_events: tuple[Mapping[str, Any], ...] = ()
    command: tuple[str, ...] = ()
    duration_seconds: float = 0.0
    stdout_truncated: bool = False
    stderr_truncated: bool = False


@dataclass(frozen=True)
class CodexFeatures:
    json_output: bool
    sandbox: bool
    cwd: bool
    add_dir: bool
    ephemeral: bool
    ignore_user_config: bool
    ignore_rules: bool
    approval: bool
    config: bool
    model: bool

    @property
    def supported(self) -> bool:
        return all(
            (
                self.json_output,
                self.sandbox,
                self.cwd,
                self.add_dir,
                self.ephemeral,
                self.ignore_user_config,
                self.ignore_rules,
                self.approval,
                self.config,
                self.model,
            )
        )


class CodexClient(Protocol):
    async def execute(self, request: CodexRequest) -> CodexResult: ...


class CodexAdapterError(RuntimeError):
    pass


class CodexUnavailableError(CodexAdapterError):
    pass


class CodexFeatureError(CodexAdapterError):
    pass


class CodexJSONLError(CodexAdapterError):
    pass


class CodexTimeoutError(CodexAdapterError, TimeoutError):
    def __init__(self, partial_result: CommandResult) -> None:
        self.partial_result = partial_result
        super().__init__(f"codex exec timed out after {partial_result.duration_seconds:.3f}s")


class CodexExecClient:
    """Validated non-interactive ``codex exec`` subprocess adapter."""

    def __init__(
        self,
        backend: ExecutionBackend | None = None,
        *,
        executable: str = "codex",
        help_timeout_seconds: int = 15,
        model: str | None = None,
        reasoning_effort: str = "xhigh",
    ) -> None:
        if not executable or "\x00" in executable:
            raise ValueError("executable must be a non-empty argument")
        if help_timeout_seconds <= 0:
            raise ValueError("help_timeout_seconds must be positive")
        if model is not None and not model.strip():
            raise ValueError("model must be nonblank when provided")
        if reasoning_effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("unsupported Codex reasoning effort")
        self._backend = backend if backend is not None else NativeBackend()
        self._executable = executable
        self._help_timeout_seconds = help_timeout_seconds
        self._model = model.strip() if model is not None else None
        self._reasoning_effort = reasoning_effort
        self._features: CodexFeatures | None = None
        self._feature_lock = asyncio.Lock()

    async def detect_features(self, cwd: Path) -> CodexFeatures:
        canonical_cwd = _canonical_directory(cwd, "Codex cwd")
        cached = self._cached_features()
        if cached is not None:
            return cached

        async with self._feature_lock:
            cached = self._cached_features()
            if cached is not None:
                return cached
            try:
                root_result = await self._backend.run(
                    CommandRequest(
                        argv=(self._executable, "--help"),
                        cwd=canonical_cwd,
                        timeout_seconds=self._help_timeout_seconds,
                        max_output_bytes=512 * 1024,
                    )
                )
                result = await self._backend.run(
                    CommandRequest(
                        argv=(self._executable, "exec", "--help"),
                        cwd=canonical_cwd,
                        timeout_seconds=self._help_timeout_seconds,
                        max_output_bytes=512 * 1024,
                    )
                )
            except (OSError, CommandTimeoutError) as exc:
                raise CodexUnavailableError(
                    f"unable to run '{self._executable} exec --help': {type(exc).__name__}"
                ) from exc
            if root_result.exit_code != 0 or result.exit_code != 0:
                detail = _redact_text(result.stderr or result.stdout)[:500]
                raise CodexUnavailableError(
                    f"'{self._executable} exec --help' exited {result.exit_code}: {detail}"
                )
            if (
                root_result.stdout_truncated
                or root_result.stderr_truncated
                or result.stdout_truncated
                or result.stderr_truncated
            ):
                raise CodexFeatureError("codex exec help output was truncated")

            help_text = f"{result.stdout}\n{result.stderr}"
            root_help = f"{root_result.stdout}\n{root_result.stderr}"
            features = CodexFeatures(
                json_output=_has_flag(help_text, "--json"),
                sandbox=_has_flag(help_text, "--sandbox"),
                cwd=bool(re.search(r"(?m)(?:^|[\s,])-C(?:[\s,=]|$)", help_text)),
                add_dir=_has_flag(help_text, "--add-dir"),
                ephemeral=_has_flag(help_text, "--ephemeral"),
                ignore_user_config=_has_flag(help_text, "--ignore-user-config"),
                ignore_rules=_has_flag(help_text, "--ignore-rules"),
                approval=_has_flag(root_help, "--ask-for-approval")
                or _has_flag(help_text, "--ask-for-approval"),
                config=_has_flag(root_help, "--config") or _has_flag(help_text, "--config"),
                model=_has_flag(root_help, "--model") or _has_flag(help_text, "--model"),
            )
            if not features.supported:
                missing = [
                    name
                    for name, present in (
                        ("--json", features.json_output),
                        ("--sandbox", features.sandbox),
                        ("-C", features.cwd),
                        ("--add-dir", features.add_dir),
                        ("--ephemeral", features.ephemeral),
                        ("--ignore-user-config", features.ignore_user_config),
                        ("--ignore-rules", features.ignore_rules),
                        ("--ask-for-approval", features.approval),
                        ("--config", features.config),
                        ("--model", features.model),
                    )
                    if not present
                ]
                raise CodexFeatureError(
                    "installed codex exec lacks required flag(s): " + ", ".join(missing)
                )
            self._features = features
            return features

    def _cached_features(self) -> CodexFeatures | None:
        # Keep the read behind a method so type narrowing remains correct across
        # the awaited lock acquisition above (another task may populate it).
        return self._features

    async def execute(self, request: CodexRequest) -> CodexResult:
        cwd = _canonical_directory(request.cwd, "Codex cwd")
        writable_paths = _canonical_writable_paths(
            cwd,
            request.writable_paths,
            allow_broader=request.allow_broader_writes,
        )
        await self.detect_features(cwd)

        argv: list[str] = [
            self._executable,
            "--ask-for-approval",
            "never",
            "--config",
            f'model_reasoning_effort="{self._reasoning_effort}"',
        ]
        if self._model is not None:
            argv.extend(("--model", self._model))
        argv.extend(
            [
                "exec",
                "--json",
                "--sandbox",
                "workspace-write",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "-C",
                str(cwd),
            ]
        )
        for writable_path in writable_paths:
            argv.extend(("--add-dir", str(writable_path)))
        argv.append("-")

        command_request = CommandRequest(
            argv=tuple(argv),
            cwd=cwd,
            timeout_seconds=request.timeout_seconds,
            stdin=request.prompt,
            max_output_bytes=request.max_output_bytes,
        )
        try:
            result = await self._backend.run(command_request)
        except CommandTimeoutError as exc:
            partial = exc.result
            raise CodexTimeoutError(
                CommandResult(
                    argv=partial.argv,
                    cwd=partial.cwd,
                    exit_code=partial.exit_code,
                    stdout=_redact_text(partial.stdout),
                    stderr=_redact_text(partial.stderr),
                    duration_seconds=partial.duration_seconds,
                    stdout_truncated=partial.stdout_truncated,
                    stderr_truncated=partial.stderr_truncated,
                    timed_out=partial.timed_out,
                    executed_argv=partial.executed_argv,
                )
            ) from exc

        if result.stdout_truncated or result.stderr_truncated:
            raise CodexJSONLError("codex output exceeded the configured bound; JSONL is incomplete")
        events = validate_codex_jsonl(result.stdout, require_event=result.exit_code == 0)
        redacted_events = tuple(_redact_json(event) for event in events)
        redacted_stdout = _serialize_jsonl(redacted_events)
        redacted_stderr = _redact_text(result.stderr)

        jsonl_path = None
        if request.jsonl_path is not None:
            allowed_roots = (cwd, *writable_paths)
            jsonl_path = _validated_output_path(request.jsonl_path, allowed_roots)
            _atomic_write_private(jsonl_path, redacted_stdout)

        return CodexResult(
            exit_code=result.exit_code,
            stdout=redacted_stdout,
            stderr=redacted_stderr,
            jsonl_path=jsonl_path,
            json_events=redacted_events,
            command=tuple(argv),
            duration_seconds=result.duration_seconds,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
        )


def validate_codex_jsonl(text: str, *, require_event: bool = True) -> tuple[Mapping[str, Any], ...]:
    events: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(
                line,
                parse_constant=lambda constant: _raise_invalid_constant(constant),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise CodexJSONLError(f"invalid Codex JSONL at line {line_number}") from exc
        if not isinstance(value, dict):
            raise CodexJSONLError(
                f"invalid Codex JSONL at line {line_number}: event must be an object"
            )
        events.append(value)
    if require_event and not events:
        raise CodexJSONLError("codex exec emitted no JSONL events")
    return tuple(events)


def _raise_invalid_constant(constant: str) -> Any:
    raise ValueError(f"invalid JSON number constant: {constant}")


def _has_flag(help_text: str, flag: str) -> bool:
    return bool(re.search(rf"(?m)(?:^|\s){re.escape(flag)}(?:[\s,=]|$)", help_text))


def _canonical_directory(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} does not exist: {path}") from exc
    if not resolved.is_dir():
        raise ValueError(f"{label} is not a directory: {path}")
    if resolved == Path(resolved.anchor):
        raise ValueError(f"{label} must not be a filesystem root")
    return resolved


def _canonical_writable_paths(
    cwd: Path,
    paths: Sequence[Path],
    *,
    allow_broader: bool,
) -> tuple[Path, ...]:
    resolved_paths: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = _canonical_directory(path, "Codex writable path")
        if not allow_broader and resolved != cwd and cwd.is_relative_to(resolved):
            raise ValueError(f"writable path would grant access broader than Codex cwd: {resolved}")
        if resolved not in seen:
            seen.add(resolved)
            resolved_paths.append(resolved)
    if cwd not in seen:
        raise ValueError(
            "Codex cwd is implicitly writable under workspace-write and must be "
            "listed explicitly in writable_paths"
        )
    return tuple(resolved_paths)


def _validated_output_path(path: Path, allowed_roots: Sequence[Path]) -> Path:
    if not path.is_absolute():
        raise ValueError(f"Codex JSONL path must be absolute: {path}")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Codex JSONL parent does not exist: {path.parent}") from exc
    target = parent / path.name
    if target.exists() and target.is_symlink():
        raise ValueError(f"Codex JSONL path must not be a symlink: {target}")
    if not any(target.is_relative_to(root) for root in allowed_roots):
        raise ValueError(f"Codex JSONL path is outside writable paths: {target}")
    return target


def _atomic_write_private(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _redact_text(text: str) -> str:
    redacted = _shared_redact_text(text)
    home = str(Path.home())
    if home and home != "/":
        redacted = redacted.replace(home, "$HOME")
    redacted = _CODEX_SECRET_PATTERNS[0].sub("[REDACTED]", redacted)
    redacted = _CODEX_SECRET_PATTERNS[1].sub("Bearer [REDACTED]", redacted)
    return _CODEX_SECRET_PATTERNS[2].sub(r"\1[REDACTED]", redacted)


def _redact_json(value: Any) -> Any:
    value = _shared_redact_data(value)
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if re.search(
                r"(?i)(authorization|api.?key|access.?token|secret|password|cookie)",
                str(key),
            )
            else _redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


def _serialize_jsonl(events: Sequence[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        for event in events
    )
