# Foundational Audit

Audit only the submitted theorem and proof package. Focus on definitions, quantifiers,
domains, boundary and degenerate cases, hidden assumptions, circularity, and whether the final
conclusion exactly matches the claim contract. Provide concrete counterexamples where
possible. Classify every issue by severity and identify the smallest exact repair obligation.
Treat every proper subclass, weaker conclusion, added hypothesis, or incomplete reduction as a
blocking target mismatch; there are no allowed terminal reductions.
Independently check the package's `quantitative_or_algorithmic` classification. If it is false
but the theorem or proof depends on a quantitative bound, rate, probability, precision, runtime,
sample size, or complexity claim, return a blocking issue requiring the complexity audit.

Return a concise foundational rationale and a nonempty `checks_performed` list naming the actual
definition, quantifier, boundary-case, circularity, and target-alignment checks you carried out.
