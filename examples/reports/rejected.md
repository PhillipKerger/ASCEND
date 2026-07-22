# MATEK report — `20260719T120000Z-example-rejected-0a1b2c`

This sanitized example shows a truthful scientific rejection.

## Execution provenance

- Model backend: Codex CLI (the recommended/default provider)
- Authentication class: ChatGPT
- Requested model: Codex account/workspace default
- Automatic API fallback: disabled

## Outcome

| Gate | Status |
| --- | --- |
| Research | `RESEARCH_REJECTED` |
| Workflow | `COMPLETE_WITH_WARNINGS` |
| Manuscript | `NOT_STARTED` |
| Publication | `NOT_ASSESSED` |
| Lean | `NOT_STARTED` |

## Strongest established result

The candidate's central lemma holds under the additional finite-support hypothesis, but the
original universal claim was not established.

## Unresolved obligations

- Repair or replace the false induction step exposed by the hostile audit.
- Prove the infinite-support case required by the exact claim contract.

## Representative artifacts

- [`research/registry.json`](../../.matek/runs/EXAMPLE/research/registry.json)
- [`research/candidate/package.json`](../../.matek/runs/EXAMPLE/research/candidate/package.json)
- [`research/audits/hostile.json`](../../.matek/runs/EXAMPLE/research/audits/hostile.json)
- [`research/verdict.json`](../../.matek/runs/EXAMPLE/research/verdict.json)
- [`report/verification_certificate.json`](../../.matek/runs/EXAMPLE/report/verification_certificate.json)

No manuscript, bibliography, or Lean stage ran after rejection. The absence of those artifacts is
part of the auditable outcome.
