# ASCEND

ASCEND (Autonomous System for Conjecture Exploration and Verified Deduction) is a local,
auditable mathematical-research and formal-verification workflow. It adapts a rigorous
research framework to a problem, coordinates independent research and adversarial audits,
writes and validates a LaTeX manuscript, and attempts Lean verification of the accepted main
result.

The recommended way to run ASCEND is through the official Codex CLI authenticated with your
ChatGPT account. No OpenAI API key is required for this mode. Codex is the default backend;
the direct OpenAI Responses API is an advanced, explicitly selected alternative.

ASCEND never accepts a model's claim of success as verification. Manuscript generation follows
the research gate, every citation must pass independent source checks, and `LEAN_VERIFIED` is
issued only after deterministic Lean checks.

## Cite ASCEND when you use it

ASCEND **must be cited in any scholarly, technical, or public work in which it is used**. Cite
both:

1. the ASCEND GitHub software repository; and
2. the ASCEND whitepaper preprint on arXiv.

Generated manuscripts must also include a **Statement of AI Usage** disclosing that the ASCEND
system with GPT 5.6 was used and citing both items. The canonical GitHub owner and arXiv
identifier have not yet been assigned in this handoff checkout, so do not invent them. Before a
public release, replace `OWNER` and `ARXIV_ID` in the following citation templates with the
canonical metadata:

```text
ASCEND contributors. ASCEND: Autonomous System for Conjecture Exploration and
Verified Deduction. Software repository,
https://github.com/OWNER/ascend-math-agent.

ASCEND contributors. ASCEND: Autonomous System for Conjecture Exploration and
Verified Deduction. arXiv preprint arXiv:ARXIV_ID.
```

## Requirements

For the recommended Codex setup you need:

- Python 3.11 or newer;
- Git;
- the official Codex CLI;
- a ChatGPT account or workspace with Codex access;
- an existing Lean/Lake project, Lean, and Lake for formal verification; and
- a LaTeX distribution with `latexmk` for manuscript compilation.

Codex access, available models, rate limits, and credits depend on the user's current account
or workspace and may change. Consult the official [Codex pricing and availability
page](https://chatgpt.com/codex/pricing/) rather than assuming a fixed or unlimited allowance.
Research-only runs may omit Lean/Lake by passing `--no-lean`; manuscript builds still require
LaTeX unless manuscript generation is disabled in configuration.

## Recommended setup: Codex CLI

Install Codex using an official method. For macOS or Linux, the standalone installer is:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

Other official options include:

```bash
npm install -g @openai/codex
# macOS with Homebrew:
brew install --cask codex
```

Windows users can use the official PowerShell installer, although ASCEND v0.2 support is
through WSL2:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
```

See the current [Codex CLI installation guide](https://developers.openai.com/codex/cli) before
installing or updating.

Sign in once and choose **Sign in with ChatGPT**:

```bash
codex login
codex login status
```

ASCEND calls `codex login status` for diagnostics. It never reads, copies, or modifies Codex
credential files.

## Install ASCEND

The GitHub commands below are release templates. Repository publication and end-to-end
`pipx`/`uv` installation validation remain pending; replace `OWNER` only after the canonical
repository URL is assigned:

```bash
pipx install 'git+https://github.com/OWNER/ascend-math-agent.git'
# or
uv tool install 'git+https://github.com/OWNER/ascend-math-agent.git'
```

For development from this checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

On WSL2, install and run ASCEND inside the Linux distribution and keep the Lean project in
the Linux filesystem. On macOS, Homebrew packages for `git` and `latexmk` work with the native
backend. Native macOS and WSL2 release validation remain outstanding; see
[`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

## Five-minute first run

Run these commands inside the existing Git and Lean/Lake project that should own the
`.ascend/` workspace:

```bash
codex login
ascend init
cp problem.example.md problem.md
# Edit problem.md into a self-contained problem with an exact requested conclusion.
ascend doctor
ascend run problem.md
```

No `OPENAI_API_KEY` is needed. To run the research/manuscript pipeline without Lean, use:

```bash
ascend run problem.md --no-lean
```

Preview the resolved backend, configuration, and stage plan without model calls:

```bash
ascend run problem.md --dry-run
```

ASCEND creates `.ascend/runs/<run-id>/` and retains the original problem, prompts, visible
worker results, audits, source checks, manuscript, Lean diagnostics, provider traces, usage,
and human/machine reports. It does not edit source files outside that run directory unless
`--allow-project-edits` is explicitly supplied.

### How model access works

By default, ASCEND invokes the locally installed official Codex CLI. Codex reuses the saved
login created by `codex login`. With **Sign in with ChatGPT**, ASCEND neither needs nor stores
an OpenAI Platform API key.

This is not offline or local-model execution: Codex communicates with OpenAI and consumes the
Codex allowance or credits available to the signed-in ChatGPT account or workspace. ASCEND does
not promise free, unlimited, or plan-specific usage.

ASCEND never silently switches from Codex mode to API mode. A missing installation, failed
login, unavailable model, usage limit, search failure, or Codex runtime error checkpoints the
run and returns an actionable error; it cannot unexpectedly create Platform API charges.

## Commands

```text
ascend init [--force]
ascend doctor [--deep] [--online]
ascend run PROBLEM_FILE [--backend codex|api] [--no-lean | --research-only] [--dry-run]
ascend status [RUN_ID]
ascend resume [RUN_ID] [--backend codex|api] [--force-stage STAGE]
ascend report [RUN_ID] [--rewrite]
ascend verify [RUN_ID]
```

`ascend doctor` performs local capability and saved-login checks without sending a model
prompt. `--deep` explicitly opts into one minimal live Codex structured-output probe and may
consume Codex allowance. `--online` separately probes the advanced API backend and requires
`OPENAI_API_KEY`; it is not needed for Codex mode.

`resume` uses the backend recorded for the run. ASCEND refuses an accidental provider change;
an explicitly requested change is recorded as a provenance event. Successfully returned calls
are checkpointed atomically, so interruption does not require completed work to be purchased
again when the selected backend supports replay. `--force-stage` starts a new call-cache
generation while retaining prior records as audit history. A fully completed resume is a no-op.

`report` is deterministic and offline by default. `--rewrite` is an explicit opt-in model call;
the resulting prose cannot change authoritative statuses, hashes, links, or certificates.
`verify` re-runs deterministic integrity, bibliography, LaTeX, and Lean checks without calling a
model.

## Configuration

New `ascend init` configurations use:

```toml
config_version = 2

[backend]
provider = "codex"
allow_automatic_fallback = false

[codex]
executable = "codex"
model = "" # empty means the user's/current Codex default
research_effort = "xhigh"
audit_effort = "xhigh"
manuscript_effort = "high"
formalization_effort = "xhigh"
max_parallel_agents = 3
max_parallel_web_agents = 2
persist_sessions = true
```

Backend selection precedence is an explicit `--backend` flag, `ASCEND_BACKEND`, the project
configuration, then the `codex` built-in default. Accepted values are `codex` and `api`.

Legacy v0.1 configurations containing the previous top-level API model or budget sections are
migrated to the namespaced `[api]` layout and retain API behavior. ASCEND prints a one-time
migration notice and does not discard settings or run state.

For safety, `codex.extra_args` accepts only the documented presentation allowlist; ASCEND owns
authentication, workspace, output, sandbox, approval, search, model, and effort flags. Broader
write access cannot be enabled in TOML or the environment. It requires
`--allow-project-edits`, and that consent is recorded with the run.

The bundled `resources/prompts/research_prompt_framework.txt` is integrity checked at runtime.
A changed bundled framework is rejected; an intentional custom framework must be selected with
`--framework PATH`, whose hash is recorded.

## Advanced: direct OpenAI API backend

The existing Responses API backend remains available for users who want direct provider
control, usage-based automation, or institutional Platform billing. Select it explicitly:

```bash
export OPENAI_API_KEY='your-platform-api-key'
ascend doctor --online
ascend run problem.md --backend api
```

Or configure:

```toml
[backend]
provider = "api"
```

OpenAI Platform API billing is separate from ChatGPT subscription billing. The API backend uses
the models, concurrency, budgets, and dated pricing entries under `[api]`; every selected model
must have a pricing entry. Review those entries against the official [API pricing
page](https://developers.openai.com/api/docs/pricing). Never put the key in `ascend.toml`.

## Optional Docker command sandbox

The optional Docker execution backend applies to configured Lean/LaTeX commands, not to the
host Codex CLI. It uses the image named by `lean.docker_image` (default
`ascend-math-agent:latest`) with networking disabled, a read-only container filesystem, and
`--pull=never`. Build or load the image before choosing `--sandbox docker`; `doctor` verifies
that it is already present.

Each command mounts its resolved working directory at `/workspace`. A concrete stage directory
under `.ascend/runs/<run-id>/` is writable; the project root and other directories are read-only.
The image must already contain the configured LaTeX compiler, Lean/Lake toolchain, and packages.
`ascend verify` currently re-runs frozen deterministic checks natively.

## Troubleshooting

- **`codex` not found:** install or update it using the official Codex CLI guide, ensure it is
  on `PATH`, then run `ascend doctor`.
- **Not signed in:** run `codex login`, choose **Sign in with ChatGPT**, confirm with
  `codex login status`, then rerun `ascend doctor`.
- **Unsupported Codex CLI:** ASCEND detects the exact noninteractive, JSONL, schema, sandbox,
  search, model, config, and session capabilities it uses. Update Codex using an official
  installation method; a version string alone is not considered sufficient.
- **Model unavailable or reasoning effort rejected:** remove the `codex.model` override to use
  the account's current default, or select a model/effort available to the workspace. Resume the
  checkpointed run afterward.
- **Rate, allowance, or credit limit reached:** completed artifacts remain saved. Wait until
  access is available and run `ascend resume RUN_ID`. ASCEND will not switch to API billing.
- **Live search unavailable:** source-dependent stages stop rather than weakening bibliography
  checks. Restore Codex search/network access, then resume.
- **Git repository required:** run ASCEND inside the intended Git project. Power users may set
  `codex.skip_git_repo_check = true`, but doing so weakens change provenance.
- **Lean/Lake or LaTeX missing:** install the research tools listed by `ascend doctor`, or use
  `--no-lean` when formalization is intentionally excluded.
- **WSL2:** install Codex and ASCEND inside WSL2 and keep the project in its Linux filesystem.
- **Use API mode intentionally:** configure `OPENAI_API_KEY` and pass `--backend api`; there is
  no automatic fallback from Codex.

## Outcomes and reports

Scientific rejection is a truthful workflow result, not necessarily a process crash. Reports
distinguish research rejection, accepted proof, manuscript/bibliography failure, statement-only
or partial Lean work, approved-axiom verification, and axiom-free `LEAN_VERIFIED`. Examples are
in [`examples/reports`](examples/reports).

Model traces retain visible outputs, request configuration, public tool/citation metadata,
session or response identifiers, and usage. They do not retain hidden chain-of-thought, raw
credentials, or duplicate secret-bearing inputs.

## Development

The default test suite makes no network or model calls:

```bash
ruff check .
ruff format --check .
mypy src
pytest -q
python scripts/verify_handoff.py
```

Live smoke tests require explicit opt-in and may consume Codex allowance or API funds. See
[`SECURITY.md`](SECURITY.md) before changing filesystem, subprocess, authentication, or logging
behavior.

```bash
ASCEND_CODEX_LIVE_TESTS=1 pytest -q -m codex_live
# Also exercise built-in live search in that tiny probe:
ASCEND_CODEX_LIVE_TESTS=1 ASCEND_CODEX_LIVE_SEARCH=1 pytest -q -m codex_live
```

The implementation follows the official [Codex CLI](https://developers.openai.com/codex/cli),
[authentication](https://developers.openai.com/codex/auth), and [non-interactive
mode](https://developers.openai.com/codex/non-interactive-mode) documentation. The advanced API
adapter follows the official [Responses structured-output
guide](https://developers.openai.com/api/docs/guides/structured-outputs) and [web-search
guide](https://developers.openai.com/api/docs/guides/tools-web-search).

## License

MIT. See [`LICENSE`](LICENSE).
