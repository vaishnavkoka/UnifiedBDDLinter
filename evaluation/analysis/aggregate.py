"""
aggregate.py -- load a results.csv and compute the numbers everything else uses.

ONE PLACE WHERE THE ARITHMETIC LIVES
------------------------------------
Every figure and every table is a view of the same result rows. If
each script did its own summing, two of them would eventually disagree, and the
disagreement would surface as a reviewer noticing that a figure and a table
report different totals for the same corpus. So the arithmetic happens exactly
once, here.

THE RULE THAT MATTERS: -1 IS NOT ZERO
-------------------------------------
A count of -1 means the linter could not measure that file -- it crashed, timed
out, or was not installed. Zero means it measured the file and found it clean.
Treating the first as the second silently inflates every reduction percentage,
because unmeasured files look like perfectly clean ones.

So -1 rows are EXCLUDED from that linter's totals, and the exclusion count is
reported alongside the result. A percentage whose denominator is unstated is not
a measurement.
"""

from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

# Per-linter column pairs: (label, before column, after column).
LINTERS = [
    ("gherkin-lint", "Gherkin_Lint_Errors_Before_nums", "Gherkin_Lint_Errors_After_nums"),
    ("cuke_linter", "Cuke_Lint_Errors_Before_nums", "Cuke_Lint_Errors_After_nums"),
    ("UnifiedBDDLinter", "BDD_Lint_Errors_Before_nums", "BDD_Lint_Errors_After_nums"),
]

# csv's default field cap is 128 KB. Violation-text cells hold a linter's full
# output for one file and routinely exceed it on large features; without this
# the loader dies with "field larger than field limit" partway through a corpus.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def _int(value):
    """Parse a count cell. Blank or unparseable means 'not measured'."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load(results_csv: Path):
    """Read result rows, keeping only the numeric fields the analysis needs.

    Violation TEXT columns are discarded here: they are the bulk of the file's
    bytes and nothing downstream aggregates them, so carrying them would mean
    holding hundreds of megabytes to compute a few dozen sums.
    """
    rows = []
    with open(results_csv, newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            row = {"repository": raw.get("Repository_Name", "?"),
                   "path": raw.get("File_Path", "")}
            for label, before, after in LINTERS:
                row[label] = (_int(raw.get(before)), _int(raw.get(after)))
            rows.append(row)
    if not rows:
        sys.exit(f"{results_csv} contains no rows")
    return rows


def corpus_totals(rows):
    """Totals per linter, with the unmeasured files counted separately."""
    out = {}
    for label, _b, _a in LINTERS:
        before = after = 0
        measured = excluded = 0
        for row in rows:
            b, a = row[label]
            # Both ends must be measured, or the pair says nothing about change.
            if b is None or a is None or b < 0 or a < 0:
                excluded += 1
                continue
            before += b
            after += a
            measured += 1
        out[label] = {
            "before": before,
            "after": after,
            "measured": measured,
            "excluded": excluded,
            "reduction_pct": (100.0 * (before - after) / before) if before else 0.0,
        }
    return out


def per_repository(rows):
    """Per-repository aggregates, for Table 1 and the size/efficacy figure."""
    repos = {}
    for row in rows:
        entry = repos.setdefault(row["repository"], {
            "files": 0,
            **{label: {"before": 0, "after": 0, "measured": 0} for label, _b, _a in LINTERS},
        })
        entry["files"] += 1
        for label, _b, _a in LINTERS:
            b, a = row[label]
            if b is None or a is None or b < 0 or a < 0:
                continue
            entry[label]["before"] += b
            entry[label]["after"] += a
            entry[label]["measured"] += 1

    for entry in repos.values():
        for label, _b, _a in LINTERS:
            stats = entry[label]
            stats["reduction_pct"] = (
                100.0 * (stats["before"] - stats["after"]) / stats["before"]
                if stats["before"] else 0.0)
    return repos


def file_outcomes(rows, linter="UnifiedBDDLinter"):
    """How individual files fared: improved, unchanged, regressed, clean.

    Reported per file rather than only in aggregate: "99% of files improved" and
    "87% of violations resolved" are different statements and can diverge widely.
    A regression -- any file where violations went UP -- is the single most
    important thing to surface, so it is counted explicitly rather than being
    hidden inside an average.
    """
    improved = unchanged = regressed = already_clean = excluded = 0
    for row in rows:
        b, a = row[linter]
        if b is None or a is None or b < 0 or a < 0:
            excluded += 1
        elif b == 0:
            already_clean += 1
        elif a < b:
            improved += 1
        elif a > b:
            regressed += 1
        else:
            unchanged += 1
    total = improved + unchanged + regressed + already_clean
    return {
        "improved": improved,
        "unchanged": unchanged,
        "regressed": regressed,
        "already_clean": already_clean,
        "excluded": excluded,
        "total_measured": total,
        "improved_pct": 100.0 * improved / total if total else 0.0,
    }


def describe(results_csv: Path) -> str:
    """Human-readable summary, printed by make_all.py and useful on its own."""
    rows = load(results_csv)
    totals = corpus_totals(rows)
    repos = per_repository(rows)
    outcomes = file_outcomes(rows)

    lines = [f"results:      {results_csv}",
             f"files:        {len(rows):,}",
             f"repositories: {len(repos)}", ""]
    lines.append(f"{'linter':<20}{'before':>14}{'after':>14}{'reduction':>12}{'unmeasured':>12}")
    for label, _b, _a in LINTERS:
        stats = totals[label]
        lines.append(f"{label:<20}{stats['before']:>14,}{stats['after']:>14,}"
                     f"{stats['reduction_pct']:>11.1f}%{stats['excluded']:>12,}")

    reductions = sorted(r["gherkin-lint"]["reduction_pct"] for r in repos.values()
                        if r["gherkin-lint"]["before"] > 0)
    if reductions:
        median = statistics.median(reductions)
        lines += ["", f"per-repository gherkin-lint reduction: median {median:.1f}%",
                  f"  above 80%: {sum(1 for r in reductions if r >= 80)} of {len(reductions)}",
                  f"  REGRESSING: {sum(1 for r in reductions if r < 0)}"]

    lines += ["", "file outcomes (UnifiedBDDLinter):",
              f"  improved      {outcomes['improved']:,} ({outcomes['improved_pct']:.1f}%)",
              f"  unchanged     {outcomes['unchanged']:,}",
              f"  REGRESSED     {outcomes['regressed']:,}",
              f"  already clean {outcomes['already_clean']:,}",
              f"  unmeasured    {outcomes['excluded']:,}"]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 analysis/aggregate.py <results.csv>")
    print(describe(Path(sys.argv[1])))
