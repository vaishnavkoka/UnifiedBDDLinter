"""Shared plumbing for the three entry points named in the paper.

The paper (Table 2, Section 5.C) documents UnifiedBDDLinter as three separate
scripts -- ``linter.py``, ``cli.py`` and ``auto_fix.py`` -- because that is how
v1.0 was laid out. The ported package exposes one ``bddlint`` command with
subcommands instead, which is the better shape for a tool people install.

Rather than choose, this package ships both. The three scripts below are thin
argument translators over the same engine, so a reader following the paper types
the commands the paper prints and gets the results the paper reports.

Nothing here re-implements linting or fixing. Each script parses the flags the
paper documents, maps them onto the package's own options, and delegates.
"""

import argparse
import sys
from pathlib import Path

# Both layouts must work: running these scripts straight out of the unpacked
# artifact (sources under src/) and running them after a pip install.
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unifiedbddlinter import cli as _cli  # noqa: E402

# Rule families, as the paper names them. `quality` is the business-readability
# family that linter.py runs and cli.py does not.
STYLE = ["S001", "S002", "S003", "S004", "S005", "S006", "T006"]
WORKFLOW = ["W001", "W002", "W003", "W004", "W005", "W006"]

# Fix classes for auto_fix.py's --no-* switches, mapped to the rules that
# actually perform each repair.
FIX_CLASSES = {
    "indentation": ["S004", "T006"],
    "spacing": ["S001", "S002", "S003"],
    # W005 (trailing full stop) was withdrawn after it was shown to break
    # Cucumber step-definition binding: the step text is the string the runtime
    # matches on. It is now detect-only, so --no-periods has nothing to switch
    # off. The flag is still accepted so commands printed in the paper run.
    "periods": [],
}


def add_lint_flags(parser: argparse.ArgumentParser) -> None:
    """The `Linting` block of Table 2, shared by linter.py and cli.py."""
    parser.add_argument("path", help=".feature file or directory")
    parser.add_argument("--format", choices=["text", "json", "sarif"],
                        default="text", help="output format (default: text)")
    parser.add_argument("--severity",
                        choices=["info", "warning", "error", "critical"],
                        default="info", help="minimum severity reported")
    parser.add_argument("--summary", action="store_true",
                        help="print counts only")
    parser.add_argument("--config", metavar="FILE",
                        help="policy file (default: nearest .unified-lintrc.json)")
    parser.add_argument("--no-config", action="store_true",
                        help="ignore any policy file and use built-in defaults")


def run_lint(args, *, full: bool, disable=()) -> int:
    """Invoke the package's lint command with a translated argument set."""
    ns = argparse.Namespace(
        path=args.path,
        format=args.format,
        severity=args.severity,
        summary=args.summary,
        no_summary=False,
        # `full` selects the rule set: all 28 for linter.py, the 20 the
        # external oracles corroborate for cli.py.
        default_rules=not full,
        config=args.config,
        no_config=args.no_config,
        fail_on="error",
        disable=list(disable),
    )
    return _cli.cmd_lint(ns)
