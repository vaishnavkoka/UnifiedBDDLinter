#!/usr/bin/env python3
"""
materialise.py -- create a run's working copy of the corpus.

    python3 corpus/materialise.py <destination>
    python3 corpus/materialise.py <destination> --sample 500
    python3 corpus/materialise.py <destination> --repos repoA repoB

WHY RUNS DO NOT READ THE MASTER DIRECTLY
----------------------------------------
The auto-fixer writes. Even in its safest configuration it renames files
(ST007), and the evaluation harness needs somewhere to put repaired copies. A
pipeline pointed at the master would therefore mutate the very thing that makes
its results comparable with every earlier run.

The master is also locked read-only, so a run pointed at it would fail
partway -- after doing some work, in an unclear state. Failing is better than
corrupting, but not failing at all is better still.

So: every run materialises its own copy here, works in it, and throws it away.
The master is read, hashed, and never written.

WHAT IS GUARANTEED
------------------
Every materialised file is verified against the manifest's SHA-256 as it is
copied. A run therefore cannot silently start from different bytes than the
run it is being compared against -- the copy fails loudly instead.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MASTER = HERE / "master"
MANIFEST = HERE / "MANIFEST.csv"
BLOCK = 1 << 20


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest():
    if not MANIFEST.is_file():
        sys.exit(f"no manifest at {MANIFEST}\nrun: python3 corpus/build_master.py")
    with open(MANIFEST, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def stratified_sample(rows, target, seed):
    """Draw ~*target* files spread proportionally across every repository.

    WHY NOT JUST TAKE THE FIRST N
    -----------------------------
    The manifest is ordered by path, so its first N entries are whatever
    repository happens to sort first. Measured on this corpus: the first 500
    rows cover 1 repository of 38, the first 5,000 cover 9. A pilot drawn that
    way tells you about one project and nothing about the corpus -- and the
    build record for a later version of this tool records exactly that mistake
    costing two mispredicted full-corpus runs.

    ALLOCATION
    ----------
    Proportional to each repository's size, but with a floor of one file per
    repository, so a repo contributing 11 files to a 20,270-file corpus is still
    represented in a 200-file pilot. That is the point of a pilot: it must be
    able to surface a problem that lives in a small repository.

    Because the floor can push the total above *target*, the result is
    approximately rather than exactly N, and the caller is told the real number.

    DETERMINISM
    -----------
    Seeded, and files within each repository are sorted before sampling, so the
    same seed and target always yield the same set on any machine and any Python
    build. A pilot you cannot reproduce is not a measurement.
    """
    import random

    by_repo = {}
    for row in rows:
        by_repo.setdefault(row["repository"], []).append(row)

    total = len(rows)
    rng = random.Random(seed)
    picked = []
    for repo in sorted(by_repo):                      # sorted: order-independent
        available = sorted(by_repo[repo], key=lambda r: r["path"])
        share = max(1, round(target * len(available) / total))
        share = min(share, len(available))
        picked.extend(rng.sample(available, share))

    # Return in manifest order so the run processes files in a stable sequence
    # and two pilots with the same seed produce comparable progress output.
    order = {row["path"]: index for index, row in enumerate(rows)}
    picked.sort(key=lambda r: order[r["path"]])
    return picked


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("destination", help="directory to create the working copy in")
    parser.add_argument("--sample", type=int, metavar="N",
                        help="materialise only the FIRST N files in manifest order. "
                             "Fast, but NOT representative: the manifest is ordered "
                             "by path, so a prefix sits inside one or two "
                             "repositories. Use --stratified for anything you intend "
                             "to draw a conclusion from.")
    parser.add_argument("--stratified", type=int, metavar="N",
                        help="materialise ~N files drawn proportionally from EVERY "
                             "repository. This is the option to use for a pilot: a "
                             "prefix sample of 5,000 files covers 9 of 38 repos, so "
                             "it cannot predict full-corpus behaviour.")
    parser.add_argument("--seed", type=int, default=20260908,
                        help="random seed for --stratified (default: fixed, so the "
                             "same pilot is reproducible and can be cited)")
    parser.add_argument("--repos", nargs="+", metavar="REPO",
                        help="restrict to these repository names")
    parser.add_argument("--force", action="store_true",
                        help="overwrite a non-empty destination")
    args = parser.parse_args()

    destination = Path(args.destination).resolve()
    # Refusing to write into the master is not paranoia: `materialise.py
    # corpus/master` is an easy thing to type, and it would destroy the one
    # artifact that makes every run comparable.
    if destination == MASTER or MASTER in destination.parents:
        sys.exit("refusing to materialise into the master corpus")
    if destination.exists() and any(destination.iterdir()):
        if not args.force:
            sys.exit(f"{destination} exists and is not empty (use --force)")
        # --force CLEARS the destination rather than writing over it. Merging
        # into leftovers is the dangerous case: materialising 400 files into a
        # directory that still holds 2,000 from a previous draw produces a tree
        # that is neither, and the run that reads it silently measures a corpus
        # nobody chose. Observed exactly that before this guard existed.
        print(f"--force: clearing {destination}")
        shutil.rmtree(destination)

    rows = load_manifest()
    if args.repos:
        wanted = set(args.repos)
        rows = [r for r in rows if r["repository"] in wanted]
        if not rows:
            sys.exit(f"no files for repositories: {', '.join(sorted(wanted))}")
    if args.sample and args.stratified:
        sys.exit("--sample and --stratified are mutually exclusive")
    if args.stratified:
        rows = stratified_sample(rows, args.stratified, args.seed)
    elif args.sample:
        rows = rows[:args.sample]

    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for row in rows:
        source = MASTER / row["path"]
        if not source.is_file():
            sys.exit(f"master is incomplete: {row['path']}\n"
                     f"run: python3 corpus/build_master.py --verify")
        target = destination / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        # Writable on purpose: this copy is the run's to modify.
        target.chmod(0o644)
        # Verify as we go rather than in a second pass, so a damaged master is
        # caught at the file that is wrong instead of after copying 4.9 GB.
        if sha256_of(target) != row["sha256"]:
            sys.exit(f"hash mismatch materialising {row['path']}")
        copied += 1
        if copied % 2000 == 0:
            print(f"  materialised {copied:,}/{len(rows):,}")

    repos = {r["repository"] for r in rows}
    all_repos = {r["repository"] for r in load_manifest()}
    print(f"materialised {copied:,} files from {len(repos)} of {len(all_repos)} repositories")
    if args.stratified:
        print(f"  stratified draw, seed {args.seed} -- reproducible")
    elif args.sample and len(repos) < len(all_repos):
        print(f"  WARNING: a prefix sample covers only {len(repos)} of "
              f"{len(all_repos)} repositories and is NOT representative.")
        print(f"           Use --stratified {args.sample} for a pilot.")
    print(f"  into {destination}")
    print("  every file verified against the manifest SHA-256")
    return 0


if __name__ == "__main__":
    sys.exit(main())
