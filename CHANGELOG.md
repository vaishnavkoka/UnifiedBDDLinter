# Changelog

## 1.0

The first release of the consolidated tool. It descends from the earlier v1.0
release, and `bddlint.py version` prints the identifier of the engine it was
ported from.

### Rules

**28 rules: 8 repaired automatically, 20 reported.** The earlier line "11 of 28
are fixable" no longer holds, and the reasons are below.

**Withdrawn from the fixer**

- **`W005` (step with trailing period)** — removing the period invents no text,
  yet step text is the string the runtime matches a step definition against. On
  a minimal Maven project this turned a passing suite into a failing one, with
  the step reported `UNDEFINED`. Now detect-only.
- **`ST001`–`ST004`, `ST006`** — repairing these meant authoring feature names,
  scenario names, or whole scenarios. v1.0 invented them. Now detect-only.
- **`Q001` (implementation detail)** — v1.0 rewrote step wording automatically,
  which changed what the test asserted. Now detect-only.

**Removed entirely**

- **`SY001` (spelling)** — found zero violations across 20,270 files, cost about
  95% of total runtime, was the package's only third-party dependency, and its
  auto-correction rewrote technical terms into nonsense (`sql`→`sol`,
  `xml`→`my`). Removing it is what makes "standard library only" true without an
  asterisk.
- **`Q007`, `ST005`, `T005`** — fired on five or fewer files each across the
  whole corpus.

Gaps in the rule numbering are these removals. Identifiers are not renumbered, so
an existing `.unified-lintrc.json` keeps working.

**Added**

- `ST013` byte-order mark, `T001` duplicate tag, `T006` tag alignment — all
  repaired, all provably free of content change.

### Behaviour

- The fixer **refuses to write in place.** `--output-dir` is effectively
  mandatory, and `--dry-run` now writes nothing at all, not even an empty
  directory.
- Default filename convention is **snake_case**, configurable to kebab-case via
  `formatting.file_name_style`. The two incumbent linters demand opposite
  conventions, so the tool must choose one.
- `.unified-lintrc.json` is load-bearing: rule severities, per-rule toggles,
  numeric thresholds, the filename convention and path exclusions all resolve
  from it. `tools/bddlint.py config` prints what actually applied.
- Reports print the file path once as a heading rather than on every line, and
  each violation carries its rule name and severity.
- SARIF output added alongside text and JSON.

### Portability

- Every file is read with an explicit encoding and a BOM-tolerant decode. v1.0
  used the platform default, which crashed on Windows.
- No third-party imports anywhere in `src/`. A test fails if one appears.
- Tested on Linux, macOS and Windows, Python 3.8 through 3.13.

### Evaluation

- The differential harness moved out of the tool into [evaluation/](evaluation/).
  It is a research instrument, not part of the tool, and the tool never invokes
  it.
- Repairs are verified by re-parsing with the official Gherkin parser and
  comparing models, rather than by trusting a drop in reported violations.
- Files the parser cannot process are excluded from that verification and
  reported separately, rather than counted as verified.
