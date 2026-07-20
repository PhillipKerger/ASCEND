# ASCEND — Coding Handoff Package

This directory is a complete implementation brief and starter scaffold for a local-first,
GitHub-distributed CLI called **ASCEND** (**Autonomous System for Conjecture Exploration and Verified Deduction**).

The intended product lets a user enter a research-level mathematics problem and runs an
auditable pipeline:

The recommended/default model backend is the official Codex CLI using the saved login from
`codex login`; **Sign in with ChatGPT** requires no Platform API key. The direct Responses API
backend is preserved as an explicit, advanced alternative, and ASCEND never silently changes
providers.

1. Adapt the bundled reusable research-prompt framework to the problem using a frontier
   reasoning model with **xhigh reasoning and web search**.
2. Run an adaptive, explicit multi-agent research process with independent approaches,
   counterexample search, synthesis, and hostile audits.
3. When a candidate proof survives the research gate, write and compile a LaTeX manuscript.
   The manuscript must contain a thorough related-work discussion, and every cited source
   must be independently verified to exist and to have correct bibliographic metadata.
   It must also contain a Statement of AI Usage naming ASCEND with GPT 5.6 and cite both the
   canonical ASCEND GitHub repository and ASCEND whitepaper arXiv preprint.
4. Assess Lean feasibility, generate and audit a human-readable `challenge.lean` statement,
   and attempt formalization using Codex CLI in the user's existing Lean project.
5. Run deterministic verification checks and produce a complete process report.

The product is intentionally local-first. No server, database service, web UI, account
system, or cloud worker is required for version 0.1.

## Start here

A coding agent should read, in order:

1. `AGENTS.md`
2. `CODING_AGENT_TASK.md`
3. `DECISIONS.md`
4. `PRODUCT_REQUIREMENTS.md`
5. `ARCHITECTURE.md`
6. `WORKFLOW_SPEC.md`
7. `CLI_SPEC.md`
8. `ARTIFACT_CONTRACT.md`
9. `TEST_PLAN.md`
10. `RELEASE_CHECKLIST.md`

The exact user-supplied prompt framework is preserved verbatim at:

`resources/prompts/research_prompt_framework.txt`

Do not shorten, paraphrase, or replace that file in the implementation.

## Intended repository name

The public repository and Python distribution should be named `ascend-math-agent`; the CLI command should be `ascend`.
