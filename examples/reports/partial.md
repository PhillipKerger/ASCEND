# ASCEND report — `20260719T120000Z-example-partial-d4e5f6`

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

- [`research/candidate/package.json`](../../.ascend/runs/EXAMPLE/research/candidate/package.json)
- [`manuscript/paper.pdf`](../../.ascend/runs/EXAMPLE/manuscript/paper.pdf)
- [`lean/challenge.lean`](../../.ascend/runs/EXAMPLE/lean/challenge.lean)
- [`lean/iterations/3/verdict.json`](../../.ascend/runs/EXAMPLE/lean/iterations/3/verdict.json)
- [`lean/iterations/3/lean_diagnostics.log`](../../.ascend/runs/EXAMPLE/lean/iterations/3/lean_diagnostics.log)
- [`report/verification_certificate.json`](../../.ascend/runs/EXAMPLE/report/verification_certificate.json)

## Reproduce or continue

```bash
ascend verify 20260719T120000Z-example-partial-d4e5f6
ascend resume 20260719T120000Z-example-partial-d4e5f6
```

Completed provider work is reused from durable redacted call/session records when the backend
supports replay. ASCEND does not switch to API billing when Codex access is unavailable.
