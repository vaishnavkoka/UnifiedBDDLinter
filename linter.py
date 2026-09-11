#!/usr/bin/env python3
"""Comprehensive linter -- entry point 1 of 3 (paper, Section 5.C.1).

Evaluates .feature files against all four rule families, including the quality
(business-readability) checks that no external linter provides.

    python3 linter.py features/
    python3 linter.py features/ --format json --severity warning

Equivalent to: bddlint lint <path>
"""

import argparse
import sys
from pathlib import Path

# Put this checkout's src/ on the path before importing the package. Nothing can
# come from unifiedbddlinter until this has run, so it cannot live in a module.
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unifiedbddlinter._compat import add_lint_flags, run_lint


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="linter.py",
        description="UnifiedBDDLinter -- all 28 rules, all four families "
                    "(style, structure, workflow, quality).")
    add_lint_flags(parser)
    args = parser.parse_args(argv)
    return run_lint(args, full=True)


if __name__ == "__main__":
    sys.exit(main())
