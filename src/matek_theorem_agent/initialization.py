"""Project initialization used by ``matek init``."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .config import AppConfig, config_as_toml
from .workspace import atomic_write_text, ensure_path_confined

EXAMPLE_PROBLEM = """# Mathematical research problem

State the setting, definitions, conventions, hypotheses, and exact desired conclusion.
Include known sources or bottlenecks when available. Replace this text before running MATEK.
"""


class InitializationError(RuntimeError):
    """Raised when initialization would overwrite user configuration."""


class InitializationResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    created: list[Path]
    overwritten: list[Path]
    preserved: list[Path]


def initialize_project(project_root: Path, *, force: bool = False) -> InitializationResult:
    """Create configuration, workspace ignore rules, and an example problem."""

    root = project_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise InitializationError(f"project root is not a directory: {root}")
    config_path = root / "matek.toml"
    if config_path.exists() and not force:
        raise InitializationError(
            f"configuration already exists: {config_path}; pass --force to replace it"
        )

    matek_dir = ensure_path_confined(root, root / ".matek")
    matek_dir.mkdir(exist_ok=True)
    entries = {
        config_path: config_as_toml(AppConfig()),
        matek_dir / ".gitignore": "*\n!.gitignore\n",
        root / "problem.example.md": EXAMPLE_PROBLEM,
    }
    created: list[Path] = []
    overwritten: list[Path] = []
    preserved: list[Path] = []
    for path, content in entries.items():
        if path.is_symlink():
            raise InitializationError(f"refusing to write through a symlink: {path}")
        existed = path.exists()
        if existed and path != config_path and not force:
            preserved.append(path)
            continue
        atomic_write_text(path, content, confinement_root=root)
        (overwritten if existed else created).append(path)
    return InitializationResult(
        created=created,
        overwritten=overwritten,
        preserved=preserved,
    )
