# How the corpus was built

Three independent searches, narrowed in three stages, then cloned. The CSVs at
each stage are the actual files that ran, and the code beside them is what
produced the next stage.

```
1-search/     what the three searches returned, and what deduplicating them left
2-metadata/   enriched from the GitHub API, then filtered against the criteria
3-clone/      feature counts, and the cloning itself
```

## The funnel

| Source | searched | deduplicated | enriched | refined | counted | cloned |
|---|--:|--:|--:|--:|--:|--:|
| `github-global-search` | 40 | 34 | 34 | 15 | 15 | 15 |
| `github-search-tool` | 59 | 51 | 51 | 51 | 51 | 24 |
| `seart-tool` | 44 | 33 | 33 | 33 | 33 | 3 |
| **total** | **143** | **118** | **118** | **99** | **99** | **42** |

Only `github-global-search` loses candidates at the refinement step, because it
was the only source whose results had not already been filtered by the search
tool itself.

## From 42 clone directories to 38 repositories

```
42  clone directories created
-3  three repositories were found by two sources each, so were cloned twice
----
39  distinct repositories
-1  one contained no .feature files despite its metadata declaring them
----
38  repositories, 20,270 .feature files
```

The three cloned twice are `cchitsiang/bdd`,
`local-web-services/local-web-services` and
`reqnroll/Reqnroll.ExploratoryTestProjects`. The one with no feature files is
`BVCOG-Contract-Management/BVGOG-Contract-Manager`; the count gate ran on
metadata before cloning, and for that repository the metadata was wrong.

The `source` column in `../MANIFEST.csv` records which search found a
repository. It describes the search, not the project, which is why three
repositories appear under two sources.

## Stages

**`1-search/`** — `<source>.csv` is what each search returned.
`<source>-deduped.csv` is the same list after `standardise_links.py` normalised
the URLs and removed repeats.

**`2-metadata/`** — `<source>.csv` carries the GitHub API fields added by
`extract_metadata.py`. `<source>-refined.csv` is what survived
`apply_criteria.py`, which applies the activity gate.

**`3-clone/`** — `<source>.csv` adds a feature-file count from
`count_features.py`, and is the input `clone_repositories.py` read.
`cloner-checkpoint.json` records those three paths, so which files were used is
a matter of record rather than recollection.

## Reproducing

These scripts call the GitHub API and will need a token in the environment.
Rate limits and repository contents have both moved on since the run, so a
repeat will not reproduce the same 38 repositories. The corpus as measured is
archived alongside this directory for that reason.
