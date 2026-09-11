# FAQ

Questions the numbers invite.

## Why can't I add the three linters' counts together?

Because one violation in one tool is not one violation in another. `gherkin-lint`
reports an indentation problem per line; `cuke_linter` has no indentation rule at
all. Summing them would count the same underlying defect a different number of
times depending on which tools happened to be installed.

Each linter is compared only against itself, before and after. That is also why
the three bars in the before/after figure are grouped rather than stacked.

## Your own linter reports the most violations. Isn't that self-serving?

It reports more because it checks more — four families, one of which no other
Gherkin linter has. But a higher starting count is not evidence of anything on
its own, which is exactly why the study uses two external oracles. If the files
had not genuinely improved, `gherkin-lint`'s count would not have fallen 86%.

The claim that matters is not "we find the most" but "what we repair is safe",
and that is established by the parser comparison, not by any violation count.

## Why does `cuke_linter` barely move?

Two reasons.

Most of its rules target semantic concerns the fixer deliberately declines to
touch — missing descriptions, step length, too many steps. Those are exactly the
things a form-preserving fixer must leave alone.

The rest is the filename conflict. `cuke_linter` wants snake_case and
`gherkin-lint` wants kebab-case, so whichever the fixer picks, one of them
records new violations. See
[EVALUATION.md](EVALUATION.md#the-filename-conflict) for both directions measured
on the same corpus.

## Why are so many violations left after fixing?

By design. 20 of the 28 rules are detect-only, and they account for the bulk of
the residue. Repairing them would mean authoring text the tool cannot derive —
naming an unnamed scenario, supplying a missing `Then`, rewording a step that
leaks implementation detail. A tool that guessed at those would be changing what
the test asserts.

[DESIGN.md](DESIGN.md#the-safe-fix-boundary) explains the boundary and the three
distinct reasons a rule lands outside it.

## What does "99.99% identical" actually measure?

That the repaired file, parsed by the official Gherkin parser, yields the same
model as the original on the fields the runtime uses: feature name, tags,
background steps, scenario names, step text.

It does **not** mean the files are byte-identical — they are not, that is the
point of a repair. It does not mean the test suite was executed. And the
denominator is the **19,492 files the parser can process**, not all 20,270. 778
files cannot be parsed before or after, so nothing about them can be verified
either way, and counting them as identical would overstate the check.

## What is the one file that regressed?

A file whose `Scenario:` header had been commented out while its steps were left
live, indented with non-breaking spaces. To the parser those lines were not
steps, because the line did not begin with a keyword. Normalising the indentation
made them real steps — and a step with no enclosing scenario is illegal.

It is one file in 19,492. It is named in
[../results/semantic_differences.csv](../results/semantic_differences.csv) rather
than excluded.

## One repository shows a 0.0% reduction. Is the tool broken?

No. `saqibrizvi11/SH2_contactMaps` contains 232 files that carry the `.feature`
extension while holding protein contact-map data. None declares a `Feature:`, so
none parses, and nothing in them is safely repairable. 0.0% is the correct
answer.

They are in the corpus because mining selected on file extension. The repository
is retained rather than removed, since excluding an input after seeing its result
would bias the evaluation.

## Does renaming a file break my step definitions?

No. Cucumber discovers feature files by directory glob and binds step definitions
by matching **step text**. A filename is not part of that.

The inverse is true and is why `W005` is detect-only: removing a step's trailing
full stop changes the text the runtime matches on, which does break binding. We
measured it on a real Maven project — `BUILD SUCCESS` before the repair,
`BUILD FAILURE` after.

## Will it work on my non-English feature files?

Partly. It will lint them, but it recognises only English Gherkin keywords, so a
file using `Functionaliteit:` or `Функция:` is reported as lacking a feature
declaration. That rule is detect-only, so the file is never modified, but the
report will be wrong about it.

Replacing the line-based scan with the official Gherkin AST would fix this, and
is the first item of planned work.

## Does the tool need Node or Ruby?

No. The linter and the fixer are pure Python 3.8+ standard library, and a test in
the suite fails if a third-party import is ever introduced.

`gherkin-lint` (Node) and `cuke_linter` (Ruby) are needed only to reproduce the
comparative evaluation, where they serve as independent oracles. Pass
`--no-oracles` to the harness and even that runs without them.

## Why do the rule identifiers have gaps?

`ST005`, `T002`–`T005`, `Q007` and `SY001` were rules that we removed after
measuring them. Each fired on five or fewer files across the entire corpus, and
`SY001` additionally cost about 95% of total runtime and was the package's only
third-party dependency.

Identifiers are not renumbered, so an existing `.unified-lintrc.json` keeps
working. [../CHANGELOG.md](../CHANGELOG.md) records what went and why.

## Why is `results.csv` so large, and why does `wc -l` disagree with it?

The per-file columns hold each linter's complete output text, which for a full
corpus runs to about 700 MB. That text contains newlines, and they sit inside
quoted CSV fields — valid CSV, but `wc -l` counts them as record separators and
reports millions of lines for twenty thousand records.

Use a CSV reader, or [../tools/inspect_run.py](../tools/inspect_run.py), which
streams the file and can render one file's violations the way the linter prints
them.
