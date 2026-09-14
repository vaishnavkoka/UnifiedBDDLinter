#!/usr/bin/env python3
"""
Phase 3 – Part 1: Feature File Counter
=======================================
For every repository row in a Phase 2 output CSV, queries the GitHub API to
count how many .feature files the repo contains, then writes an enriched CSV
with two new columns appended:

    feature_file_count   – total number of .feature files found
    has_min_features     – True / False  (True when count >= MIN_FEATURE_THRESHOLD)

The enriched file is used downstream by Phase 3 Part 2 (the Cloner/Refiner),
which clones only the repos where has_min_features == True.

Key design decisions
--------------------
  • Menu-driven (Phase 2 style): no mandatory CLI flags, but CLI flags are still
    accepted for automation / pipeline use.
  • Each input CSV is processed independently; output naming is
    {stem}_phase-3-feature-count-output.csv.
  • Five tqdm bars (pinned at top), each a different colour:
      pos=0  Overall Rows    (green)
      pos=1  Files Done      (cyan)
      pos=2  Current File    (yellow)
      pos=3  API Rate Limit  (magenta)
      pos=4  Rows Written    (blue)
  • Logger  → "phase-3-insertion.log"
  • Checkpoint → "phase-3-insertion-checkpoint.json"  (saved every CKPT_INTERVAL rows)
  • API call tracking is always live – bootstrapped once from /rate_limit on
    startup, then decremented in-process; never uses hardcoded values.
  • ISO 8601 UTC timestamps throughout.
  • Every row reports how many API calls it consumed.
  • Sample-test mode: processes 3 rows, prints a tabular result, then asks
    whether to continue with the full run.
"""

from __future__ import annotations

# ── stdlib ──────────────────────────────────────────────────────────────────────
import argparse
import csv
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── third-party ─────────────────────────────────────────────────────────────────
try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed.  Run: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from tqdm import tqdm
except ImportError:
    print("ERROR: 'tqdm' not installed.  Run: pip install tqdm", file=sys.stderr)
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════════

LOG_FILE_NAME       = "phase-3-insertion.log"
CHECKPOINT_FILE     = "phase-3-insertion-checkpoint.json"
OUTPUT_SUFFIX       = "phase-3-feature-count-output"

MIN_FEATURE_THRESHOLD = int(os.environ.get("MIN_FEATURE_THRESHOLD", "10"))
CKPT_INTERVAL         = int(os.environ.get("CKPT_INTERVAL", "10"))
REQUEST_TIMEOUT       = int(os.environ.get("REQUEST_TIMEOUT", "20"))
RATE_LIMIT_SAFETY     = int(os.environ.get("RATE_LIMIT_SAFETY", "50"))   # pause below this
MAX_BACKOFF           = int(os.environ.get("MAX_BACKOFF", "300"))

GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"

# ── tqdm colour codes ────────────────────────────────────────────────────────────
BAR_OVERALL   = {"colour": "green",   "position": 0, "leave": True,  "dynamic_ncols": True}
BAR_FILES     = {"colour": "cyan",    "position": 1, "leave": True,  "dynamic_ncols": True}
BAR_CURRENT   = {"colour": "yellow",  "position": 2, "leave": True,  "dynamic_ncols": True}
BAR_RATELIMIT = {"colour": "magenta", "position": 3, "leave": True,  "dynamic_ncols": True}
BAR_WRITTEN   = {"colour": "white",   "position": 4, "leave": True,  "dynamic_ncols": True}

# ── output columns added by this script ─────────────────────────────────────────
NEW_COLUMNS = ["feature_file_count", "has_min_features"]


# ══════════════════════════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════════════════════════

def _setup_logger(log_dir: Path) -> logging.Logger:
    log_path = log_dir / LOG_FILE_NAME
    logger = logging.getLogger("phase3_feature_counter")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    # rotating file handler: 10 MB × 5 backups
    fh = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # stream handler for warnings+
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


# ══════════════════════════════════════════════════════════════════════════════════
# Data-classes
# ══════════════════════════════════════════════════════════════════════════════════

@dataclass
class RateState:
    """Live GitHub API rate-limit state.  Never hardcoded – always fetched."""
    limit:     int = 5000
    remaining: int = 5000
    reset_ts:  int = 0          # Unix timestamp
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, limit: int, remaining: int, reset_ts: int) -> None:
        with self._lock:
            self.limit     = limit
            self.remaining = remaining
            self.reset_ts  = reset_ts

    def decrement(self, n: int = 1) -> None:
        with self._lock:
            self.remaining = max(0, self.remaining - n)

    @property
    def reset_utc(self) -> str:
        return datetime.fromtimestamp(self.reset_ts, tz=timezone.utc).strftime("%H:%M:%S UTC")

    @property
    def bar_desc(self) -> str:
        return f"API remaining={self.remaining}/{self.limit} reset={self.reset_utc}"


@dataclass
class FileStats:
    """Per-input-file processing statistics."""
    filename:         str
    total_rows:       int   = 0
    processed:        int   = 0
    skipped_ckpt:     int   = 0
    errors:           int   = 0
    api_calls_total:  int   = 0
    rows_written:     int   = 0
    started_at:       str   = ""
    finished_at:      str   = ""

    def api_calls_per_row(self) -> float:
        denom = max(self.processed - self.skipped_ckpt, 1)
        return round(self.api_calls_total / denom, 2)


# ══════════════════════════════════════════════════════════════════════════════════
# Progress Dashboard  (5 pinned tqdm bars)
# ══════════════════════════════════════════════════════════════════════════════════

class ProgressDashboard:
    """Five persistent tqdm bars rendered at fixed screen positions."""

    def __init__(self, total_rows: int, total_files: int, rate: RateState) -> None:
        self._rate = rate
        self.overall  = tqdm(total=total_rows,  desc="Overall Rows    ", **BAR_OVERALL,
                             bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]")
        self.files    = tqdm(total=total_files, desc="Files Completed ", **BAR_FILES,
                             bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}")
        self.current  = tqdm(total=1,           desc="Current File    ", **BAR_CURRENT,
                             bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} rows")
        self.ratelimit = tqdm(total=rate.limit, desc=rate.bar_desc,   **BAR_RATELIMIT,
                              bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} calls left")
        self.written  = tqdm(total=total_rows,  desc="Rows Written    ", **BAR_WRITTEN,
                             bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}")

    def start_file(self, filename: str, row_count: int) -> None:
        self.current.reset(total=row_count)
        self.current.set_description(f"File: {filename[:30]}")

    def tick_row(self) -> None:
        self.overall.update(1)
        self.current.update(1)

    def tick_written(self) -> None:
        self.written.update(1)

    def tick_file_done(self) -> None:
        self.files.update(1)

    def sync_rate(self) -> None:
        self.ratelimit.n = self._rate.remaining
        self.ratelimit.set_description(self._rate.bar_desc)
        self.ratelimit.refresh()

    def close(self) -> None:
        for bar in (self.overall, self.files, self.current, self.ratelimit, self.written):
            bar.close()


# ══════════════════════════════════════════════════════════════════════════════════
# GitHub API Client
# ══════════════════════════════════════════════════════════════════════════════════

class GitHubClient:
    """
    Thin GitHub REST client.

    API call accounting:
      • _call() increments the cumulative counter and decrements rate.remaining
        after EVERY real HTTP request.
      • bootstrap_rate_limit() is called once at startup to get the live
        remaining count – no hardcoded values.
    """

    def __init__(self, token: str, rate: RateState, logger: logging.Logger) -> None:
        self._token  = token
        self._rate   = rate
        self._logger = logger
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization":        f"Bearer {token}",
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent":           "phase3-feature-counter/1.0",
        })
        self._total_calls = 0   # session-lifetime counter

    @property
    def total_calls(self) -> int:
        return self._total_calls

    def _call(self, path: str, params: Optional[dict] = None) -> Tuple[int, Optional[Any], dict]:
        """
        Make one GET request.  Returns (status_code, json_body, response_headers).
        Increments internal and rate counters.  Handles 403/429 back-off.
        """
        url = f"{GITHUB_API}{path}"
        backoff = 5
        while True:
            try:
                resp = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                self._logger.warning("Network error %s %s: %s", path, params, exc)
                return -1, None, {}

            self._total_calls += 1
            self._rate.decrement(1)

            # refresh rate-limit from response headers
            hdrs = resp.headers
            try:
                self._rate.update(
                    int(hdrs.get("X-RateLimit-Limit",     self._rate.limit)),
                    int(hdrs.get("X-RateLimit-Remaining", self._rate.remaining)),
                    int(hdrs.get("X-RateLimit-Reset",     self._rate.reset_ts)),
                )
            except (ValueError, TypeError):
                pass

            if resp.status_code in (403, 429):
                retry_after = int(hdrs.get("Retry-After", backoff))
                self._logger.warning("Rate-limit hit. Sleeping %ds.", retry_after)
                tqdm.write(f"  ⏸  Rate-limit – sleeping {retry_after}s …")
                time.sleep(min(retry_after, MAX_BACKOFF))
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue

            if resp.status_code == 200:
                try:
                    return resp.status_code, resp.json(), dict(hdrs)
                except ValueError:
                    return resp.status_code, None, dict(hdrs)

            return resp.status_code, None, dict(hdrs)

    def bootstrap_rate_limit(self) -> None:
        """Fetch live rate-limit state. Called once at program start."""
        status, body, _ = self._call("/rate_limit")
        if status == 200 and isinstance(body, dict):
            core = body.get("resources", {}).get("core", {})
            self._rate.update(
                core.get("limit",     5000),
                core.get("remaining", 5000),
                core.get("reset",     0),
            )
            tqdm.write(
                f"\nGitHub API Rate Limit (live)\n"
                f"  Limit     : {self._rate.limit}\n"
                f"  Remaining : {self._rate.remaining}\n"
                f"  Reset UTC : {self._rate.reset_utc}\n"
            )
        else:
            self._logger.warning("Could not bootstrap rate-limit (status=%d).", status)

    def get_default_branch(self, full_name: str) -> Tuple[Optional[str], int]:
        """Returns (branch_name, api_calls_used).  1 API call."""
        status, body, _ = self._call(f"/repos/{full_name}")
        if status == 200 and isinstance(body, dict):
            return body.get("default_branch", "main"), 1
        return None, 1

    def count_feature_files(self, full_name: str, branch: str) -> Tuple[int, int, Optional[str]]:
        """
        Count .feature files via the Git Trees API (recursive).
        Returns (count, api_calls_used, error_message_or_None).
        1 API call.  Falls back to Contents API walk on truncated trees (+many calls).
        """
        status, body, _ = self._call(
            f"/repos/{full_name}/git/trees/{branch}",
            params={"recursive": "1"},
        )
        if status != 200 or not isinstance(body, dict):
            return 0, 1, f"tree API returned {status}"

        if body.get("truncated"):
            # large repo — fall back to contents walk (expensive)
            count, extra_calls = self._count_via_contents(full_name)
            return count, 1 + extra_calls, "truncated_tree"

        feature_count = sum(
            1 for item in body.get("tree", [])
            if item.get("type") == "blob" and item.get("path", "").endswith(".feature")
        )
        return feature_count, 1, None

    def _count_via_contents(self, full_name: str, path: str = "", depth: int = 0) -> Tuple[int, int]:
        """Recursive directory walk via Contents API. Used only on truncated trees."""
        if depth > 8:
            return 0, 0
        status, body, _ = self._call(f"/repos/{full_name}/contents/{path}")
        calls = 1
        if status != 200 or not isinstance(body, list):
            return 0, calls
        count = 0
        for item in body:
            if item.get("type") == "file" and item.get("name", "").endswith(".feature"):
                count += 1
            elif item.get("type") == "dir":
                sub_count, sub_calls = self._count_via_contents(full_name, item["path"], depth + 1)
                count += sub_count
                calls += sub_calls
        return count, calls

    def safety_wait_if_needed(self) -> None:
        """Pause execution if remaining calls drop below RATE_LIMIT_SAFETY."""
        if self._rate.remaining <= RATE_LIMIT_SAFETY:
            wait = max(0, self._rate.reset_ts - int(time.time())) + 5
            tqdm.write(f"  ⏸  Approaching rate limit ({self._rate.remaining} left). "
                       f"Sleeping {wait}s until reset …")
            time.sleep(wait)
            self.bootstrap_rate_limit()


# ══════════════════════════════════════════════════════════════════════════════════
# Checkpoint Manager
# ══════════════════════════════════════════════════════════════════════════════════

class CheckpointManager:
    """Persists progress to phase-3-insertion-checkpoint.json."""

    def __init__(self, ckpt_path: Path, logger: logging.Logger) -> None:
        self._path   = ckpt_path
        self._logger = logger
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._logger.info("Checkpoint loaded from %s", self._path)
                return data
            except (json.JSONDecodeError, OSError) as exc:
                self._logger.warning("Could not load checkpoint: %s", exc)
        return {}

    def save(self, file_key: str, last_index: int, api_calls: int) -> None:
        self._data[file_key] = {
            "last_index":  last_index,
            "api_calls":   api_calls,
            "updated_at":  _now_iso(),
        }
        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            tmp.replace(self._path)
        except OSError as exc:
            self._logger.error("Checkpoint write failed: %s", exc)

    def get_resume_index(self, file_key: str) -> int:
        """Returns the last saved row index + 1 (0 if no checkpoint)."""
        return self._data.get(file_key, {}).get("last_index", -1) + 1

    def get_saved_api_calls(self, file_key: str) -> int:
        return self._data.get(file_key, {}).get("api_calls", 0)

    def clear_file(self, file_key: str) -> None:
        self._data.pop(file_key, None)
        self.save.__func__(self, "__meta__", -1, 0)   # force a write


# ══════════════════════════════════════════════════════════════════════════════════
# CSV helpers
# ══════════════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read a CSV and return (fieldnames, rows)."""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return [], []
        fieldnames = list(reader.fieldnames)
        rows = list(reader)
    return fieldnames, rows


def _output_path_for(input_path: Path, output_dir: Path) -> Path:
    stem = input_path.stem
    return output_dir / f"{stem}_{OUTPUT_SUFFIX}.csv"


# ══════════════════════════════════════════════════════════════════════════════════
# Main pipeline
# ══════════════════════════════════════════════════════════════════════════════════

def _extract_full_name(row: Dict[str, str]) -> Optional[str]:
    """Try to get 'owner/repo' from various column naming conventions."""
    fn = row.get("full_name", "").strip()
    if fn and "/" in fn:
        return fn
    owner = row.get("owner", "").strip()
    for key in ("repo_name", "name", "repository"):
        repo = row.get(key, "").strip()
        if owner and repo:
            return f"{owner}/{repo}"
    return None


def _process_row(
    row: Dict[str, str],
    row_index: int,
    client: GitHubClient,
    logger: logging.Logger,
) -> Tuple[Dict[str, str], int, Optional[str]]:
    """
    Enrich one CSV row with feature_file_count and has_min_features.
    Returns (enriched_row, api_calls_used_this_row, error_or_None).
    """
    calls_before = client.total_calls
    full_name = _extract_full_name(row)
    enriched = dict(row)

    if not full_name:
        enriched["feature_file_count"] = ""
        enriched["has_min_features"]   = "false"
        return enriched, client.total_calls - calls_before, "no full_name"

    client.safety_wait_if_needed()

    # Step 1: get default branch (1 API call)
    branch, _ = client.get_default_branch(full_name)
    if branch is None:
        enriched["feature_file_count"] = ""
        enriched["has_min_features"]   = "false"
        logger.warning("Could not get branch for %s", full_name)
        return enriched, client.total_calls - calls_before, "branch_fetch_failed"

    # Step 2: count .feature files (1 API call, possibly more if tree truncated)
    count, _, err = client.count_feature_files(full_name, branch)

    enriched["feature_file_count"] = str(count)
    enriched["has_min_features"]   = "true" if count >= MIN_FEATURE_THRESHOLD else "false"

    api_this_row = client.total_calls - calls_before
    logger.debug(
        "row=%d  repo=%s  feature_count=%d  has_min=%s  api_calls_this_row=%d  cumulative_api=%d",
        row_index, full_name, count, enriched["has_min_features"], api_this_row, client.total_calls,
    )
    return enriched, api_this_row, err


def _process_file(
    input_path: Path,
    output_dir: Path,
    client: GitHubClient,
    ckpt: CheckpointManager,
    dash: ProgressDashboard,
    logger: logging.Logger,
    max_rows: Optional[int] = None,
    sample_mode: bool = False,
) -> FileStats:
    """Process one input CSV file end-to-end."""
    stats = FileStats(
        filename   = input_path.name,
        started_at = _now_iso(),
    )

    fieldnames, rows = _read_csv(input_path)
    if not fieldnames:
        logger.error("Could not read CSV: %s", input_path)
        return stats

    if max_rows:
        rows = rows[:max_rows]
    stats.total_rows = len(rows)

    out_path = _output_path_for(input_path, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    file_key    = input_path.name
    resume_from = ckpt.get_resume_index(file_key)
    if resume_from > 0:
        tqdm.write(f"  ↺  Resuming {input_path.name} from row {resume_from}")
        logger.info("Resuming %s from row index %d", input_path.name, resume_from)

    # Build output fieldnames (avoid duplicating new columns)
    out_fields = fieldnames.copy()
    for col in NEW_COLUMNS:
        if col not in out_fields:
            out_fields.append(col)

    # Open output CSV (append if resuming, write header only on fresh start)
    write_mode  = "a" if resume_from > 0 and out_path.exists() else "w"
    write_header = write_mode == "w"
    out_fh      = open(out_path, write_mode, encoding="utf-8", newline="")
    writer      = csv.DictWriter(out_fh, fieldnames=out_fields, extrasaction="ignore",
                                   quoting=csv.QUOTE_ALL)
    if write_header:
        writer.writeheader()

    dash.start_file(input_path.name, stats.total_rows)

    try:
        for idx, row in enumerate(rows):
            if idx < resume_from:
                stats.skipped_ckpt += 1
                dash.tick_row()
                continue

            enriched, api_this_row, err = _process_row(row, idx, client, logger)
            stats.processed    += 1
            stats.api_calls_total += api_this_row

            if err and err not in ("truncated_tree",):
                stats.errors += 1

            writer.writerow(enriched)
            out_fh.flush()
            stats.rows_written += 1

            dash.tick_row()
            dash.tick_written()
            dash.sync_rate()

            # Log per-row details at DEBUG level only (not shown in terminal)
            full_name = _extract_full_name(row) or "?"
            count_str = enriched.get("feature_file_count", "?")
            logger.debug(
                "row=%d  %s  feature_count=%s  api_this_row=%d  cumulative_api=%d%s",
                idx + 1, full_name, count_str, api_this_row, client.total_calls,
                f"  WARN:{err}" if err else "",
            )

            # Checkpoint every CKPT_INTERVAL rows
            if (idx + 1) % CKPT_INTERVAL == 0:
                ckpt.save(file_key, idx, client.total_calls)

            if sample_mode and stats.processed >= 3:
                break

    finally:
        out_fh.close()

    ckpt.save(file_key, len(rows) - 1, client.total_calls)
    dash.tick_file_done()
    stats.finished_at = _now_iso()
    return stats


# ══════════════════════════════════════════════════════════════════════════════════
# Sample test
# ══════════════════════════════════════════════════════════════════════════════════

def run_sample_test(
    input_files: List[Path],
    output_dir: Path,
    client: GitHubClient,
    logger: logging.Logger,
) -> None:
    """Process 3 rows from the first input file and print a tabular result."""
    if not input_files:
        print("No input files found for sample test.")
        return

    src = input_files[0]
    _, rows = _read_csv(src)
    sample_rows = rows[:3]

    print(f"\n{'='*80}")
    print(f"PHASE 3 (Part 1) – SAMPLE TEST   (3 rows from {src.name})")
    print(f"Timestamp : {_now_iso()}")
    print(f"Threshold : MIN_FEATURE_THRESHOLD = {MIN_FEATURE_THRESHOLD}")
    print(f"{'='*80}")
    print(f"{'#':<4} {'repo':<45} {'feature_count':>14} {'has_min':>8} {'api_calls':>10} {'cumulative':>11}")
    print("-" * 96)

    calls_before = client.total_calls
    for i, row in enumerate(sample_rows, 1):
        enriched, api_this_row, err = _process_row(row, i - 1, client, logger)
        fn  = _extract_full_name(row) or "?"
        cnt = enriched.get("feature_file_count", "?")
        has = enriched.get("has_min_features", "?")
        print(
            f"  {i:<4} {fn:<45} {cnt:>14} {has:>8} {api_this_row:>10} {client.total_calls:>11}"
            + (f"   WARN:{err}" if err else "")
        )
        print(f"       api_calls_this_row={api_this_row}  cumulative_api={client.total_calls}")

    print("-" * 96)
    print(f"Total API calls in sample: {client.total_calls - calls_before}")
    print(f"{'='*80}\n")


# ══════════════════════════════════════════════════════════════════════════════════
# Interactive Menu
# ══════════════════════════════════════════════════════════════════════════════════

def detect_input_row_counts(input_path: Path) -> List[Tuple[str, int]]:
    files: List[Path]
    if input_path.is_file() and input_path.suffix.lower() == ".csv":
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(input_path.glob("*.csv"))
    else:
        return []

    counts: List[Tuple[str, int]] = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                n = sum(1 for _ in reader) if reader.fieldnames else 0
                counts.append((f.name, n))
        except Exception:
            counts.append((f.name, 0))
    return counts


def interactive_menu(
    default_input_dir: Path,
    default_output_dir: Path,
) -> Tuple[Path, Path, Optional[int], bool]:
    """
    Returns (input_path, output_dir, max_rows, run_sample_first).
    Modelled on the Phase 2 menu style.
    """
    global MIN_FEATURE_THRESHOLD  # noqa: PLW0603
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║   Phase 3 – Part 1 : Feature File Counter            ║")
    print("╠══════════════════════════════════════════════════════╣")
    print("║  1. Use default Phase 2 output folder                ║")
    print("║  2. Use a specific CSV file                          ║")
    print("║  3. Use a specific directory of CSV files            ║")
    print("╚══════════════════════════════════════════════════════╝")

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
        print(f"\n  ✗  Path not found: {input_path}")
        print(  "     Please re-run and enter a valid path.\n")
        sys.exit(1)

    counts = detect_input_row_counts(input_path)
    if counts:
        print(f"\n  Detected {len(counts)} input file(s):")
        for name, n in counts:
            print(f"    • {name}  ({n} rows)")
    else:
        print("  ✗  No CSV files found at the given path.")
        sys.exit(1)

    print(f"\n  Default output dir: {default_output_dir}")
    out_raw = input("  Output directory (blank = use default): ").strip()
    output_dir = Path(out_raw).expanduser().resolve() if out_raw else default_output_dir

    max_raw = input("  Rows to process per file (blank = all): ").strip()
    max_rows = int(max_raw) if max_raw else None

    thresh_raw = input(f"  Min .feature files threshold (blank = {MIN_FEATURE_THRESHOLD}): ").strip()
    if thresh_raw:
        MIN_FEATURE_THRESHOLD = int(thresh_raw)

    return input_path, output_dir, max_rows, False


# ══════════════════════════════════════════════════════════════════════════════════
# Argument parser (for automation / pipeline use)
# ══════════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 3 Part 1 – Count .feature files per repository"
    )
    p.add_argument("--input",     help="Phase 2 CSV file or directory of CSV files")
    p.add_argument("--output",    help="Output directory for enriched CSVs")
    p.add_argument("--max-rows",  type=int, help="Process only this many rows per file")
    p.add_argument("--threshold", type=int, default=MIN_FEATURE_THRESHOLD,
                   help=f"Min .feature files to set has_min_features=true (default={MIN_FEATURE_THRESHOLD})")
    p.add_argument("--sample",    action="store_true", help="Run 3-row sample test then exit")
    return p.parse_args()


# ══════════════════════════════════════════════════════════════════════════════════
# Token loader
# ══════════════════════════════════════════════════════════════════════════════════

def _load_token() -> str:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from github_token import token  # type: ignore
        return token
    except Exception:
        pass
    tok = os.environ.get("GITHUB_TOKEN", "")
    if not tok:
        print(
            "ERROR: No GitHub token found.\n"
            "  Either set GITHUB_TOKEN env-var or ensure github_token.py is present.",
            file=sys.stderr,
        )
        sys.exit(1)
    return tok


# ══════════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════════

def main() -> int:
    global MIN_FEATURE_THRESHOLD  # noqa: PLW0603
    args = parse_args()

    # ── resolve defaults ────────────────────────────────────────────────────────
    script_dir      = Path(__file__).resolve().parent
    phase3_root     = script_dir.parent
    # default_in_dir  = phase3_root.parent / "phase-2 (column data)" / "phase-2-output-files" / "phase-2-output-files-extractor-1"
    # default_out_dir = phase3_root / "phase-3-output-files" / "phase-3-feature-count-output"
    default_in_dir  = Path("/home/vaishnavkoka/RE4BDD/Phase-workout-for-bdd/phase-3 (refinement)/phase-3-input-files")
    default_out_dir = Path("/home/vaishnavkoka/RE4BDD/Phase-workout-for-bdd/phase-3 (refinement)/phase-3-output-files")

    if args.threshold != MIN_FEATURE_THRESHOLD:
        MIN_FEATURE_THRESHOLD = args.threshold

    # ── decide: CLI or interactive menu ────────────────────────────────────────
    if args.input:
        input_path  = Path(args.input).expanduser().resolve()
        output_dir  = Path(args.output).expanduser().resolve() if args.output else default_out_dir
        max_rows    = args.max_rows
        run_sample  = args.sample
    else:
        input_path, output_dir, max_rows, run_sample = interactive_menu(
            default_in_dir, default_out_dir
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    # ── logging ─────────────────────────────────────────────────────────────────
    logger = _setup_logger(script_dir)
    logger.info("=" * 60)
    logger.info("Phase 3 Part 1 started at %s", _now_iso())
    logger.info("input_path=%s", input_path)
    logger.info("output_dir=%s", output_dir)
    logger.info("max_rows=%s  threshold=%d", max_rows, MIN_FEATURE_THRESHOLD)

    # ── collect input files ─────────────────────────────────────────────────────
    if input_path.is_file():
        input_files = [input_path]
    else:
        input_files = sorted(input_path.glob("*.csv"))

    if not input_files:
        print(f"ERROR: No CSV files found at {input_path}", file=sys.stderr)
        return 1

    # ── shared objects ──────────────────────────────────────────────────────────
    token  = _load_token()
    rate   = RateState()
    client = GitHubClient(token, rate, logger)
    ckpt   = CheckpointManager(script_dir / CHECKPOINT_FILE, logger)

    # ── banner ──────────────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  Phase 3 – Part 1 : Feature File Counter")
    print(f"  Started    : {_now_iso()}")
    print(f"  Input      : {input_path}")
    print(f"  Output dir : {output_dir}")
    print(f"  Files      : {len(input_files)}")
    print(f"  Threshold  : {MIN_FEATURE_THRESHOLD} .feature files")
    print(f"  Log        : {script_dir / LOG_FILE_NAME}")
    print(f"  Checkpoint : {script_dir / CHECKPOINT_FILE}")
    print(f"{'─'*60}\n")

    # ── bootstrap rate limit (live, not hardcoded) ───────────────────────────
    client.bootstrap_rate_limit()

    # ── sample test ─────────────────────────────────────────────────────────────
    if run_sample:
        run_sample_test(input_files, output_dir, client, logger)
        cont = input("Continue with full processing? [Y/n]: ").strip().lower()
        if cont in ("n", "no"):
            print("Exiting after sample test.")
            return 0

    # ── count total rows for overall progress bar ───────────────────────────────
    total_rows = 0
    for f in input_files:
        _, rows = _read_csv(f)
        total_rows += min(len(rows), max_rows) if max_rows else len(rows)

    # ── progress dashboard ───────────────────────────────────────────────────────
    dash = ProgressDashboard(total_rows=total_rows, total_files=len(input_files), rate=rate)

    all_stats: List[FileStats] = []
    try:
        for inp in input_files:
            logger.info("Processing file: %s", inp.name)
            stats = _process_file(
                input_path  = inp,
                output_dir  = output_dir,
                client      = client,
                ckpt        = ckpt,
                dash        = dash,
                logger      = logger,
                max_rows    = max_rows,
                sample_mode = False,
            )
            all_stats.append(stats)
            logger.info(
                "Finished %s: processed=%d errors=%d api_calls=%d avg_api/row=%.2f",
                inp.name, stats.processed, stats.errors,
                stats.api_calls_total, stats.api_calls_per_row(),
            )
    finally:
        dash.close()

    # ── summary ──────────────────────────────────────────────────────────────────
    _print_summary(all_stats, client, output_dir, logger)
    logger.info("Phase 3 Part 1 finished at %s  total_api_calls=%d", _now_iso(), client.total_calls)
    return 0


def _print_summary(stats_list: List[FileStats], client: GitHubClient, output_dir: Path, logger: logging.Logger) -> None:
    sep = "=" * 70
    lines = [
        sep,
        "SUMMARY",
        sep,
        f"  Finished at       : {_now_iso()}",
        f"  Output dir        : {output_dir}",
        f"  Total API calls   : {client.total_calls}",
        f"  Threshold used    : {MIN_FEATURE_THRESHOLD} .feature files",
        "",
    ]
    for s in stats_list:
        out = _output_path_for(Path(s.filename), output_dir)
        lines += [
            f"  File : {s.filename}",
            f"    Rows total      : {s.total_rows}",
            f"    Processed       : {s.processed}",
            f"    Skipped (ckpt)  : {s.skipped_ckpt}",
            f"    Errors          : {s.errors}",
            f"    Rows written    : {s.rows_written}",
            f"    API calls       : {s.api_calls_total}  ({s.api_calls_per_row()} avg/row)",
            f"    Started         : {s.started_at}",
            f"    Finished        : {s.finished_at}",
            f"    Output          : {out}",
            "",
        ]
    lines.append(sep)

    block = "\n".join(lines)
    print(f"\n{block}\n")
    for line in lines:
        logger.info("%s", line)


if __name__ == "__main__":
    raise SystemExit(main())
