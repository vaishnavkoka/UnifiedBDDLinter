"""Browse a run's results.csv without opening it.

A full-corpus results.csv is ~718 MB, mostly violation text. No editor will open
that -- VS Code tries to tokenise the whole file and dies -- and neither will a
spreadsheet. This reads it as a stream, so memory stays flat regardless of size.

    # what is in the run?
    python3 misc/demo/inspect_run.py <run-dir> --summary

    # which repositories, and how did each do?
    python3 misc/demo/inspect_run.py <run-dir> --repos

    # one file's violations, formatted the way the tool prints them
    python3 misc/demo/inspect_run.py <run-dir> --file georeferencing

    # every file still failing a given rule after repair
    python3 misc/demo/inspect_run.py <run-dir> --rule Q003 --after
"""

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

csv.field_size_limit(2**31 - 1)

# Severity is not stored in the CSV -- the harness records "<rule> L<n>: <text>"
# to keep the file small. The catalogue knows the severity, so it is looked up
# here and the line is rendered exactly as `bddlint lint` would print it.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))
try:
    from unifiedbddlinter.catalogue import BY_ID
except ImportError:
    BY_ID = {}

ORANGE = "\033[38;5;166m"
GREEN = "\033[38;5;29m"
DIM = "\033[38;5;244m"
BOLD = "\033[1m"
RESET = "\033[0m"

LINE = re.compile(r"^([A-Z]{1,2}\d{3})\s+L(\d+):\s*(.*)$")


def rows(run_dir):
    path = Path(run_dir)
    if path.is_dir():
        path = path / "results.csv"
    if not path.is_file():
        sys.exit(f"no results.csv at {path}")
    with open(path, newline="", encoding="utf-8", errors="replace") as handle:
        for row in csv.DictReader(handle):
            yield row


def render(text, colour):
    """Print stored violations in the tool's own report format."""
    for raw in (text or "").splitlines():
        match = LINE.match(raw.strip())
        if not match:
            continue
        rule_id, line_no, message = match.groups()
        rule = BY_ID.get(rule_id)
        # catalogue severities are plain strings; a Violation's is an enum.
        # Accept either rather than silently defaulting everything to "info".
        severity = getattr(rule, "severity", None)
        severity = getattr(severity, "value", severity) or "info"
        name = getattr(rule, "name", "")
        # Messages often already begin with "Line N:"; saying it twice is noise.
        message = re.sub(rf"^Line\s+{line_no}\s*:?\s*", "", message)
        head = f"[{severity.upper():<7}] {rule_id}"
        print(f"  {colour}{head}{RESET}: {name}" if name
              else f"  {colour}{head}{RESET}")
        print(f"    Line {line_no}: {message}")


def cmd_summary(run_dir):
    n = 0
    totals = Counter()
    before, after = Counter(), Counter()
    for row in rows(run_dir):
        n += 1
        for key in ("Gherkin_Lint", "Cuke_Lint", "BDD_Lint"):
            for when, bucket in (("Before", before), ("After", after)):
                try:
                    bucket[key] += int(row[f"{key}_Errors_{when}_nums"] or 0)
                except (KeyError, TypeError, ValueError):
                    pass
        for raw in (row.get("BDD_Lint_Errors_Before") or "").splitlines():
            m = LINE.match(raw.strip())
            if m:
                totals[m.group(1)] += 1
    print(f"\n  {BOLD}{n:,} files{RESET}\n")
    print(f"  {'linter':20}{'before':>12}{'after':>12}{'change':>10}")
    for key, label in (("BDD_Lint", "UnifiedBDDLinter"),
                       ("Gherkin_Lint", "gherkin-lint"),
                       ("Cuke_Lint", "cuke_linter")):
        b, a = before[key], after[key]
        pct = f"{(a - b) / b * 100:8.2f}%" if b else "       -"
        print(f"  {label:20}{b:12,}{a:12,}{pct:>10}")
    print(f"\n  {'our rules, most frequent':30}")
    for rule_id, count in totals.most_common(10):
        name = getattr(BY_ID.get(rule_id), "name", "")
        print(f"    {rule_id:7}{count:10,}   {name}")


def cmd_repos(run_dir, limit):
    agg = defaultdict(lambda: [0, 0, 0])
    for row in rows(run_dir):
        entry = agg[row["Repository_Name"]]
        entry[0] += 1
        try:
            entry[1] += int(row["BDD_Lint_Errors_Before_nums"] or 0)
            entry[2] += int(row["BDD_Lint_Errors_After_nums"] or 0)
        except (TypeError, ValueError):
            pass
    print(f"\n  {'repository':46}{'files':>7}{'before':>10}{'after':>9}{'change':>9}")
    ordered = sorted(agg.items(), key=lambda kv: -(kv[1][1]))
    for name, (files, b, a) in ordered[:limit]:
        pct = f"{(a - b) / b * 100:7.1f}%" if b else "      -"
        print(f"  {name[:45]:46}{files:7}{b:10,}{a:9,}{pct:>9}")


def cmd_file(run_dir, needle):
    found = 0
    for row in rows(run_dir):
        if needle.lower() not in row["File_Path"].lower():
            continue
        found += 1
        if found > 3:
            print(f"\n  {DIM}… more matches; narrow the search{RESET}")
            return
        print(f"\n  {BOLD}{row['File_Path']}{RESET}")
        print(f"  {DIM}{row['Repository_Name']}{RESET}\n")
        print(f"  {ORANGE}{BOLD}BEFORE{RESET}  "
              f"({row.get('BDD_Lint_Errors_Before_nums', '?')} violations)")
        render(row.get("BDD_Lint_Errors_Before"), ORANGE)
        print(f"\n  {GREEN}{BOLD}AFTER{RESET}   "
              f"({row.get('BDD_Lint_Errors_After_nums', '?')} violations)")
        render(row.get("BDD_Lint_Errors_After"), GREEN)
    if not found:
        print(f"  no file matching {needle!r}")


def cmd_rule(run_dir, rule_id, after, limit):
    column = "BDD_Lint_Errors_After" if after else "BDD_Lint_Errors_Before"
    when = "after repair" if after else "before repair"
    rule = BY_ID.get(rule_id)
    print(f"\n  {BOLD}{rule_id}{RESET} "
          f"{getattr(rule, 'name', '')} — files still reporting it {when}\n")
    shown = total = 0
    for row in rows(run_dir):
        hits = [l for l in (row.get(column) or "").splitlines()
                if l.strip().startswith(rule_id + " ")]
        if not hits:
            continue
        total += 1
        if shown < limit:
            shown += 1
            print(f"  {row['File_Path']}  {DIM}({len(hits)}){RESET}")
    print(f"\n  {total:,} files" + (f", showing {shown}" if total > shown else ""))



def cmd_split(run_dir, outdir):
    """Write one CSV per repository, so each is small enough to open.

    A 718 MB results.csv defeats every editor. Split by repository it becomes
    38 files, most of them a few megabytes, which open normally -- and that is
    usually the unit someone wants to look at anyway.
    """
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    handles, writers, counts = {}, {}, Counter()
    header = None
    try:
        for row in rows(run_dir):
            if header is None:
                header = list(row)
            name = re.sub(r"[^A-Za-z0-9_.-]", "_", row["Repository_Name"])
            if name not in writers:
                handle = open(out / f"{name}.csv", "w", newline="", encoding="utf-8")
                handles[name] = handle
                writers[name] = csv.DictWriter(handle, fieldnames=header)
                writers[name].writeheader()
            writers[name].writerow(row)
            counts[name] += 1
    finally:
        for handle in handles.values():
            handle.close()

    print(f"\n  {len(counts)} files written to {out}\n")
    print(f"  {'repository':46}{'rows':>8}{'size':>10}")
    big = 0
    for name, n in sorted(counts.items(), key=lambda kv: -(out / f"{kv[0]}.csv").stat().st_size):
        size = (out / f"{name}.csv").stat().st_size
        mark = "  <-- still large" if size > 50 * 1024 * 1024 else ""
        big += size > 50 * 1024 * 1024
        print(f"  {name[:45]:46}{n:8,}{size/1024/1024:9.1f}M{mark}")
    if big:
        print(f"\n  {big} file(s) above 50 MB -- use --file / --rule for those.")



def cmd_rows(run_dir, limit, repo=None):
    """A plain table of counts, one line per file.

    Only the six numeric columns are shown. The other ten hold each linter's
    full violation text, which contains newlines and runs to kilobytes per cell
    -- that is what makes the CSV unreadable in a spreadsheet, and printing it
    here would reproduce the problem rather than solve it. Use --file to read
    the text for one file.
    """
    pairs = [("BDD_Lint_Errors_Before_nums", "BDD_Lint_Errors_After_nums", "ours"),
             ("Gherkin_Lint_Errors_Before_nums", "Gherkin_Lint_Errors_After_nums", "gherkin-lint"),
             ("Cuke_Lint_Errors_Before_nums", "Cuke_Lint_Errors_After_nums", "cuke_linter")]
    print(f"\n  {'file':46}{'ours':>13}{'gherkin-lint':>15}{'cuke_linter':>14}")
    print("  " + "-" * 86)
    shown = 0
    for row in rows(run_dir):
        if repo and repo.lower() not in row["Repository_Name"].lower():
            continue
        cells = []
        for before, after, _ in pairs:
            try:
                b, a = int(row[before] or 0), int(row[after] or 0)
            except (TypeError, ValueError):
                b = a = 0
            colour = GREEN if a < b else (DIM if a == b else ORANGE)
            cells.append(f"{colour}{b:>5}→{a:<5}{RESET}")
        name = row["File_Path"].split("/")[-1][:45]
        print(f"  {name:46}{cells[0]:>22}{cells[1]:>24}{cells[2]:>23}")
        shown += 1
        if shown >= limit:
            break
    print(f"\n  {shown} row(s). Use --file <substring> to read one file's violations.")



def cmd_full(run_dir, count, needle=None, width=100):
    """Whole rows, every column, laid out vertically.

    Sixteen columns of which ten hold multi-line text will never fit a grid, so
    each field gets its own block. Long lines are clipped to *width*; the intent
    is to read the record, not to reproduce the file.
    """
    shown = 0
    for row in rows(run_dir):
        if needle and needle.lower() not in row["File_Path"].lower():
            continue
        if not needle:
            # Without a search term, pick rows small enough to read whole.
            try:
                n = int(row["BDD_Lint_Errors_Before_nums"] or 0)
            except (TypeError, ValueError):
                n = 0
            if not 3 <= n <= 8:
                continue
        shown += 1
        pad = max(len(k) for k in row)
        print(f"\n  {BOLD}ROW {shown}{RESET}  {DIM}({len(row)} columns){RESET}")
        print("  " + "=" * 78)
        for key, value in row.items():
            value = value or ""
            colour = (ORANGE if key.endswith("Before") or key.endswith("Before_nums")
                      else GREEN if "After" in key else "")
            if "\n" in value:
                print(f"  {colour}{key:{pad}}{RESET} |")
                for line in value.splitlines():
                    print(f"  {'':{pad}} |   {line[:width]}")
            else:
                print(f"  {colour}{key:{pad}}{RESET} | {value[:width]}")
        if shown >= count:
            break
    if not shown:
        print(f"  nothing matched {needle!r}")



def cmd_flatten(run_dir, out_path):
    """Write a copy with one PHYSICAL line per file.

    The canonical results.csv is correct but its violation-text cells contain
    real newlines, so `wc -l` reports 4.4 million for 20,270 records and no
    line-oriented tool (grep, awk, head, most spreadsheets) can be trusted on
    it. This writes a derived copy in which those newlines are escaped as the
    two characters \\ and n.

    Reversible and lossless: nothing is dropped, and "\\n" -> newline restores
    the original text exactly. The numeric columns are untouched, so every
    published figure is unaffected -- this is a reading convenience, never the
    file of record.
    """
    out = Path(out_path)
    written = 0
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = None
        for row in rows(run_dir):
            if writer is None:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
            writer.writerow({k: (v or "").replace("\\", "\\\\")
                                        .replace("\r\n", "\\n")
                                        .replace("\n", "\\n")
                                        .replace("\r", "\\n")
                             for k, v in row.items()})
            written += 1
    print(f"\n  {written:,} records -> {out}")
    print(f"  size {out.stat().st_size/1024/1024:.0f} MB")
    with open(out, encoding="utf-8") as handle:
        physical = sum(1 for _ in handle)
    print(f"  physical lines now {physical:,} (header + {written:,} records)")
    print("\n  This is a derived convenience copy. results.csv stays the file of record.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", help="run directory, or a results.csv")
    parser.add_argument("--summary", action="store_true", help="totals and top rules")
    parser.add_argument("--repos", action="store_true", help="per-repository table")
    parser.add_argument("--file", metavar="SUBSTRING", help="one file's violations")
    parser.add_argument("--rule", metavar="ID", help="files reporting a rule")
    parser.add_argument("--after", action="store_true",
                        help="with --rule, look at the repaired files instead")
    parser.add_argument("--split", metavar="OUTDIR",
                        help="write one CSV per repository into OUTDIR, each "
                             "small enough for an editor to open")
    parser.add_argument("--rows", action="store_true",
                        help="table of counts, one line per file")
    parser.add_argument("--repo", metavar="SUBSTRING",
                        help="with --rows, restrict to one repository")
    parser.add_argument("--full", type=int, nargs="?", const=1, metavar="N",
                        help="print N whole rows, every column (default 1). "
                             "Combine with --file to choose which.")
    parser.add_argument("--flatten", metavar="OUT.csv",
                        help="write a copy with one physical line per file "
                             "(newlines in violation text escaped as \\n)")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()

    if args.flatten:
        cmd_flatten(args.run, args.flatten)
    elif args.full:
        cmd_full(args.run, args.full, args.file)
    elif args.rows:
        cmd_rows(args.run, args.limit, args.repo)
    elif args.split:
        cmd_split(args.run, args.split)
    elif args.file:
        cmd_file(args.run, args.file)
    elif args.rule:
        cmd_rule(args.run, args.rule, args.after, args.limit)
    elif args.repos:
        cmd_repos(args.run, args.limit)
    else:
        cmd_summary(args.run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
