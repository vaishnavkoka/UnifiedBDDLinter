#!/usr/bin/env python3
"""Oracle-corroborated subset -- entry point 2 of 3 (paper, Section 5.C.1).

Runs the style/structure/workflow subset that gherkin-lint and cuke_linter also
check, with severity filtering, family toggles and JSON output. This is the rule
set the published differential evaluation measures, so it is the one to use when
reproducing the reported numbers.

    python3 cli.py features/ --format json
    python3 cli.py features/ --no-style

Equivalent to: bddlint lint <path> --default-rules
"""

import argparse
import sys
from pathlib import Path

# Put this checkout's src/ on the path before importing the package. Nothing can
# come from unifiedbddlinter until this has run, so it cannot live in a module.
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unifiedbddlinter._compat import STYLE, WORKFLOW, add_lint_flags, run_lint


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="UnifiedBDDLinter -- the style/structure/workflow subset "
                    "the external oracles corroborate.")
    add_lint_flags(parser)
    parser.add_argument("--no-style", action="store_true",
                        help="disable the style family")
    parser.add_argument("--no-workflow", action="store_true",
                        help="disable the workflow family")
    args = parser.parse_args(argv)

    disable = []
    if args.no_style:
        disable += STYLE
    if args.no_workflow:
        disable += WORKFLOW
    return run_lint(args, full=False, disable=disable)


if __name__ == "__main__":
    sys.exit(main())
