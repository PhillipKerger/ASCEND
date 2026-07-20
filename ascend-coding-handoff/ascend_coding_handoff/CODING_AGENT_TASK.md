# Coding Agent Task

Implement **ASCEND** (**Autonomous System for Conjecture Exploration and Verified Deduction**), an installable local CLI that executes an auditable
mathematical research, manuscript, and Lean-verification workflow.

## Deliverable

Turn this handoff scaffold into a complete GitHub-ready repository. The expected command is:

```bash
ascend run problem.md
```

when invoked from a folder containing an existing Lean/Lake project. The workflow must also
support research-only use when Lean is absent or disabled.

## Required commands

```text
ascend init
ascend doctor
ascend run PROBLEM_FILE
ascend status [RUN_ID]
ascend resume [RUN_ID]
ascend report [RUN_ID]
ascend verify [RUN_ID]
```

## Required implementation order

1. Project discovery, configuration, workspace creation, atomic state persistence.
2. A common model-backend interface, Codex CLI default backend using saved ChatGPT
   authentication, and advanced OpenAI Responses adapter. Both require structured outputs,
   search control, usage accounting, retries, incomplete-response handling, and redaction.
3. Prompt compilation from the verbatim framework.
4. Adaptive research rounds, approach registry, candidate packaging, audits, final judge.
5. Manuscript generation, bibliography verification, LaTeX compilation gate.
6. Lean feasibility, `challenge.lean`, statement alignment, Codex formalization loop.
7. Deterministic Lean verification and final report.
8. Docker backend only after the native backend is stable.

## Acceptance scenario

Using fake adapters and fixtures, an end-to-end test must demonstrate:

- a problem file is ingested;
- the framework is adapted and saved;
- two research rounds are executed;
- a candidate proof is audited and accepted;
- a manuscript and `.bib` file are generated;
- every bibliography item is marked verified;
- LaTeX compilation is simulated successfully;
- a `challenge.lean` theorem is generated and statement-aligned;
- Codex formalization is simulated;
- Lean verification is simulated successfully;
- the final report says `LEAN_VERIFIED` and links every artifact;
- resuming the same run does not repeat completed paid stages.

A second fixture must exercise a rejected proof and confirm that manuscript and Lean stages
are not run.

## Current backend assumptions

The recommended/default path invokes official `codex exec`, reuses `codex login` authentication,
and requires no Platform API key when signed in with ChatGPT. The Responses API remains an
explicit, separately billed alternative. Both support structured output and search through
their respective adapters. Current CLI/API details must be re-verified and isolated behind
those adapters. See `SOURCES.md`.
