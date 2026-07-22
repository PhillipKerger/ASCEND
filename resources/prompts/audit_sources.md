# Imported-Theorem and Source Audit

Verify every external theorem used in the proof against an authoritative source. Check that
the cited result exists, is stated accurately, and applies under every actual hypothesis. Do
not infer a theorem from a paper title or secondary summary. Record source evidence and any
uncertainty.

When the target is reported as already solved, independently verify that the literature theorem
matches the exact claim contract rather than only a nearby statement. Record differences in
hypotheses, quantifiers, domains, exceptional cases, and conclusion, and reject unsupported
novelty claims.

Return a concise source-audit rationale and a nonempty `checks_performed` list naming each
imported theorem, identifier, hypothesis match, and novelty characterization actually checked.
