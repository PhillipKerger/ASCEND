# Locked Product Decisions

These decisions define the current v0.x product line and should not be reopened unless
implementation reveals a concrete blocker.

## Distribution and execution

- Local-first, open GitHub repository, installable Python CLI.
- No hosted service or web UI.
- No Postgres, queue, workflow server, or account system.
- State and artifacts live under `.ascend/` in the current project.
- Native execution is default; Docker is optional.
- Officially support Linux, macOS, and WSL2 initially.

## Orchestration

- Explicit application-level agents are the stable default.
- Run workers concurrently with `asyncio` and bounded concurrency.
- The research coordinator creates an initial diverse portfolio and dynamically plans later
  rounds based on an explicit approach registry.
- Hosted multi-agent features may later be added as an experimental backend, never as a
  required dependency.

## Model configuration

- The official Codex CLI, authenticated through the user's saved ChatGPT login, is the
  recommended/default model-execution backend.
- The direct OpenAI Responses API remains available only through explicit `api` selection and
  separate Platform billing.
- Never silently fall back between providers.
- All model IDs, reasoning modes, efforts, token limits, and tool availability are config.
- Suggested API-backend defaults:
  - prompt compiler: `gpt-5.6-sol`, pro/xhigh, web search on;
  - research coordinator/workers/auditors: `gpt-5.6-sol`, pro/max, web search configurable;
  - manuscript and bibliography agents: `gpt-5.6-sol`, pro/high or xhigh, web search on;
  - low-risk formatting/status tasks may use a cheaper configurable model later.
- Do not encode ChatGPT product labels such as “Ultra session” as API primitives.

## Lean and Codex

- Reuse an existing Lean project by default.
- Generate files only under `.ascend/runs/<run-id>/lean/`.
- Use Codex CLI's non-interactive mode through a subprocess adapter.
- The common Codex backend can execute research, audits, manuscript work, and formalization.
  Research-only mode omits Lean but still needs the selected model backend. Users without Codex
  may explicitly select the API backend.
- Never claim Lean verification from a model judgment. Run Lean/Lake deterministically.

## Manuscript and citations

- Manuscript creation occurs before Lean.
- A complete related-work section is mandatory.
- References must be verified independently, not merely copied from the research response.
- Any unresolved, contradictory, or likely fabricated citation blocks the manuscript gate.
- Every manuscript includes a Statement of AI Usage naming ASCEND with GPT 5.6 and cites both
  the canonical ASCEND GitHub repository and ASCEND whitepaper arXiv preprint.
- Any scholarly, technical, or public work that uses ASCEND must cite both the software
  repository and whitepaper preprint, whether or not ASCEND generated the final manuscript.
- Prefer primary sources: publisher pages, DOI/Crossref metadata, arXiv records, journal or
  conference proceedings, and authors' official pages where appropriate.

## Safety and integrity

- No project edits outside `.ascend/` without `--allow-project-edits`.
- No API key or Codex credential in files or logs.
- ASCEND never reads Codex credential files; it uses only `codex login status` for diagnostics.
- Truthful terminal statuses; partial work is preserved rather than mislabeled as solved.
- The exact prompt framework remains immutable and its SHA-256 is checked at runtime.
