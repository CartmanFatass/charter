# An error message may classify a failure it recognised; it must never ass

_2026-08-13 12:04 · persistent_

An error message may classify a failure it recognised; it must never assert a cause it has not verified. A vague error keeps you looking, a confident wrong one tells you to stop — so an unearned diagnosis is worse than none. charter's 1Password _fail once stapled a read-only-token hint to every write failure (earned once against a real 101), which sent issue #78's reporter to audit tokens while op was actually refusing to parse a template. Rule + rationale in docs/adr/0009. Matching a third-party CLI's stderr is acceptable ONLY when unmatched degrades to honest uncertainty, and only for signatures with recorded provenance.
