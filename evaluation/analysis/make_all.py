#!/usr/bin/env python3
"""
make_all.py -- produce every figure and table from one run's results.

    python3 analysis/make_all.py <run-dir-or-results.csv> [--outdir DIR]

By default the outputs land in `artifacts/figures/` and `artifacts/tables/`.

ORDER AND INDEPENDENCE
----------------------
Every generator reads the same `results.csv` and none reads another's output, so
they can be run in any order or individually while iterating on one figure. This
script exists to run them together and, more importantly, to write SUMMARY.md --
the single place where the run's headline numbers are recorded in prose.

That summary matters more than it looks. Numbers that live only inside a figure
get transcribed by hand, and hand-transcribed numbers drift. Written once,
mechanically, from the same data the figures used, they cannot disagree.
"""

import argparse
import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACT_ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import aggregate                       # noqa: E402
import fig2_before_after               # noqa: E402
import fig3_efficacy_vs_size           # noqa: E402
import table1_repositories             # noqa: E402


def resolve_results(target: Path) -> Path:
    """Accept either a run directory or the results.csv inside one."""
    if target.is_dir():
        candidate = target / "results.csv"
        if not candidate.is_file():
            sys.exit(f"no results.csv in {target}")
        return candidate
    if not target.is_file():
        sys.exit(f"not found: {target}")
    return target


def _relative(path: Path) -> str:
    """Render *path* relative to the artifact root when it lies inside it.

    A generated document is read on machines other than the one that produced
    it, so an absolute path from the author's laptop is noise at best and a
    broken instruction at worst. Paths outside the artifact (a scratch run under
    /tmp, say) are left absolute, because a relative path to somewhere outside
    the tree would be actively misleading.
    """
    try:
        return str(path.resolve().relative_to(ARTIFACT_ROOT))
    except ValueError:
        return str(path)


def pick_driving_linter(results: Path, preferred: str) -> str:
    """Return *preferred* if it measured anything, else the first that did.

    "Measured anything" means a non-zero total before fixing. A linter that was
    skipped records -1 per file, which `corpus_totals` excludes, leaving a
    before-total of zero -- indistinguishable from a linter that ran and found
    a perfectly clean corpus, which is not a thing that happens here.
    """
    totals = aggregate.corpus_totals(aggregate.load(results))
    if totals.get(preferred, {}).get("before", 0) > 0:
        return preferred
    for label, _b, _a in aggregate.LINTERS:
        if totals[label]["before"] > 0:
            return label
    sys.exit("no linter recorded any violations in this run; nothing to plot")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", help="run directory, or a results.csv")
    parser.add_argument("--outdir", help="base output directory "
                                         "(default: artifacts/)")
    parser.add_argument("--linter", default="gherkin-lint",
                        help="which linter drives the per-repository figure and "
                             "table (default: gherkin-lint)")
    args = parser.parse_args()

    results = resolve_results(Path(args.run))
    base = Path(args.outdir) if args.outdir else ARTIFACT_ROOT / "artifacts"
    figures = base / "figures"
    tables = base / "tables"

    print(aggregate.describe(results))
    print()

    # The per-repository figure and table are keyed to one linter. If that
    # linter was not measured -- a run with --no-oracles records -1 for both
    # external tools -- fall back to one that was, rather than failing.
    # Refusing to plot would be defensible; refusing to plot when a perfectly
    # good measurement is sitting in the same file is just unhelpful.
    driver = pick_driving_linter(results, args.linter)
    if driver != args.linter:
        print(f"note: '{args.linter}' has no measured violations in this run "
              f"(--no-oracles?); using '{driver}' for the per-repository "
              f"figure and table.\n")

    produced = [fig2_before_after.draw(results, figures),
                fig3_efficacy_vs_size.draw(results, figures, driver)]
    csv_path, tex_path, summary = table1_repositories.build(results, tables, driver)
    produced += [csv_path, tex_path]

    # Carry the run's provenance next to its outputs. A figure separated from
    # the record of what produced it is decoration, not evidence.
    run_dir = results.parent
    provenance = run_dir / "versions.txt"
    if provenance.is_file():
        shutil.copy2(provenance, base / "versions.txt")
        produced.append(base / "versions.txt")

    rows = aggregate.load(results)
    totals = aggregate.corpus_totals(rows)
    outcomes = aggregate.file_outcomes(rows)

    # ---- emit DATA ---------------------------------------------------------
    # A CSV, not a written-up summary. The figures and tables are the outputs;
    # the interpretation belongs to whoever reads them.
    summary_csv = base / "summary.csv"
    with open(summary_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        writer.writerow(["results", _relative(results)])
        writer.writerow(["generated", datetime.now().isoformat(timespec="seconds")])
        writer.writerow(["files", len(rows)])
        writer.writerow(["repositories", summary["repositories"]])
        writer.writerow(["driving_linter", driver])
        for label, _b, _a in aggregate.LINTERS:
            stats = totals[label]
            writer.writerow([f"{label}_before", stats["before"]])
            writer.writerow([f"{label}_after", stats["after"]])
            writer.writerow([f"{label}_resolved_pct", f"{stats['reduction_pct']:.2f}"])
            writer.writerow([f"{label}_unmeasured", stats["excluded"]])
        writer.writerow(["aggregate_reduction_pct", f"{summary['aggregate_pct']:.2f}"])
        writer.writerow(["median_repo_reduction_pct", f"{summary['median_pct']:.2f}"])
        writer.writerow(["repos_above_80", summary["above_80"]])
        writer.writerow(["repos_regressing", summary["regressing"]])
        writer.writerow(["files_improved", outcomes["improved"]])
        writer.writerow(["files_improved_pct", f"{outcomes['improved_pct']:.2f}"])
        writer.writerow(["files_unchanged", outcomes["unchanged"]])
        writer.writerow(["files_regressed", outcomes["regressed"]])
        writer.writerow(["files_already_clean", outcomes["already_clean"]])
        writer.writerow(["files_unmeasured", outcomes["excluded"]])
    produced.append(summary_csv)

    print("written:")
    for path in produced:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
