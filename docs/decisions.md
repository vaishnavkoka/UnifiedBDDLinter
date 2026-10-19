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

## 2026-10-19 — Use the version DOI everywhere, superseding the split above

**Decision.** Reverses the entry above. Every DOI reference in this repository
names the v1.1 version DOI `10.5281/zenodo.23309593`: the badge, the Data
availability block, `CITATION.cff`, the v1.1 release notes and the repository
homepage link. The concept DOI is no longer referenced.

**Why.** Author's call. One identifier that matches what the Zenodo record page
shows, with nothing for a reader to reconcile. The earlier split put two
different numbers in front of the same reader and leaned on a sentence to
explain which was which.

**Alternatives.** The concept-DOI badge of the previous entry. It kept itself
current across deposits, but at the cost of showing a number that appears
nowhere on the record a reader lands on.

**Consequences.** Each new Zenodo deposit now needs the DOI changed by hand in
five places, listed above. Nothing updates itself. A stale badge after a future
deposit is the failure mode to watch for, so the DOI bump belongs in the release
checklist alongside the tag.
