# ASCEND report — `20260719T120000Z-example-success-a1b2c3`

This is a shortened, sanitized example of the report produced by a successful fixture run.

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
| Lean | `LEAN_VERIFIED` |

## Strongest established result

The exact theorem in the frozen claim contract was proved by two independent routes, passed
all mandatory audits, and was accepted by the final judge.

## Unresolved obligations

None recorded.

## Representative artifacts

- [`prompts/compiled_problem.json`](../../.ascend/runs/EXAMPLE/prompts/compiled_problem.json)
- [`research/rounds/1/plan.json`](../../.ascend/runs/EXAMPLE/research/rounds/1/plan.json)
- [`research/rounds/2/plan.json`](../../.ascend/runs/EXAMPLE/research/rounds/2/plan.json)
- [`research/candidate/package.json`](../../.ascend/runs/EXAMPLE/research/candidate/package.json)
- [`research/verdict.json`](../../.ascend/runs/EXAMPLE/research/verdict.json)
- [`manuscript/bibliography_audit.json`](../../.ascend/runs/EXAMPLE/manuscript/bibliography_audit.json)
- [`manuscript/paper.pdf`](../../.ascend/runs/EXAMPLE/manuscript/paper.pdf)
- [`lean/CLAIM_ALIGNMENT.json`](../../.ascend/runs/EXAMPLE/lean/CLAIM_ALIGNMENT.json)
- [`lean/build.log`](../../.ascend/runs/EXAMPLE/lean/build.log)
- [`lean/axioms.txt`](../../.ascend/runs/EXAMPLE/lean/axioms.txt)
- [`report/verification_certificate.json`](../../.ascend/runs/EXAMPLE/report/verification_certificate.json)

Every real link is run-relative and accompanied by a SHA-256 digest and byte count.

## Reproduce

```bash
ascend status 20260719T120000Z-example-success-a1b2c3
ascend verify 20260719T120000Z-example-success-a1b2c3
ascend resume 20260719T120000Z-example-success-a1b2c3
```
