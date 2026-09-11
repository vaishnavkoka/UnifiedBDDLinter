"""Enables ``python -m unifiedbddlinter`` as an alias for the ``bddlint``
command. Useful when the launcher script is not on PATH -- notably on Windows,
where ``python -m`` is the most reliable way to run an installed package."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
