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

from _compat import add_lint_flags, run_lint


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
