#!/usr/bin/env python3
"""
check_environment.py -- confirm the harness can run, before it runs.

    python3 evaluation/check_environment.py

A full corpus run takes a long time. Discovering at minute forty that
cuke_linter was never on PATH -- and that the run has been silently recording
"no measurement" for every file -- wastes the run and, worse, produces a
results file that looks complete.

So this checks everything up front and reports versions against the ones the
published results used, because a version mismatch does not stop a run but does
mean the numbers are not directly comparable.

Exit codes: 0 ready · 1 the tool itself is broken · 2 oracles missing or
mismatched (the tool is still fine; use --no-oracles).
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACT_ROOT = HERE.parent
sys.path.insert(0, str(ARTIFACT_ROOT / "src"))
sys.path.insert(0, str(HERE))

import oracles  # noqa: E402

# The versions used for the published run, from
# bdd_pipeline_output/run-2026-08-14_corpus-42repos/versions.txt.
PUBLISHED = {"gherkin-lint": "4.2.4", "cuke_linter": "1.4.0", "cuke_modeler": "3.28.0"}


def main() -> int:
    ok, warn = True, False
    print("UnifiedBDDLinter -- validation harness environment check\n")

    print("the tool itself (needs nothing but Python)")
    try:
        from unifiedbddlinter import __version__, catalogue
        from unifiedbddlinter.engine import UnifiedLinter
        from unifiedbddlinter.config import LinterConfig
        UnifiedLinter(full=True, config=LinterConfig())
        print(f"  OK       UnifiedBDDLinter {__version__}, "
              f"{len(catalogue.RULES)} rules")
    except Exception as exc:
        print(f"  BROKEN   {type(exc).__name__}: {exc}")
        return 1
    print(f"  OK       python {sys.version.split()[0]}")

    print("\nthe corpus")
    manifest = ARTIFACT_ROOT / "corpus" / "MANIFEST.csv"
    master = ARTIFACT_ROOT / "corpus" / "master"
    if manifest.is_file() and master.is_dir():
        lines = sum(1 for _ in open(manifest, encoding="utf-8")) - 1
        print(f"  OK       master present, manifest lists {lines:,} files")
        print(f"           verify with: python3 corpus/build_master.py --verify")
    else:
        print("  MISSING  build it: python3 corpus/build_master.py")
        warn = True

    print("\nexternal oracles (ONLY needed to reproduce the evaluation)")
    environment = oracles.probe()
    for name, expected in PUBLISHED.items():
        path, version = environment.get(name, (None, None))
        if name != "cuke_modeler" and not path:
            print(f"  MISSING  {name}  -- install it, or run with --no-oracles")
            warn = True
        elif version is None:
            print(f"  UNKNOWN  {name}  version could not be determined")
            warn = True
        elif version != expected:
            # Not fatal. The run will work; its numbers just are not directly
            # comparable with the published ones, and that must be said out loud
            # rather than discovered later in a figure that disagrees.
            print(f"  DIFFERS  {name}  {version}  (published run used {expected})")
            warn = True
        else:
            print(f"  OK       {name}  {version}")

    print()
    if warn:
        print("Ready to run the TOOL. The harness is missing something above --\n"
              "either install it, or pass --no-oracles to measure only our linter.")
        return 2
    print("Ready. Environment matches the published run exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
