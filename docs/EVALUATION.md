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
[../scripts/inspect_run.py](../scripts/inspect_run.py).

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

Per-repository figures for all 38 are tabulated below.

**The three counts are not comparable to one another and must never be summed.**
Each linter has its own rule set. Compare a linter only against itself.

## Evaluated repositories

All 38, with the reduction in `gherkin-lint` violations after repair.
The same data is in [../results/table1_repositories.csv](../results/table1_repositories.csv).

| Repository | Files | Before → After | Reduction |
|---|--:|--:|--:|
| [keygen-sh/keygen-api](https://github.com/keygen-sh/keygen-api) | 218 | 80,970 → 1,679 | 97.9% |
| [Novus-Engine/novuspack](https://github.com/Novus-Engine/novuspack) | 1,399 | 48,827 → 1,975 | 96.0% |
| [CriminalInjuriesCompensationAuthority/q-templates-application](https://github.com/CriminalInjuriesCompensationAuthority/q-templates-application) | 512 | 104,115 → 4,681 | 95.5% |
| [opencypher/openCypher](https://github.com/opencypher/openCypher) | 440 | 24,994 → 1,182 | 95.3% |
| [bdewey/git-stack](https://github.com/bdewey/git-stack) | 205 | 4,376 → 287 | 93.4% |
| [HarrisClover/RequireCEG](https://github.com/HarrisClover/RequireCEG) | 1,225 | 39,364 → 2,703 | 93.1% |
| [reqnroll/Reqnroll.ExploratoryTestProjects](https://github.com/reqnroll/Reqnroll.ExploratoryTestProjects) | 1,214 | 158,090 → 12,136 | 92.3% |
| [csu0077/project3_testing](https://github.com/csu0077/project3_testing) | 35 | 1,229 → 97 | 92.1% |
| [git-town/git-town](https://github.com/git-town/git-town) | 1,345 | 22,905 → 1,856 | 91.9% |
| [Corvusoft/restq](https://github.com/Corvusoft/restq) | 229 | 29,662 → 2,522 | 91.5% |
| [projectestac/alexandria](https://github.com/projectestac/alexandria) | 1,064 | 63,910 → 5,478 | 91.4% |
| [inukshuk/citeproc](https://github.com/inukshuk/citeproc) | 780 | 6,695 → 739 | 89.0% |
| [kabisa/books](https://github.com/kabisa/books) | 20 | 705 → 80 | 88.7% |
| [iriusrisk/bdd-security](https://github.com/iriusrisk/bdd-security) | 11 | 441 → 51 | 88.4% |
| [buildingSMART/ifc-gherkin-rules](https://github.com/buildingSMART/ifc-gherkin-rules) | 101 | 1,567 → 193 | 87.7% |
| [Sylius/Sylius](https://github.com/Sylius/Sylius) | 835 | 26,992 → 3,344 | 87.6% |
| [cchitsiang/bdd](https://github.com/cchitsiang/bdd) | 1,262 | 36,448 → 4,528 | 87.6% |
| [inventorypapa/free-dropshipping-automation-software](https://github.com/inventorypapa/free-dropshipping-automation-software) | 643 | 18,547 → 2,310 | 87.5% |
| [trydirect/sylius](https://github.com/trydirect/sylius) | 646 | 17,085 → 2,159 | 87.4% |
| [SU-SWS/linky_clicky](https://github.com/SU-SWS/linky_clicky) | 462 | 16,131 → 2,236 | 86.1% |
| [skgopinath/featuretagselector](https://github.com/skgopinath/featuretagselector) | 300 | 2,100 → 300 | 85.7% |
| [maurafitz/coop-workshift-app](https://github.com/maurafitz/coop-workshift-app) | 21 | 832 → 120 | 85.6% |
| [local-web-services/local-web-services](https://github.com/local-web-services/local-web-services) | 1,936 | 536,464 → 90,198 | 83.2% |
| [ashwanth1109/gherkin-feature-parser](https://github.com/ashwanth1109/gherkin-feature-parser) | 843 | 20,310 → 3,518 | 82.7% |
| [rpm-software-management/ci-dnf-stack](https://github.com/rpm-software-management/ci-dnf-stack) | 365 | 17,419 → 3,208 | 81.6% |
| [pherkin/test-bdd-cucumber-perl](https://github.com/pherkin/test-bdd-cucumber-perl) | 11 | 363 → 70 | 80.7% |
| [openshift/verification-tests](https://github.com/openshift/verification-tests) | 235 | 30,127 → 6,584 | 78.1% |
| [learningequality/kolibri](https://github.com/learningequality/kolibri) | 729 | 33,578 → 7,991 | 76.2% |
| [hmcts/ia-ccd-e2e-tests](https://github.com/hmcts/ia-ccd-e2e-tests) | 298 | 63,232 → 15,171 | 76.0% |
| [Rotbarsch/NatLaRestTest](https://github.com/Rotbarsch/NatLaRestTest) | 21 | 367 → 89 | 75.7% |
| [vanderbilt-redcap/redcap_rsvc](https://github.com/vanderbilt-redcap/redcap_rsvc) | 347 | 25,236 → 6,766 | 73.2% |
| [actiontech/dble-test-suite](https://github.com/actiontech/dble-test-suite) | 264 | 33,793 → 9,159 | 72.9% |
| [esg4aspl/SPL-ESG-Examples](https://github.com/esg4aspl/SPL-ESG-Examples) | 807 | 7,352 → 2,421 | 67.1% |
| [Sahamati/certification-framework](https://github.com/Sahamati/certification-framework) | 353 | 2,489 → 1,108 | 55.5% |
| [BRP-API/Haal-Centraal-BRP-bevragen](https://github.com/BRP-API/Haal-Centraal-BRP-bevragen) | 373 | 7,951 → 4,374 | 45.0% |
| [SoftEng-UniGE/BEWT-Specifications](https://github.com/SoftEng-UniGE/BEWT-Specifications) | 284 | 2,807 → 1,827 | 34.9% |
| [bcgov/onRouteBCSpecification](https://github.com/bcgov/onRouteBCSpecification) | 205 | 1,559 → 1,227 | 21.3% |
| [saqibrizvi11/SH2_contactMaps](https://github.com/saqibrizvi11/SH2_contactMaps) | 232 | 1,604 → 1,604 | 0.0% |
| **Total (38)** | **20,270** | **1,490,636 → 205,971** | **86.2%** |

---

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
