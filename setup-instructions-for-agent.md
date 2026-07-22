# MATEK Setup Instructions for a Coding Agent

This document is intended to be given to a coding agent that has terminal access to the
machine where MATEK will run. Its goal is to prepare and verify the local environment, not to
start a paid research run.

A user can give their coding agent this instruction:

> Follow `setup-instructions-for-agent.md` to prepare this project for MATEK. Ask before any
> privileged system change or interactive authentication step. Set up the least expensive mode
> that satisfies my intended workflow, run `matek doctor`, and report anything I must finish
> manually. Do not start `matek run` unless I separately ask you to do so.

## Safety and scope

- Work inside the user's existing Git project. For formal verification, it must be the relevant
  Lean/Lake project.
- Do not inspect, print, copy, or modify credential files, API keys, tokens, or saved Codex
  authentication.
- Do not request secrets through chat. If a command asks for a password, token, or browser login,
  stop and ask the user to complete that step directly.
- Ask before running `sudo`, changing system packages, executing a remote installation script,
  or modifying shell startup files.
- Use only official Codex installation methods. Never configure the separately billed OpenAI API
  backend unless the user explicitly requests it.
- Do not run `matek doctor --deep` or `matek run` during setup unless the user explicitly opts
  in; both can consume Codex allowance. Ordinary `matek doctor` does not make a model call.
- Do not modify the user's project source to accommodate MATEK. Normal MATEK output belongs
  under `.matek/`.

## 1. Determine the requested workflow

Ask the user to choose one mode if it is not already clear:

1. **Research only:** requires Python, Git, Codex, and MATEK. Lean and LaTeX are unnecessary.
2. **Research and manuscript:** additionally requires a LaTeX distribution with `latexmk`.
3. **Full workflow:** additionally requires an existing Lean/Lake project with working `lean`
   and `lake` commands.

Use WSL2 rather than native Windows. Inside WSL2, keep the project and tools in the Linux
filesystem. MATEK is primarily validated on Linux; native macOS and WSL2 validation may lag the
Linux release.

## 2. Inspect before changing anything

From the project root, record only nonsecret capability information:

```bash
python3 --version
git --version
command -v codex || true
command -v pipx || true
command -v uv || true
command -v latexmk || true
command -v lean || true
command -v lake || true
```

Require Python 3.11 or newer. Do not replace a working tool merely to standardize the setup.
When a required system tool is missing, explain the official installation command appropriate
for the detected operating system and ask before installing it.

For a full workflow, also confirm that the current project contains a Lean project marker such
as `lean-toolchain`, `lakefile.toml`, or `lakefile.lean`. Do not create a new Lean project unless
the user explicitly asks for one.

## 3. Install and authenticate Codex

If `codex` is missing, use an official installation method selected with the user:

```bash
# Official standalone installer for macOS or Linux; obtain confirmation before running it.
curl -fsSL https://chatgpt.com/codex/install.sh | sh

# Alternative official npm installation.
npm install -g @openai/codex

# Alternative on macOS.
brew install --cask codex
```

Consult the current official documentation before adapting these commands:
<https://learn.chatgpt.com/docs/codex/cli>.

Check authentication only through the supported command:

```bash
codex login status
```

If Codex is not signed in, ask the user to run the following command directly and choose
**Sign in with ChatGPT**:

```bash
codex login
```

Wait for the user to finish, then rerun `codex login status`. A ChatGPT-authenticated Codex setup
does not require an `OPENAI_API_KEY`. Do not silently switch to API authentication or billing.

## 4. Install MATEK

For an end-user installation, prefer an isolated tool installer:

```bash
pipx install 'git+https://github.com/PhillipKerger/matek-theorem-agent.git'
# or
uv tool install 'git+https://github.com/PhillipKerger/matek-theorem-agent.git'
```

If MATEK is already installed, use `pipx upgrade matek-theorem-agent` or the corresponding `uv`
command only when the user asks to update it.

When setting up a MATEK source checkout for development, use a project-local virtual
environment instead:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Confirm the executable resolves without starting a workflow:

```bash
matek --help
```

If a tool installer reports that its binary directory is not on `PATH`, describe the required
change and ask before modifying shell startup files.

## 5. Verify optional prerequisites

For research and manuscript mode, require:

```bash
latexmk --version
```

Install a supported LaTeX distribution only with user approval. On Linux this is generally
provided by TeX Live packages; on macOS it is commonly provided by MacTeX. Package names vary by
operating system, so inspect the platform rather than guessing.

For the full workflow, run non-destructive local checks from the existing Lean project:

```bash
lean --version
lake --version
lake env lean --version
```

Do not upgrade the project's Lean toolchain or dependencies automatically. Respect its
`lean-toolchain` and Lake configuration.

## 6. Initialize and diagnose the project

From the project root, initialize MATEK only if it has not already been initialized:

```bash
matek init
```

Do not pass `--force` unless the user explicitly asks to replace generated setup files. Then run
the offline-first diagnostic:

```bash
matek doctor
```

Read every warning and failure. Fix only setup issues within the selected workflow mode. For
example, missing Lean is acceptable for research-only use, but missing LaTeX is not acceptable
for a manuscript run.

Optionally validate a problem and resolved stage plan without making a model call:

```bash
matek run problem.md --dry-run
```

Run this only when a real problem file exists. Do not create or overwrite the user's mathematical
problem without instructions.

## 7. Report completion

Return a concise setup report containing:

- detected operating system and Python version;
- selected workflow mode;
- paths and versions for `matek`, `codex`, and required optional tools;
- whether `codex login status` reports an authenticated session, without reproducing raw identity
  or credential data;
- the result of ordinary `matek doctor`;
- commands or interactive steps still required from the user; and
- the exact next command appropriate to the selected mode, without running it.

Typical next commands are:

```bash
matek run problem.md --research-only  # Research only
matek run problem.md --no-lean        # Research and manuscript
matek run problem.md                  # Full workflow
```

Do not claim setup is complete while a required doctor check is failing. Do not start a paid or
allowance-consuming MATEK run merely to prove the installation works.
