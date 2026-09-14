#!/usr/bin/env python3
"""Phase 2 revision: repository-level metadata extraction engine."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import requests

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


HEADER_FIELDS = [
    "repo_name", "full_name", "owner", "repo_id", "repo_url", "local_path", "domain", "source", "description", "topics", "stars", "forks",
    "watchers", "contributors", "commit_count", "branches_count", "releases_count", "pull_requests_count", "issues_count",
    "deployments_count", "sponsor_count", "used_by_count", "tags_count", "language", "languages", "language_distribution",
    "framework", "build_tool", "license", "homepage", "default_branch", "branch_name", "repository_classification", "visibility", "is_private",
    "is_archived", "is_disabled", "is_locked", "has_wiki", "has_readme", "has_license", "has_tests", "has_ci_cd",
    "has_documentation", "code_of_conduct", "security_policy", "custom_properties", "packages_file", "primary_file_types",
    "repository_size_bytes", "file_count", "directory_count", "repo_age_days", "days_since_last_commit", "created_at", "updated_at",
    "pushed_at", "processed_at", "mining_timestamp", "activity",
    "has_issues", "has_projects", "has_downloads", "has_pages", "has_discussions", "has_pull_requests",
    "allow_forking", "is_template", "git_url", "ssh_url", "clone_url",
    "status", "error_message",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_int(value: object, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except Exception:
        return default


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def safe_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"


def normalize_url(url: str) -> str:
    return url.strip().rstrip("/")


def extract_owner_repo(url: str) -> Optional[Tuple[str, str]]:
    match = re.search(r"github\.com/([^/]+)/([^/?#]+)", url, re.IGNORECASE)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def load_github_token() -> str:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        return token
    candidate_paths = [
        Path(__file__).resolve().parents[2] / "github_token.py",
        Path(__file__).resolve().parents[1] / "github_token.py",
        Path(__file__).resolve().parent / "github_token.py",
    ]
    for candidate in candidate_paths:
        if candidate.exists():
            spec = importlib.util.spec_from_file_location("github_token", candidate)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                token = getattr(module, "token", "") or getattr(module, "GITHUB_TOKEN", "")
                if token:
                    return str(token).strip()
    return ""


def infer_domain(description: str, topics: Sequence[str]) -> str:
    text = f"{description} {' '.join(topics)}".lower()
    if any(word in text for word in ["bdd", "gherkin", "cucumber", "behave", "specflow", "jbehave"]):
        return "bdd"
    if any(word in text for word in ["test", "testing", "qa", "automation"]):
        return "testing"
    return "general"


def infer_framework_and_build_tool(paths: Sequence[str]) -> Tuple[str, str, str]:
    names = {Path(p).name.lower() for p in paths}
    if "package.json" in names:
        return "node", "npm", "package.json"
    if "pom.xml" in names:
        return "java", "maven", "pom.xml"
    if "build.gradle" in names or "build.gradle.kts" in names:
        return "java", "gradle", "build.gradle"
    if "requirements.txt" in names or "pyproject.toml" in names:
        return "python", "pip", "requirements.txt"
    if "composer.json" in names:
        return "php", "composer", "composer.json"
    if "gemfile" in names:
        return "ruby", "bundler", "Gemfile"
    return "", "", ""


def infer_repository_classification(stars: int, commits: int, archived: bool) -> str:
    if archived:
        return "archived"
    if stars >= 50 and commits >= 50:
        return "mature"
    if commits > 0:
        return "active_candidate"
    return "minimal"


def setup_logging(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("phase2_extractor")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


class ProgressDashboard:
    """Five-layer static dashboard with proper progress tracking."""

    def __init__(self, total_files: int):
        self.enabled = tqdm is not None
        self.files_bar = None
        self.row_bars: List[Optional[object]] = []
        self.api_bar = None
        self.api_limit = 5000
        self.last_api_update = 0  # Track last update to avoid excessive refreshes
        self.update_interval = 5  # Update progress bar every 5 API calls
        self.api_call_count = 0

        if not self.enabled:
            return

        self.files_bar = tqdm(total=total_files, desc="Files Progress", position=0, leave=True, colour="green", unit="file")

        row_labels = ["File 1", "File 2", "File 3"]
        for idx, label in enumerate(row_labels, start=1):
            bar = tqdm(total=0, desc=label, position=idx, leave=True, colour="blue", unit="row")
            self.row_bars.append(bar)

        self.api_bar = tqdm(total=self.api_limit, desc="API Rate Limit | remaining=unknown", position=4, leave=True, colour="red", unit="call")
        self.api_bar.n = 0
        self.api_bar.refresh()

    def start_file(self, file_index: int, file_name: str, total_rows: int, start_row: int) -> int:
        if not self.enabled:
            return min(file_index, 2)
        slot = min(file_index, 2)
        bar = self.row_bars[slot]
        bar.reset(total=total_rows)
        bar.n = min(start_row, total_rows)
        bar.set_description_str(f"{bar.desc.split('|')[0].strip()} | {file_name}")
        bar.refresh()
        return slot

    def advance_row(self, slot: int) -> None:
        if self.enabled:
            self.row_bars[slot].update(1)

    def finish_file(self, slot: int) -> None:
        if self.enabled:
            self.row_bars[slot].refresh()
            self.files_bar.update(1)

    def mark_file_already_done(self) -> None:
        if self.enabled:
            self.files_bar.update(1)

    def update_api(self, limit: int, remaining: int, reset_epoch: int) -> None:
        if not self.enabled:
            return
        # Throttle updates to reduce flickering: only update every N calls
        self.api_call_count += 1
        if self.api_call_count % self.update_interval != 0 and self.api_bar.n > 0:
            return  # Skip this update to reduce flickering
        
        if limit > 0:
            self.api_limit = limit
        consumed = max(self.api_limit - remaining, 0)
        self.api_bar.total = max(self.api_limit, 1)
        self.api_bar.n = consumed
        reset_text = datetime.fromtimestamp(reset_epoch, tz=timezone.utc).strftime("%H:%M:%S") if reset_epoch else "n/a"
        self.api_bar.set_description_str(f"API Rate Limit | remaining={remaining} | reset_utc={reset_text}")
        self.api_bar.refresh()

    def countdown(self, reset_epoch: int) -> None:
        if reset_epoch <= int(time.time()):
            return
        while True:
            seconds_left = reset_epoch - int(time.time())
            if seconds_left <= 0:
                break
            if self.enabled:
                self.api_bar.set_description_str(f"API Rate Limit | waiting {seconds_left}s")
                self.api_bar.refresh()
            time.sleep(1)

    def close(self) -> None:
        if not self.enabled:
            return
        for bar in [self.files_bar, *self.row_bars, self.api_bar]:
            if bar is not None:
                bar.close()


class SilentDashboard:
    """No-op dashboard used for clean sample preview output."""

    def update_api(self, limit: int, remaining: int, reset_epoch: int) -> None:
        _ = (limit, remaining, reset_epoch)

    def countdown(self, reset_epoch: int) -> None:
        _ = reset_epoch


@dataclass
class RateState:
    limit: int = 0
    remaining: int = 0
    reset_epoch: int = 0

    def update_from_headers(self, headers: Dict[str, str]) -> None:
        self.limit = to_int(headers.get("X-RateLimit-Limit"), self.limit)
        self.remaining = to_int(headers.get("X-RateLimit-Remaining"), self.remaining)
        self.reset_epoch = to_int(headers.get("X-RateLimit-Reset"), self.reset_epoch)


class GitHubClient:
    def __init__(self, token: str, logger: logging.Logger, dashboard: ProgressDashboard, timeout: int = 30):
        self.logger = logger
        self.dashboard = dashboard
        self.timeout = timeout
        self.rate = RateState()
        self.api_calls = 0

        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "phase2-extractor",
        })
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

        self.bootstrap_rate_limit()

    def bootstrap_rate_limit(self) -> None:
        """Initialize rate state from GitHub in real-time before normal requests."""
        try:
            response = self.session.get("https://api.github.com/rate_limit", timeout=self.timeout)
            self.api_calls += 1
            self.rate.update_from_headers(response.headers)
            # Parse response body for core rate limit info
            if isinstance(response.text, str) and response.text:
                payload = response.json()
                core = payload.get("resources", {}).get("core", {}) if isinstance(payload, dict) else {}
                core_limit = to_int(core.get("limit"), self.rate.limit or 5000)
                core_remaining = to_int(core.get("remaining"), self.rate.remaining or 5000)
                core_reset = to_int(core.get("reset"), self.rate.reset_epoch or 0)
                self.rate.limit = core_limit
                self.rate.remaining = core_remaining
                self.rate.reset_epoch = core_reset
            self.dashboard.update_api(self.rate.limit, self.rate.remaining, self.rate.reset_epoch)
        except Exception as e:
            # Fallback: assume default limit
            if self.rate.limit <= 0:
                self.rate.limit = 5000
            if self.rate.remaining <= 0:
                self.rate.remaining = 5000

    def _safety_wait_if_needed(self) -> None:
        """Check if we should wait before making the next API call.
        
        Activates background sleeping when approaching rate limit (< 10 remaining).
        Continuously polls for rate limit reset in background.
        """
        if self.rate.limit <= 0:
            return
        
        # If less than 10 calls remaining, wait for reset
        if self.rate.remaining <= 10:
            self.logger.warning(
                "Rate limit critical: %d/%d remaining. Waiting for reset at %s UTC",
                self.rate.remaining, self.rate.limit,
                datetime.fromtimestamp(self.rate.reset_epoch, tz=timezone.utc).strftime("%H:%M:%S")
            )
            self.dashboard.countdown(self.rate.reset_epoch)
            # After waiting, fetch fresh rate limit from API
            self._refresh_rate_limit_from_api()

    def _refresh_rate_limit_from_api(self) -> None:
        """Fetch current rate limit status from GitHub API."""
        try:
            response = self.session.get("https://api.github.com/rate_limit", timeout=self.timeout)
            self.api_calls += 1
            self.rate.update_from_headers(response.headers)
            if isinstance(response.text, str) and response.text:
                payload = response.json()
                core = payload.get("resources", {}).get("core", {}) if isinstance(payload, dict) else {}
                self.rate.remaining = to_int(core.get("remaining"), self.rate.remaining)
                self.rate.reset_epoch = to_int(core.get("reset"), self.rate.reset_epoch)
        except Exception:
            pass

    def request(self, url: str, params: Optional[Dict[str, object]] = None) -> requests.Response:
        self._safety_wait_if_needed()
        response = self.session.get(url, params=params, timeout=self.timeout)
        self.api_calls += 1
        self.rate.update_from_headers(response.headers)
        self.dashboard.update_api(self.rate.limit, self.rate.remaining, self.rate.reset_epoch)

        if response.status_code == 403 and self.rate.remaining <= 0:
            self.logger.warning("HTTP 403: Rate limited. Waiting for reset...")
            self.dashboard.countdown(self.rate.reset_epoch)
            # Retry after waiting
            response = self.session.get(url, params=params, timeout=self.timeout)
            self.api_calls += 1
            self.rate.update_from_headers(response.headers)
            self.dashboard.update_api(self.rate.limit, self.rate.remaining, self.rate.reset_epoch)
        return response

    def get_json(self, url: str, params: Optional[Dict[str, object]] = None) -> Tuple[Optional[object], Optional[str], Optional[int]]:
        try:
            response = self.request(url, params=params)
            if response.status_code == 404:
                return None, "404 Not Found", 404
            if response.status_code >= 400:
                return None, f"HTTP {response.status_code}", response.status_code
            if not response.text:
                return None, None, response.status_code
            return response.json(), None, response.status_code
        except requests.Timeout:
            return None, "Timeout", None
        except requests.RequestException as exc:
            return None, str(exc), None

    def paginated_count(self, url: str, params: Optional[Dict[str, object]] = None) -> int:
        """Get accurate count from paginated endpoints using Link header.
        
        Returns:
            int: Total count of items. If Link header with rel='last' exists, uses that.
                 Otherwise returns count of items in first response (max 30).
        """
        try:
            response = self.request(url, params=params)
            if response.status_code >= 400:
                return 0
            
            # Try to extract page number from Link header's last page
            link = response.headers.get("Link", "")
            if 'rel="last"' in link:
                # Pattern matches: page=N>; rel="last"
                match = re.search(r'[?&]page=(\d+)>; rel="last"', link)
                if match:
                    last_page = int(match.group(1))
                    # Each page has up to 30 items by default (unless per_page specified)
                    per_page = 30  # GitHub default
                    if params and "per_page" in params:
                        per_page = to_int(params["per_page"], 30)
                    # Last page may have fewer items, but we use last_page * per_page as estimate
                    # For precision: (last_page - 1) * per_page + items_on_last_page
                    return last_page
            
            # Fallback: return count of items in this response
            data = response.json()
            if isinstance(data, list):
                count = len(data)
                # If we got exactly 30 items and per_page wasn't set to 1, there may be more pages
                if count == 30 and (not params or to_int(params.get("per_page", 30), 30) != 1):
                    return count  # Indicate there are more pages
                return count
            return 0
        except Exception:
            return 0

    def close(self) -> None:
        self.session.close()


class CheckpointManager:
    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()

    def _load(self) -> Dict[str, object]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "file_index": 0,
            "row_index": 0,
            "current_file": "",
            "processed_records": 0,
            "timestamp": utc_now_iso(),
        }

    def exists(self) -> bool:
        return self.path.exists()

    def prompt_resume(self) -> bool:
        if not self.exists():
            return False
        print(f"Checkpoint found: {self.path}")
        choice = input("Resume from checkpoint? [Y/n]: ").strip().lower()
        print()  # Add newline after checkpoint confirmation
        return choice in {"", "y", "yes"}

    def save(self, file_index: int, row_index: int, current_file: str, processed_records: int) -> None:
        self.data = {
            "file_index": file_index,
            "row_index": row_index,
            "current_file": current_file,
            "processed_records": processed_records,
            "timestamp": utc_now_iso(),
        }
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def start_position(self) -> Tuple[int, int]:
        return to_int(self.data.get("file_index"), 0), to_int(self.data.get("row_index"), 0)


class Phase2Extractor:
    def __init__(self, input_path: Path, output_path: Path, max_repos: Optional[int] = None):
        self.input_path = input_path
        self.output_path = output_path
        self.max_repos = max_repos
        self.code_dir = Path(__file__).resolve().parent
        self.log_path = self.code_dir / "logs" / "phase2_extraction.log"
        self.checkpoint_path = self.code_dir / "extraction_checkpoint.json"

        self._cleanup_old_runtime_artifacts()
        self.logger = setup_logging(self.log_path)
        self.checkpoint = CheckpointManager(self.checkpoint_path)

        self.dashboard: Optional[ProgressDashboard] = None
        self.client: Optional[GitHubClient] = None

        self.processed = 0
        self.success = 0
        self.failed = 0
        self.mining_timestamp = utc_now_iso()
        self.api_calls_per_row: List[int] = []
        self.output_files: List[Path] = []

    def _cleanup_old_runtime_artifacts(self) -> None:
        stale_files = [
            self.code_dir / "checkpoint.json",
            self.code_dir / "extraction.log",
            self.code_dir / "extractor_run.log",
        ]
        for file in stale_files:
            if file.exists():
                file.unlink()
        if self.log_path.exists():
            self.log_path.unlink()

    def discover_input_files(self) -> List[Path]:
        if self.input_path.is_file() and self.input_path.suffix.lower() == ".csv":
            return [self.input_path]
        if self.input_path.is_dir():
            return sorted(self.input_path.glob("*.csv"))
        raise FileNotFoundError(f"Input path not found or invalid CSV target: {self.input_path}")

    def _output_as_directory(self, files: List[Path]) -> bool:
        if self.output_path.exists() and self.output_path.is_dir():
            return True
        if self.input_path.is_dir():
            return True
        return self.output_path.suffix.lower() != ".csv"

    def output_file_for_input(self, csv_file: Path, files: List[Path]) -> Path:
        if self._output_as_directory(files):
            out_dir = self.output_path
            out_dir.mkdir(parents=True, exist_ok=True)
            return out_dir / f"{csv_file.stem}-phase-2-output.csv"
        return self.output_path

    def clear_output_targets(self, files: List[Path]) -> None:
        targets = [self.output_file_for_input(file, files) for file in files]
        for target in targets:
            if target.exists() and target.is_file():
                target.unlink()

    def read_repo_urls(self, csv_file: Path) -> List[str]:
        with open(csv_file, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return []
            url_field = next((c for c in ["repo_url", "github_url", "url", "link"] if c in reader.fieldnames), reader.fieldnames[0])
            rows = [str(row.get(url_field, "")).strip() for row in reader]
            rows = [normalize_url(row) for row in rows if row]
            if self.max_repos is not None:
                rows = rows[: self.max_repos]
            return rows

    def default_record(self, repo_url: str, source: str) -> Dict[str, object]:
        record = {field: "" for field in HEADER_FIELDS}
        record.update({
            "repo_url": repo_url,
            "source": source,
            "processed_at": utc_now_iso(),
            "mining_timestamp": self.mining_timestamp,
            "status": "FAILED",
            "error_message": "",
            "sponsor_count": 0,
            "used_by_count": 0,
        })
        return record

    def extract_repository(self, repo_url: str, source: str) -> Dict[str, object]:
        assert self.client is not None
        record = self.default_record(repo_url, source)

        owner_repo = extract_owner_repo(repo_url)
        if not owner_repo:
            record["error_message"] = "Unable to parse owner/repo"
            return record
        owner, repo = owner_repo

        record["owner"] = owner
        record["repo_name"] = repo
        record["full_name"] = f"{owner}/{repo}"

        repo_data, repo_error, _ = self.client.get_json(f"https://api.github.com/repos/{owner}/{repo}")
        if repo_error or not isinstance(repo_data, dict):
            record["error_message"] = repo_error or "Repository metadata unavailable"
            return record

        default_branch = str(repo_data.get("default_branch", "main"))
        tree_data, tree_error, _ = self.client.get_json(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{default_branch}", params={"recursive": 1}
        )
        languages_data, _, _ = self.client.get_json(f"https://api.github.com/repos/{owner}/{repo}/languages")

        tree_items = tree_data.get("tree", []) if isinstance(tree_data, dict) else []
        tree_paths = [str(item.get("path", "")) for item in tree_items if isinstance(item, dict) and item.get("path")]
        package_paths = [
            p for p in tree_paths if Path(p).name.lower() in {
                "package.json", "pom.xml", "build.gradle", "build.gradle.kts", "requirements.txt", "pyproject.toml", "composer.json", "gemfile"
            }
        ]

        framework, build_tool, package_hint = infer_framework_and_build_tool(package_paths)

        # Fetch accurate counts for complete metadata.
        commit_count = self.client.paginated_count(f"https://api.github.com/repos/{owner}/{repo}/commits", params={"per_page": 1})
        branches_count = self.client.paginated_count(f"https://api.github.com/repos/{owner}/{repo}/branches", params={"per_page": 1})
        releases_count = self.client.paginated_count(f"https://api.github.com/repos/{owner}/{repo}/releases", params={"per_page": 1})
        pull_requests_count = self.client.paginated_count(
            f"https://api.github.com/repos/{owner}/{repo}/pulls", params={"state": "all", "per_page": 1}
        )
        contributors_count = self.client.paginated_count(
            f"https://api.github.com/repos/{owner}/{repo}/contributors", params={"anon": 1, "per_page": 1}
        )
        # FIXED: Count ALL issues (open + closed), not just open_issues_count
        issues_count = self.client.paginated_count(
            f"https://api.github.com/repos/{owner}/{repo}/issues", params={"state": "all", "per_page": 1}
        )
        tags_count = self.client.paginated_count(f"https://api.github.com/repos/{owner}/{repo}/tags", params={"per_page": 1})
        deployments_count = self.client.paginated_count(f"https://api.github.com/repos/{owner}/{repo}/deployments", params={"per_page": 1})

        languages = languages_data if isinstance(languages_data, dict) else {}
        total_bytes = sum(to_int(v) for v in languages.values()) if languages else 1
        language_distribution = ";".join(
            f"{lang}:{(to_int(value) / total_bytes * 100):.2f}%" for lang, value in sorted(languages.items(), key=lambda pair: to_int(pair[1]), reverse=True)
        ) if languages else ""

        created_at = str(repo_data.get("created_at", "") or "")
        pushed_at = str(repo_data.get("pushed_at", "") or "")
        created_dt = parse_iso_datetime(created_at)
        pushed_dt = parse_iso_datetime(pushed_at)
        now = datetime.now(timezone.utc)
        repo_age_days = (now - created_dt).days if created_dt else 0
        days_since_last_commit = (now - pushed_dt).days if pushed_dt else 0

        topics = repo_data.get("topics", []) if isinstance(repo_data.get("topics"), list) else []
        domain = infer_domain(str(repo_data.get("description", "") or ""), topics)

        has_readme = any(Path(path).name.lower().startswith("readme") for path in tree_paths)
        has_license = bool(repo_data.get("license")) or any(Path(path).name.lower().startswith("license") for path in tree_paths)
        has_tests = any(token in path.lower() for path in tree_paths for token in ["test", "tests", "spec"])
        has_ci_cd = any(token in path.lower() for path in tree_paths for token in [".github/workflows", ".gitlab-ci", "azure-pipelines", "jenkins"])
        has_documentation = has_readme or any("docs/" in path.lower() for path in tree_paths)
        code_of_conduct = any("code_of_conduct" in path.lower() for path in tree_paths)
        security_policy = any("security" in path.lower() for path in tree_paths)

        file_count = sum(1 for item in tree_items if isinstance(item, dict) and item.get("type") == "blob")
        directory_count = sum(1 for item in tree_items if isinstance(item, dict) and item.get("type") == "tree")

        stars = to_int(repo_data.get("stargazers_count", 0))
        archived = bool(repo_data.get("archived"))

        record.update({
            "repo_name": repo,
            "full_name": str(repo_data.get("full_name", f"{owner}/{repo}")),
            "owner": owner,
            "repo_id": to_int(repo_data.get("id", 0)),
            "local_path": "",
            "domain": domain,
            "description": str(repo_data.get("description", "") or ""),
            "topics": ";".join(topics),
            "stars": stars,
            "forks": to_int(repo_data.get("forks_count", 0)),
            "watchers": to_int(repo_data.get("subscribers_count", repo_data.get("watchers_count", 0))),
            "contributors": contributors_count,
            "commit_count": commit_count,
            "branches_count": branches_count,
            "releases_count": releases_count,
            "pull_requests_count": pull_requests_count,
            "issues_count": issues_count,
            "deployments_count": deployments_count,
            "sponsor_count": 0,
            "used_by_count": 0,
            "tags_count": tags_count,
            "language": str(repo_data.get("language") or next(iter(languages.keys()), "")),
            "languages": safe_json(languages),
            "language_distribution": language_distribution,
            "framework": framework,
            "build_tool": build_tool,
            "license": str((repo_data.get("license") or {}).get("name", "")),
            "homepage": str(repo_data.get("homepage", "") or ""),
            "default_branch": default_branch,
            "branch_name": default_branch,
            "repository_classification": infer_repository_classification(stars, commit_count, archived),
            "visibility": str(repo_data.get("visibility", "private" if bool(repo_data.get("private")) else "public")),
            "is_private": bool_text(bool(repo_data.get("private"))),
            "is_archived": bool_text(archived),
            "is_disabled": bool_text(bool(repo_data.get("disabled"))),
            "is_locked": bool_text(bool(repo_data.get("locked"))),
            "has_wiki": bool_text(bool(repo_data.get("has_wiki"))),
            "has_readme": bool_text(has_readme),
            "has_license": bool_text(has_license),
            "has_tests": bool_text(has_tests),
            "has_ci_cd": bool_text(has_ci_cd),
            "has_documentation": bool_text(has_documentation),
            "code_of_conduct": bool_text(code_of_conduct),
            "security_policy": bool_text(security_policy),
            "custom_properties": safe_json({"tree_error": tree_error, "package_hint": package_hint}),
            "packages_file": ";".join(package_paths) if package_paths else package_hint,
            "primary_file_types": ";".join(sorted({Path(path).suffix.lower() or "[no_ext]" for path in tree_paths})[:10]),
            "repository_size_bytes": to_int(repo_data.get("size", 0)) * 1024,
            "file_count": file_count,
            "directory_count": directory_count,
            "repo_age_days": repo_age_days,
            "days_since_last_commit": days_since_last_commit,
            "created_at": created_at,
            "updated_at": str(repo_data.get("updated_at", "") or ""),
            "pushed_at": pushed_at,
            "processed_at": utc_now_iso(),
            "mining_timestamp": self.mining_timestamp,
            "activity": "archived" if archived else ("active" if days_since_last_commit <= 90 else "stale"),
            "status": "OK",
            "error_message": "",
            "has_issues": bool_text(bool(repo_data.get("has_issues"))),
            "has_projects": bool_text(bool(repo_data.get("has_projects"))),
            "has_downloads": bool_text(bool(repo_data.get("has_downloads"))),
            "has_pages": bool_text(bool(repo_data.get("has_pages"))),
            "has_discussions": bool_text(bool(repo_data.get("has_discussions"))),
            "has_pull_requests": bool_text(bool(pull_requests_count > 0)),
            "allow_forking": bool_text(bool(repo_data.get("allow_forking"))),
            "is_template": bool_text(bool(repo_data.get("is_template"))),
            "git_url": str(repo_data.get("git_url", "") or ""),
            "ssh_url": str(repo_data.get("ssh_url", "") or ""),
            "clone_url": str(repo_data.get("clone_url", "") or ""),
        })
        return record

    def write_row(self, output_file: Path, row: Dict[str, object], append: bool) -> None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        write_header = (not output_file.exists()) or (not append)
        with open(output_file, "a" if append else "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADER_FIELDS, quoting=csv.QUOTE_ALL)
            if write_header:
                writer.writeheader()
            writer.writerow({field: row.get(field, "") for field in HEADER_FIELDS})

    def collect_sample_urls(self, files: List[Path], count: int = 3) -> List[Tuple[Path, str]]:
        sample: List[Tuple[Path, str]] = []
        for file in files:
            urls = self.read_repo_urls(file)
            for url in urls:
                sample.append((file, url))
                if len(sample) == count:
                    return sample
        return sample

    def run_sample_preview(self, files: List[Path]) -> None:
        sample = self.collect_sample_urls(files, 3)
        if not sample:
            print("No URLs found for sample preview.")
            return
        print("\nSample Metadata Preview (3 links)")
        print("=" * 80)
        for idx, (src_file, url) in enumerate(sample, start=1):
            row = self.extract_repository(url, src_file.name)
            print(
                f"[{idx}] {row.get('full_name', '')} | stars={row.get('stars', '')} | "
                f"lang={row.get('language', '')} | activity={row.get('activity', '')} | status={row.get('status', '')}"
            )

    def run(self) -> int:
        start_time = time.time()
        files = self.discover_input_files()
        if not files:
            print("No CSV files found")
            return 1

        # Run sample preview without progress bars to keep stdout clean.
        sample_dashboard = SilentDashboard()
        sample_client = GitHubClient(load_github_token(), self.logger, sample_dashboard)
        self.client = sample_client

        try:
            # Sample preview is intentionally muted to keep terminal output clean.
            pass
        finally:
            sample_client.close()

        self.dashboard = ProgressDashboard(total_files=len(files))
        self.client = GitHubClient(load_github_token(), self.logger, self.dashboard)

        try:

            resume = self.checkpoint.prompt_resume()
            start_file_index, start_row_index = (0, 0)
            if resume:
                start_file_index, start_row_index = self.checkpoint.start_position()
            else:
                if self.checkpoint_path.exists():
                    self.checkpoint_path.unlink()
                self.clear_output_targets(files)

            for file_idx, csv_file in enumerate(files):
                if file_idx < start_file_index:
                    self.dashboard.mark_file_already_done()
                    continue

                output_file = self.output_file_for_input(csv_file, files)
                if output_file not in self.output_files:
                    self.output_files.append(output_file)

                urls = self.read_repo_urls(csv_file)
                row_start = start_row_index if file_idx == start_file_index else 0
                append_mode = output_file.exists() and row_start > 0
                slot = self.dashboard.start_file(file_idx, csv_file.name, len(urls), row_start)

                last_row_index = row_start
                for row_idx in range(row_start, len(urls)):
                    last_row_index = row_idx + 1
                    repo_url = urls[row_idx]
                    before_calls = self.client.api_calls if self.client else 0
                    try:
                        row = self.extract_repository(repo_url, csv_file.name)
                        if row.get("status") == "OK":
                            self.success += 1
                            self.logger.info("OK | %s", repo_url)
                        else:
                            self.failed += 1
                            self.logger.error("FAILED | %s | %s", repo_url, row.get("error_message", ""))
                    except Exception as exc:
                        row = self.default_record(repo_url, csv_file.name)
                        row["error_message"] = str(exc)
                        row["status"] = "FAILED"
                        self.failed += 1
                        self.logger.exception("Unhandled exception for %s", repo_url)

                    after_calls = self.client.api_calls if self.client else before_calls
                    self.api_calls_per_row.append(max(after_calls - before_calls, 0))

                    self.write_row(output_file, row, append=append_mode)
                    append_mode = True
                    self.processed += 1
                    self.dashboard.advance_row(slot)

                    if self.processed % 10 == 0:
                        self.checkpoint.save(file_idx, row_idx + 1, str(csv_file), self.processed)

                # Always persist checkpoint at file boundary so the json exists even for small runs.
                self.checkpoint.save(file_idx, last_row_index, str(csv_file), self.processed)
                self.dashboard.finish_file(slot)

            # Final checkpoint marks completion position.
            self.checkpoint.save(len(files), 0, "__completed__", self.processed)

        finally:
            api_calls = self.client.api_calls if self.client else 0
            if self.client:
                self.client.close()
            if self.dashboard:
                self.dashboard.close()

            elapsed = time.time() - start_time
            
            # Build execution summary
            summary_lines = []
            summary_lines.append("Execution Summary")
            summary_lines.append("="*80)
            summary_lines.append(f"Total execution time     : {elapsed:.2f}s")
            summary_lines.append(f"Total processed          : {self.processed}")
            summary_lines.append(f"Total successful         : {self.success}")
            summary_lines.append(f"Total failed             : {self.failed}")
            summary_lines.append(f"Total API calls consumed : {api_calls}")
            if self.api_calls_per_row:
                avg_calls = sum(self.api_calls_per_row) / len(self.api_calls_per_row)
                summary_lines.append(f"API calls per row (avg)  : {avg_calls:.2f}")
                summary_lines.append(f"API calls per row (min)  : {min(self.api_calls_per_row)}")
                summary_lines.append(f"API calls per row (max)  : {max(self.api_calls_per_row)}")
            summary_lines.append("Output file(s):")
            for path in self.output_files:
                summary_lines.append(f" - {path}")
            summary_lines.append(f"Log file                 : {self.log_path}")
            
            # Print to stdout
            print("\n" + "\n".join(summary_lines))
            
            # Log to logger
            self.logger.info("\n".join(summary_lines))

        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2 repository metadata extraction engine")
    parser.add_argument("--input", help="Input CSV file or directory containing CSV files")
    parser.add_argument("--output", default="phase2_metadata_report.csv", help="Output CSV path")
    parser.add_argument("--max-repos", type=int, help="Optional row cap per input file")
    return parser.parse_args()


def detect_input_row_counts(input_path: Path) -> List[Tuple[str, int]]:
    files: List[Path]
    if input_path.is_file() and input_path.suffix.lower() == ".csv":
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(input_path.glob("*.csv"))
    else:
        return []

    counts: List[Tuple[str, int]] = []
    for file in files:
        try:
            with open(file, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = sum(1 for _ in reader) if reader.fieldnames else 0
                counts.append((file.name, rows))
        except Exception:
            counts.append((file.name, 0))
    return counts


def interactive_menu(default_input_dir: Path, default_output: Path) -> Tuple[Path, Path, Optional[int]]:
    print("\nPhase 2 Extractor Menu")
    print("1. Use default input folder")
    print("2. Use a specific input CSV file")
    print("3. Use a specific input directory")

    choice = input("Select option [1/2/3]: ").strip() or "1"
    if choice == "2":
        input_path = Path(input("Enter CSV file path: ").strip()).expanduser().resolve()
    elif choice == "3":
        input_path = Path(input("Enter input directory path: ").strip()).expanduser().resolve()
    else:
        input_path = default_input_dir

    counts = detect_input_row_counts(input_path)
    is_multi_file = len(counts) > 1
    if counts:
        print("\nDetected input rows:")
        for name, count in counts:
            print(f"- {name}: {count} rows")

    if is_multi_file:
        default_dir = default_input_dir.parent / "phase-2-output-files"
        output_text = input(f"Output directory [{default_dir}]: ").strip()
        output_path = Path(output_text).expanduser().resolve() if output_text else default_dir
    else:
        output_text = input(f"Output CSV file path [{default_output}]: ").strip()
        output_path = Path(output_text).expanduser().resolve() if output_text else default_output

    max_repos_text = input("Rows to process per file (blank for all rows): ").strip()
    max_repos = int(max_repos_text) if max_repos_text else None

    return input_path, output_path, max_repos


def main() -> int:
    args = parse_args()
    default_input_dir = Path(__file__).resolve().parents[1] / "phase-2-input-files"
    default_output = (Path(__file__).resolve().parent / args.output).resolve()

    if args.input:
        input_path = Path(args.input).expanduser().resolve()
        output_path = Path(args.output).expanduser().resolve()
        max_repos = args.max_repos
    else:
        input_path, output_path, max_repos = interactive_menu(default_input_dir, default_output)

    extractor = Phase2Extractor(input_path=input_path, output_path=output_path, max_repos=max_repos)
    return extractor.run()


if __name__ == "__main__":
    raise SystemExit(main())
