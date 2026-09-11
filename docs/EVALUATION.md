# Evaluation

How the corpus was built, how the study was run, and what the numbers mean.

## Corpus

Repository URLs were drawn from three independent mining sources — a global code
search, a GitHub search tool, and the SEART GHS tool — then normalised and
deduplicated. Each candidate was enriched with 73 metadata fields through the
GitHub API and filtered by an activity gate of at least one star and one commit.
Repositories declaring ten or more `.feature` files were cloned in full.

**38 repositories, 20,270 `.feature` files**, cloned **19 May 2026**.

[../corpus/MANIFEST.csv](../corpus/MANIFEST.csv) records the path, source,
repository, size and a content fingerprint for every file, so the exact
contents measured can be identified even though public repositories keep
changing.

The mining selected on file **extension**, not content. One consequence is
visible in the results and discussed under [Limitations](#limitations).

## Method

For every file, three linters run **before** the repair, the fixer produces a
repaired copy, and the same three linters run **after**:

```
                 ┌─ gherkin-lint ─┐                  ┌─ gherkin-lint ─┐
original file ───┼─ cuke_linter ──┤── auto_fix ──────┼─ cuke_linter ──┤── compare
                 └─ UnifiedBDD ───┘   (new copy)     └─ UnifiedBDD ───┘
```

`gherkin-lint` and `cuke_linter` are **independent oracles**. The tool does not
use them, call them, or depend on them. They are present so that the claim "the
files got better" does not rest on our own linter's opinion of its own work.

The run operates on a materialised working copy, never on the corpus master, and
the harness refuses to run if pointed at the master directly.

### Output

One row per file, 16 columns:

| Column | Meaning |
|---|---|
| `Repository_Name`, `File_Path` | identity |
| `<linter>_Errors_Before` | that linter's full output text, before |
| `<linter>_Errors_Before_nums` | its violation **count**, before |
| `<linter>_Errors_After` | full output text, after |
| `<linter>_Errors_After_nums` | violation count, after |
| `Gherkin_Issues_Fixed`, `Cuke_Issues_Fixed` | per-file deltas for the two oracles |

The text columns contain embedded newlines, which is valid CSV but means `wc -l`
reports far more lines than records. Use a CSV reader, or
[../demo/inspect_run.py](../demo/inspect_run.py).

## Verifying that repairs preserve meaning

A drop in reported violations shows only that the linters are satisfied. It does
not show that the specification still says what it said.

So every repaired file is re-parsed with the **official Gherkin parser** and
compared with its original on the fields the runtime uses: feature name, tags,
background steps, scenario names, step text. Layout is absent from that
comparison by construction — the parser does not expose indentation or blank
lines, so a form-preserving repair is invisible to it while any change to what
the specification asserts is not.

Separately, each repaired file is classified by the kind of edit it actually
received, determined by comparing the two files rather than by trusting the rule
that fired. The strongest test is the simplest: strip **all** whitespace from
both files and compare. If they are equal, every difference was whitespace.

## Results

| Linter | Before | After | Change |
|---|--:|--:|--:|
| `gherkin-lint` | 1,490,636 | 205,971 | −86.2% |
| UnifiedBDDLinter | 1,542,605 | 316,828 | −79.5% |
| `cuke_linter` | 304,064 | 290,520 | −4.5% |

- 19,910 files (98.2%) improved, 338 unchanged, **0 regressed**
- median repository reduction 86.8%, with 26 of 38 above 80%
- **0** files received a content edit
- of the 19,492 files the parser can process, **19,491 (99.99%)** produce an
  identical model after repair

Per-repository figures for all 38 are in
[../results/table1_repositories.csv](../results/table1_repositories.csv).

**The three counts are not comparable to one another and must never be summed.**
Each linter has its own rule set. Compare a linter only against itself.

## The filename conflict

`gherkin-lint` requires kebab-case filenames. `cuke_linter` requires snake_case.
Running the fixer over the same corpus under each convention, changing nothing
else:

| Fixer configured to | `gherkin-lint` `file-name` | `cuke_linter` `FeatureFileWithInvalidName` |
|---|--:|--:|
| kebab-case | 14,829 → 2,529 (−12,300) | 7,347 → 19,034 (**+11,687**) |
| snake_case | 14,829 → 18,697 (**+3,868**) | 7,347 → 1,789 (−5,558) |

Satisfying one necessarily violates the other. This is most of why
`cuke_linter`'s aggregate moves so little, and it is invisible to either linter
run alone.

## Reproducing

```bash
npm install -g gherkin-lint
gem install cuke_linter
pip install -r ../evaluation/requirements.txt

python3 evaluation/phase3_bdd_pipeline_full.py -r <repos-dir> -o out/
```

`-o` is a parent directory; each run creates its own timestamped subdirectory and
generates figures and tables itself. `--no-oracles` measures our tool alone and
needs neither Node nor Ruby. `--resume` continues an interrupted run.

A full-corpus run takes roughly 35 minutes on 24 cores and produces about 900 MB.

## Limitations

**Localised Gherkin.** 380 files (1.9%) declare a non-English `# language:`
header, predominantly Dutch. The line-based scan reports them as lacking a
feature declaration, although the official parser reads them correctly. The
affected rule is detect-only, so these files are never modified, but their
violation counts are overstated.

**Files that never parse.** 778 files cannot be parsed before or after repair.
They are excluded from the semantic verification rather than counted as
verified. 232 of them come from one repository whose files carry the `.feature`
extension while containing protein contact-map data — a consequence of mining on
extension. That repository is retained and reports a 0.0% reduction, because
excluding an input after observing its result would bias the evaluation.

**One parse regression.** A single file parsed before repair and not after. Its
`Scenario:` header had been commented out while its steps were left live under
non-breaking-space indentation. Normalising that indentation made the orphaned
steps parseable as steps, which the grammar rejects. It is reported, not
excluded, in
[../results/semantic_differences.csv](../results/semantic_differences.csv).

**Corpus currency.** Public repositories change. Re-running against today's
versions will not reproduce these figures exactly.
