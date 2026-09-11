"""
config.py -- rule configuration for UnifiedBDDLinter.

WHY THIS MODULE EXISTS
----------------------
UnifiedBDDLinter v1.0 shipped a file called ``.unified-lintrc.json`` that no
code ever opened.  Every threshold (name lengths, step counts) was a hardcoded
default argument inside ``unified_linter.py``, and every severity was frozen at
the value the rule constructor happened to pass.  Users could edit the config
file all day and observe no change in behaviour.

This module makes that file real.  It is the single place where an external
setting is turned into something the engine consults, so a reader looking for
"can I change X?" has exactly one file to read.

DESIGN CONSTRAINTS
------------------
* Standard library only.  The tool ships as a zero-install bundle, so JSON is
  the config format because ``json`` is always present -- YAML would mean a
  dependency, and TOML parsing is only stdlib from Python 3.11.
* A missing or malformed config must never be fatal.  A linter that refuses to
  start because of a stray comma is worse than one that warns and uses
  defaults, so ``load()`` degrades to ``LinterConfig()`` and reports why.
* Defaults must equal v1.0's hardcoded values exactly.  Running the new package
  with no config file must reproduce the published v1.0 numbers; if a default
  here disagreed with the old inline default, every result would silently move.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Name of the configuration file searched for on disk.  Kept identical to v1.0
# so an existing project config keeps working -- it simply starts taking effect.
CONFIG_NAME = ".unified-lintrc.json"

# Severity vocabulary, ordered weakest to strongest.  The order is what makes
# `--severity warning` mean "warning and above" rather than "warning exactly".
SEVERITY_ORDER: Dict[str, int] = {"info": 0, "warning": 1, "error": 2, "critical": 3}

# Thresholds, copied from the default arguments in v1.0's rule signatures:
#   StyleRules.check_name_length(max_feature=80, max_scenario=80, max_step=100)
#   WorkflowRules.check_too_many_steps(max_steps=10)
# `min_steps_per_scenario` had no enforcing rule in v1.0; it is carried because
# the v1.0 config file declared it, and is documented as inert.
DEFAULT_LIMITS: Dict[str, int] = {
    "max_feature_name": 80,
    "max_scenario_name": 80,
    "max_step_length": 100,
    "max_steps_per_scenario": 10,
    "min_steps_per_scenario": 3,
}

# Directories never worth descending into.  Matching is on a single path
# component, not a glob over the whole path, because that is far cheaper and
# these are all directory names rather than patterns.
DEFAULT_IGNORE_DIRS = frozenset(
    {"node_modules", "dist", "build", "target", ".git", ".svn", ".hg", "vendor"}
)


class ConfigError(Exception):
    """Raised only by :meth:`LinterConfig.load_strict`; ``load`` never raises."""


class LinterConfig:
    """Resolved configuration: what the engine actually consults at run time.

    Every attribute is populated whether or not a config file was found, so
    callers never have to test for absence.  ``source`` records where the
    values came from, which is what lets a run be reproduced later -- a result
    whose configuration provenance is unknown is not a reproducible result.
    """

    def __init__(self) -> None:
        # Rule identifiers the user switched off, e.g. {"Q002", "SY001"}.
        self.disabled: set = set()
        # Rule id -> severity name, overriding whatever the rule declares.
        self.severity_overrides: Dict[str, str] = {}
        # Numeric thresholds; starts as a copy so mutation cannot corrupt the
        # module-level defaults shared by every other LinterConfig instance.
        self.limits: Dict[str, int] = dict(DEFAULT_LIMITS)
        # Directory components skipped during a recursive scan.
        self.ignore_dirs: set = set(DEFAULT_IGNORE_DIRS)
        # SY001 (spelling) opt-in. Retained for backward compatibility with
        # existing config files; the rule itself was removed.
        self.spellcheck: bool = False
        # Which filename convention ST007 enforces and S005 checks.
        #
        # This exists because the two incumbent linters demand OPPOSITE
        # conventions and cannot both be satisfied: gherkin-lint's `file-name`
        # rule wants kebab-case, cuke_linter's `feature_file_with_invalid_name`
        # wants snake_case. Renaming a file to please one offends the other,
        # measured at exactly one violation each way per file.
        #
        # The default is snake_case because it is the cheaper side on this
        # corpus: cuke_linter carries ~68k violations in total, so the ~19k it
        # attributes to filenames dominates its result, whereas the same count
        # is noise against gherkin-lint's ~1.49M.
        #
        # A project should set this to whichever convention its own toolchain
        # enforces. The tool cannot resolve the conflict -- nothing can -- but
        # it can stop being the one that picks a side arbitrarily.
        self.file_name_style: str = "snake_case"
        # Provenance: absolute path of the file used, or "<defaults>".
        self.source: str = "<defaults>"
        # Non-fatal problems encountered while loading, surfaced by the CLI.
        self.warnings: List[str] = []

    # -- lookups the engine performs per rule ------------------------------

    def is_enabled(self, rule_id: str) -> bool:
        """True unless the user explicitly disabled *rule_id*."""
        return rule_id not in self.disabled

    def severity_for(self, rule_id: str, declared: str) -> str:
        """Severity to report *rule_id* at.

        *declared* is what the rule itself set.  An override wins, which is how
        a team downgrades a rule to advisory without losing its detection.
        """
        return self.severity_overrides.get(rule_id, declared)

    def limit(self, name: str) -> int:
        """Threshold *name*, falling back to the built-in default."""
        return self.limits.get(name, DEFAULT_LIMITS[name])

    # -- construction ------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[str] = None, start: Optional[str] = None) -> "LinterConfig":
        """Find and parse a config, degrading to defaults on any problem.

        Resolution order, first hit wins:

        1. *path*, when the caller passed ``--config`` explicitly.
        2. ``$UNIFIED_LINTRC``, for CI where editing the checkout is awkward.
        3. ``.unified-lintrc.json`` in *start*, then each parent directory up
           to the filesystem root -- the same upward walk ESLint and Git use,
           so a repository-wide config governs every subdirectory.

        Problems are appended to ``warnings`` rather than raised, because a
        linter must still lint when its configuration is unreadable.
        """
        cfg = cls()

        candidate: Optional[Path] = None
        if path:
            candidate = Path(path).expanduser()
            if not candidate.is_file():
                # An explicit path that does not exist is a user error worth
                # naming loudly -- unlike a merely absent discovered config.
                cfg.warnings.append(f"config file not found: {candidate}")
                return cfg
        elif os.environ.get("UNIFIED_LINTRC"):
            env_path = Path(os.environ["UNIFIED_LINTRC"]).expanduser()
            if env_path.is_file():
                candidate = env_path
            else:
                cfg.warnings.append(f"UNIFIED_LINTRC points at a missing file: {env_path}")
        else:
            candidate = cls._discover(Path(start or ".").resolve())

        if candidate is None:
            return cfg  # No config anywhere: defaults, silently. Normal case.

        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # ValueError covers JSONDecodeError; OSError covers permissions and
            # unreadable files.  Either way: warn, keep the defaults, continue.
            cfg.warnings.append(f"could not read {candidate}: {exc}")
            return cfg

        cfg.source = str(candidate)
        cfg._apply(raw)
        return cfg

    @classmethod
    def load_strict(cls, path: Optional[str] = None, start: Optional[str] = None) -> "LinterConfig":
        """As :meth:`load`, but raise :class:`ConfigError` on any warning.

        Used by the test suite and by CI gates, where silently falling back to
        defaults would let a broken config pass unnoticed.
        """
        cfg = cls.load(path=path, start=start)
        if cfg.warnings:
            raise ConfigError("; ".join(cfg.warnings))
        return cfg

    @staticmethod
    def _discover(start: Path) -> Optional[Path]:
        """Walk upward from *start* looking for ``.unified-lintrc.json``."""
        # `[start] + parents` so a config in the starting directory itself is
        # found; `Path.parents` deliberately excludes the path it is taken from.
        for directory in [start] + list(start.parents):
            candidate = directory / CONFIG_NAME
            if candidate.is_file():
                return candidate
        return None

    def _apply(self, raw: Any) -> None:
        """Merge a parsed JSON document into this config.

        Every section is optional and every unknown key is ignored, so a config
        written for a newer version still loads here.  Values of the wrong type
        are reported and skipped rather than crashing the run.
        """
        if not isinstance(raw, dict):
            self.warnings.append(f"{self.source}: top level must be an object")
            return

        rules = raw.get("rules")
        if isinstance(rules, dict):
            disabled = rules.get("disabled")
            if isinstance(disabled, list):
                self.disabled = {str(r) for r in disabled}
            elif disabled is not None:
                self.warnings.append(f"{self.source}: rules.disabled must be a list")

            overrides = rules.get("severity_overrides")
            if isinstance(overrides, dict):
                for rule_id, sev in overrides.items():
                    sev = str(sev).lower()
                    if sev in SEVERITY_ORDER:
                        self.severity_overrides[str(rule_id)] = sev
                    else:
                        # Naming the offending rule matters: a typo'd severity
                        # in a 28-entry table is otherwise unfindable.
                        self.warnings.append(
                            f"{self.source}: unknown severity {sev!r} for rule {rule_id}"
                        )
            elif overrides is not None:
                self.warnings.append(f"{self.source}: rules.severity_overrides must be an object")

        limits = raw.get("limits")
        if isinstance(limits, dict):
            for key, value in limits.items():
                if key not in DEFAULT_LIMITS:
                    self.warnings.append(f"{self.source}: unknown limit {key!r}")
                elif isinstance(value, bool) or not isinstance(value, int):
                    # bool is an int subclass in Python, so it must be excluded
                    # first or `"max_step_length": true` would be accepted as 1.
                    self.warnings.append(f"{self.source}: limit {key!r} must be an integer")
                elif value < 1:
                    self.warnings.append(f"{self.source}: limit {key!r} must be >= 1")
                else:
                    self.limits[key] = value
        elif limits is not None:
            self.warnings.append(f"{self.source}: limits must be an object")

        quality = raw.get("quality")
        if isinstance(quality, dict) and "spellcheck" in quality:
            value = quality["spellcheck"]
            if isinstance(value, bool):
                self.spellcheck = value
            else:
                self.warnings.append(f"{self.source}: quality.spellcheck must be true or false")

        formatting = raw.get("formatting")
        if isinstance(formatting, dict) and "file_name_style" in formatting:
            value = str(formatting["file_name_style"])
            if value in ("snake_case", "kebab-case"):
                self.file_name_style = value
            else:
                self.warnings.append(
                    f"{self.source}: formatting.file_name_style must be "
                    f"'snake_case' or 'kebab-case', got {value!r}")

        patterns = raw.get("ignore_patterns")
        if isinstance(patterns, list):
            for pattern in patterns:
                # v1.0's config wrote glob form ("node_modules/**").  Only the
                # leading component is meaningful for a directory-prune, so it
                # is extracted and the rest ignored, which keeps old configs
                # working without pretending full glob support exists.
                head = str(pattern).replace("\\", "/").split("/", 1)[0]
                if head and head != "**":
                    self.ignore_dirs.add(head)
        elif patterns is not None:
            self.warnings.append(f"{self.source}: ignore_patterns must be a list")

    def describe(self) -> str:
        """Human-readable dump, printed by ``bddlint config --show``."""
        lines = [f"source: {self.source}"]
        lines.append(f"disabled rules: {', '.join(sorted(self.disabled)) or '(none)'}")
        lines.append("limits:")
        for key in sorted(self.limits):
            marker = "" if self.limits[key] == DEFAULT_LIMITS[key] else "   <- overridden"
            lines.append(f"    {key:<24} {self.limits[key]}{marker}")
        lines.append(f"severity overrides: {len(self.severity_overrides)}")
        for rule_id in sorted(self.severity_overrides):
            lines.append(f"    {rule_id:<8} -> {self.severity_overrides[rule_id]}")
        lines.append(f"file name style: {self.file_name_style}")
        lines.append(f"ignored directories: {', '.join(sorted(self.ignore_dirs))}")
        for warning in self.warnings:
            lines.append(f"WARNING: {warning}")
        return "\n".join(lines)


def emit_warnings(cfg: LinterConfig, stream=sys.stderr) -> None:
    """Print any config warnings to *stream*.

    Warnings go to stderr, never stdout, so that ``bddlint lint --format json``
    remains machine-parseable even when the configuration is imperfect.
    """
    for warning in cfg.warnings:
        print(f"unifiedbddlinter: config: {warning}", file=stream)
