#!/usr/bin/env python3
"""
Phase 3 – Part 2: Repository Cloner
=====================================
Reads the output CSVs produced by Phase 3 Part 1 (count_features.py),
filters rows where feature_file_count >= 10, then full-clones each qualifying
repo.  Each input CSV gets its own *method sub-folder* inside the output root,
so repos and result CSVs are kept completely separate by collection method.

Output directory layout
------------------------
  <output_dir>/
    github-global-search/                        ← method sub-folder
      <owner>-<repo>/                            ← cloned repositories
      <input_stem>_phase-3-output-feature-cloner.csv
      <input_stem>_feature-less-10.csv
    github-search-tool/
      ...
    seart-tool/
      ...
    phase-3-cloner.log                           ← single log for all methods
    phase-3-cloner-checkpoint.json               ← single checkpoint

Progress bars  (all created upfront – never move)
-------------
  Bar 0  green   – Files: N / M input CSV files complete
  Bar 1  yellow  – Disk used: actual du on output root (live, accurate)
  Bar 2  cyan    – Overall rows / ETA across ALL files
  Bar 3..N+2     – One per input CSV file (blue, magenta, red, white, …)
                   Timer reset when file starts so per-file ETA is accurate.

Checkpoint
----------
  Saved every CKPT_INTERVAL rows AND on SIGINT.
  On startup, if a checkpoint exists the user is prompted to resume or restart.

Security notes
--------------
  * GitHub token read once from github_token.py; never written to log.
  * Atomic checkpoint write: tmp file -> rename.
  * No shell=True subprocess invocations.
  * DISK_THRESHOLD_GB env var (default 100) prevents disk exhaustion.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from tqdm import tqdm
except ImportError:
    print("ERROR: 'tqdm' not installed.  Run: pip install tqdm", file=sys.stderr)
    sys.exit(1)

# ==============================================================================
# Constants
# ==============================================================================

LOG_FILE_NAME   = "phase-3-cloner.log"
CHECKPOINT_FILE = "phase-3-cloner-checkpoint.json"
OUTPUT_SUFFIX   = "phase-3-output-feature-cloner"
LESS10_SUFFIX   = "feature-less-10"

FULL_CLONE_DEFAULT = True

CLONE_MAX_RETRIES  = int(os.environ.get("CLONE_MAX_RETRIES",   "3"))
CLONE_TIMEOUT      = int(os.environ.get("CLONE_TIMEOUT_S",     "300"))
CKPT_INTERVAL      = int(os.environ.get("CKPT_INTERVAL",       "10"))
BACKOFF_BASE       = int(os.environ.get("CLONE_BACKOFF_BASE",   "5"))
MAX_BACKOFF        = int(os.environ.get("MAX_BACKOFF_S",        "120"))
DISK_THRESHOLD_GB  = float(os.environ.get("DISK_THRESHOLD_GB", "100"))
DISK_WARN_PCT      = float(os.environ.get("DISK_WARN_PCT",     "90"))
# How often (in clones) to re-measure disk usage from disk (vs. every clone)
DISK_MEASURE_EVERY = int(os.environ.get("DISK_MEASURE_EVERY",  "1"))

NEW_COLUMNS = ["clone_status", "clone_path", "clone_time_s", "cloned_at", "clone_error"]

# Colors for per-file progress bars (cycles if more files than colors)
FILE_BAR_COLORS = ["blue", "magenta", "red", "white", "cyan"]

# Shutdown flag – set by SIGINT handler or disk-limit guard; checked inside loops
_shutdown = threading.Event()

# Mutable container so the SIGINT handler can kill the active git subprocess
# without needing a global declaration inside clone_repo().
_proc_state: Dict[str, Any] = {"current": None}
_proc_lock   = threading.Lock()

# How many times Ctrl-C has been pressed:
#   1st press → graceful stop (save checkpoint, finish current row)
#   2nd press → immediate force-quit
_interrupt_count = 0

# One-time SSL warning: emitted the first time an SSL interception error is
# detected so the user gets a clear fix instruction rather than repeating noise.
_ssl_warned = False

def _ssl_warn_once(logger: logging.Logger) -> None:
    global _ssl_warned
    if _ssl_warned:
        return
    _ssl_warned = True
    msg = (
        "\n  SSL INTERCEPTION DETECTED\n"
        "  Git cannot reach github.com because a network proxy is presenting\n"
        "  its own certificate (e.g. an institutional/corporate SSL proxy).\n"
        "\n"
        "  Quick fix – re-run with the --no-ssl-verify flag:\n"
        "    python3 clone_repositories.py --no-ssl-verify ...\n"
        "\n"
        "  This disables SSL certificate checking for git clone only.\n"
        "  It is safe on trusted institutional networks.\n"
    )
    tqdm.write(msg)
    logger.warning("SSL interception proxy detected – re-run with --no-ssl-verify")
# Signal handler
# ==============================================================================

def _install_sigint_handler() -> None:
    def _handler(signum: int, frame: Any) -> None:
        global _interrupt_count
        _interrupt_count += 1
        if _interrupt_count == 1:
            _shutdown.set()
            # Terminate the active git clone subprocess immediately so that
            # communicate() returns quickly and the loop can save checkpoint.
            with _proc_lock:
                proc = _proc_state.get("current")
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            tqdm.write(
                "\n  WARNING  Ctrl-C received – stopping after current operation "
                "and saving checkpoint.\n"
                "  Press Ctrl-C again to force-quit immediately."
            )
        else:
            tqdm.write("\n  FORCE QUIT\n")
            os._exit(1)
    signal.signal(signal.SIGINT, _handler)


# ==============================================================================
# Logging
# ==============================================================================

def _setup_logger(log_dir: Path) -> logging.Logger:
    log_path = log_dir / LOG_FILE_NAME
    logger = logging.getLogger("phase3_cloner")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    fh = RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # Only warnings and above go to stderr (tqdm owns stdout)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


# ==============================================================================
# Data-classes
# ==============================================================================

@dataclass
class CloneResult:
    status:    str            # success | skipped | failed | filtered_out
    path:      str   = ""
    time_s:    float = 0.0
    cloned_at: str   = ""
    error:     str   = ""


@dataclass
class FileStats:
    filename:     str
    method_name:  str  = ""
    method_dir:   str  = ""
    total_rows:   int  = 0
    qualified:    int  = 0
    cloned_ok:    int  = 0
    skipped:      int  = 0
    failed:       int  = 0
    filtered_out: int  = 0
    rows_written: int  = 0
    started_at:   str  = ""
    finished_at:  str  = ""


# ==============================================================================
# Checkpoint Manager
# ==============================================================================

class CheckpointManager:
    _SECTION = "cloner"

    def __init__(self, path: Path, logger: logging.Logger) -> None:
        self._path   = path
        self._logger = logger
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                self._logger.warning("Could not load checkpoint: %s", exc)
        return {}

    def _write(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            tmp.replace(self._path)
        except OSError as exc:
            self._logger.error("Checkpoint write failed: %s", exc)

    def _section(self, file_key: str) -> Dict[str, Any]:
        return self._data.setdefault(self._SECTION, {}).setdefault(file_key, {})

    def mark_done(self, file_key: str, row_index: int, repo: str) -> None:
        sec = self._section(file_key)
        sec["last_index"] = row_index
        sec["last_repo"]  = repo
        sec["updated_at"] = _now_iso()
        self._write()

    def get_resume_index(self, file_key: str) -> int:
        return self._section(file_key).get("last_index", -1) + 1

    def mark_repo_done(self, repo: str, status: str) -> None:
        done = self._data.setdefault(self._SECTION, {}).setdefault("done_repos", {})
        done[repo] = status

    def store_inputs(self, input_files: List[Path]) -> None:
        """Persist input file names so we can detect mismatches on resume."""
        self._data.setdefault(self._SECTION, {})["input_files"] = [
            str(f) for f in input_files
        ]
        self._write()

    def check_inputs(self, input_files: List[Path]) -> Optional[str]:
        """Return a warning string when current inputs differ from checkpoint's."""
        stored = self._data.get(self._SECTION, {}).get("input_files")
        if not stored:
            return None
        current = [str(f) for f in input_files]
        if stored != current:
            return (
                "  WARNING  Input files differ from what this checkpoint recorded!\n"
                f"    Checkpoint used : {stored}\n"
                f"    Current input   : {current}\n"
                "  Resuming with different inputs may produce incorrect results.\n"
                "  Strongly recommended: start fresh (enter N)."
            )
        return None

    def clear(self) -> None:
        self._data = {}
        self._write()

    def exists(self) -> bool:
        return self._path.exists() and self._path.stat().st_size > 0

    @property
    def path(self) -> Path:
        return self._path


# ==============================================================================
# Utility helpers
# ==============================================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return [], []
        fieldnames = list(reader.fieldnames)
        rows = list(reader)
    return fieldnames, rows


def _output_path_for(input_path: Path, method_dir: Path) -> Path:
    return method_dir / f"{input_path.stem}_{OUTPUT_SUFFIX}.csv"


def _less10_path_for(input_path: Path, method_dir: Path) -> Path:
    return method_dir / f"{input_path.stem}_{LESS10_SUFFIX}.csv"


def _get_clone_url(row: Dict[str, str]) -> Optional[str]:
    url = row.get("clone_url", "").strip()
    if url:
        return url
    fn = row.get("full_name", "").strip()
    if fn and "/" in fn:
        return f"https://github.com/{fn}.git"
    return None


def _get_full_name(row: Dict[str, str]) -> str:
    fn = row.get("full_name", "").strip()
    if fn:
        return fn
    owner = row.get("owner", "unknown")
    repo  = row.get("repo_name", row.get("name", "unknown"))
    return f"{owner}/{repo}"


# Columns that must be present (any one of them) for a file to be Phase 3 Part 1 output
_QUALIFY_COLS = {"feature_file_count", "feature_count", "has_min_features"}


def _is_qualified(row: Dict[str, str]) -> bool:
    """True when feature_file_count (or feature_count) >= 10."""
    for col in ("feature_file_count", "feature_count"):
        val = row.get(col, "").strip()
        if val:
            try:
                return int(val) >= 10
            except ValueError:
                pass
    return row.get("has_min_features", "").strip().lower() == "true"


def _check_input_columns(files: List[Path]) -> bool:
    """
    Verify each input CSV has at least one qualification column.

    If ANY file is missing all of (feature_file_count, feature_count,
    has_min_features) it is almost certainly a Phase 2 output file passed
    by mistake.  Prints a clear diagnostic and returns False in that case.
    """
    all_ok = True
    for f in files:
        try:
            fieldnames, _ = _read_csv(f)
        except Exception:
            continue
        cols = set(fieldnames)
        if not (cols & _QUALIFY_COLS):
            sample = sorted(cols)[:10]
            sample_str = ", ".join(sample) + ("  ..." if len(cols) > 10 else "")
            print(
                f"\n  ERROR  Input file is missing feature-count columns:"
                f"\n    File   : {f.name}"
                f"\n    Needed : any of {sorted(_QUALIFY_COLS)}"
                f"\n    Found  : {sample_str}"
                f"\n"
                f"\n  This looks like a Phase 2 output file (no feature counts)."
                f"\n  Phase 3 Part 2 (cloner) requires Phase 3 Part 1 output files"
                f"\n  that contain a 'feature_file_count' column."
                f"\n"
                f"\n  Steps:"
                f"\n    1. Run count_features.py on your Phase 2 CSVs."
                f"\n    2. Pass the resulting output directory as --input here."
                f"\n    3. Default input folder: phase-3-output-files/"
                f"\n"
            )
            all_ok = False
    return all_ok


def _extract_method_name(path: Path) -> str:
    """
    Derive a short method name from an input CSV filename.

    Examples:
      github-global-search-output-phase-2-output-refiner_...  →  github-global-search
      github-search-tool-output-phase-2-output-refiner_...    →  github-search-tool
      seart-tool-output-phase-2-output-refiner_...            →  seart-tool
    """
    stem = path.stem
    idx = stem.find("-output")
    if idx > 0:
        return stem[:idx]
    idx2 = stem.find("_")
    return stem[:idx2] if idx2 > 0 else stem


def _collect_input_files(input_path: Path) -> List[Path]:
    """
    Return qualifying CSV input files, excluding any produced by this script.
    """
    if input_path.is_file() and input_path.suffix.lower() == ".csv":
        stem = input_path.stem
        if OUTPUT_SUFFIX in stem or LESS10_SUFFIX in stem:
            return []
        return [input_path]
    files = sorted(input_path.glob("*.csv"))
    return [
        f for f in files
        if OUTPUT_SUFFIX not in f.stem and LESS10_SUFFIX not in f.stem
    ]


def _dest_dir(method_dir: Path, full_name: str) -> Path:
    """Clone destination: <method_dir>/<owner>-<repo>."""
    parts = full_name.split("/", 1)
    owner = parts[0] if parts else "unknown"
    repo  = parts[1] if len(parts) > 1 else "unknown"
    return method_dir / f"{owner}-{repo}"


def _is_non_empty_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        next(path.iterdir())
        return True
    except StopIteration:
        return False


def _dir_size_gb(path: Path) -> float:
    """
    Return the actual disk footprint of a directory in GB.

    Uses 'du -s --block-size=1' (POSIX bytes, no apparent-size trick) which
    reports the ALLOCATED disk blocks, matching what a file manager shows.
    Falls back to 'du -sb' (apparent bytes) if the block-size flag is absent.
    """
    if not path.exists():
        return 0.0
    # Try allocated-block measurement first (most accurate)
    for cmd in (
        ["du", "-s", "--block-size=1", str(path)],
        ["du", "-sb", str(path)],
    ):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(None, 1)
                if parts:
                    return int(parts[0]) / (1024 ** 3)
        except Exception:
            continue
    return 0.0


# ==============================================================================
# Git cloner
# ==============================================================================

def clone_repo(
    clone_url:     str,
    dest:          Path,
    full_clone:    bool,
    force_reclone: bool,
    logger:        logging.Logger,
    on_progress:   Optional[Callable[[], None]] = None,
    ssl_verify:    bool = True,
) -> CloneResult:
    if not force_reclone and _is_non_empty_dir(dest):
        logger.debug("SKIP %s - already cloned at %s", clone_url, dest)
        return CloneResult(status="skipped", path=str(dest), cloned_at=_now_iso())

    if force_reclone and dest.exists():
        shutil.rmtree(dest, ignore_errors=True)

    depth_flag  = [] if full_clone else ["--depth", "1"]
    ssl_flag    = [] if ssl_verify else ["-c", "http.sslVerify=false"]
    cmd = ["git"] + ssl_flag + ["clone", "--single-branch"] + depth_flag + [clone_url, str(dest)]

    backoff  = BACKOFF_BASE
    last_err = ""
    for attempt in range(1, CLONE_MAX_RETRIES + 1):
        if _shutdown.is_set():
            return CloneResult(
                status="failed", cloned_at=_now_iso(), error="interrupted"
            )

        t0 = time.time()
        _active: Optional[subprocess.Popen] = None
        stderr_lines: List[str] = []

        def _drain_stderr(pipe: Any, buf: List[str]) -> None:
            """Background thread: drain stderr so the pipe buffer never fills."""
            try:
                for line in pipe:
                    buf.append(line)
            except Exception:
                pass

        try:
            _active = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            with _proc_lock:
                _proc_state["current"] = _active

            # Drain stderr in a background thread so the OS pipe buffer never
            # fills and blocks git (can happen for large-repo progress output).
            drain = threading.Thread(
                target=_drain_stderr,
                args=(_active.stderr, stderr_lines),
                daemon=True,
                name="stderr-drain",
            )
            drain.start()

            # Poll every 1 s: handle shutdown, timeout, and bar refreshes
            deadline = time.time() + CLONE_TIMEOUT
            while _active.poll() is None:
                if _shutdown.is_set():
                    _active.terminate()
                    try:
                        _active.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        _active.kill()
                    break
                if time.time() >= deadline:
                    _active.kill()
                    try:
                        _active.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    elapsed = round(time.time() - t0, 2)
                    last_err = f"timeout after {elapsed}s"
                    logger.warning(
                        "Timeout cloning %s after %.1fs (attempt %d)",
                        clone_url, elapsed, attempt,
                    )
                    if dest.exists():
                        shutil.rmtree(dest, ignore_errors=True)
                    drain.join(timeout=3)
                    # fall through to retry logic at bottom of attempt loop
                    break
                if on_progress:
                    on_progress()   # refresh all bars – keeps timers live
                time.sleep(1.0)

            drain.join(timeout=5)
            elapsed = round(time.time() - t0, 2)

            if _shutdown.is_set():
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                return CloneResult(
                    status="failed", cloned_at=_now_iso(), error="interrupted"
                )

            # Process timed out (we already set last_err above) – skip to retry
            if _active.returncode is None:
                if attempt < CLONE_MAX_RETRIES and not _shutdown.is_set():
                    time.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                continue

            if _active.returncode == 0:
                logger.info("CLONED %s -> %s  (%.1fs)", clone_url, dest, elapsed)
                return CloneResult(
                    status="success", path=str(dest),
                    time_s=elapsed, cloned_at=_now_iso(),
                )

            last_err = "".join(stderr_lines).strip()
            # Detect SSL interception (corporate/university proxy) and surface
            # a clear, actionable message instead of the raw git error.
            if "SSL" in last_err and "certificate subject name" in last_err:
                _ssl_warn_once(logger)
            logger.warning(
                "Attempt %d/%d failed for %s: %s",
                attempt, CLONE_MAX_RETRIES, clone_url, last_err,
            )
            if attempt < CLONE_MAX_RETRIES and not _shutdown.is_set():
                logger.info(
                    "Retry %d/%d for %s in %ds",
                    attempt, CLONE_MAX_RETRIES, clone_url, backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)

        except FileNotFoundError:
            with _proc_lock:
                _proc_state["current"] = None
            return CloneResult(
                status="failed", cloned_at=_now_iso(),
                error="git not found - is git installed?",
            )
        finally:
            with _proc_lock:
                if _proc_state.get("current") is _active:
                    _proc_state["current"] = None

    return CloneResult(
        status="failed", cloned_at=_now_iso(),
        error=last_err[:200] or "max retries exhausted",
    )


# ==============================================================================
# Core file processor
# ==============================================================================

def _process_file(
    input_path:    Path,
    method_dir:    Path,
    output_dir:    Path,          # used for accurate whole-tree disk measurement
    ckpt:          CheckpointManager,
    logger:        logging.Logger,
    overall_bar:   "tqdm[Any]",   # cumulative rows across all files
    disk_bar:      "tqdm[Any]",
    file_bar:      "tqdm[Any]",   # this file's progress bar; pre-reset by main()
    full_clone:    bool = True,
    force_reclone: bool = False,
    max_repos:     Optional[int] = None,
    ssl_verify:    bool = True,
) -> FileStats:
    """
    Process one input CSV.  All bars are owned by the caller (main).

    Disk usage is measured by running 'du' on the entire output_dir after
    each successful or skipped clone, giving an accurate live reading.

    All output is isolated inside ``method_dir``:
      Cloned repos   →  method_dir / "<owner>-<repo>/"
      Main CSV       →  method_dir / "<stem>_phase-3-output-feature-cloner.csv"
      Less-10 CSV    →  method_dir / "<stem>_feature-less-10.csv"
    """
    stats = FileStats(
        filename=input_path.name,
        method_name=method_dir.name,
        method_dir=str(method_dir),
        started_at=_now_iso(),
    )
    fieldnames, rows = _read_csv(input_path)
    if not fieldnames:
        logger.error("Cannot read %s - empty or unreadable", input_path)
        return stats

    stats.total_rows = len(rows)

    out_fields = fieldnames.copy()
    for col in NEW_COLUMNS:
        if col not in out_fields:
            out_fields.append(col)

    method_dir.mkdir(parents=True, exist_ok=True)
    out_path  = _output_path_for(input_path, method_dir)
    less_path = _less10_path_for(input_path, method_dir)

    file_key    = input_path.name
    resume_from = ckpt.get_resume_index(file_key)
    if resume_from > 0:
        logger.info("Resuming %s from row %d", input_path.name, resume_from)

    write_mode   = "a" if resume_from > 0 and out_path.exists() else "w"
    write_header = (write_mode == "w")

    out_fh = open(out_path, write_mode, encoding="utf-8", newline="")
    writer = csv.DictWriter(
        out_fh, fieldnames=out_fields, extrasaction="ignore", quoting=csv.QUOTE_ALL
    )
    if write_header:
        writer.writeheader()

    less_is_new = not less_path.exists() or less_path.stat().st_size == 0
    less_fh     = open(less_path, "a", encoding="utf-8", newline="")
    less_writer = csv.DictWriter(
        less_fh, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_ALL
    )
    if less_is_new:
        less_writer.writeheader()

    cloned_this_file = 0
    clones_since_measure = 0   # tracks when to re-measure disk

    # Callback passed to clone_repo(): called every ~1 s during git clone so
    # that all bar timers stay live without a background refresh thread.
    def _on_progress() -> None:
        overall_bar.refresh()
        disk_bar.refresh()
        file_bar.refresh()

    try:
        for idx, row in enumerate(rows):
            if _shutdown.is_set():
                ckpt.mark_done(file_key, max(idx - 1, 0), "interrupted")
                break

            # Skip already-processed rows when resuming (advance bar instantly)
            if idx < resume_from:
                file_bar.update(1)
                overall_bar.update(1)
                continue

            full_name = _get_full_name(row)
            result: CloneResult

            if not _is_qualified(row):
                result = CloneResult(status="filtered_out", cloned_at=_now_iso())
                stats.filtered_out += 1
                less_writer.writerow(row)
                less_fh.flush()
                logger.debug(
                    "Row %d/%d  %-50s  filtered_out (feature_count<10)",
                    idx + 1, stats.total_rows, full_name,
                )

            elif max_repos is not None and cloned_this_file >= max_repos:
                result = CloneResult(
                    status="filtered_out", cloned_at=_now_iso(),
                    error="max_repos limit reached",
                )
                stats.filtered_out += 1

            else:
                stats.qualified += 1
                clone_url = _get_clone_url(row)
                dest      = _dest_dir(method_dir, full_name)

                if not clone_url:
                    result = CloneResult(
                        status="failed", cloned_at=_now_iso(),
                        error="no clone URL found in row",
                    )
                    stats.failed += 1
                    logger.error("Row %d  %-50s  no clone URL", idx + 1, full_name)

                else:
                    result = clone_repo(
                        clone_url, dest, full_clone, force_reclone, logger,
                        on_progress=_on_progress,
                        ssl_verify=ssl_verify,
                    )

                    if result.status == "success":
                        stats.cloned_ok += 1
                        cloned_this_file += 1
                    elif result.status == "skipped":
                        stats.skipped += 1
                        cloned_this_file += 1
                    else:
                        stats.failed += 1

                    logger.info(
                        "Row %d/%d  %-50s  status=%s  time=%.2fs",
                        idx + 1, stats.total_rows, full_name,
                        result.status, result.time_s,
                    )
                    ckpt.mark_repo_done(full_name, result.status)

                    # ── Disk-space accounting (measure actual output tree) ──
                    if result.status in ("success", "skipped"):
                        clones_since_measure += 1
                        if clones_since_measure >= DISK_MEASURE_EVERY:
                            clones_since_measure = 0
                            # Measure the entire output directory for accuracy.
                            # This uses actual allocated blocks, matching what
                            # a file manager reports.
                            actual_gb = _dir_size_gb(output_dir)
                            disk_bar.n = actual_gb
                            disk_bar.refresh()

                            warn_threshold = DISK_THRESHOLD_GB * DISK_WARN_PCT / 100
                            if actual_gb >= DISK_THRESHOLD_GB:
                                tqdm.write(
                                    f"\n  DISK LIMIT REACHED  "
                                    f"{actual_gb:.2f} GB >= {DISK_THRESHOLD_GB:.1f} GB "
                                    f"threshold – stopping to protect disk.\n"
                                    f"  Increase DISK_THRESHOLD_GB env var to allow more."
                                )
                                logger.error(
                                    "Disk threshold reached: %.2f GB >= %.1f GB – halting",
                                    actual_gb, DISK_THRESHOLD_GB,
                                )
                                _shutdown.set()
                            elif actual_gb >= warn_threshold:
                                tqdm.write(
                                    f"  DISK WARNING  {actual_gb:.2f} GB used "
                                    f"({100 * actual_gb / DISK_THRESHOLD_GB:.0f}% of "
                                    f"{DISK_THRESHOLD_GB:.1f} GB threshold)"
                                )
                                logger.warning(
                                    "Disk at %.0f%%: %.2f GB of %.1f GB",
                                    100 * actual_gb / DISK_THRESHOLD_GB,
                                    actual_gb, DISK_THRESHOLD_GB,
                                )

            # Write enriched row to output CSV
            enriched = dict(row)
            enriched["clone_status"] = result.status
            enriched["clone_path"]   = result.path
            enriched["clone_time_s"] = str(result.time_s)
            enriched["cloned_at"]    = result.cloned_at
            enriched["clone_error"]  = result.error
            writer.writerow(enriched)
            out_fh.flush()
            stats.rows_written += 1

            file_bar.update(1)
            overall_bar.update(1)

            if (idx + 1) % CKPT_INTERVAL == 0:
                ckpt.mark_done(file_key, idx, full_name)

    finally:
        out_fh.close()
        less_fh.close()
        # file_bar is owned by main() – do NOT close it here

    if not _shutdown.is_set():
        ckpt.mark_done(file_key, len(rows) - 1, "")

    stats.finished_at = _now_iso()

    logger.info(
        "File done: %s  total=%d  qualified=%d  cloned=%d  skipped=%d  "
        "failed=%d  filtered=%d  written=%d",
        input_path.name, stats.total_rows, stats.qualified,
        stats.cloned_ok, stats.skipped, stats.failed,
        stats.filtered_out, stats.rows_written,
    )
    return stats


# ==============================================================================
# Row-count detector
# ==============================================================================

def detect_input_row_counts(files: List[Path]) -> List[Tuple[str, int, int]]:
    """Return list of (filename, total_rows, qualified_rows)."""
    result: List[Tuple[str, int, int]] = []
    for f in files:
        try:
            _, rows = _read_csv(f)
            total     = len(rows)
            qualified = sum(1 for r in rows if _is_qualified(r))
            result.append((f.name, total, qualified))
        except Exception:
            result.append((f.name, 0, 0))
    return result


# ==============================================================================
# Summary (uses tqdm.write so it never corrupts bar display)
# ==============================================================================

def _log_summary(
    stats_list: List[FileStats],
    output_dir: Path,
    logger:     logging.Logger,
) -> None:
    total_cloned  = sum(s.cloned_ok    for s in stats_list)
    total_skipped = sum(s.skipped      for s in stats_list)
    total_failed  = sum(s.failed       for s in stats_list)
    total_filter  = sum(s.filtered_out for s in stats_list)
    total_rows    = sum(s.total_rows   for s in stats_list)

    lines = [
        "",
        "=" * 72,
        "SUMMARY  -  Phase 3 Part 2 : Cloner",
        "=" * 72,
        f"  Finished at      : {_now_iso()}",
        f"  Output root      : {output_dir}",
        f"  Total rows       : {total_rows}",
        f"  Cloned OK        : {total_cloned}",
        f"  Skipped (exists) : {total_skipped}",
        f"  Failed           : {total_failed}",
        f"  Filtered out     : {total_filter}  (feature_count < 10)",
        "",
    ]
    for s in stats_list:
        mdir = Path(s.method_dir) if s.method_dir else output_dir
        out  = _output_path_for(Path(s.filename), mdir)
        less = _less10_path_for(Path(s.filename), mdir)
        lines += [
            f"  File            : {s.filename}",
            f"    Method        : {s.method_name}",
            f"    Method dir    : {mdir}",
            f"    Rows total    : {s.total_rows}",
            f"    Qualified     : {s.qualified}",
            f"    Cloned OK     : {s.cloned_ok}",
            f"    Skipped       : {s.skipped}",
            f"    Failed        : {s.failed}",
            f"    Filtered out  : {s.filtered_out}",
            f"    Rows written  : {s.rows_written}",
            f"    Started       : {s.started_at}",
            f"    Finished      : {s.finished_at}",
            f"    Output CSV    : {out}",
            f"    Less-10 CSV   : {less}",
            "",
        ]
    lines.append("=" * 72)

    summary_text = "\n".join(lines)
    # tqdm.write ensures the summary appears BELOW all bars without corrupting them
    tqdm.write(summary_text)
    for line in lines:
        logger.info(line)


# ==============================================================================
# Interactive menu
# ==============================================================================

def interactive_menu(
    default_input_dir:  Path,
    default_output_dir: Path,
) -> Tuple[Path, Path, Optional[int], bool, bool]:
    """
    Returns (input_path, output_dir, max_repos, full_clone, force_reclone).
    """
    print()
    print("+=============================================================+")
    print("|   Phase 3 - Part 2 : Repository Cloner                     |")
    print("+-------------------------------------------------------------+")
    print("|  1. Use default Phase 3 Part 1 output folder  (default)    |")
    print("|  2. Use a specific CSV file                                 |")
    print("|  3. Use a specific directory of CSV files                   |")
    print("+=============================================================+")

    choice = input("Select option [1/2/3] (default=1): ").strip() or "1"

    if choice == "2":
        raw = input("  CSV file path: ").strip()
        input_path = Path(raw).expanduser().resolve()
    elif choice == "3":
        raw = input("  Directory path: ").strip()
        input_path = Path(raw).expanduser().resolve()
    else:
        input_path = default_input_dir

    if not input_path.exists():
        print(f"\n  ERROR  Path not found: {input_path}\n")
        sys.exit(1)

    files = _collect_input_files(input_path)
    if not files:
        print(
            "  ERROR  No qualifying CSV files found.\n"
            "     (Existing output files and feature-less-10 files are excluded.)"
        )
        sys.exit(1)

    counts = detect_input_row_counts(files)
    print(f"\n  Detected {len(counts)} input file(s):")
    for name, total, qual in counts:
        method = _extract_method_name(Path(name))
        print(f"    * {name}")
        print(f"      method: {method}  |  {total} rows total,  {qual} qualify for cloning")

    print(f"\n  Default output root : {default_output_dir}")
    print("  Each method gets its own sub-folder inside the output root.")
    raw_out = input("  Output root directory (blank = use default): ").strip()
    output_dir = Path(raw_out).expanduser().resolve() if raw_out else default_output_dir

    raw_max = input("\n  Max repos to clone per file (blank = unlimited): ").strip()
    max_repos = int(raw_max) if raw_max.isdigit() else None

    raw_full = input(
        "  Full clone? [Y/n]  (recommended - complete history for analysis): "
    ).strip().lower()
    full_clone = raw_full not in ("n", "no")

    raw_force = input("  Force re-clone existing directories? [y/N]: ").strip().lower()
    force_reclone = raw_force in ("y", "yes")

    return input_path, output_dir, max_repos, full_clone, force_reclone


# ==============================================================================
# Argument parser
# ==============================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Phase 3 Part 2 – Clone repos with feature_file_count >= 10. "
            "Each input CSV gets its own method sub-folder inside --output."
        )
    )
    p.add_argument("--input",  help="Phase 3 Part 1 CSV or directory of CSVs")
    p.add_argument(
        "--output",
        help=(
            "Root output directory.  Each method gets a sub-folder here "
            "containing its cloned repos and result CSVs."
        ),
    )
    p.add_argument("--max-repos",     type=int, help="Clone at most this many repos per file")
    p.add_argument("--full-clone",    action="store_true", help="Full clone (default)")
    p.add_argument("--force-reclone", action="store_true", help="Re-clone existing dirs")
    p.add_argument(
        "--no-ssl-verify",
        action="store_true",
        help=(
            "Disable SSL certificate verification for git clone.  "
            "Use this when behind a corporate/university SSL-inspection proxy "
            "that presents its own certificate instead of GitHub's."
        ),
    )
    return p.parse_args()


# ==============================================================================
# Entry point
# ==============================================================================

def main() -> int:
    args = parse_args()
    _install_sigint_handler()

    default_in_dir = Path(
        "/home/vaishnavkoka/RE4BDD/Phase-workout-for-bdd"
        "/phase-3 (refinement)/phase-3-output-files"
    )
    default_out_dir = Path(
        "/home/vaishnavkoka/RE4BDD/Phase-workout-for-bdd"
        "/phase-3 (refinement)/phase-3-cloner-output"
    )

    if args.input:
        input_path    = Path(args.input).expanduser().resolve()
        output_dir    = Path(args.output).expanduser().resolve() if args.output else default_out_dir
        max_repos     = args.max_repos
        full_clone    = args.full_clone or FULL_CLONE_DEFAULT
        force_reclone = args.force_reclone
        ssl_verify    = not args.no_ssl_verify
    else:
        (input_path, output_dir,
         max_repos, full_clone, force_reclone) = interactive_menu(
            default_in_dir, default_out_dir
        )
        ssl_verify = True

    output_dir.mkdir(parents=True, exist_ok=True)
    logger = _setup_logger(output_dir)
    logger.info("=" * 60)
    logger.info("Phase 3 Part 2 (Cloner) started at %s", _now_iso())
    logger.info("input=%s  output_root=%s", input_path, output_dir)
    logger.info(
        "max_repos=%s  full_clone=%s  force_reclone=%s  ssl_verify=%s "
        "disk_threshold=%.1fGB  disk_warn_pct=%.0f%%",
        max_repos, full_clone, force_reclone, ssl_verify, DISK_THRESHOLD_GB, DISK_WARN_PCT,
    )

    input_files = _collect_input_files(input_path)
    if not input_files:
        msg = (
            f"No qualifying CSV files found at {input_path}.\n"
            "(Output files and feature-less-10 files are excluded automatically.)"
        )
        print(f"ERROR: {msg}", file=sys.stderr)
        logger.error(msg)
        return 1

    # Validate input file columns BEFORE doing anything else so the user gets
    # a clear diagnostic if they accidentally passed Phase 2 files instead of
    # Phase 3 Part 1 output.
    if not _check_input_columns(input_files):
        return 1

    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        print("ERROR: git is not installed or not in PATH.", file=sys.stderr)
        return 1

    # Checkpoint
    ckpt_path = output_dir / CHECKPOINT_FILE
    ckpt      = CheckpointManager(ckpt_path, logger)
    if ckpt.exists():
        # Warn if the checkpoint was created with different input files
        mismatch_msg = ckpt.check_inputs(input_files)
        if mismatch_msg:
            print(mismatch_msg)
        print(f"\n  Checkpoint found: {ckpt_path}")
        ans = input("  Resume from last saved position? [Y/n]: ").strip().lower()
        if ans in ("n", "no"):
            ckpt.clear()
            logger.info("Checkpoint cleared – starting fresh")
            print("  Starting fresh.\n")
        else:
            if mismatch_msg:
                logger.warning("Resuming checkpoint with mismatched input files")
            logger.info("Resuming from checkpoint %s", ckpt_path)
            print("  Resuming.\n")

    # Pre-calculate row counts for all files (needed for total_bar and per-file bars)
    method_dirs: Dict[Path, Path] = {}
    file_row_counts: Dict[Path, int] = {}
    total_qualified = 0
    for inp in input_files:
        method_dirs[inp] = output_dir / _extract_method_name(inp)
        _, rows = _read_csv(inp)
        file_row_counts[inp] = len(rows)
        q = sum(1 for r in rows if _is_qualified(r))
        total_qualified += min(q, max_repos) if max_repos else q

    total_rows_all = sum(file_row_counts.values())

    if total_qualified == 0:
        print(
            "\n  Nothing to clone – no rows with feature_file_count >= 10."
            "\n  Possible causes:"
            "\n    * All repos were filtered out (feature count < 10 in ALL rows)."
            "\n    * Wrong input directory – Phase 2 files were passed instead of"
            "\n      Phase 3 Part 1 output.  Re-run and choose the folder produced"
            "\n      by count_features.py (default: phase-3-output-files/)."
            "\n"
        )
        logger.info("total_qualified=0 – nothing to clone")
        return 0

    # Measure existing disk usage before starting (shows correct initial value)
    initial_disk_gb = _dir_size_gb(output_dir)

    # Banner (printed before bars are created)
    print(f"\n{'─'*70}")
    print(f"  Phase 3 – Part 2 : Repository Cloner")
    print(f"  Started      : {_now_iso()}")
    print(f"  Input        : {input_path}")
    print(f"  Output root  : {output_dir}")
    print(f"  Disk limit   : {DISK_THRESHOLD_GB:.1f} GB  (warn at {DISK_WARN_PCT:.0f}%)")
    print(f"  Mode         : {'full clone' if full_clone else 'shallow (--depth 1)'}")
    print(f"  Log          : {output_dir / LOG_FILE_NAME}")
    print(f"  Sub-folders  :")
    for inp, mdir in method_dirs.items():
        print(f"    {mdir.name}/  ←  {inp.name}")
    print(f"  Repos to clone : {total_qualified}")
    print(f"{'─'*70}\n")

    logger.info("Repos to clone: %d across %d file(s)", total_qualified, len(input_files))

    # Persist the input file list in checkpoint so resume can detect mismatches
    ckpt.store_inputs(input_files)

    # ── Create progress bars ───────────────────────────────────────────────────
    #
    # Bar layout (2 + N bars, N = number of input files):
    #
    #   position=0  cyan    – Overall rows (cumulative across all files) + ETA
    #   position=1  yellow  – Disk used (real-time)
    #   position=2  …       – File 1 per-row progress
    #   position=3  …       – File 2 per-row progress   (if N >= 2)
    #   …
    #
    # All bars have leave=True so they remain visible after completion.
    # smoothing=0.1 gives a stable, non-jittery ETA.
    # mininterval=1.0 limits each bar to redrawing at most once per second;
    # this prevents cursor-flicker when many filtered rows are processed quickly
    # (without it every update() causes visible cursor up/down movement).
    #
    # No background refresh thread is needed.  Instead, clone_repo() uses a
    # 1-second poll loop and calls an on_progress() callback each iteration,
    # which refresh()es all three relevant bars, keeping timers live even
    # during long git-clone operations.
    #
    # tqdm renders position=0 at the BOTTOM of the multi-bar group and higher
    # position numbers progressively higher up the screen.  To get:
    #
    #   Overall     ← TOP    (position = n_files + 1)
    #   Disk used            (position = n_files)
    #   file 1               (position = n_files - 1)
    #   …
    #   file N      ← BOTTOM (position = 0)
    #
    # tqdm position=0 → TOP of terminal display; higher positions go DOWNWARD.
    # Layout (top→bottom): Overall (0), Disk (1), file[0] (2), file[1] (3), …
    n_files = len(input_files)

    overall_bar = tqdm(
        total=total_rows_all,
        desc="Overall    ",
        position=0,                 # TOP
        leave=True,
        colour="cyan",
        smoothing=0.1,
        mininterval=1.0,
        dynamic_ncols=True,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} rows  ETA {remaining}  [{elapsed}]",
    )

    disk_bar = tqdm(
        total=DISK_THRESHOLD_GB,
        desc="Disk used  ",
        position=1,                 # below Overall
        leave=True,
        colour="yellow",
        smoothing=0.1,
        mininterval=1.0,
        dynamic_ncols=True,
        bar_format="{l_bar}{bar}| {n:.2f}/{total:.1f} GB  [{elapsed}]  (limit={total:.1f} GB)",
    )
    # Seed with actual current usage so the bar is correct from the first moment
    disk_bar.n = initial_disk_gb
    disk_bar.refresh()

    # One bar per input file – all created upfront so they are all visible
    # simultaneously.  Each bar is reset() just before its file is processed
    # so that elapsed/ETA reflect only that file's active processing time.
    # file[0] gets position=2 (just below Disk); file[N-1] gets the bottom slot.
    file_bars: List[Any] = []
    for i, inp in enumerate(input_files):
        color = FILE_BAR_COLORS[i % len(FILE_BAR_COLORS)]
        fbar = tqdm(
            total=file_row_counts[inp],
            desc=f"  {inp.name[:40]:<40}",
            position=2 + i,         # 2, 3, 4, … → below Disk, increasing downward
            leave=True,
            colour=color,
            smoothing=0.1,
            mininterval=1.0,
            dynamic_ncols=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} rows  [{elapsed}<{remaining}]",
        )
        file_bars.append(fbar)

    # ── Main processing loop ───────────────────────────────────────────────────
    all_stats: List[FileStats] = []
    try:
        for i, inp in enumerate(input_files):
            if _shutdown.is_set():
                tqdm.write(
                    f"  WARNING  Skipping remaining {len(input_files) - i} file(s) – "
                    "checkpoint saved."
                )
                break
            logger.info("Processing file %d/%d: %s", i + 1, len(input_files), inp.name)

            # Reset this file's bar so elapsed starts from 0 for this file.
            file_bars[i].reset()

            stats = _process_file(
                input_path    = inp,
                method_dir    = method_dirs[inp],
                output_dir    = output_dir,
                ckpt          = ckpt,
                logger        = logger,
                overall_bar   = overall_bar,
                disk_bar      = disk_bar,
                file_bar      = file_bars[i],
                full_clone    = full_clone,
                force_reclone = force_reclone,
                max_repos     = max_repos,
                ssl_verify    = ssl_verify,
            )
            all_stats.append(stats)
    finally:
        # Close from bottom to top (highest position first) so that the cursor
        # ends up at position=0 (Overall, top) after all bars are finalised.
        for fb in reversed(file_bars):
            fb.close()
        disk_bar.close()
        overall_bar.close()

    if _shutdown.is_set():
        tqdm.write("\n  Partial run complete – checkpoint saved. Re-run to resume.")
        logger.info("Run interrupted – checkpoint saved at %s", ckpt_path)

    _log_summary(all_stats, output_dir, logger)
    logger.info("Phase 3 Part 2 finished at %s", _now_iso())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
