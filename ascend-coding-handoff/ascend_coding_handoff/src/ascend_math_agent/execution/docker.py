from __future__ import annotations

import re
from pathlib import Path

from .base import CommandRequest, CommandResult
from .native import NativeBackend

_IMAGE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@:+-]*\Z")
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class DockerBackend:
    """Optional restricted container backend; it never pulls an image implicitly."""

    def __init__(
        self,
        image: str = "ascend-math-agent:latest",
        *,
        docker_executable: str = "docker",
        native_backend: NativeBackend | None = None,
        network_enabled: bool = False,
    ) -> None:
        if not _IMAGE_PATTERN.fullmatch(image) or image.startswith("-"):
            raise ValueError(f"invalid Docker image reference: {image!r}")
        if not docker_executable or "\x00" in docker_executable:
            raise ValueError("docker_executable must be a non-empty executable name")
        self._image = image
        self._docker_executable = docker_executable
        self._native = native_backend or NativeBackend()
        self._network_enabled = network_enabled

    async def run(self, request: CommandRequest) -> CommandResult:
        cwd = _safe_mount_source(request.cwd)
        if "," in str(cwd):
            raise ValueError("Docker workspace paths containing commas are unsupported")
        mount_spec = f"type=bind,source={cwd},target=/workspace"
        if not _is_validated_run_stage(cwd):
            mount_spec += ",readonly"

        argv = (
            self._docker_executable,
            "run",
            "--rm",
            "--init",
            "--pull=never",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=256",
            "--network",
            "bridge" if self._network_enabled else "none",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=256m",
            "--mount",
            mount_spec,
            "--workdir",
            "/workspace",
            self._image,
            *request.argv,
        )
        wrapped = CommandRequest(
            argv=argv,
            cwd=cwd,
            timeout_seconds=request.timeout_seconds,
            stdin=request.stdin,
            max_output_bytes=request.max_output_bytes,
        )
        result = await self._native.run(wrapped)
        return CommandResult(
            argv=request.argv,
            cwd=cwd,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=result.duration_seconds,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            timed_out=result.timed_out,
            executed_argv=result.executed_argv,
        )


def _safe_mount_source(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Docker workspace does not exist: {path}") from exc
    if not resolved.is_dir():
        raise ValueError(f"Docker workspace is not a directory: {path}")
    if resolved == Path(resolved.anchor):
        raise ValueError("refusing to mount a filesystem root into Docker")
    return resolved


def _is_validated_run_stage(path: Path) -> bool:
    """Limit writable binds to a concrete stage below ``.ascend/runs/<id>``."""

    parts = path.parts
    for index in range(len(parts) - 3):
        if parts[index : index + 2] != (".ascend", "runs"):
            continue
        run_id = parts[index + 2]
        if _RUN_ID_PATTERN.fullmatch(run_id) and len(parts) > index + 3:
            return True
    return False
