# Instructions for the Coding Agent

You are implementing a production-quality local CLI, not merely writing a design document.
Read every specification file in this repository before editing code.

## Primary objective

Build the project described in `PRODUCT_REQUIREMENTS.md` and make every required automated
acceptance test pass. The finished repository must be installable from GitHub and usable by
mathematicians from a terminal inside an existing Lean project.

## Non-negotiable product rules

- Preserve `resources/prompts/research_prompt_framework.txt` verbatim.
- Use an explicit application-level multi-agent workflow as the stable default. Do not make
  an experimental hosted multi-agent API a hard dependency.
- Use a narrow model-backend abstraction. The official Codex CLI with saved ChatGPT
  authentication is the recommended/default backend; preserve the OpenAI Responses API as an
  advanced, explicitly selected adapter. All model IDs and reasoning settings must be
  configurable.
- Never silently switch from Codex to the separately billed API backend.
- The prompt compiler defaults to a frontier reasoning model with `reasoning.mode = "pro"`,
  `reasoning.effort = "xhigh"`, and web search enabled.
- Research is adaptive across rounds. The initial portfolio must be diverse, and later agent
  tasks must be chosen from the current approach registry and audit findings.
- Never treat a model's self-declared success as proof verification.
- A manuscript may be generated only after the research acceptance gate passes.
- The manuscript stage must thoroughly discuss related and existing work.
- The manuscript must include a Statement of AI Usage naming ASCEND with GPT 5.6 and citing the
  canonical ASCEND GitHub repository and ASCEND whitepaper arXiv preprint.
- Any scholarly, technical, or public work in which ASCEND is used must cite both the canonical
  ASCEND GitHub repository and the ASCEND whitepaper arXiv preprint.
- Every citation must be independently verified to exist; title, authors, year, venue or
  publication status, and stable identifier/URL must be checked before the manuscript gate
  passes. Invented or unresolved references block progression to Lean.
- Lean theorem-statement alignment must be audited before proof implementation.
- Do not edit the user's source tree outside `.ascend/` unless the user passes an explicit
  opt-in flag.
- A Lean success status requires deterministic compiler and placeholder/axiom checks.
- Secrets must never be written to run artifacts, traces, reports, command output, or Git.
- All workflow stages must be resumable from on-disk state.
- Unit tests must not make network calls.
- Ordinary `ascend doctor` must not make a model call; live Codex/API probes require explicit
  opt-in.

## Engineering expectations

- Python 3.11+.
- Use type hints throughout and pass strict static checks selected in `pyproject.toml`.
- Keep API-specific code in adapters so documentation/API changes are localized.
- Prefer clear, boring code over a generic agent framework.
- Use dependency injection for model, Codex, shell, clock, and filesystem-sensitive behavior.
- Write tests before or alongside implementation for every state transition and gate.
- Never silently weaken a failed gate. Record the failure and return a truthful status.
- Keep the CLI usable on Linux, macOS, and Windows through WSL2 for v0.1.

## Completion standard

Do not declare the project complete until `RELEASE_CHECKLIST.md` is satisfied, all offline
unit tests pass, the fixture-based end-to-end run passes, and the README contains exact
installation and first-run instructions.
