# Lean Statement Generator

Create a human-auditable `challenge.lean` containing imports, minimal definitions, and the exact
main theorem statement. During statement generation, proof placeholders are permitted only in a
clearly marked temporary location that is not eligible for verification status.

Also create a plain-language back-translation and a field-by-field mapping to the frozen claim
contract. Do not encode the theorem as an axiom or weaken it for convenience.
