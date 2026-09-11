#!/usr/bin/env python3
"""
table1_repositories.py -- per-repository results, as CSV.

    python3 evaluation/analysis/table1_repositories.py <results.csv> <outdir> [--linter NAME]

ORDERING AND TOTALS
-------------------
Rows are ordered by reduction, best first. The total line is
computed from the summed before/after counts, NOT by averaging the per-repository
percentages: a mean of percentages weights an 11-file repository the same as a
1,900-file one and would report a different corpus figure than the corpus has.
"""

import argparse
import csv
import statistics
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aggregate import load, per_repository


def build(results_csv: Path, outdir: Path, linter: str = "gherkin-lint"):
    rows = load(results_csv)
    repos = per_repository(rows)

    table = []
    for name, entry in repos.items():
        stats = entry[linter]
        table.append({
            "repository": name,
            "files": entry["files"],
            "before": stats["before"],
            "after": stats["after"],
            "reduction_pct": stats["reduction_pct"],
        })
    table.sort(key=lambda r: (-r["reduction_pct"], r["repository"]))

    total_files = sum(r["files"] for r in table)
    total_before = sum(r["before"] for r in table)
    total_after = sum(r["after"] for r in table)
    aggregate_pct = (100.0 * (total_before - total_after) / total_before
                     if total_before else 0.0)

    scored = sorted(r["reduction_pct"] for r in table if r["before"] > 0)
    # statistics.median, not scored[n//2]: with an even number of
    # repositories the latter returns the upper of the two middle values,
    # which is not the median and disagreed with fig3's np.median.
    median_pct = statistics.median(scored) if scored else 0.0

    outdir.mkdir(parents=True, exist_ok=True)

    csv_path = outdir / "table1_repositories.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["repository", "files", "before",
                                                    "after", "reduction_pct"])
        writer.writeheader()
        for row in table:
            writer.writerow({**row, "reduction_pct": f"{row['reduction_pct']:.1f}"})
        writer.writerow({"repository": f"TOTAL ({len(table)})", "files": total_files,
                         "before": total_before, "after": total_after,
                         "reduction_pct": f"{aggregate_pct:.1f}"})

    # GitHub owner/repo for the table, and for the links. Not derivable from the
    # directory name: the corpus flattens "owner/repo" to "owner-repo", and both
    # halves may contain hyphens, so there is no correct split point.
    mapping = {}
    map_file = Path(__file__).resolve().parent / "repositories.json"
    if map_file.is_file():
        mapping = {k: v for k, v in json.loads(map_file.read_text(encoding="utf-8")).items()
                   if not k.startswith("_")}
    missing = [r["repository"] for r in table if r["repository"] not in mapping]
    if missing:
        # Named, not silently dropped: a repository shown under its directory
        # name is a mapping that needs updating, and an unlabelled row in a
        # published table is worse than a noisy warning here.
        print(f"note: no GitHub path recorded for {len(missing)} repositories; "
              f"they appear under their directory names: {', '.join(missing[:4])}"
              + (" ..." if len(missing) > 4 else ""), file=sys.stderr)

    return csv_path, {
        "repositories": len(table), "files": total_files,
        "before": total_before, "after": total_after,
        "aggregate_pct": aggregate_pct, "median_pct": median_pct,
        "above_80": sum(1 for r in table if r["reduction_pct"] >= 80),
        "regressing": sum(1 for r in table if r["reduction_pct"] < 0),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results"); parser.add_argument("outdir")
    parser.add_argument("--linter", default="gherkin-lint")
    args = parser.parse_args()
    csv_path, summary = build(Path(args.results), Path(args.outdir), args.linter)
    print(csv_path)
    print(f"  {summary['repositories']} repos, {summary['files']:,} files, "
          f"{summary['before']:,} -> {summary['after']:,} "
          f"({summary['aggregate_pct']:.1f}% aggregate, "
          f"{summary['median_pct']:.1f}% median, "
          f"{summary['regressing']} regressing)")
