# Complexity and Quantitative Audit

When the target is algorithmic, asymptotic, probabilistic, or quantitative, audit the formal
input model, bit complexity, precision, parameter dependencies, constants, probability event,
termination, reconstruction, and full inequality chain. Check for pseudo-polynomial or
nonuniform dependence and for errors that accumulate across iterations.
Reject every weaker bound, approximation factor, parameter dependence, probabilistic guarantee,
or restricted input class not permitted by the exact claim contract. There are no allowed
terminal reductions.

Return a concise complexity-audit rationale and a nonempty `checks_performed` list naming the
actual model, precision, parameter, probability, termination, and inequality checks performed.
