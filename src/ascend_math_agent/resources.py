"""Access immutable package resources in wheels and source checkouts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath


class ResourceError(FileNotFoundError):
    """Raised when a required bundled resource is unavailable."""


def _validated_parts(relative: str) -> tuple[str, ...]:
    candidate = PurePosixPath(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ResourceError(f"invalid package resource path: {relative!r}")
    return candidate.parts


def _target(relative: str) -> Traversable:
    parts = _validated_parts(relative)
    packaged = files("ascend_math_agent").joinpath("resources", *parts)
    if packaged.is_file():
        return packaged
    source_checkout = Path(__file__).resolve().parents[2] / "resources" / Path(*parts)
    if source_checkout.is_file():
        return source_checkout
    raise ResourceError(f"required bundled resource is missing: resources/{relative}")


def read_resource_bytes(relative: str) -> bytes:
    """Read a file below the packaged ``resources`` directory."""

    target = _target(relative)
    return target.read_bytes()


def read_resource_text(relative: str) -> str:
    return read_resource_bytes(relative).decode("utf-8")


@contextmanager
def resource_path(relative: str) -> Iterator[Path]:
    """Yield a real path for a resource, extracting it temporarily when necessary."""

    target = _target(relative)
    with as_file(target) as materialized:
        yield materialized


@contextmanager
def resource_paths(*relative_paths: str) -> Iterator[dict[str, Path]]:
    """Materialize several resources for the duration of one adapter/stage call."""

    with ExitStack() as stack:
        yield {
            relative: stack.enter_context(resource_path(relative)) for relative in relative_paths
        }
