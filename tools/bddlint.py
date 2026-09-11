#!/usr/bin/env python3
"""
bddlint.py -- zero-install launcher for UnifiedBDDLinter.

Run the tool straight from this directory, on any operating system, with no
installation step::

    python3 bddlint.py lint features/          Linux / macOS
    py -3 bddlint.py lint features\            Windows

WHY THIS FILE EXISTS
--------------------
The package lives under ``src/`` so that the source tree matches what would be
installed, rather than relying on the current working directory happening to
contain the modules -- the mistake that forced v1.0 to be run from inside its
own folder.  A ``src`` layout is unimportable without either installing the
package or extending ``sys.path``; this launcher does the latter, which is what
"zero-install" means here.

It resolves its own location rather than using the working directory, so the
tool works when invoked by absolute path from anywhere::

    python3 /opt/UnifiedBDDLinter-Artifact/bddlint.py lint .
"""

import sys
from pathlib import Path

# resolve() follows symlinks, so a symlink placed on PATH still finds the real
# package rather than looking for src/ beside the link.
_SRC = Path(__file__).resolve().parent.parent / "src"
if not (_SRC / "unifiedbddlinter" / "__init__.py").is_file():
    sys.exit(f"bddlint: package not found under {_SRC}\n"
             f"         This launcher must stay beside the src/ directory.")

# Prepend, not append: if an older copy of the package is installed system-wide,
# the one shipped beside this launcher must win, or a user would silently run a
# different version than the one they are looking at.
sys.path.insert(0, str(_SRC))

if sys.version_info < (3, 8):
    sys.exit(f"bddlint: Python 3.8 or newer required, found "
             f"{sys.version_info.major}.{sys.version_info.minor}")

from unifiedbddlinter.cli import main  # noqa: E402  (import follows path setup)

if __name__ == "__main__":
    sys.exit(main())
