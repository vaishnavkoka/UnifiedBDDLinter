# Results

Headline measurements from the evaluation described in
[../docs/EVALUATION.md](../docs/EVALUATION.md). The complete per-file dataset is
too large for git and is archived on Zenodo: **[TBD]**

## Files

| File | What it holds |
|---|---|
| `summary.csv` | corpus totals, per-linter before/after, per-file outcomes |
| `table1_repositories.csv` / `.tex` | per-repository reduction for all 38 |
| `per_rule_violations.csv` | how often each of the 28 rules fired, before and after |
| `edit_classes.csv` | what kind of edit each repaired file actually received |
| `semantic_verification.csv` | Gherkin-parser comparison of every before/after pair |
| `semantic_differences.csv` | every difference the parser found, one row each |
| `versions.txt` | tool, oracle and environment versions for the run |
| `figures/` | the figures used in the paper |

## Reading `summary.csv`

| Metric | Meaning |
|---|---|
| `files` | `.feature` files measured |
| `repositories` | repositories they came from |
| `<linter>_before` / `_after` | total violations that linter reported, across the corpus |
| `<linter>_resolved_pct` | percentage reduction for that linter |
| `aggregate_reduction_pct` | reduction for `driving_linter`, corpus-wide |
| `median_repo_reduction_pct` | median of the per-repository reductions |
| `repos_above_80` | repositories exceeding an 80% reduction |
| `repos_regressing` | repositories where violations increased |
| `files_improved` | files whose violation count fell |
| `files_unchanged` | files whose count did not move |
| `files_regressed` | files whose count rose |
| `files_already_clean` | files with no violations before repair |

The three linters' counts are **not comparable to one another and must never be
added together.** Each has its own rule set, and one violation in one tool is not
one violation in another. Compare a linter only against itself, before and after.

## Reading `edit_classes.csv`

Each repaired file is classified by comparing it with its original, **not** by
which rule claimed to fire. Classes overlap: a file can be both `whitespace` and
`filename`, so the column does not sum to the corpus size.

`content` is the one that matters. It counts files whose text differs once all
whitespace is removed — that is, files where a word changed. It is **0**.

## Reading `semantic_verification.csv`

| Metric | Meaning |
|---|---|
| `files_compared` | pairs submitted to the parser |
| `unparseable_both_sides` | files the Gherkin parser rejects before *and* after — nothing about them can be verified either way, so they are excluded rather than counted as identical |
| `files_verified` | `files_compared` minus the above |
| `semantically_identical` | of those verified, how many parse to an identical model |
| `differing_parse-regression` | files that parsed before repair and not after |

## Sampling

`sample_results.csv` is a small excerpt of the per-file rows, so the column
layout can be inspected without downloading the full dataset. The 16 columns are
described in [../docs/EVALUATION.md](../docs/EVALUATION.md).

A full results file contains embedded newlines inside its violation-text columns,
which is valid CSV but means `wc -l` reports far more lines than there are
records. Use a real CSV reader, or [../scripts/inspect_run.py](../scripts/inspect_run.py).
