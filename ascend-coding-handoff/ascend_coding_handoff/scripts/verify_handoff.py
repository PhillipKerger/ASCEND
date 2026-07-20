from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK = ROOT / "resources" / "prompts" / "research_prompt_framework.txt"
EXPECTED_FRAMEWORK_SHA256 = "bd724294a261f4bc2e5da2191813e40c1340bc6ee039c753cb5c60276e7a512c"


def main() -> None:
    actual = hashlib.sha256(FRAMEWORK.read_bytes()).hexdigest()
    if actual != EXPECTED_FRAMEWORK_SHA256:
        raise SystemExit(f"Framework hash mismatch: {actual}")
    for schema_path in (ROOT / "resources" / "schemas").glob("*.json"):
        json.loads(schema_path.read_text(encoding="utf-8"))
    print("Handoff integrity checks passed.")


if __name__ == "__main__":
    main()
