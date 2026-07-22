# MATEK report — `20260719T120000Z-example-success-a1b2c3`

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
| Workflow | `COMPLETE` |
| Manuscript | `PUBLICATION_READY` |
| Publication | `READY` |
| Lean | `LEAN_VERIFIED` |

## Strongest established result

The exact theorem in the frozen claim contract was proved by two independent routes, passed
all mandatory audits, and was accepted by the final judge.

## Unresolved obligations

None recorded.

## Representative artifacts

- [`prompts/compiled_problem.json`](../../.matek/runs/EXAMPLE/prompts/compiled_problem.json)
- [`research/coordinator/state.json`](../../.matek/runs/EXAMPLE/research/coordinator/state.json)
- [`research/events/00000001.json`](../../.matek/runs/EXAMPLE/research/events/00000001.json)
- [`research/decisions/00000001.json`](../../.matek/runs/EXAMPLE/research/decisions/00000001.json)
- [`research/candidate/package.json`](../../.matek/runs/EXAMPLE/research/candidate/package.json)
- [`research/verdict.json`](../../.matek/runs/EXAMPLE/research/verdict.json)
- [`manuscript/bibliography_audit.json`](../../.matek/runs/EXAMPLE/manuscript/bibliography_audit.json)
- [`manuscript/paper.pdf`](../../.matek/runs/EXAMPLE/manuscript/paper.pdf)
- [`lean/CLAIM_ALIGNMENT.json`](../../.matek/runs/EXAMPLE/lean/CLAIM_ALIGNMENT.json)
- [`lean/build.log`](../../.matek/runs/EXAMPLE/lean/build.log)
- [`lean/axioms.txt`](../../.matek/runs/EXAMPLE/lean/axioms.txt)
- [`report/verification_certificate.json`](../../.matek/runs/EXAMPLE/report/verification_certificate.json)

Every real link is run-relative and accompanied by a SHA-256 digest and byte count.

## Reproduce

```bash
matek status 20260719T120000Z-example-success-a1b2c3
matek verify 20260719T120000Z-example-success-a1b2c3
matek resume 20260719T120000Z-example-success-a1b2c3
```
