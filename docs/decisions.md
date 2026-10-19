# Decisions

A dated log of choices a newcomer would otherwise have to ask about. Newest
entry at the bottom.

## 2026-10-19 — Badge carries the concept DOI, data availability carries both

**Decision.** The DOI badge at the top of `README.md` links to the concept DOI
`10.5281/zenodo.22715477`. The Data availability section names the v1.1 version
DOI `10.5281/zenodo.23309593` first and the concept DOI second.
`CITATION.cff` takes the version DOI as `doi:` and the concept DOI under
`identifiers:`.

**Why.** Zenodo now holds more than one version of the record, and the two DOIs
answer different questions. A reader checking the numbers in the paper needs the
exact deposit those numbers were measured against, which only the version DOI
pins. A reader who just wants the data needs whatever is current, which only the
concept DOI tracks. A badge is the one place that cannot be kept in step by
hand, so it gets the DOI that never goes stale.

**Alternatives.** Putting the version DOI everywhere was the previous state. It
broke the moment a second version was deposited: the badge pointed at v1.0 while
the record page showed v1.1. Putting the concept DOI everywhere loses the
ability to cite a fixed artifact, which an evaluated tool paper cannot give up.

**Consequences.** Every new Zenodo version requires one edit, the version DOI in
the Data availability block and in `CITATION.cff`. The badge is left alone. The
two DOIs will visibly differ, so the README states in a sentence what each one
is for rather than leaving a reader to guess.
