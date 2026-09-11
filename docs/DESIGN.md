# Design

## One parse, four families

A `.feature` file is read once. `UnifiedParser` produces a line-indexed model,
and every rule reads from that model rather than re-reading the file. The rules
are stateless functions over it, grouped into four families:

| Family | Question it asks |
|---|---|
| **Style** | Is the surface form consistent? |
| **Structure** | Are the required elements present and unique? |
| **Workflow** | Does the scenario follow Given/When/Then discipline? |
| **Quality** | Does this read as a specification, or as a script? |

The scan is **line-based, not AST-based**. That keeps the tool dependency-free
and fast enough for a twenty-thousand-file corpus, and it costs the tool the
parser's knowledge of localised keywords — see [Limitations](#limitations).

The complete catalogue, with each quality rule's actual mechanism, is in
[RULES.md](RULES.md).

## The safe-fix boundary

The central design decision is not which rules exist but which may be repaired
automatically.

> **A repair may alter neither the words in the specification nor any string the
> runtime binds a step definition to.**

Both clauses are load-bearing, and the second is the one that is easy to get
wrong.

### Why the second clause exists

`W005` reports a step ending in a full stop. Removing it invents no text and
deletes no word, so it passes the first clause cleanly. It is still unsafe.

Cucumber binds a step definition by matching the **step text**. A project whose
definition includes the trailing period stops matching the moment the period is
removed, and the step is reported `UNDEFINED`. This is not hypothetical: on a
minimal Maven project, `mvn test` reported `BUILD SUCCESS` before the repair and
`BUILD FAILURE` after it.

`W005` was implemented, measured, and then withdrawn. It is detect-only.

### Why rules are detect-only

Three distinct reasons, which is why "form versus semantic" is the wrong axis:

| Reason | Rules |
|---|---|
| repairing requires **authoring text** the tool cannot derive | `ST001`–`ST004`, `ST006`, most of `W*`, all of `Q*` |
| repairing changes **a string the runtime binds on** | `W005` |
| **no single correct repair exists** | `S006` — shortening a name means choosing which words to drop |

`S005`, `S006` and `W005` are all *form* violations that are nonetheless never
repaired. The honest split is **repairable (8) versus reported (20)**.

### What the eight repairs do

| Rule | Repair | Why it is safe |
|---|---|---|
| `S001` | strip trailing whitespace | invisible to the parser |
| `S002` | collapse consecutive blank lines | invisible to the parser |
| `S003` | add a final newline | invisible to the parser |
| `S004` | normalise indentation | Gherkin's grammar does not carry meaning in indentation |
| `T006` | align a tag line with its element | whitespace only |
| `T001` | remove a repeated tag | the tag *set* is unchanged, so tag filters select the same scenarios |
| `ST013` | delete a UTF-8 byte-order mark | three bytes that carry no content; the file could not be parsed at all before |
| `ST007` | rename the **file** to match the feature name | no `.feature` content changes, and Cucumber discovers features by directory glob, never by filename |

`ST007` is the only repair that touches anything outside a file's own bytes, and
it was the one the reviewer of an earlier draft questioned. It is safe for the
reason above, and the inverse case — `W005` — is what actually breaks binding.

## The fixer never writes in place

`auto_fix.py` refuses to modify its input. `--output-dir` is effectively
mandatory, and `--dry-run` writes nothing at all, not even an empty directory.
A repair you cannot undo by deleting a directory is a repair nobody will try.

## Configuration

`.unified-lintrc.json` is discovered by walking upward from the target, the same
way ESLint and Git resolve their configuration, so one file at a repository root
governs every subdirectory. It controls rule severities, per-rule enable and
disable, numeric thresholds, the filename convention, and path exclusions.

A malformed file **warns and falls back to defaults** rather than refusing to
run. A linter that will not lint because its configuration has a typo is worse
than one that lints with defaults and says so.

`python3 tools/bddlint.py config` prints what actually resolved, and from where.

## Limitations

**English keywords only.** The line-based scan looks for `Feature:`, `Scenario:`
and the English step keywords. Gherkin supports localised keywords through a
`# language:` header, which the official parser honours and this scan does not.
Files in other languages are reported as lacking a feature declaration. The
affected rule is detect-only, so such files are never modified, but their
violation counts are overstated.

**Heuristics, not linguistics.** Every quality rule is a keyword or shape match.
`Q005` is a length threshold; `Q008` compares the first three words of scenario
names. Their precision has not been measured — the evaluation establishes that
repairs are safe, which is a different question.
