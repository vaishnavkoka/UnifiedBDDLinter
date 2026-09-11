#!/usr/bin/env python3
"""
phase3_bdd_pipeline_full.py -- the differential lint / fix / lint harness.

    python3 evaluation/phase3_bdd_pipeline_full.py --corpus <dir> --output <run dir>
    python3 evaluation/phase3_bdd_pipeline_full.py --corpus <dir> --output <dir> --sample 500

WHAT IT DOES, PER FILE
----------------------
    1. lint with all three linters, recording violation TEXT and a COUNT
    2. run our auto-fixer, writing the repaired copy to a separate tree
    3. lint the repaired copy with the same three linters
    4. emit one 16-field row

The 16 fields, and their order, are unchanged from the published run so that
old and new result CSVs can be compared directly.

THIS IS NOT PART OF THE TOOL
----------------------------
UnifiedBDDLinter needs neither Node nor Ruby. This harness does, because it
runs two other people's linters as independent witnesses to our fixer's claims.
The tool lives in ../tool/ and knows nothing about any of this.

WHY OUR LINTER IS CALLED IN-PROCESS
-----------------------------------
The original harness spawned `python3 cli.py` once per file, re-importing the
engine 20,270 times. Here the engine is imported once per worker and called
directly. Identical code, identical output -- it is the same UnifiedLinter
class the CLI uses -- but without paying process startup per file.

The external oracles still run as subprocesses because there is no other way to
invoke them, and that is now the dominant cost, which is the honest place for
the cost to be.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACT_ROOT = HERE.parent
TOOL_SRC = ARTIFACT_ROOT / "src"

sys.path.insert(0, str(TOOL_SRC))
sys.path.insert(0, str(HERE))

import oracles  # noqa: E402
from unifiedbddlinter.catalogue import RULES, SAFE, UNSAFE  # noqa: E402

# Derived, never hardcoded: these counts appear in the run banner and in
# versions.txt, which is provenance. A stale literal there would misdescribe a
# published run, and the catalogue is the only thing that knows the real answer.
_SAFE_RULES = sum(1 for r in RULES if r.fixability == SAFE)
_UNSAFE_RULES = sum(1 for r in RULES if r.fixability == UNSAFE)
# The before/after comparison is the headline result -- it answers the question
# the run was started to ask. Every other figure is supporting detail.
HEADLINE_FIGURE = "fig2_before_after.png"


def _open_figures(figures_dir, log, every=False) -> None:
    """Show the generated figures in the desktop's image viewer.

    By default only the before/after figure opens, so what appears on screen is
    the answer rather than whichever filename happened to sort last. With
    *every*, the rest open first and the headline opens last, because the window
    opened last is the one left in front.

    Opt-in (--open) rather than automatic: a headless run -- CI, a container, a
    remote shell -- has no viewer, and a tool that tries to open windows there
    prints noise or hangs. Failure here is never fatal; the files are on disk
    either way and the paths were just printed.
    """
    figures_dir = Path(figures_dir)
    headline = figures_dir / HEADLINE_FIGURE
    if every:
        ordered = [f for f in sorted(figures_dir.glob("*.png")) if f != headline]
        ordered.append(headline)
    else:
        ordered = [headline]

    viewer = ("open" if sys.platform == "darwin"
              else "start" if os.name == "nt" else "xdg-open")
    for figure in ordered:
        if not figure.is_file():
            continue
        try:
            subprocess.Popen([viewer, str(figure)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            log(f"could not open {short(figure)} -- no desktop viewer found")
            return
        # A viewer launched a millisecond after another may raise its window
        # first. A short gap makes the final ordering the one asked for.
        time.sleep(0.4)
    log(f"opened {short(ordered[-1])}")


def short(path) -> str:
    """Display a path relative to the working directory when it lives there.

    A run directory nested under the artifact is often wider than the terminal,
    and the leading half is the half the reader already knows. Anything outside
    the working directory is printed in full, because shortening it would be
    misleading rather than merely long.
    """
    try:
        return str(Path(path).resolve().relative_to(Path.cwd()))
    except (ValueError, OSError):
        return str(path)


FORM_ONLY_LABEL = f"form-only ({_SAFE_RULES} rules, no invented text)"
INJECTION_LABEL = (f"text-injection ({_SAFE_RULES + _UNSAFE_RULES} rules, "
                   f"historical)")

# A violation-text cell holds one linter's entire output for one file. On the
# largest corpus files that is hundreds of kilobytes, far past csv's default
# 128 KB field cap -- which raises "field larger than field limit" on READ, and
# only when resuming, which is the worst time to discover it. Raised to the
# platform maximum.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def csv_safe(value):
    """Make *value* writable by the csv module.

    C0 control characters other than tab, newline and carriage return cannot be
    represented in a CSV field: the writer raises "need to escape, but no
    escapechar set" and the whole run dies at that row.

    This is not hypothetical. Exactly one file in the evaluation corpus --
    `stanford_capx.feature` -- contains 9 NUL bytes and 350 other control
    characters, and it is the same file recorded in the original run's
    provenance as "failed on CSV escaping". That is why that run reported
    20,269 files processed out of 20,270 discovered.

    Replaced with U+FFFD rather than deleted, so the cell still shows that
    something unrepresentable was there. The .feature files themselves are
    never modified by this -- it affects only how a linter's output is recorded.
    """
    if not isinstance(value, str):
        return value
    return "".join(
        "\ufffd" if (ord(ch) < 32 and ch not in "\t\n\r") else ch
        for ch in value
    )


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------
# tqdm is optional. A run must never fail because a progress bar is missing, so
# an absent tqdm degrades to periodic printed lines rather than to an
# ImportError -- the same policy the linter applies to its own optional
# dependency.
try:
    from tqdm import tqdm as _tqdm

    HAVE_TQDM = True
except ImportError:                                   # pragma: no cover
    HAVE_TQDM = False

    class _tqdm:                                      # minimal stand-in
        """No-op shim exposing the parts of tqdm's API this file uses."""

        def __init__(self, total=None, **_kwargs):
            self.total = total
            self.n = 0                       # accepts and ignores tqdm's kwargs

        def update(self, count=1):
            self.n += count

        def set_postfix_str(self, _text):
            pass

        def write(self, text):
            print(text)

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()


class RunLog:
    """Tee log lines to a file and to the terminal.

    Everything the run prints also lands in `run.log`, because a long run is
    typically started in a terminal that is later closed, and the scrollback is
    then the only record of what happened. Timestamps are absolute rather than
    elapsed so a line can be correlated with a system log or a monitoring graph.

    The file is line-buffered and flushed on every write: if the process is
    killed, the log must still explain how far it got.
    """

    def __init__(self, path: Path):
        self.path = path
        self.handle = open(path, "a", encoding="utf-8", buffering=1)

    def __call__(self, message: str = "", to_terminal=True, bar=None):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.handle.write(f"{stamp}  {message}\n")
        self.handle.flush()
        if to_terminal:
            # Printing through the bar keeps tqdm from being overwritten.
            (bar.write if bar is not None else print)(message)

    def close(self):
        self.handle.close()


def write_checkpoint(path: Path, payload: dict) -> None:
    """Write the checkpoint atomically.

    Written to a sibling temp file and then renamed, because a checkpoint is
    only useful if it is never observed half-written -- and a run interrupted
    during the write is exactly when it will be read.
    """
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)

# Column order is a compatibility contract with the published results CSV.
# Do not reorder: downstream figure scripts and the earlier run both rely on it.
CSV_FIELDS = [
    "Repository_Name", "File_Path",
    "Gherkin_Lint_Errors_Before", "Gherkin_Lint_Errors_Before_nums",
    "Cuke_Lint_Errors_Before", "Cuke_Lint_Errors_Before_nums",
    "BDD_Lint_Errors_Before", "BDD_Lint_Errors_Before_nums",
    "Gherkin_Lint_Errors_After", "Gherkin_Lint_Errors_After_nums",
    "Cuke_Lint_Errors_After", "Cuke_Lint_Errors_After_nums",
    "BDD_Lint_Errors_After", "BDD_Lint_Errors_After_nums",
    "Gherkin_Issues_Fixed", "Cuke_Issues_Fixed",
]

# Per-worker state. Building the linter once per process rather than once per
# file matters: at 20k files the constructor cost would otherwise dominate.
_WORKER = {}


def _init_worker(gherkin_lint_bin, cuke_linter_bin, safe_fix, use_oracles):
    """Initialise one worker process."""
    from unifiedbddlinter.engine import UnifiedLinter
    from unifiedbddlinter.config import LinterConfig

    _WORKER["gherkin_lint"] = gherkin_lint_bin
    _WORKER["cuke_linter"] = cuke_linter_bin
    _WORKER["safe_fix"] = safe_fix
    _WORKER["use_oracles"] = use_oracles
    # full=False is the 18-rule oracle-checkable subset -- the set the external
    # linters can corroborate, and the set the published results measured.
    _WORKER["linter"] = UnifiedLinter(full=False, config=LinterConfig())


def _lint_native(path: Path):
    """Lint with UnifiedBDDLinter, returning (text, count)."""
    linter = _WORKER["linter"]
    try:
        violations = linter.lint_file(str(path))
    except Exception as exc:
        return f"{oracles.TOOL_ERROR_TEXT}: {exc}", -1
    text = "\n".join(f"{v.rule_id} L{v.line}: {v.message}" for v in violations)
    return text, len(violations)


def _lint_all(path: Path):
    """Run every configured linter over *path*."""
    if _WORKER["use_oracles"]:
        gl = oracles.run_gherkin_lint(_WORKER["gherkin_lint"], path)
        cl = oracles.run_cuke_linter(_WORKER["cuke_linter"], path)
    else:
        # Explicitly "not measured", never 0 -- see oracles._run.
        gl = cl = (oracles.TOOL_ERROR_TEXT, -1)
    return gl, cl, _lint_native(path)


def process_file(job):
    """Lint, fix, re-lint one file. Runs inside a worker process."""
    source_str, corpus_root_str, fixed_root_str, single_repo = job
    source = Path(source_str)
    corpus_root = Path(corpus_root_str)
    fixed_root = Path(fixed_root_str)

    relative = source.relative_to(corpus_root)
    if single_repo:
        # --corpus points at one repository, not at a tree of them, so every
        # file belongs to it. Without this the second path component -- an
        # ordinary subdirectory -- would be mistaken for a repository name, and
        # a 230-file clone would report itself as 168 repositories.
        repository = single_repo
    else:
        # The repository is the second path component: <source>/<repo>/...
        parts = relative.parts
        repository = parts[1] if len(parts) > 1 else (parts[0] if parts else "?")

    before_gl, before_cl, before_native = _lint_all(source)

    # The fixer writes into a mirror tree, so the run's input copy is itself
    # left untouched and a failed run can simply be deleted and repeated.
    destination_dir = fixed_root / relative.parent
    destination_dir.mkdir(parents=True, exist_ok=True)

    try:
        from unifiedbddlinter.fixer import AutoFixer
        fixer = AutoFixer(safe=_WORKER["safe_fix"], quiet=True)
        fixed_path = Path(fixer.fix_file(str(source), output_dir=str(destination_dir)))
    except Exception as exc:
        return {
            **{field: "" for field in CSV_FIELDS},
            "Repository_Name": repository,
            "File_Path": str(relative).replace(os.sep, "/"),
            "BDD_Lint_Errors_Before": f"{oracles.TOOL_ERROR_TEXT}: fix failed: {exc}",
            "BDD_Lint_Errors_Before_nums": -1,
        }, False

    after_gl, after_cl, after_native = _lint_all(fixed_path)

    def delta(before, after):
        """Violations resolved. Meaningless if either side was not measured."""
        return before - after if before >= 0 and after >= 0 else ""

    return {
        "Repository_Name": repository,
        "File_Path": str(relative).replace(os.sep, "/"),
        "Gherkin_Lint_Errors_Before": before_gl[0],
        "Gherkin_Lint_Errors_Before_nums": before_gl[1],
        "Cuke_Lint_Errors_Before": before_cl[0],
        "Cuke_Lint_Errors_Before_nums": before_cl[1],
        "BDD_Lint_Errors_Before": before_native[0],
        "BDD_Lint_Errors_Before_nums": before_native[1],
        "Gherkin_Lint_Errors_After": after_gl[0],
        "Gherkin_Lint_Errors_After_nums": after_gl[1],
        "Cuke_Lint_Errors_After": after_cl[0],
        "Cuke_Lint_Errors_After_nums": after_cl[1],
        "BDD_Lint_Errors_After": after_native[0],
        "BDD_Lint_Errors_After_nums": after_native[1],
        "Gherkin_Issues_Fixed": delta(before_gl[1], after_gl[1]),
        "Cuke_Issues_Fixed": delta(before_cl[1], after_cl[1]),
    }, True


def discover(root: Path):
    """Every .feature FILE under *root*, sorted for a deterministic run order."""
    for path in sorted(root.rglob("*.feature")):
        # Directories named *.feature exist in this corpus, and handing one to
        # open() raises IsADirectoryError. This cost a completed run once.
        if path.is_file() and not path.is_symlink():
            yield path


def write_provenance(destination: Path, args, environment, counts, elapsed):
    """Record everything needed to judge whether two runs are comparable.

    A results file without this is not evidence: it cannot be told apart from a
    run of different code, on different inputs, with a different rule set.
    """
    import hashlib

    def md5(path):
        return hashlib.md5(Path(path).read_bytes()).hexdigest()

    tool = TOOL_SRC / "unifiedbddlinter"
    lines = [
        "# Provenance for this run. Written automatically; do not edit.",
        "# A provenance file that is quietly corrected after the fact is not provenance.",
        "",
        "[run]",
        f"completed        = {datetime.now().isoformat(timespec='seconds')}",
        f"corpus           = {args.corpus}",
        f"files_discovered = {counts['discovered']}",
        f"files_processed  = {counts['processed']}",
        f"fix_failures     = {counts['failures']}",
        f"workers          = {args.workers}",
        f"rule_set         = default (18 oracle-checkable rules)",
        f"fixer_mode       = {INJECTION_LABEL if args.allow_text_injection else FORM_ONLY_LABEL}",
        f"oracles_used     = {not args.no_oracles}",
        f"elapsed_seconds  = {elapsed:.1f}",
        "",
        "[tool]",
    ]
    for name in ("engine.py", "fixer.py", "config.py", "catalogue.py", "cli.py"):
        target = tool / name
        if target.is_file():
            lines.append(f"{name:<16} = md5 {md5(target)}")
    lines += ["", "[oracles]"]
    for name, (path, version) in environment.items():
        lines.append(f"{name:<16} = {version or 'UNKNOWN'}   ({path or 'n/a'})")
    lines.append(f"{'gherkin-lintrc':<16} = md5 {md5(oracles.GHERKIN_LINTRC)}")
    lines.append(f"{'cukelinter':<16} = md5 {md5(HERE / 'config' / '.cukelinter')}")
    lines += ["", "[runtime]",
              f"python           = {sys.version.split()[0]}",
              f"platform         = {platform.platform()}"]
    (destination / "versions.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # -r/--repos-root and -o are the spellings printed in the paper (Table 2);
    # --corpus/--output are this harness's own names. Both work.
    parser.add_argument("--corpus", "-r", "--repos-root", required=True,
                        help="working copy of the corpus (see corpus/materialise.py). "
                             "Never point this at corpus/master.")
    parser.add_argument("--output", "-o", required=True,
                        help="parent directory for runs. Each run creates its "
                             "own run-<timestamp>/ inside it, so runs never "
                             "overwrite each other.")
    parser.add_argument("--repo-name", metavar="NAME", default="",
                        help="treat the whole corpus directory as one "
                             "repository with this name. Detected automatically "
                             "when it contains a .git directory.")
    parser.add_argument("--open", action="store_true",
                        help="show the before/after figure when the run "
                             "finishes (desktop sessions only)")
    parser.add_argument("--open-all", action="store_true",
                        help="with --open, show every generated figure; the "
                             "before/after one is opened last so it ends up in "
                             "front")
    parser.add_argument("--no-analysis", action="store_true",
                        help="stop after the results CSV; do not generate the "
                             "figures and tables")
    parser.add_argument("--no-timestamp", action="store_true",
                        help="write directly into --output instead of a stamped "
                             "subdirectory (reproduces the documented paths)")
    parser.add_argument("--workers", "-w", type=int, default=max(1, (os.cpu_count() or 4)),
                        help="parallel worker processes (default: CPU count)")
    parser.add_argument("--sample", "-n", type=int, metavar="N", help="process only N files")
    parser.add_argument("--allow-text-injection", action="store_true",
                        help="ALSO apply the five rules repairable only by "
                             "authoring text (ST001-ST004, ST006), reproducing "
                             "the historical v1.0 behaviour. Not used for any "
                             "reported result.")
    parser.add_argument("--safe-fix", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-oracles", action="store_true",
                        help="skip the external linters; measures only our own tool")
    parser.add_argument("--resume", action="store_true",
                        help="continue a run that was interrupted: skip files "
                             "already present in the output results.csv")
    parser.add_argument("--max-hours", type=float, default=6.0,
                        help="abort early if the measured rate projects a longer "
                             "run than this (default: 6). Set 0 to disable.")
    args = parser.parse_args()

    corpus = Path(args.corpus).resolve()
    if not corpus.is_dir():
        sys.exit(f"corpus not found: {corpus}")
    master = ARTIFACT_ROOT / "corpus" / "master"
    if corpus == master or master in corpus.parents:
        sys.exit("refusing to run against corpus/master -- materialise a working "
                 "copy first:\n  python3 corpus/materialise.py <dir>")

    environment = oracles.probe()
    if not args.no_oracles:
        missing = [n for n in ("gherkin-lint", "cuke_linter") if not environment[n][0]]
        if missing:
            sys.exit(f"missing external oracle(s): {', '.join(missing)}\n"
                     f"install them (see README), or pass --no-oracles to measure "
                     f"only UnifiedBDDLinter.")

    # Each run lands in its own stamped directory under --output, so a second
    # run never overwrites the first. Only the directory carries the stamp; the
    # files inside keep plain, predictable names (results.csv, run.log, ...) so
    # the analysis scripts and the documented paths stay readable.
    parent = Path(args.output).resolve()
    if args.no_timestamp:
        destination = parent
    elif args.resume:
        # Resuming means continuing a specific run, so reuse the newest stamped
        # directory rather than starting an empty one beside it.
        previous = sorted(d for d in parent.glob("run-*") if d.is_dir())
        if not previous:
            sys.exit(f"--resume: no run-* directory to continue in {parent}\n"
                     f"pass --no-timestamp if the run wrote directly to {parent}.")
        destination = previous[-1]
    else:
        destination = parent / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    destination.mkdir(parents=True, exist_ok=True)
    print(f"run directory: {short(destination)}")
    fixed_root = destination / "fixed"
    fixed_root.mkdir(exist_ok=True)
    results_csv = destination / "results.csv"

    files = list(discover(corpus))
    discovered = len(files)
    if args.sample:
        files = files[:args.sample]

    # Resume. A full-corpus run takes tens of minutes; losing it at minute 38 to
    # a dropped connection and restarting from zero is a waste that a few lines
    # of bookkeeping prevents. Completed rows are identified by File_Path, which
    # is unique within a run because it is the path relative to the corpus root.
    already_done = set()
    if args.resume and results_csv.is_file():
        with open(results_csv, newline="", encoding="utf-8") as handle:
            already_done = {row["File_Path"] for row in csv.DictReader(handle)}
        before = len(files)
        files = [f for f in files
                 if str(f.relative_to(corpus)).replace(os.sep, "/") not in already_done]
        print(f"resuming: {len(already_done):,} rows already recorded, "
              f"{len(files):,} of {before:,} remaining\n")

    if not files:
        if already_done:
            print("nothing left to do -- the run is already complete.")
            return 0
        sys.exit(f"no .feature files under {corpus}")

    log = RunLog(destination / "run.log")
    checkpoint_path = destination / "checkpoint.json"
    fixer_label = (INJECTION_LABEL
                   if args.allow_text_injection
                   else FORM_ONLY_LABEL)

    log(f"=== run started {datetime.now().isoformat(timespec='seconds')} ===")
    log(f"corpus     {short(corpus)}")
    log(f"output     {short(destination)}")
    log(f"files      {len(files):,} of {discovered:,} discovered")
    log(f"workers    {args.workers}")
    log(f"fixer      {fixer_label}")
    log(f"oracles    {'disabled' if args.no_oracles else 'gherkin-lint + cuke_linter'}")
    if not HAVE_TQDM:
        log("progress   tqdm not installed -- printing periodic lines instead")
    log("")

    # The checkpoint's static half. Written once up front so that a run killed
    # in its first seconds still leaves a record of what it was trying to do.
    checkpoint = {
        "run": {
            "started": datetime.now().isoformat(timespec="seconds"),
            "corpus": str(corpus),
            "output": str(destination),
            "fixer_mode": ("text-injection" if args.allow_text_injection
                       else "form-only"),
            "rule_set": "default (18 oracle-checkable rules)",
            "oracles": not args.no_oracles,
            "workers": args.workers,
        },
        "progress": {"total": len(files), "processed": len(already_done),
                     "failures": 0, "status": "running"},
    }
    write_checkpoint(checkpoint_path, checkpoint)

    # A .git directory at the root means this is one cloned repository rather
    # than a corpus of them -- the shape a demo or a one-off check uses.
    single_repo = corpus.name if (corpus / ".git").exists() else ""
    if args.repo_name:
        single_repo = args.repo_name
    if single_repo:
        log(f"repository {single_repo} (single repository, not a corpus tree)")
    jobs = [(str(f), str(corpus), str(fixed_root), single_repo) for f in files]
    processed = failures = 0
    start = time.time()

    mode = "a" if already_done else "w"
    aborted = False
    with open(results_csv, mode, newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if not already_done:
            writer.writeheader()
        # disable=None makes tqdm draw only on a real terminal. Redirected to a
        # file or a pipe -- which is how a long run is actually started -- the
        # bar would otherwise write one line per update and bury the log in
        # thousands of carriage-returned fragments. Progress is still recorded:
        # the periodic lines below go to run.log regardless.
        bar = _tqdm(total=len(files), unit="file", dynamic_ncols=True,
                    desc="lint/fix/lint", smoothing=0.1, disable=None)
        with bar, concurrent.futures.ProcessPoolExecutor(
                max_workers=args.workers,
                initializer=_init_worker,
                initargs=(environment["gherkin-lint"][0], environment["cuke_linter"][0],
                          not args.allow_text_injection,
                          not args.no_oracles)) as pool:
            for row, ok in pool.map(process_file, jobs, chunksize=8):
                writer.writerow({k: csv_safe(v) for k, v in row.items()})
                processed += 1
                bar.update(1)
                if not ok:
                    failures += 1
                    # Failures are logged individually, not just counted: a
                    # tally tells you something went wrong, the path tells you
                    # what. Kept off the terminal to avoid shredding the bar.
                    log(f"FIX FAILED  {row.get('File_Path', '?')}", to_terminal=False)

                if processed % 100 == 0:
                    elapsed_so_far = time.time() - start
                    rate = processed / elapsed_so_far if elapsed_so_far else 0
                    remaining = (len(files) - processed) / rate if rate else 0
                    bar.set_postfix_str(f"{rate:.1f} f/s · {failures} failed")
                    handle.flush()   # so `tail -f` on the CSV shows live progress

                    checkpoint["progress"].update(
                        processed=processed + len(already_done),
                        failures=failures,
                        rate_files_per_sec=round(rate, 2),
                        eta_minutes=round(remaining / 60, 1),
                        updated=datetime.now().isoformat(timespec="seconds"))
                    write_checkpoint(checkpoint_path, checkpoint)

                if processed % 1000 == 0:
                    elapsed_so_far = time.time() - start
                    rate = processed / elapsed_so_far if elapsed_so_far else 0
                    remaining = (len(files) - processed) / rate if rate else 0
                    # Echoed to the terminal only when the bar is not drawing,
                    # so a redirected run still shows progress and an
                    # interactive one is not printed over twice.
                    log(f"  {processed:,}/{len(files):,}  {rate:.1f} files/s  "
                        f"eta {remaining/60:.1f} min  {failures} failures",
                        to_terminal=bool(getattr(bar, "disable", True)))

                if processed % 250 == 0:
                    elapsed_so_far = time.time() - start
                    rate = processed / elapsed_so_far if elapsed_so_far else 0
                    remaining = (len(files) - processed) / rate if rate else 0
                    # Rate gate. A pilot on a few hundred files has repeatedly
                    # failed to predict full-corpus behaviour for this tool --
                    # the build record for a later version records it
                    # mispredicting twice. So the run checks ITSELF against the
                    # projection instead of trusting the pilot, and stops early
                    # rather than discovering at hour five that it will not
                    # finish. The partial results.csv is valid and --resume
                    # picks it up, so stopping costs nothing already done.
                    if args.max_hours and remaining / 3600 > args.max_hours:
                        aborted = True
                        log(f"ABORTING: measured {rate:.1f} files/s projects "
                            f"{remaining/3600:.1f} more hours, over --max-hours "
                            f"{args.max_hours}.", bar=bar)
                        log(f"  {processed:,} rows are written and valid. Re-run "
                            f"with --resume to continue, or raise --max-hours.",
                            bar=bar)
                        break

    elapsed = time.time() - start
    total_processed = processed + len(already_done)
    write_provenance(destination, args, environment,
                     {"discovered": discovered, "processed": total_processed,
                      "failures": failures}, elapsed)

    checkpoint["progress"].update(
        processed=total_processed, failures=failures,
        status="aborted" if aborted else "complete",
        finished=datetime.now().isoformat(timespec="seconds"),
        elapsed_seconds=round(elapsed, 1))
    write_checkpoint(checkpoint_path, checkpoint)

    log("")
    log(f"processed {processed:,} files in {elapsed/60:.1f} min "
        f"({processed/elapsed:.1f} files/s)")
    log(f"fix failures: {failures}")
    log(f"results:      {short(results_csv)}")
    log(f"provenance:   {short(destination / 'versions.txt')}")
    log(f"log:          {short(destination / 'run.log')}")
    log(f"checkpoint:   {short(checkpoint_path)}")
    log(f"=== run {'ABORTED' if aborted else 'complete'} ===")

    # Generate the figures and tables straight away. A run that stops at a CSV
    # is not finished from the reader's point of view -- they wanted the result,
    # not the intermediate -- and asking for a second command by hand is how the
    # two drift apart. Skipped after an abort, because a partial CSV would make
    # a plot that looks authoritative and is not.
    if not aborted and not args.no_analysis:
        analysis_dir = destination / "analysis"
        maker = Path(__file__).resolve().parent / "analysis" / "make_all.py"
        if maker.is_file():
            log("")
            log("generating figures and tables ...")
            proc = subprocess.run(
                [sys.executable, str(maker), str(destination),
                 "--outdir", str(analysis_dir)],
                capture_output=True, text=True)
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    item = line.strip()
                    # Only the bare artefact paths. make_all also echoes its
                    # input as "results: <csv>", which was just printed above.
                    if (item.endswith((".png", ".csv"))
                            and " " not in item and ":" not in item):
                        log(f"  {short(item)}")
                log(f"analysis:     {short(analysis_dir)}")
                if args.open:
                    _open_figures(analysis_dir / "figures", log,
                                  every=args.open_all)
            else:
                # Missing matplotlib is the usual cause, and it must not fail the
                # run: the CSV is the result, the figures are a convenience.
                log("analysis skipped -- see the reason below; the results CSV "
                    "above is complete and make_all.py can be run by hand.")
                for line in (proc.stderr or "").strip().splitlines()[-3:]:
                    log(f"  {line}")

    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
