#!/usr/bin/env python3
"""
per_rule_repair.py -- what each rule's repair actually did, rule by rule.

    python3 analysis/per_rule_repair.py <run-dir> [--outdir DIR]

WHY THIS EXISTS
---------------
A corpus-level reduction figure hides the thing a reader most needs to know:
*what kind of edit* produced it. "85% of violations resolved" reads the same
whether the tool re-indented some lines or rewrote the text of every step, and
those have wildly different consequences for a test suite.

So this reports two things per rule:

  1. violations before and after, and how many the repair resolved
  2. the CLASS of edit the repair performs, verified against the actual
     before/after file pairs rather than asserted from the rule's description

The edit classes, in increasing order of risk:

    whitespace   indentation, blank lines, trailing spaces, a final newline.
                 Invisible to the Gherkin parser. Cannot affect behaviour.
    filename     the file is renamed; not one byte of content changes.
                 Safe with Cucumber, which discovers features by directory glob
                 and binds by step text -- verified with a real `mvn test` in
                 misc/binding-experiment/.
    content      a byte inside the file changed that is not whitespace.
                 THIS IS THE CLASS THAT CAN BREAK A TEST SUITE, because step
                 text is what a step definition is matched against.

The tool is designed so that `content` is empty. This script is how that claim
is checked rather than repeated: it compares every repaired file against its
original and counts what actually moved.
"""

from __future__ import annotations

import argparse
import collections
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifiedbddlinter import catalogue

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# "S004 L12: Expected indent 2, got 8"  -- the shape the harness records.
VIOLATION = re.compile(r"^([A-Z]+[0-9]+)\s+L(\d+):")


def rule_counts(cell: str) -> collections.Counter:
    counts = collections.Counter()
    for line in (cell or "").splitlines():
        match = VIOLATION.match(line.strip())
        if match:
            counts[match.group(1)] += 1
    return counts


def classify_edit(original: Path, fixed: Path):
    """Return the set of edit classes between two files.

    Whitespace is compared by removing it entirely: if the two files are equal
    once all whitespace is stripped, then every difference was whitespace, no
    matter how it was arranged.
    """
    classes = set()
    if original.name != fixed.name:
        classes.add("filename")
    try:
        before = original.read_text(encoding="utf-8", errors="replace")
        after = fixed.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return classes

    if before == after:
        return classes

    # A byte-order mark is content by this measure but carries no meaning, so
    # it is normalised away before the comparison rather than reported as a
    # content change -- removing it is what makes the file parseable at all.
    before_n = before.lstrip("﻿")
    after_n = after.lstrip("﻿")
    if before_n != before or after_n != after:
        classes.add("byte-order-mark")

    squashed_before = re.sub(r"\s+", "", before_n)
    squashed_after = re.sub(r"\s+", "", after_n)
    if squashed_before == squashed_after:
        if before_n != after_n:
            classes.add("whitespace")
        return classes

    # Something other than whitespace moved. Removing a REPEATED tag is the one
    # such edit the tool makes, and it is safe for a specific reason: Cucumber
    # selects scenarios by tag SET membership, so dropping a second copy of a
    # tag already present cannot change which scenarios run. Distinguished from
    # a real content change rather than lumped in with it, because the two have
    # completely different consequences.
    TAG = r"@[\w:.=()#/,+*-]+"
    tags_before = re.findall(TAG, before_n)
    tags_after = re.findall(TAG, after_n)
    body_before = re.sub(TAG, "", squashed_before)
    body_after = re.sub(TAG, "", squashed_after)
    if (body_before == body_after
            and set(tags_before) == set(tags_after)
            and len(tags_after) <= len(tags_before)):
        classes.add("duplicate-tag")
    else:
        classes.add("content")
    return classes


_MAPPING_CACHE = {}


def directory_mapping(corpus_dir: Path, fixed_dir: Path):
    """Map each repaired file in *fixed_dir* back to its original.

    Three approaches were tried before this one, and the first two produced
    plausible nonsense rather than an error:

      1. sorted position -- the fixer renames files, so the directories do not
         line up. Every pair existed; every pair was a different feature.
      2. recomputing the expected name -- correct until two originals slugify
         to the same name, which happens whenever several files share a Feature
         line. One directory here has SIX originals all naming themselves
         `injuries.feature`.
      3. replaying the fixer's collision algorithm in sorted order -- still
         wrong, because the fixer runs in PARALLEL. Which of the six wins the
         unsuffixed name is a race between workers, not a function of order.

    What is reliable is the collision suffix itself. The fixer writes the loser
    of a collision as `{slug}--{original stem}.feature`, and that stem names its
    original exactly, whoever won the race. So suffixed files are matched
    directly, and the single unsuffixed file is whichever original is left over.
    """
    key = (str(corpus_dir), str(fixed_dir))
    if key in _MAPPING_CACHE:
        return _MAPPING_CACHE[key]

    originals = {f.stem: f for f in sorted(corpus_dir.glob("*.feature")) if f.is_file()}
    mapping, claimed = {}, set()

    plain = []
    for fixed in sorted(fixed_dir.glob("*.feature")):
        if not fixed.is_file():
            continue
        stem = fixed.stem
        if "--" in stem:
            candidate = stem.split("--", 1)[1]
            if candidate in originals:
                mapping[fixed.name] = originals[candidate]
                claimed.add(candidate)
                continue
        plain.append(fixed)

    # Whatever original no suffixed file claimed is the one that won the race.
    leftover = [name for name in originals if name not in claimed]
    if len(plain) == 1 and len(leftover) == 1:
        mapping[plain[0].name] = originals[leftover[0]]
    elif len(plain) == len(leftover):
        # No collisions in this directory: recompute names to pair them.
        from unifiedbddlinter.fixer import AutoFixer
        for fixed in plain:
            for name in leftover:
                original = originals[name]
                content = original.read_text(encoding="utf-8", errors="replace")
                if AutoFixer.expected_output_name(content, original.stem) == fixed.name:
                    mapping[fixed.name] = original
                    break

    _MAPPING_CACHE[key] = mapping
    return mapping


def pair_for(fixed: Path, fixed_root: Path, corpus: Path):
    """The original a repaired file came from, or None."""
    relative = fixed.parent.relative_to(fixed_root)
    directory = corpus / relative
    if not directory.is_dir():
        return None
    return directory_mapping(directory, fixed.parent).get(fixed.name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run")
    parser.add_argument("--outdir")
    parser.add_argument("--corpus", help="input tree (default: from versions.txt)")
    args = parser.parse_args()

    run_dir = Path(args.run).resolve()
    results = run_dir / "results.csv"
    if not results.is_file():
        sys.exit(f"no results.csv in {run_dir}")

    # ---- 1. per-rule violation deltas, from the results CSV --------------
    before_total, after_total = collections.Counter(), collections.Counter()
    files_with = collections.Counter()
    rows = 0
    with open(results, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            b = rule_counts(row.get("BDD_Lint_Errors_Before"))
            a = rule_counts(row.get("BDD_Lint_Errors_After"))
            before_total.update(b)
            after_total.update(a)
            for rule in b:
                files_with[rule] += 1

    # ---- 2. edit classes, from the actual file pairs ---------------------
    corpus = Path(args.corpus).resolve() if args.corpus else None
    if corpus is None:
        for line in (run_dir / "versions.txt").read_text(encoding="utf-8").splitlines():
            if line.startswith("corpus"):
                corpus = Path(line.split("=", 1)[1].strip())
                break

    edit_classes = collections.Counter()
    pairs_checked = 0
    unpaired = 0
    fixed_root = run_dir / "fixed"
    if corpus and corpus.is_dir() and fixed_root.is_dir():
        for fixed in fixed_root.rglob("*.feature"):
            if not fixed.is_file():
                continue
            original = pair_for(fixed, fixed_root, corpus)
            if original is None:
                unpaired += 1
                continue
            found = classify_edit(original, fixed)
            pairs_checked += 1
            if not found:
                edit_classes["unchanged"] += 1
            for kind in found:
                edit_classes[kind] += 1

    # ---- emit DATA ---------------------------------------------------------
    # Two CSVs, no prose. What the numbers mean is the reader's call, not this
    # script's: a measurement tool that also writes the conclusion has decided
    # the answer before anyone has looked at it.
    known = {r.rule_id: r for r in catalogue.RULES}
    outdir = Path(args.outdir) if args.outdir else run_dir
    outdir.mkdir(parents=True, exist_ok=True)

    rules_csv = outdir / "per_rule_violations.csv"
    with open(rules_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rule", "fixability", "before", "after", "resolved",
                         "resolved_pct", "files_with_rule"])
        for rule_id in sorted(before_total, key=lambda r: -before_total[r]):
            b, a = before_total[rule_id], after_total[rule_id]
            rule = known.get(rule_id)
            writer.writerow([rule_id,
                             rule.fixability if rule else "UNKNOWN",
                             b, a, b - a,
                             f"{(100*(b-a)/b if b else 0):.2f}",
                             files_with[rule_id]])

    edits_csv = outdir / "edit_classes.csv"
    with open(edits_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["edit_class", "files"])
        for kind in ("whitespace", "filename", "byte-order-mark",
                     "duplicate-tag", "content", "unchanged"):
            writer.writerow([kind, edit_classes[kind]])
        writer.writerow(["unpaired", unpaired])

    # ---- print a readable table (terminal only) ----------------------------
    print(f"run: {run_dir.name}   files: {rows:,}   "
          f"rule set: default ({len(catalogue.rules_in_mode(False))} rules)\n")
    print(f"{'rule':<8}{'fix':<9}{'before':>12}{'after':>12}{'resolved':>12}{'%':>8}")
    for rule_id in sorted(before_total, key=lambda r: -before_total[r]):
        b, a = before_total[rule_id], after_total[rule_id]
        rule = known.get(rule_id)
        print(f"{rule_id:<8}{(rule.fixability if rule else '?'):<9}"
              f"{b:>12,}{a:>12,}{b-a:>12,}{(100*(b-a)/b if b else 0):>7.1f}%")

    print(f"\nedit classes over {pairs_checked:,} before/after file pairs")
    for kind in ("whitespace", "filename", "byte-order-mark",
                 "duplicate-tag", "content", "unchanged"):
        print(f"  {kind:<20}{edit_classes[kind]:>10,}")
    print(f"  {'unpaired':<20}{unpaired:>10,}")

    print(f"\nwritten: {rules_csv}")
    print(f"         {edits_csv}")
    return 0 if edit_classes["content"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
