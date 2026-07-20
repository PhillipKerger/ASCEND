# Implementation Plan

The current backend migration adds one cross-cutting invariant to every milestone: the official
Codex CLI with saved ChatGPT authentication is the recommended/default model backend; the
Responses API is retained as an explicit advanced backend, and no provider fallback is silent.

## Milestone 1 — CLI foundation

- Packaging, schema-v2 backend configuration/migration, `init`, grouped `doctor`, project
  discovery.
- Workspace and atomic `state.json`.
- Event logging, redaction, run IDs.
- Fake adapters and first unit tests.

Exit criterion: offline tests cover discovery, config precedence, path confinement, atomic
state, interruption, and resumption.

## Milestone 2 — Model adapter and prompt compiler

- Common backend interface, Codex CLI adapter, and advanced Responses API adapter.
- Structured outputs with Pydantic.
- Web-search configuration.
- Usage and incomplete-response handling.
- Framework integrity check and placeholder validation.

Exit criterion: fixture-based prompt compilation writes all contracted artifacts.

## Milestone 3 — Adaptive research engine

- Round planner, concurrent workers, approach registry.
- Candidate package and independent audit suite.
- Final judge and repair-loop transitions.

Exit criterion: accepted, repairable, rejected, partial, and budget-limited fixtures all work.

## Milestone 4 — Manuscript and bibliography

- Manuscript prompt and source generation.
- Independent citation verification and correction loop.
- LaTeX compiler adapter.

Exit criterion: fake valid bibliography passes; invented/mismatched citation blocks Lean.

## Milestone 5 — Lean and Codex

- Lean project detection.
- Feasibility and statement alignment.
- Codex subprocess adapter and iteration loop.
- Deterministic Lean verifier and scans.

Exit criterion: fixture proves correct statuses for verified, placeholder, changed statement,
unapproved axiom, compiler failure, and partial formalization.

## Milestone 6 — Reports and release hardening

- Human/machine reports.
- `verify` and `resume` commands.
- Documentation, example runs, packaging, CI.
- Optional Docker backend.

Exit criterion: `RELEASE_CHECKLIST.md` passes.
