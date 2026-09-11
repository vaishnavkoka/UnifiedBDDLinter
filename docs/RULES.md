# Rule reference

Generated from `src/unifiedbddlinter/catalogue.py`. Do not edit by hand —
regenerate with `python3 tools/generate_rule_docs.py`.

**28 rules.** 20 run in default mode; 8 are safely fixable.

## Fixability

| | meaning |
|---|---|
| `SAFE` | repairable from the file's own content; applied by default |
| `UNSAFE` | repairable only by authoring text the tool cannot derive; **detect-only** |
| `NONE` | no automatic repair exists or should exist — a human judgement |


## Style — surface form

| ID | Rule | Severity | Fix | Detects |
|---|---|---|---|---|
| `S001` | No trailing spaces | warning | `SAFE` | Whitespace at end of line. Invisible, produces noisy diffs. |
| `S002` | No multiple empty lines | info | `SAFE` | More than one consecutive blank line. |
| `S003` | EOF newline | info | `SAFE` | File does not end with a newline; POSIX tools mishandle the last line. |
| `S004` | Indentation | error | `SAFE` | Indentation inconsistent with Gherkin nesting. |
| `S005` | File name format | error | `NONE` | File name is not snake_case. Renaming may break external references, so this is reported for a human to action. |
| `S006` | Name length | warning | `NONE` | Feature, scenario or step name exceeds its configured limit. Shortening requires knowing what may be dropped. |
| `T006` | Tag indentation mismatch | info | `SAFE` | A tag line is not aligned with the element beneath it. Whitespace only. |

## Structure — required elements

| ID | Rule | Severity | Fix | Detects |
|---|---|---|---|---|
| `ST001` | Unnamed feature | error | `UNSAFE` | 'Feature:' with no name. A name cannot be derived from the file. |
| `ST002` | Unnamed scenario | error | `UNSAFE` | 'Scenario:' with no name. Naming it requires reading its intent. |
| `ST003` | Empty file | error | `UNSAFE` | No content at all. Repair would mean authoring an entire scenario. |
| `ST004` | No feature | error | `UNSAFE` | No 'Feature:' header anywhere in the file. |
| `ST006` | Duplicate scenario name | warning | `UNSAFE` | Two scenarios share a name. v1.0 appended a numeric suffix, which MASKED the Q008 'similar scenarios' finding underneath. |
| `ST013` | Byte-order mark | error | `SAFE` | File begins with a UTF-8 BOM. The official Gherkin parser rejects the file outright -- it looks perfect and the suite will not start. Repaired by deleting three bytes that carry no content; every character of Gherkin is byte-identical afterwards. |
| `T001` | Duplicate tag | warning | `SAFE` | A tag is repeated on one element. Removing the repeat cannot change which scenarios a tag filter selects: the tag SET is unchanged, and Cucumber collapses duplicates anyway. |
| `ST007` | Feature/file name match *(full mode only)* | error | `SAFE` | Feature name disagrees with file name. Repaired by renaming the FILE, so no .feature content changes. Safe with Cucumber, which resolves step definitions by annotation, never by filename. |

## Workflow — Given/When/Then discipline

| ID | Rule | Severity | Fix | Detects |
|---|---|---|---|---|
| `W001` | Only one When | warning | `NONE` | Multiple 'When' steps: the scenario tests more than one action. |
| `W002` | GWT order | error | `NONE` | Given/When/Then out of order. Reordering could change meaning. |
| `W003` | No verification step | error | `NONE` | No 'Then'. The scenario asserts nothing, so it cannot fail for the right reason. Only the author knows what should be asserted. |
| `W004` | No action step | error | `NONE` | No 'When'. The scenario exercises no behaviour. |
| `W005` | Step with period | info | `NONE` | Step ends with '.'. DETECT-ONLY: removing the period changes the step TEXT, which is the string Cucumber matches a step definition against. A project whose definition includes the period stops binding, and the step becomes UNDEFINED. Demonstrated with a real `mvn test` run in misc/binding-experiment/: BUILD SUCCESS before the repair, BUILD FAILURE after. |
| `W006` | Too many steps | warning | `NONE` | Step count exceeds the configured maximum; the scenario is doing too much. Splitting it is a design decision. |

## Quality — is this a specification or a script?

| ID | Rule | Severity | Fix | Detects |
|---|---|---|---|---|
| `Q001` | Implementation detail *(full mode only)* | warning | `NONE` | Steps name UI mechanics ('click the button') rather than business intent. v1.0 rewrote these words automatically, which changed what the test asserted. Now detect-only. |
| `Q002` | Vague language *(full mode only)* | info | `NONE` | Words like 'properly', 'correctly' that assert nothing checkable. |
| `Q003` | Step count *(full mode only)* | info | `NONE` | Scenario length signalling a readability problem. |
| `Q004` | Test jargon in name *(full mode only)* | info | `NONE` | Names containing 'test', 'verify', 'check' -- describes the activity rather than the behaviour. |
| `Q005` | Vague scenario name *(full mode only)* | info | `NONE` | Name too short to convey which behaviour is verified ('login'). |
| `Q006` | Mock data *(full mode only)* | info | `NONE` | Placeholder data ('foo', 'test123') obscuring the real case. |
| `Q008` | Similar scenarios *(full mode only)* | info | `NONE` | Near-duplicate scenarios that likely should be a Scenario Outline. |

## How the Quality family decides

Every quality rule is a keyword or shape heuristic over the raw lines.
There is no parsing of meaning, no lexicon and no NLP. That is the cost
of the tool having no dependencies, and it bounds what these rules can
see.

**`Q001` — Implementation detail**

A specification should say what the business needs, not which buttons to press. Case-insensitive substring match on step lines against 19 UI terms (clicks, selects, types, button, link, checkbox, ...). First hit wins, at most one violation per step.

**`Q002` — Vague language**

Words that sound like a requirement but cannot be tested. Word-boundary regex on step lines against 12 terms (basic, simple, some, various, stuff, etc, maybe, probably, might, ...). The word boundary matters: 'some' must not fire inside 'something'.

**`Q003` — Step count**

A scenario with two steps usually has not finished explaining itself, and one with fifteen is doing several things at once. Counts step lines between Scenario headers and flags anything outside 3-7. Configurable via limits.min_steps_per_scenario / max_steps_per_scenario.

**`Q004` — Test jargon in name**

The name should describe the behaviour, not the fact that it is being tested. Word-boundary regex on the scenario name for test, verify, check, validate, should.

**`Q005` — Vague scenario name**

A name too short to say which behaviour is checked -- 'Login' does not distinguish success from lockout. Implemented purely as len(name) < 10 characters. This is the crudest rule in the family: a short precise name is flagged, and a long vague one is not.

**`Q006` — Mock data**

Placeholder values hide the real case -- 'password123' does not say whether the rule concerns length, expiry or reuse. Five case-insensitive regexes on step lines: user123, test@test.com, password123, john[ ._-]?doe, 999-99-9999.

**`Q008` — Similar scenarios**

Two scenarios opening the same way are usually one scenario with different data, which Gherkin already expresses as a Scenario Outline. Keys each scenario by its first three words, lowercased, and reports a repeat against the line of the first occurrence.

