# MATEK report — `20260719T120000Z-example-partial-d4e5f6`

This sanitized example shows a valid paper proof whose formalization is incomplete.

## Execution provenance

- Model backend: Codex CLI (the recommended/default provider)
- Authentication class: ChatGPT
- Requested model: Codex account/workspace default
- Automatic API fallback: disabled

## Outcome

| Gate | Status |
| --- | --- |
| Research | `RESEARCH_ACCEPTED_FOR_MANUSCRIPT` |
| Manuscript | `BIBLIOGRAPHY_VERIFIED` |
| Lean | `LEAN_PARTIAL` |

## Strongest established result

The paper proof passed the independent research and bibliography gates. Kernel verification is
not claimed.

## Unresolved obligations

- Formalize the compactness lemma without introducing a new axiom.
- Resolve the final typeclass inference error recorded in iteration 3.

## Representative artifacts

- [`research/candidate/package.json`](../../.matek/runs/EXAMPLE/research/candidate/package.json)
- [`manuscript/paper.pdf`](../../.matek/runs/EXAMPLE/manuscript/paper.pdf)
- [`lean/challenge.lean`](../../.matek/runs/EXAMPLE/lean/challenge.lean)
- [`lean/iterations/3/verdict.json`](../../.matek/runs/EXAMPLE/lean/iterations/3/verdict.json)
- [`lean/iterations/3/lean_diagnostics.log`](../../.matek/runs/EXAMPLE/lean/iterations/3/lean_diagnostics.log)
- [`report/verification_certificate.json`](../../.matek/runs/EXAMPLE/report/verification_certificate.json)

## Reproduce or continue

```bash
matek verify 20260719T120000Z-example-partial-d4e5f6
matek resume 20260719T120000Z-example-partial-d4e5f6
```

Completed provider work is reused from durable redacted call/session records when the backend
supports replay. MATEK does not switch to API billing when Codex access is unavailable.
