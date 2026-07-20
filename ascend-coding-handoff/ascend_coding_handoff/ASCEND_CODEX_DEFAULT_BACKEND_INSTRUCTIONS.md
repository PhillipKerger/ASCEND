# ASCEND Implementation Instructions:
# Make ChatGPT-Authenticated Codex CLI the Default Backend

**Document purpose:** Hand this file to the coding agent that built the existing ASCEND project.

**Project:** ASCEND — Autonomous System for Conjecture Exploration and Verified Deduction

**Change requested:** Add a complete Codex CLI execution backend that can use a user's saved **Sign in with ChatGPT** authentication, so ordinary users can run ASCEND without supplying an OpenAI API key. Present this Codex/CLI path as the normal and recommended way to use ASCEND. Preserve the existing OpenAI Responses API implementation as an advanced, explicitly selected secondary backend.

**Important terminology:** This is a **no-API-key** mode, not an offline mode. Codex still connects to OpenAI through the official Codex client and consumes the user's available ChatGPT/Codex allowance or credits.

---

## 1. Required outcome

After this change, a normal new user should be able to install Codex, sign in with ChatGPT once, install ASCEND, and run:

```bash
codex login
ascend doctor
ascend run problem.md
```

No `OPENAI_API_KEY` should be required for this default path.

The direct API workflow must remain available through an explicit selection:

```bash
ascend run problem.md --backend api
```

or:

```toml
[backend]
provider = "api"
```

The default for all newly created configurations and configuration-free runs must be:

```toml
[backend]
provider = "codex"
```

Do **not** implement silent fallback from Codex to the API. An exhausted Codex allowance, unavailable Codex installation, failed ChatGPT login, or Codex runtime error must not unexpectedly create API charges. In such cases, checkpoint the run, explain the failure, and tell the user how to resume or explicitly select the API backend.

---

## 2. Product presentation and naming

Every user-facing surface should present the two modes in this order:

1. **Codex mode — recommended and default**
   - Uses the locally installed official Codex CLI.
   - Supports `Sign in with ChatGPT`.
   - Does not require an API key when ChatGPT authentication is active.
   - Uses the user's Codex access, limits, and credits associated with their ChatGPT account/workspace.

2. **OpenAI API mode — advanced/optional**
   - Uses ASCEND's existing Responses API integration.
   - Requires an OpenAI Platform API key and API billing.
   - Offers direct provider control and may be useful for automation, institutional use, or users who prefer usage-based API access.

Use phrases such as:

> The recommended way to run ASCEND is through Codex CLI authenticated with your ChatGPT account. No API key is required for this mode.

Do not use misleading phrases such as:

- "free execution";
- "offline execution";
- "local AI";
- "unlimited with Plus/Pro";
- "uses the ChatGPT web app";
- "launches an Ultra session";
- "API usage is included with Plus/Pro."

Codex allowance, plan availability, supported models, and credit rules may change. The README should link to current official Codex pricing/availability information rather than promise fixed quotas.

---

## 3. Preserve the existing workflow

Do not redesign or weaken the completed ASCEND workflow. The new backend must plug into the existing stage orchestration and preserve, at minimum:

1. research-prompt framework adaptation;
2. multi-agent mathematical research;
3. adaptive follow-up rounds;
4. synthesis;
5. foundational, domain, hostile-counterexample, source, and complexity audits as applicable;
6. final proof judgment;
7. LaTeX manuscript generation;
8. thorough related-work discussion;
9. independent verification that every cited source exists and is characterized accurately;
10. LaTeX compilation and citation checks;
11. Lean feasibility assessment;
12. Lean statement-alignment audit;
13. iterative Lean formalization;
14. deterministic Lean verification;
15. final process report;
16. checkpointing and resumption.

The manuscript must still be completed and pass its bibliography gate **before** Lean formalization begins.

Backend choice changes how model work is executed; it must not change the mathematical acceptance criteria.

---

## 4. Architecture change

### 4.1 Introduce or complete a backend abstraction

If the project already has a model-provider abstraction, adapt it rather than creating a parallel orchestration path. Otherwise introduce a narrow protocol similar to:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class AgentRequest:
    stage: str
    role: str
    prompt: str
    workspace: Path
    output_schema_path: Path | None
    output_path: Path
    events_path: Path
    sandbox: str
    web_search: bool
    model: str | None
    reasoning_effort: str | None
    timeout_seconds: int
    resumable_session_id: str | None = None
    ephemeral: bool = True


@dataclass(frozen=True)
class AgentResult:
    backend: str
    final_output: str
    parsed_output: Mapping[str, Any] | None
    session_id: str | None
    usage: Mapping[str, Any] | None
    exit_code: int
    stdout_path: Path
    stderr_path: Path
    events_path: Path
    retryable: bool
    error_kind: str | None
    error_message: str | None


class AgentBackend(Protocol):
    async def run(self, request: AgentRequest) -> AgentResult:
        ...
```

Provide two implementations:

```python
class CodexCliBackend(AgentBackend):
    ...

class OpenAIResponsesBackend(AgentBackend):
    ...
```

The existing workflow stages must depend on `AgentBackend`, not directly on the OpenAI SDK.

### 4.2 Backend resolver

Implement a resolver with this policy:

```text
explicit CLI flag
    ↓
ASCEND_BACKEND environment variable, if supported
    ↓
ascend.toml
    ↓
"default = codex"
```

Accepted values should be:

```text
codex
api
```

An optional `auto` value may be supported for power users, but it must not be the documented or generated default. If `auto` is implemented, it may select a valid Codex installation first, but it must **not** silently fall through to API billing. It should require explicit consent before switching providers.

### 4.3 Preserve backward compatibility

Existing installations may have API-specific configuration but no `[backend]` section. Add a versioned migration rule:

- New configuration generated after this change: `provider = "codex"`.
- Existing configuration that clearly contains the legacy API model/provider setup: preserve its previous API behavior and insert or infer `provider = "api"`.
- Print a one-time migration notice.
- Never discard API settings, model settings, budgets, or saved run state.

Add a configuration schema version if one does not already exist.

---

## 5. Codex CLI integration

### 5.1 Use the official CLI non-interactively

Invoke the installed `codex` executable through `asyncio.create_subprocess_exec` or an equivalent argument-array subprocess API.

Do not:

- invoke through `shell=True`;
- concatenate untrusted prompt text into a shell command;
- write authentication tokens into command arguments;
- read or copy `~/.codex/auth.json`;
- implement a custom OAuth flow;
- scrape the ChatGPT website;
- use browser automation.

Prompts should be supplied through standard input using `-` as the prompt argument. This avoids command-length limits and shell-injection hazards.

A representative invocation is:

```bash
codex exec \
  --json \
  --sandbox read-only \
  --ask-for-approval never \
  --search \
  --output-schema /absolute/path/to/schema.json \
  --output-last-message /absolute/path/to/final.json \
  --cd /absolute/path/to/workspace \
  --config 'model_reasoning_effort="xhigh"' \
  -
```

Construct this as an argument list, not as one shell string.

The exact placement of global flags can vary across Codex versions. Confirm the supported syntax against `codex exec --help` in integration tests and centralize command construction in one module.

### 5.2 Required Codex features

ASCEND's doctor check must verify that the installed Codex version supports the features ASCEND uses:

- `codex login status`;
- `codex exec`;
- `--json`;
- `--output-last-message` / `-o`;
- `--output-schema`;
- `--sandbox`;
- `--ask-for-approval`;
- `--cd` / `-C`;
- `--search`;
- `--model` / `-m`;
- `--config`;
- `codex exec resume`, if session resumption is enabled.

Prefer capability detection from `codex exec --help` over a brittle hard-coded version check. Also record `codex --version`.

If a minimum tested version is documented, keep it in one constant and in the README. Still use capability checks because packaged versions and operating systems can differ.

### 5.3 Authentication

The default Codex path must reuse the user's official saved Codex authentication.

Use:

```bash
codex login status
```

to determine whether Codex is authenticated.

Classify only the minimum information needed:

```text
chatgpt
api_key
access_token
authenticated_unknown
not_authenticated
error
```

Do not inspect credential files. Do not log tokens, cookies, authorization headers, or credential paths.

When unauthenticated, `ascend doctor` and `ascend run` should print:

```text
Codex CLI is installed but is not signed in.

Run:
    codex login

Then choose "Sign in with ChatGPT" for subscription access.
After login, rerun:
    ascend doctor
```

The Codex backend may work with any valid Codex authentication method, but documentation must lead with ChatGPT sign-in.

When spawning Codex in the default backend:

- do not set `CODEX_API_KEY`;
- do not pass the Platform API key to Codex;
- avoid inheriting a process-scoped `CODEX_API_KEY`;
- redact API-related environment variables from diagnostic logs;
- do not modify the user's Codex login state.

If the user's active Codex login itself uses an API key, report that fact neutrally. ASCEND should not log the key and should not automatically log the user out.

### 5.4 Model and reasoning effort

Do not assume every account exposes the same model catalog.

Recommended behavior:

```toml
[codex]
model = ""                 # Empty means use the user's/current Codex default.
research_effort = "xhigh"
audit_effort = "xhigh"
manuscript_effort = "high"
formalization_effort = "xhigh"
```

Allow an explicit model override:

```toml
[codex]
model = "gpt-5.6"
```

If a model is configured, pass `--model <model>`. Otherwise omit the flag.

Pass reasoning effort as a one-run configuration override, for example:

```text
--config
model_reasoning_effort="xhigh"
```

Ensure quoting is correct when passed as an argument-array element. Do not rely on the user's global Codex configuration for the required research effort.

If the selected model rejects a configured effort, produce a clear error and optionally retry once using a compatible lower effort according to a documented policy. Record the actual configuration used.

### 5.5 Web search

Use Codex's built-in live web search for stages that require current external research:

- problem-framework adaptation where literature/status checking is permitted;
- literature research;
- source audits;
- manuscript related-work research;
- bibliography verification;
- checking the exact statement and hypotheses of external theorems.

Enable it with `--search`.

Do not grant arbitrary shell network access merely to enable Codex web search. The shell sandbox should remain network-disabled unless a stage has a separately justified need.

If live web search is unavailable, do not silently downgrade the bibliography-verification gate. Fail that gate with a clear, resumable status unless the user explicitly chose an offline/research-without-sources mode already supported by the project.

The bibliography gate must continue to verify, for every cited item:

- that the work exists;
- exact title;
- author list;
- publication year or version date;
- venue or publication status;
- DOI, arXiv ID, ISBN, or other stable identifier when available;
- that the cited theorem or claim appears in the source;
- that ASCEND's manuscript describes the result accurately;
- that the result is applied under its real hypotheses.

### 5.6 Sandbox policy by stage

Use least privilege.

Recommended defaults:

| Stage | Sandbox | Live search | Writes by agent |
|---|---|---:|---:|
| Prompt compilation | `read-only` | Yes when permitted | No |
| Research workers | `read-only` | Yes | No |
| Synthesis | `read-only` | Usually no | No |
| Proof audits | `read-only` | As needed | No |
| Manuscript reasoning | `read-only` or isolated `workspace-write` | Yes | Prefer ASCEND-controlled output |
| Bibliography audit | `read-only` | Yes | No |
| Lean statement audit | `read-only` | No | No |
| Lean implementation | `workspace-write` | Usually no | Yes, only in authorized workspace |
| Final report | `read-only` | No | No |

For research and audit stages, prefer returning structured output and having ASCEND write the artifact. This avoids granting write access merely to produce a response file.

For Lean formalization, preserve the project's existing file-isolation policy. At minimum:

- create a pre-run Git/status snapshot;
- define an allowed path set;
- inspect all changed files afterward;
- reject or revert unauthorized modifications;
- never allow edits outside the intended project/run workspace without explicit user permission.

Prefer a run-scoped Git worktree or isolated copy if the existing project already supports one.

Never use:

```text
--dangerously-bypass-approvals-and-sandbox
--yolo
danger-full-access
```

on the user's ordinary host checkout. Such modes are allowed only inside an explicitly isolated container/VM and must not be the default.

### 5.7 Structured output

For every stage that currently uses a JSON schema with the Responses API:

1. write the equivalent JSON Schema to a run-scoped file;
2. pass it with `--output-schema`;
3. write the final result with `--output-last-message`;
4. independently parse and validate the result with the project's existing Pydantic or JSON Schema validator;
5. retain the JSONL event stream.

Do not trust successful process exit alone.

If final output is missing or invalid:

- retain stdout, stderr, and JSONL;
- classify the error;
- retry once with a targeted repair instruction when appropriate;
- otherwise checkpoint and fail the stage visibly.

Do not infer a successful mathematical stage from an unstructured natural-language final message when a schema is required.

### 5.8 JSONL event handling

Run Codex with `--json`. Store the complete event stream under the ASCEND run directory, for example:

```text
.ascend/runs/<run-id>/traces/codex/<stage>/<role>.jsonl
```

Parse at least:

- `thread.started`;
- `turn.started`;
- `item.started`;
- `item.completed`;
- `turn.completed`;
- `turn.failed`;
- `error`.

Extract and record when present:

- thread/session ID;
- input tokens;
- cached input tokens;
- output tokens;
- reasoning output tokens;
- tool usage;
- web-search events;
- command execution;
- file changes;
- completion/failure status.

Never expose hidden chain-of-thought. Store only the events that Codex officially emits. Apply the project's existing redaction rules before including traces in a user-facing report.

### 5.9 Session strategy

Use fresh independent Codex sessions for agents that must remain independent.

Recommended policy:

- primary solver, alternative solver, hostile auditor, and domain auditor: separate sessions;
- one-shot agents: use `--ephemeral` if no continuation is required;
- adaptive follow-up on the same route: either use `codex exec resume <SESSION_ID>` or launch a fresh session with the previous route artifact supplied as explicit context;
- Lean formalization loop: use a resumable session when this improves continuity, while still compiling and checking after every iteration.

Do not resume one solver's session for a supposedly independent audit.

Persist session IDs only as nonsecret run metadata.

---

## 6. Multi-agent orchestration under Codex

Reuse the existing ASCEND research coordinator. Replace individual API model calls with `CodexCliBackend.run(...)`.

Do not rely entirely on Codex's internal subagent feature for the first implementation. ASCEND should continue to own:

- agent-role separation;
- concurrency;
- adaptive rounds;
- stage budgets;
- artifact boundaries;
- proof gates;
- independent audits;
- retry policy;
- final judgment.

This preserves reproducibility and makes Codex and API runs comparable.

Codex-internal subagents may be added later as an optional optimization, but they must not collapse the required independent audit contexts.

### 6.1 Concurrency defaults

Subscription-backed users may have stricter usage limits than API users. Add backend-specific defaults.

Example:

```toml
[codex]
max_parallel_agents = 3
max_parallel_web_agents = 2

[api]
max_parallel_agents = 8
```

Choose conservative defaults and allow users to increase them.

When rate-limited:

- classify the failure as retryable;
- use bounded exponential backoff with jitter;
- checkpoint all completed agent outputs;
- resume rather than restart the entire run;
- show the user which stage is waiting or failed;
- do not switch to the API automatically.

### 6.2 Budgets

The existing API dollar budget does not map directly to ChatGPT subscription usage.

Add Codex-specific limits such as:

```toml
[codex.limits]
max_agent_calls = 100
max_research_rounds = 8
max_codex_threads = 40
max_wall_clock_minutes = 480
max_formalization_iterations = 60
```

When token usage is available in JSONL, record it, but do not claim an exact dollar cost unless the user's current plan/credit rate is known from an authoritative source.

Keep the existing API budget settings unchanged for API mode.

---

## 7. CLI changes

### 7.1 Default invocation

This must use Codex:

```bash
ascend run problem.md
```

### 7.2 Explicit backend selection

Support:

```bash
ascend run problem.md --backend codex
ascend run problem.md --backend api
```

The selected backend must be persisted in the run state so that `ascend resume` uses the same backend unless the user explicitly migrates the run.

Do not allow an accidental provider change during resume. If the user requests one, print a warning that model behavior and provenance will differ, and record the switch in the report.

### 7.3 Doctor command

Expand `ascend doctor` to report two separate capability groups.

Example:

```text
ASCEND environment

Default model backend: Codex CLI

Codex backend
  ✓ codex executable found
  ✓ codex exec supported
  ✓ structured output supported
  ✓ JSONL output supported
  ✓ live web search flag supported
  ✓ authenticated with ChatGPT
  ✓ active Codex workspace available
  ✓ Git repository detected

OpenAI API backend (optional)
  ○ OPENAI_API_KEY not configured
  → This is not required for the default Codex workflow.

Research tools
  ✓ Git
  ✓ LaTeX compiler
  ✓ Lean
  ✓ Lake
  ✓ lean-toolchain
```

Provide:

```bash
ascend doctor --deep
```

for an optional minimal live Codex smoke test. The ordinary doctor command should avoid consuming model allowance merely to check installation.

The deep check may:

- make a tiny structured-output call;
- verify saved authentication is usable;
- verify live web search when required;
- verify output-schema handling;
- write all probe artifacts to a temporary directory and delete them afterward.

### 7.4 Setup assistance

Optionally add:

```bash
ascend setup
```

This may:

- detect missing Codex;
- print official installation commands;
- invoke `codex login` interactively after user confirmation;
- generate `ascend.toml` with `provider = "codex"`;
- run `ascend doctor`.

Do not install software or modify authentication silently.

### 7.5 Status and reports

`ascend status` and the final report must show:

```text
Backend: Codex CLI
Authentication class: ChatGPT subscription
Codex version: ...
Configured model: default / ...
Configured reasoning effort: ...
Live web search: enabled for stages ...
```

Do not display usernames, access tokens, or account identifiers unless already intentionally exposed by the official status command and necessary—which should normally be avoided.

---

## 8. Configuration changes

Provide a documented configuration structure similar to:

```toml
config_version = 2

[backend]
provider = "codex"       # codex | api
allow_automatic_fallback = false

[codex]
executable = "codex"
model = ""
research_effort = "xhigh"
audit_effort = "xhigh"
manuscript_effort = "high"
formalization_effort = "xhigh"
max_parallel_agents = 3
max_parallel_web_agents = 2
persist_sessions = true
skip_git_repo_check = false
extra_args = []

[codex.limits]
max_agent_calls = 100
max_research_rounds = 8
max_codex_threads = 40
max_wall_clock_minutes = 480
max_formalization_iterations = 60

[api]
# Preserve all existing API settings here.
# The exact keys should match the current project.
```

Security requirements for `extra_args`:

- validate against an allowlist or clearly mark as unsafe;
- reject flags that disable sandboxing unless the execution backend is an isolated container;
- reject attempts to override output paths, working directories, or authentication in ways that bypass ASCEND controls.

Support environment overrides where consistent with the existing project, for example:

```text
ASCEND_BACKEND=codex
ASCEND_CODEX_MODEL=...
ASCEND_CODEX_EXECUTABLE=...
```

Do not require these.

---

## 9. Error classification

Add Codex-specific error types. At minimum:

```text
CODEX_NOT_INSTALLED
CODEX_NOT_AUTHENTICATED
CODEX_AUTH_EXPIRED
CODEX_UNSUPPORTED_VERSION
CODEX_REQUIRED_FLAG_MISSING
CODEX_MODEL_UNAVAILABLE
CODEX_REASONING_EFFORT_UNSUPPORTED
CODEX_RATE_LIMITED
CODEX_ALLOWANCE_EXHAUSTED
CODEX_NETWORK_OR_SEARCH_UNAVAILABLE
CODEX_PROCESS_TIMEOUT
CODEX_PROCESS_CRASH
CODEX_SCHEMA_VALIDATION_FAILED
CODEX_OUTPUT_MISSING
CODEX_SESSION_RESUME_FAILED
CODEX_UNAUTHORIZED_FILE_CHANGE
CODEX_UNKNOWN_ERROR
```

Every failure should include:

- stage and role;
- whether retry is safe;
- checkpoint location;
- a concise remedy;
- paths to redacted logs.

Examples:

```text
ASCEND stopped at bibliography verification because live Codex web search
was unavailable. No Lean work was started.

After fixing Codex search access, run:
    ascend resume <run-id>
```

```text
ASCEND reached the configured Codex usage limit. Completed artifacts were
saved. ASCEND did not switch to the billable API backend.

Resume later with:
    ascend resume <run-id>

Or explicitly use API mode for a new run:
    ascend run problem.md --backend api
```

---

## 10. Run artifacts and provenance

Extend run metadata to include:

```json
{
  "backend": "codex",
  "backend_version": "codex-cli ...",
  "authentication_class": "chatgpt",
  "model_requested": null,
  "model_observed": "when available",
  "reasoning_effort_requested": "xhigh",
  "web_search_enabled": true,
  "session_id": "...",
  "usage": {
    "input_tokens": 0,
    "cached_input_tokens": 0,
    "output_tokens": 0,
    "reasoning_output_tokens": 0
  }
}
```

Do not store secrets.

Recommended artifact layout:

```text
.ascend/runs/<run-id>/
├── config/
│   ├── effective_config.toml
│   └── backend_manifest.json
├── prompts/
├── research/
├── manuscript/
├── lean/
├── report/
└── traces/
    └── codex/
        ├── prompt_compiler/
        ├── solvers/
        ├── audits/
        ├── manuscript/
        └── formalization/
```

For API runs, retain the existing API trace structure.

The final report must clearly distinguish:

```text
Model execution backend: Codex CLI using saved ChatGPT authentication
```

from:

```text
Model execution backend: OpenAI Responses API using Platform API billing
```

---

## 11. README rewrite

Revise the top of the main README so Codex is unquestionably the default.

A recommended structure follows.

### 11.1 Opening

```markdown
# ASCEND

ASCEND is a local autonomous mathematical-research and formal-verification
workflow. It turns a research problem into a structured research prompt,
coordinates independent research and auditing agents, writes and validates a
LaTeX manuscript, and attempts Lean verification of the main result.

## Recommended setup: Codex CLI

Most users should run ASCEND through Codex CLI and sign in with their ChatGPT
account. This mode does not require an OpenAI API key.
```

### 11.2 Requirements

Lead with:

```markdown
You need:

- Python [supported version];
- Git;
- Codex CLI;
- a ChatGPT account with Codex access;
- Lean/Lake for formal verification;
- a LaTeX distribution for manuscript compilation.
```

Mention that Plus and Pro are suitable examples, but do not imply that only those plans can ever have Codex access or promise fixed allowances.

### 11.3 Quickstart

Put this before any API instructions:

```bash
# 1. Install Codex CLI using an official method.
# Example:
npm install -g @openai/codex

# 2. Sign in through your ChatGPT account.
codex login

# 3. Install ASCEND.
pipx install git+https://github.com/<OWNER>/<REPO>.git
# or the project's preferred installation command

# 4. Check the environment.
ascend doctor

# 5. Run ASCEND inside your project or Lean repository.
ascend run problem.md
```

Also provide the official standalone installer instructions or link rather than relying only on npm.

### 11.4 Explain model access

Add a short section:

```markdown
### How model access works

By default, ASCEND invokes the official local Codex CLI. Codex reuses the login
created by `codex login`. When you choose "Sign in with ChatGPT," ASCEND does
not need or store an OpenAI API key.

This is not offline execution: Codex communicates with OpenAI and usage is
subject to the Codex access, limits, and credits of your ChatGPT account or
workspace.
```

### 11.5 API mode as secondary

Move API instructions into a later section titled:

```markdown
## Advanced: direct OpenAI API backend
```

Explain:

```bash
export OPENAI_API_KEY="..."
ascend run problem.md --backend api
```

State explicitly that ChatGPT subscription billing and OpenAI Platform API billing are separate.

### 11.6 No silent fallback

Include:

```markdown
ASCEND never silently switches from Codex mode to API mode. This prevents an
authentication or usage-limit issue from unexpectedly creating API charges.
```

### 11.7 Troubleshooting

Add common cases:

- Codex not found;
- not signed in;
- `codex login status`;
- model unavailable;
- rate/credit limit reached;
- live search unavailable;
- unsupported CLI version;
- Git repository requirement;
- Windows/WSL differences;
- how to resume;
- how to select API mode explicitly.

### 11.8 Update all other docs

Search the complete repository for statements implying that an API key is mandatory.

Update at least:

- main README;
- installation guide;
- quickstart;
- FAQ;
- architecture document;
- configuration reference;
- CLI help;
- example `.env`;
- example configuration;
- contributor guide;
- troubleshooting guide;
- release checklist;
- generated documentation;
- sample runs.

The top-level examples should use Codex. API examples should remain, but later.

---

## 12. Installation documentation

Use official Codex installation methods and link to the official documentation.

At the time this instruction file was written, official examples include:

macOS/Linux standalone installer:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

Windows PowerShell standalone installer:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
```

npm:

```bash
npm install -g @openai/codex
```

Homebrew:

```bash
brew install --cask codex
```

Do not vendor Codex or download an unaudited binary yourself.

Because Codex commands and availability can evolve, link to official docs and keep the integration isolated behind one adapter.

---

## 13. Security requirements

The Codex backend must satisfy all of the following:

1. Never read or copy the Codex credential file.
2. Never print authentication tokens.
3. Never include secrets in prompts.
4. Never invoke Codex with `shell=True`.
5. Pass prompts over stdin.
6. Use absolute, normalized output and workspace paths.
7. Prevent path traversal.
8. Default to `read-only`.
9. Use `workspace-write` only when the stage needs edits.
10. Never use `danger-full-access` on the host by default.
11. Never silently enable shell network access.
12. Keep built-in web search distinct from shell network access.
13. Audit file changes after write-capable runs.
14. Redact environment variables and command logs.
15. Never silently switch to API billing.
16. Do not weaken the existing Lean checks.
17. Do not weaken the existing bibliography checks.
18. Treat Codex output and web content as untrusted input.
19. Preserve timeout, process-tree termination, and resource limits.
20. On cancellation, terminate child processes cleanly and persist the checkpoint.

If Docker execution already exists, allow users to combine:

```bash
ascend run problem.md --backend codex --sandbox docker
```

but do not make Docker mandatory merely to add the Codex backend.

---

## 14. Testing requirements

### 14.1 Unit tests

Add tests for:

- backend resolution;
- new-config default is Codex;
- legacy API config migration;
- explicit `--backend api`;
- no silent fallback;
- command construction;
- stdin prompt delivery;
- quoting of `model_reasoning_effort`;
- optional model flag;
- optional `--search`;
- sandbox selection;
- output-schema path;
- output-last-message path;
- JSONL parsing;
- session-ID extraction;
- usage extraction;
- error classification;
- auth-status parsing;
- redaction;
- timeout handling;
- cancellation;
- unauthorized file-change detection;
- rate-limit retry policy;
- resumption with the original backend.

### 14.2 Fake Codex executable

Create a deterministic fake `codex` executable for tests. It should simulate:

- `--version`;
- `login status`;
- help output;
- successful JSONL;
- valid structured output;
- malformed output;
- missing output;
- rate limits;
- expired authentication;
- model unavailable;
- search unavailable;
- nonzero exit;
- hung process;
- resumed thread;
- unauthorized file changes.

Most CI tests must not require a real ChatGPT account.

### 14.3 Opt-in integration tests

Add tests marked, for example:

```text
codex_live
```

that require an installed authenticated Codex CLI. They should be skipped by default.

A minimal live test should verify:

- `codex login status`;
- one tiny `codex exec --json`;
- one output-schema response;
- one read-only run;
- optional live web search;
- a resumable session, if ASCEND uses it.

Do not run expensive research in CI.

### 14.4 End-to-end acceptance tests

Create a small mathematical example whose workflow can complete cheaply. Test both:

```bash
ascend run examples/small_problem.md --backend codex
ascend run examples/small_problem.md --backend api
```

The outputs should satisfy the same project schemas and stage ordering.

Also test:

- Codex unavailable;
- not authenticated;
- citation gate failure;
- interrupted run and resume;
- Lean failure;
- API backend still working;
- old config migration.

---

## 15. Acceptance criteria

The work is complete only when all of the following are true.

### Default experience

- [ ] `ascend run problem.md` selects Codex on a new installation.
- [ ] A ChatGPT-authenticated Codex user can complete a run without `OPENAI_API_KEY`.
- [ ] `ascend doctor` clearly explains Codex setup.
- [ ] The README quickstart uses Codex first.
- [ ] API setup appears later as advanced/optional.
- [ ] No documentation says an API key is universally required.

### Correctness

- [ ] Every existing ASCEND stage works through `CodexCliBackend`.
- [ ] Structured outputs are validated.
- [ ] Multi-agent independence is preserved.
- [ ] Manuscript precedes Lean.
- [ ] Related-work and bibliography gates remain mandatory.
- [ ] Lean compilation and axiom/placeholder checks remain mandatory.
- [ ] Resume preserves backend provenance.

### Safety

- [ ] No credential files are read.
- [ ] No secrets are logged.
- [ ] No shell string invocation is used.
- [ ] No silent API fallback exists.
- [ ] No dangerous sandbox mode is used on the host by default.
- [ ] Unauthorized file changes are detected.
- [ ] Cancellation cleans up subprocesses.

### Compatibility

- [ ] Existing API users can continue using the API backend.
- [ ] Legacy configuration is migrated safely.
- [ ] Existing run artifacts remain readable.
- [ ] Linux and macOS work.
- [ ] Windows behavior matches the project's declared support policy; WSL2 instructions are provided where appropriate.
- [ ] Tests pass without a live Codex account.
- [ ] Opt-in live Codex tests pass in a properly authenticated environment.

### Documentation

- [ ] README reordered.
- [ ] Installation guide updated.
- [ ] Configuration reference updated.
- [ ] CLI help updated.
- [ ] FAQ and troubleshooting updated.
- [ ] Architecture diagram shows Codex as primary.
- [ ] API mode is described accurately as separately billed.
- [ ] "No API key required" is not misrepresented as offline or unlimited.

---

## 16. Suggested implementation sequence

Implement in this order:

1. Add the backend interface without changing behavior.
2. Wrap the existing API implementation as `OpenAIResponsesBackend`.
3. Add backend configuration and migration.
4. Implement Codex installation/capability detection.
5. Implement safe `codex exec` subprocess execution.
6. Implement JSONL and final-output parsing.
7. Implement structured-output validation.
8. Implement authentication diagnostics.
9. Route one low-risk stage through Codex.
10. Route all read-only research/audit stages.
11. Route manuscript generation and bibliography audit.
12. Integrate the existing Lean/Codex formalization path with the common backend.
13. Add retries, resumption, and rate-limit handling.
14. Add unit and fake-executable tests.
15. Add opt-in live tests.
16. Change new-install defaults to Codex.
17. Rewrite README and all user documentation.
18. Run both Codex and API end-to-end tests.
19. Add a migration note and changelog entry.
20. Release as a backward-compatible minor version if the configuration migration is transparent; otherwise use the project's normal major-version policy.

Do not switch the documented default until the Codex end-to-end path passes the same stage and artifact checks as the API path.

---

## 17. Recommended user-facing release note

```markdown
## Codex is now ASCEND's default backend

ASCEND can now run through the official Codex CLI using your saved ChatGPT
login. For most users, this removes the need to create or manage an OpenAI API
key.

Get started:

1. Install Codex CLI.
2. Run `codex login` and choose "Sign in with ChatGPT."
3. Run `ascend doctor`.
4. Run `ascend run problem.md`.

The existing direct OpenAI API backend remains fully supported:

    ascend run problem.md --backend api

ASCEND never silently switches from Codex to API mode.
```

---

## 18. Official references for implementation

Check these official sources while implementing, because CLI details may evolve:

- Codex CLI overview and installation:  
  https://developers.openai.com/codex/cli

- Codex authentication:  
  https://developers.openai.com/codex/auth

- Codex non-interactive mode (`codex exec`):  
  https://developers.openai.com/codex/non-interactive-mode

- Codex CLI command reference:  
  https://developers.openai.com/codex/cli/reference

- Codex configuration:  
  https://developers.openai.com/codex/config-basic  
  https://developers.openai.com/codex/config-reference  
  https://developers.openai.com/codex/config-advanced

- Codex sandbox and approval security:  
  https://developers.openai.com/codex/agent-approvals-security

- Codex pricing and plan availability:  
  https://chatgpt.com/codex/pricing/

Use current official behavior as the source of truth when it differs from an example in this file. Preserve the product-level requirements: Codex is the default, ChatGPT sign-in requires no API key, API mode remains explicit and secondary, and no silent billable fallback is permitted.
