#!/usr/bin/env python3
"""
run_tests.py -- run the whole suite with no test framework installed.

    python3 tests/run_tests.py            everything
    python3 tests/run_tests.py -v         verbose
    python3 tests/run_tests.py boundary   only modules matching "boundary"

WHY NOT PYTEST
--------------
The tool ships as a zero-install bundle, and a test suite that cannot be run
without first installing something is a test suite most people will not run.
Everything here is `unittest` from the standard library, so verifying this
package needs exactly what running it needs: a Python interpreter.

pytest still works if you have it (`pytest tests/`) -- unittest classes are
collected natively. This runner just removes the requirement.
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def main(argv) -> int:
    verbosity = 2 if ("-v" in argv or "--verbose" in argv) else 1
    selectors = [a for a in argv if not a.startswith("-")]

    # Tests import `_support`, which sits beside them, so the test directory
    # must be importable regardless of where the runner was invoked from.
    sys.path.insert(0, str(TESTS))

    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS), pattern="test_*.py")

    if selectors:
        # Filter by module name so `run_tests.py config` runs test_3_config.
        filtered = unittest.TestSuite()

        def keep(test):
            return any(s.lower() in test.id().lower() for s in selectors)

        def walk(item):
            if isinstance(item, unittest.TestSuite):
                for child in item:
                    walk(child)
            elif keep(item):
                filtered.addTest(item)

        walk(suite)
        suite = filtered

    print(f"UnifiedBDDLinter test suite  |  python {sys.version.split()[0]}  "
          f"|  {suite.countTestCases()} tests")
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)

    if result.wasSuccessful():
        print("\nALL TESTS PASSED")
        return 0
    print(f"\nFAILED: {len(result.failures)} failure(s), {len(result.errors)} error(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
