from __future__ import annotations

from pathlib import Path

import pytest

from matek_theorem_agent.config import load_config
from matek_theorem_agent.initialization import InitializationError, initialize_project


def test_init_creates_complete_local_scaffold_without_secrets(tmp_path: Path) -> None:
    result = initialize_project(tmp_path)
    config = (tmp_path / "matek.toml").read_text(encoding="utf-8")
    assert "config_version = 2" in config
    assert '[backend]\nprovider = "codex"' in config
    assert "allow_automatic_fallback = false" in config
    assert "[codex]" in config
    assert "[codex.limits]" in config
    assert "[api.models.prompt_compiler]" in config
    assert 'reasoning_mode = "pro"' in config
    assert "api_key" not in config.lower()
    resolved = load_config(tmp_path / "matek.toml", env={})
    assert resolved.backend.provider == "codex"
    assert resolved.migration_notice is None
    assert (tmp_path / ".matek" / ".gitignore").is_file()
    assert (tmp_path / "problem.example.md").is_file()
    assert len(result.created) == 3


def test_init_refuses_to_overwrite_config_without_force(tmp_path: Path) -> None:
    (tmp_path / "matek.toml").write_text("sentinel = true\n", encoding="utf-8")
    with pytest.raises(InitializationError, match="--force"):
        initialize_project(tmp_path)
    assert (tmp_path / "matek.toml").read_text() == "sentinel = true\n"
