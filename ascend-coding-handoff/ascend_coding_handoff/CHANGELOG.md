# Changelog

## 0.2.0 — 2026-07-19

### Codex is now the default backend

- ASCEND now runs structured model stages through the official Codex CLI by default and reuses
  the saved authentication established by `codex login`. ChatGPT-authenticated use does not
  require an OpenAI Platform API key.
- The existing Responses API backend remains supported through explicit `--backend api` or
  `[backend] provider = "api"` selection. ASCEND never silently falls back to API billing.
- Configuration schema v2 adds provider-specific Codex/API settings and conservatively migrates
  legacy API-shaped configuration with a one-time notice.
- Runs now retain provider-scoped call caches, Codex JSONL traces, backend/authentication
  provenance, backend-specific limits, and explicit provider-migration history.
- `ascend doctor` separates Codex and optional API checks; `--deep` is the opt-in live Codex
  structured-output probe.

### Manuscript disclosure

- Generated manuscripts must include a Statement of AI Usage stating that the ASCEND system
  with GPT 5.6 was used and must cite both the canonical ASCEND GitHub repository and ASCEND
  whitepaper arXiv preprint.
- Deterministic manuscript and reproduction checks reject missing disclosures, missing
  citations, and placeholder repository/arXiv identifiers.
