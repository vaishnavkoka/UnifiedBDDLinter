#!/usr/bin/env python3
"""Form-preserving auto-fixer -- entry point 3 of 3 (paper, Section 5.C.2).

Applies an ordered sequence of mechanical, meaning-preserving transforms and
writes the corrected specifications to a new directory. The input is never
modified.

    python3 auto_fix.py features/ -o fixed/
    python3 auto_fix.py features/ --dry-run

Equivalent to: bddlint fix <path> --output-dir <dir>

Scope note. A repair may change neither the words in the file nor any string the
Cucumber runtime binds a step definition to. Eight of the twenty-eight rules meet
that bar and are repaired; the rest are reported for a human to resolve.
"""

import argparse
import sys
from pathlib import Path

# Put this checkout's src/ on the path before importing the package. Nothing can
# come from unifiedbddlinter until this has run, so it cannot live in a module.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unifiedbddlinter._compat import FIX_CLASSES, _cli


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="auto_fix.py",
        description="UnifiedBDDLinter -- form-preserving repair. Writes to a "
                    "new directory and never modifies the input.")
    parser.add_argument("path", help=".feature file or directory")
    parser.add_argument("-o", "--output", metavar="DIR", default="fixed_features",
                        help="output directory (default: fixed_features/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="preview fixes, write nothing")
    parser.add_argument("--config", metavar="FILE",
                        help="policy file (default: nearest .unified-lintrc.json)")
    parser.add_argument("--no-config", action="store_true",
                        help="ignore any policy file and use built-in defaults")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="print only the summary")
    for name in FIX_CLASSES:
        parser.add_argument(f"--no-{name}", action="store_true",
                            help=f"skip the {name} fix class")
    args = parser.parse_args(argv)

    disable = []
    for name, rule_ids in FIX_CLASSES.items():
        if getattr(args, f"no_{name.replace('-', '_')}"):
            if not rule_ids:
                # --no-periods survives as an accepted flag only so that the
                # command line printed in the paper still runs. The repair it
                # named was withdrawn, so there is nothing to switch off, and
                # saying so is better than silently accepting a no-op.
                print(f"auto_fix.py: note: --no-{name} has no effect; the "
                      f"trailing-period repair (W005) was withdrawn because it "
                      f"alters the string Cucumber binds on, and is now "
                      f"detect-only.", file=sys.stderr)
            disable += rule_ids

    ns = argparse.Namespace(
        path=args.path,
        output_dir=args.output,
        dry_run=args.dry_run,
        quiet=args.quiet,
        config=args.config,
        no_config=args.no_config,
        allow_text_injection=False,
        disable=disable,
    )
    return _cli.cmd_fix(ns)


if __name__ == "__main__":
    sys.exit(main())
