from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from matek_theorem_agent import __version__  # noqa: E402
from matek_theorem_agent.schema_artifacts import generated_resource_schemas  # noqa: E402

FRAMEWORK = ROOT / "resources" / "prompts" / "research_prompt_framework.txt"
EXPECTED_FRAMEWORK_SHA256 = "bd724294a261f4bc2e5da2191813e40c1340bc6ee039c753cb5c60276e7a512c"


def main() -> None:
    actual = hashlib.sha256(FRAMEWORK.read_bytes()).hexdigest()
    if actual != EXPECTED_FRAMEWORK_SHA256:
        raise SystemExit(f"Framework hash mismatch: {actual}")
    schema_dir = ROOT / "resources" / "schemas"
    expected_schemas = generated_resource_schemas()
    parsed_schemas = {
        schema_path.name: json.loads(schema_path.read_text(encoding="utf-8"))
        for schema_path in schema_dir.glob("*.json")
    }
    if parsed_schemas.keys() != expected_schemas.keys():
        missing = sorted(expected_schemas.keys() - parsed_schemas.keys())
        unexpected = sorted(parsed_schemas.keys() - expected_schemas.keys())
        raise SystemExit(
            "Packaged schema set drift; run scripts/generate_model_schemas.py "
            f"(missing={missing}, unexpected={unexpected})"
        )
    for filename, expected in expected_schemas.items():
        if parsed_schemas.get(filename) != expected:
            raise SystemExit(
                f"Generated schema drift: {filename}; run scripts/generate_model_schemas.py"
            )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    distribution_version = project["project"]["version"]
    if distribution_version != __version__:
        raise SystemExit(
            "Version drift: pyproject.toml declares "
            f"{distribution_version!r}, package declares {__version__!r}"
        )
    print("Project integrity checks passed.")


if __name__ == "__main__":
    main()
