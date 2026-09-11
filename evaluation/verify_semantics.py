#!/usr/bin/env python3
"""
verify_semantics.py -- did the repair change what Cucumber sees?

    python3 evaluation/verify_semantics.py <run-dir> [--limit N]

WHAT THIS ANSWERS, AND WHY IT IS NOT OPTIONAL
---------------------------------------------
A linter reporting fewer violations after a repair proves only that the linter
is happier. It says nothing about whether the specification still MEANS what it
meant, or whether the test suite still binds to its step definitions.

Those are the claims that matter, and they can only be settled by an
authority outside our own rule engine. So this compares, for every repaired
file, the model produced by the OFFICIAL Cucumber Gherkin parser (via
cuke_modeler) before and after:

    feature name · tags · background steps · scenario names · STEP TEXT

Step text is the critical field. Cucumber matches a step definition against the
step's text; change one character of it and a previously bound step can become
UNDEFINED. Everything a form-preserving repair is permitted to touch --
indentation, blank lines, trailing whitespace, the filename -- is invisible to
this comparison, by design.

WHAT COUNTS AS A FAILURE
------------------------
    step-text change      the repair altered a string Cucumber matches on.
                          A binding can break. This is the serious one.
    structure change      a scenario or feature name moved, or a step
                          appeared/disappeared.
    parse regression      the file parsed before and does not parse after.
                          The worst possible outcome.

An empirically confirmed example: the rule that strips a trailing full stop
from a step changes step text, and a project whose step definition includes
that full stop stops binding. Demonstrated with a real `mvn test` run under
`misc/binding-experiment/`.

REQUIREMENTS
------------
Ruby with the `cuke_modeler` gem -- the same dependency the evaluation already
needs. Without it this script cannot run, and says so rather than guessing.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACT_ROOT = HERE.parent
DUMPER = HERE / "gherkin_model_dump.rb"

# tqdm is optional, and its absence must never stop a verification run.
# disable=None draws the bar only on a real terminal, so a redirected run
# leaves a readable log instead of thousands of carriage-returned fragments.
try:
    from tqdm import tqdm as _tqdm
except ImportError:                                   # pragma: no cover
    def _tqdm(iterable=None, **_kwargs):
        return iterable if iterable is not None else []

# Ruby startup dominates for small batches, so files are dumped in chunks.
# Too large a chunk risks the OS argument-length limit on some platforms.
CHUNK = 200


def dump_models(paths, label="parsing"):
    """Parse *paths* with the official Gherkin parser; return {path: model}.

    Ruby startup dominates for small batches, so files are parsed in chunks and
    the progress bar counts files rather than chunks -- a bar that jumps 200 at
    a time tells the user less than one that moves steadily.
    """
    models = {}
    chunks = range(0, len(paths), CHUNK)
    bar = _tqdm(chunks, total=len(chunks), unit="chunk", desc=label,
                dynamic_ncols=True, disable=None)
    for start in bar:
        batch = [str(p) for p in paths[start:start + CHUNK]]
        try:
            completed = subprocess.run(["ruby", str(DUMPER), *batch],
                                       capture_output=True, text=True, timeout=600)
        except FileNotFoundError:
            sys.exit("ruby not found. This check needs Ruby and the cuke_modeler "
                     "gem:\n    gem install cuke_modeler")
        for line in completed.stdout.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            models[record["path"]] = record
    return models


def compare(before: dict, after: dict):
    """Return a list of differences that would matter to Cucumber."""
    problems = []
    if "error" in after and "error" not in before:
        problems.append(("parse-regression", after["error"]))
        return problems
    if "error" in before or "error" in after:
        return problems          # unparseable both sides: nothing to compare

    if before.get("feature") != after.get("feature"):
        problems.append(("feature-name",
                         f"{before.get('feature')!r} -> {after.get('feature')!r}"))
    if before.get("background") != after.get("background"):
        problems.append(("background-steps", "background step text changed"))

    tb, ta = before.get("tests", []), after.get("tests", [])
    if len(tb) != len(ta):
        problems.append(("scenario-count", f"{len(tb)} -> {len(ta)}"))
        return problems

    for index, (one, two) in enumerate(zip(tb, ta)):
        if one["name"] != two["name"]:
            problems.append(("scenario-name", f"{one['name']!r} -> {two['name']!r}"))
        if one["steps"] != two["steps"]:
            # Report the first differing step: a whole-list dump is unreadable
            # and the first divergence is what a reader needs to see.
            for sb, sa in zip(one["steps"], two["steps"]):
                if sb != sa:
                    problems.append(("STEP-TEXT", f"{sb!r} -> {sa!r}"))
                    break
            else:
                problems.append(("step-count",
                                 f"{len(one['steps'])} -> {len(two['steps'])}"))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", help="a run directory containing fixed/")
    parser.add_argument("--corpus", help="the input tree the run read "
                                         "(default: read from the run's versions.txt)")
    parser.add_argument("--limit", type=int, help="check only the first N files")
    parser.add_argument("--outdir", help="write semantic_verification.md here "
                                         "(default: alongside the run)")
    args = parser.parse_args()

    if not shutil.which("ruby"):
        sys.exit("ruby not found. Install Ruby and: gem install cuke_modeler")

    run_dir = Path(args.run).resolve()
    fixed_root = run_dir / "fixed"
    if not fixed_root.is_dir():
        sys.exit(f"no fixed/ directory in {run_dir}")

    corpus = Path(args.corpus).resolve() if args.corpus else None
    if corpus is None:
        provenance = run_dir / "versions.txt"
        for line in provenance.read_text(encoding="utf-8").splitlines():
            if line.startswith("corpus"):
                corpus = Path(line.split("=", 1)[1].strip())
                break
    if corpus is None or not corpus.is_dir():
        sys.exit("could not determine the input corpus; pass --corpus")

    # Pair each repaired file with its original.
    #
    # This is the part that is easy to get wrong and hard to notice: the fixer
    # RENAMES files, so the two trees share neither names nor sorted order, and
    # a positional pairing silently compares unrelated features. It produces a
    # full set of pairs and a page of confident nonsense.
    #
    # `per_rule_repair.pair_for` replays the fixer's own naming, including the
    # collision suffix that identifies an original exactly. Reused here rather
    # than reimplemented, so the two analyses cannot disagree about which file
    # came from which.
    sys.path.insert(0, str(HERE / "analysis"))
    from per_rule_repair import pair_for

    pairs = []
    for fixed in sorted(fixed_root.rglob("*.feature")):
        if not fixed.is_file():
            continue
        original = pair_for(fixed, fixed_root, corpus)
        if original is not None:
            pairs.append((original, fixed))
    if args.limit:
        pairs = pairs[:args.limit]
    if not pairs:
        sys.exit("no before/after pairs found")

    print(f"comparing {len(pairs):,} repaired files against their originals")
    print("using the official Cucumber Gherkin parser (cuke_modeler)\n")

    before = dump_models([p[0] for p in pairs], "parsing originals")
    after = dump_models([p[1] for p in pairs], "parsing repaired ")

    counts = {}
    examples = {}
    clean = 0
    # A file the parser rejects on BOTH sides yields no differences, because
    # there is nothing to compare. Counting it as "identical" overstates the
    # verification: nothing was actually verified about it. Track it separately.
    unparseable = 0
    for original, fixed in _tqdm(pairs, unit="file", desc="comparing       ",
                                 dynamic_ncols=True, disable=None):
        model_before = before.get(str(original), {})
        model_after = after.get(str(fixed), {})
        if "error" in model_before and "error" in model_after:
            unparseable += 1
            continue
        problems = compare(model_before, model_after)
        if not problems:
            clean += 1
            continue
        for kind, detail in problems:
            counts[kind] = counts.get(kind, 0) + 1
            # Every instance is kept: the CSV is the record, and truncating it
            # would hide exactly the cases someone needs to look at.
            examples.setdefault(kind, []).append((original.name, detail))

    # ---- emit DATA ---------------------------------------------------------
    # One row per file that differs, plus a counts file. No prose: whether a
    # difference matters is a judgement about the file, and this script cannot
    # make it.
    outdir = Path(args.outdir) if args.outdir else run_dir
    outdir.mkdir(parents=True, exist_ok=True)

    def portable(text):
        """Strip this machine's paths out of a parser message.

        Parser errors quote the absolute path of the file they failed on. That
        is useful while debugging and wrong in a shipped artifact: it names a
        directory that exists on one computer, and it makes two runs of the same
        check diff against each other for no reason.
        """
        return str(text).replace(str(ARTIFACT_ROOT) + "/", "")

    differences_csv = outdir / "semantic_differences.csv"
    with open(differences_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file", "difference_kind", "detail"])
        for kind in sorted(counts, key=lambda k: -counts[k]):
            for name, detail in examples[kind]:
                writer.writerow([name, kind, portable(detail)])

    summary_csv = outdir / "semantic_verification.csv"
    with open(summary_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        writer.writerow(["files_compared", len(pairs)])
        writer.writerow(["files_verified", len(pairs) - unparseable])
        writer.writerow(["semantically_identical", clean])
        writer.writerow(["identical_pct",
                         f"{100*clean/max(1, len(pairs)-unparseable):.2f}"])
        writer.writerow(["unparseable_both_sides", unparseable])
        for kind in sorted(counts, key=lambda k: -counts[k]):
            writer.writerow([f"differing_{kind}", counts[kind]])

    # ---- print (terminal only) ---------------------------------------------
    print(f"files compared          {len(pairs):>9,}")
    print(f"unparseable both sides  {unparseable:>9,}   (not verifiable either way)")
    print(f"files verified          {len(pairs)-unparseable:>9,}")
    print(f"semantically identical  {clean:>9,}   "
          f"({100*clean/max(1, len(pairs)-unparseable):.2f}%)")
    if counts:
        print("\ndifferences the Gherkin parser reports:")
        for kind in sorted(counts, key=lambda k: -counts[k]):
            print(f"  {kind:<20}{counts[kind]:>8,}")
            for name, detail in examples[kind][:3]:
                print(f"      {name}: {portable(detail)[:96]}")
    print(f"\nwritten: {summary_csv}")
    print(f"         {differences_csv}")

    return 1 if counts else 0


if __name__ == "__main__":
    sys.exit(main())
