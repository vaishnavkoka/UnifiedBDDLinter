"""
_support.py -- shared test helpers.

Named with a leading underscore so unittest discovery does not mistake it for a
test module. It exists so every test file finds the package the same way: the
suite must run against the source tree in `src/`, never against a copy that
happens to be installed elsewhere, or a green run would prove nothing about the
code being shipped.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def add_src_to_path() -> None:
    """Put this checkout's `src/` at the front of sys.path, idempotently."""
    path = str(SRC)
    if path in sys.path:
        sys.path.remove(path)      # re-insert at the front, never duplicate
    sys.path.insert(0, path)
