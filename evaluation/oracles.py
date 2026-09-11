"""
oracles.py -- wrappers around the two external Gherkin linters.

WHAT AN ORACLE IS FOR HERE
--------------------------
UnifiedBDDLinter does not use gherkin-lint or cuke_linter. It never calls them,
imports them, or depends on them in any way -- see ../tool/, which is pure
standard-library Python.

They appear in this directory for one reason: to serve as INDEPENDENT WITNESSES.
If our fixer claims to have resolved a violation, that claim is worth more when
a tool written by someone else, in another language, agrees the violation is
gone. Self-reported improvement is not evidence; corroborated improvement is.

That is also why their counts are reported separately and never pooled with
ours. Summing three linters' violation counts would double-count the defects
they agree on and present the total as if it meant something.

CROSS-PLATFORM RESOLUTION
-------------------------
The original harness hardcoded an absolute path to a Node binary inside one
checkout. Here both oracles are resolved from PATH, with the Windows wrapper
extensions handled, and their absence is reported as a clear, actionable
message rather than a FileNotFoundError traceback.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path
from typing import Optional, Tuple

HERE = Path(__file__).resolve().parent
GHERKIN_LINTRC = HERE / "config" / ".gherkin-lintrc"
CUKE_LINTER_CONFIG_DIR = HERE / "config"

# Per-file timeout. A handful of corpus files are pathologically large and can
# hang a linter; 60s was the value used for the published run.
TOOL_TIMEOUT = 60

# Written into a text cell when a linter could not run at all, so that
# "no violations" and "no measurement" stay distinguishable in the results.
TOOL_ERROR_TEXT = "<TOOL_ERROR>"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove terminal colour codes before parsing.

    Both oracles colourise when they believe they are on a terminal, and the
    escape sequences would otherwise be counted as part of a message.
    """
    return _ANSI.sub("", text)


def find_executable(name: str) -> Optional[str]:
    """Locate *name* on PATH, including Windows wrapper forms.

    npm installs `gherkin-lint.cmd` on Windows and RubyGems installs
    `cuke_linter.bat`; `shutil.which` finds those only if PATHEXT is set as
    expected, which it is not in every shell. Trying the suffixes explicitly is
    cheap and removes a whole class of "works on my machine".
    """
    found = shutil.which(name)
    if found:
        return found
    for suffix in (".cmd", ".bat", ".exe", ".ps1"):
        found = shutil.which(name + suffix)
        if found:
            return found
    return None


def probe():
    """Report which oracles are usable, for the environment check.

    Returns a dict of name -> (path or None, version or None). Version strings
    are recorded in each run's provenance file: `cuke_modeler`'s version is not
    implied by `cuke_linter`'s (the constraint is >= 1.5, < 4.0), so a run that
    did not record it cannot be compared with confidence.
    """
    report = {}

    gl_path = find_executable("gherkin-lint")
    report["gherkin-lint"] = (gl_path, _gherkin_lint_version(gl_path) if gl_path else None)

    cl_path = find_executable("cuke_linter")
    report["cuke_linter"] = (cl_path, _version_flag(cl_path) if cl_path else None)

    # cuke_modeler is the parser underneath cuke_linter. Its version is NOT
    # implied by cuke_linter's -- the dependency constraint is >= 1.5, < 4.0 --
    # so a run that failed to record it cannot be compared with confidence.
    report["cuke_modeler"] = (None, _gem_version("cuke_modeler"))
    return report


def _version_flag(binary: str):
    """Version from `<binary> --version`, for tools that support it."""
    try:
        completed = subprocess.run([binary, "--version"], capture_output=True,
                                   text=True, timeout=30)
    except Exception:
        return None
    text = _strip_ansi(completed.stdout + completed.stderr).strip()
    return text or None


def _gherkin_lint_version(binary: str):
    """Version of gherkin-lint, which has NO --version flag.

    Asking it for `--version` returns "error: unknown option", and a naive
    probe records that string -- or worse, records "?" and lets a reader assume
    the version changed between runs when it did not.

    Two sources, in order:

      1. the package.json beside the resolved binary. npm installs the CLI as a
         symlink into `bin/`, so the link is followed to reach the real package
         directory. This is authoritative: it is the code that actually ran.
      2. `npm ls -g --depth=0 --json`, as a fallback when the layout is unusual.

    Returns None rather than a guess when neither works, so the provenance file
    records an honest UNKNOWN.
    """
    try:
        real = Path(binary).resolve()
        # node_modules/gherkin-lint/dist/main.js -> walk up for package.json
        for parent in [real.parent, *real.parents]:
            candidate = parent / "package.json"
            if candidate.is_file():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if data.get("name") == "gherkin-lint" and data.get("version"):
                    return str(data["version"])
    except Exception:
        pass
    try:
        completed = subprocess.run(["npm", "ls", "-g", "--depth=0", "--json"],
                                   capture_output=True, text=True, timeout=60)
        data = json.loads(completed.stdout)
        version = data.get("dependencies", {}).get("gherkin-lint", {}).get("version")
        if version:
            return str(version)
    except Exception:
        pass
    return None


def _gem_version(gem: str):
    """Installed version of a Ruby gem, via `gem list`."""
    try:
        completed = subprocess.run(["gem", "list", "-e", gem], capture_output=True,
                                   text=True, timeout=60)
    except Exception:
        return None
    match = re.search(rf"^{re.escape(gem)}\s+\(([^)]+)\)", completed.stdout, re.M)
    return match.group(1).split(",")[0].strip() if match else None


def _run(cmd, cwd=None) -> Optional[str]:
    """Execute *cmd*, returning combined output, or None if it could not run.

    None means "no measurement", which the callers turn into TOOL_ERROR_TEXT
    and a count of -1. A count of 0 would be a lie: it would claim the file was
    checked and found clean.

    OUTPUT GOES TO TEMPORARY FILES, NOT PIPES. THIS IS NOT A STYLE CHOICE.
    ---------------------------------------------------------------------
    `subprocess.run(capture_output=True)` connects the child's stdout and
    stderr to PIPES. Node.js makes writes to a pipe asynchronous, and
    `process.exit()` does not wait for them to drain -- so a Node program that
    prints a lot and then exits loses however much was still buffered.

    gherkin-lint does exactly that. Measured on one 4,418-line corpus file,
    five sequential runs through a pipe returned:

        655, 3643, 2431, 599, 4812        (same file, no load, no timeout)

    and five runs with output redirected to a file returned:

        4812, 4812, 4812, 4812, 4812

    4812 is the true count. Every piped run silently under-reported, by up to
    87%, with no error and a zero exit status.

    Writes to a FILE are synchronous in Node, so nothing is lost. The cost is
    one temporary file per invocation, which is negligible beside the cost of
    a measurement that is wrong.

    This matters beyond tidiness: an under-count on the BEFORE measurement
    inflates the apparent reduction, and an under-count on the AFTER
    measurement deflates it. Neither is detectable from the results alone.
    """
    try:
        # delete=False plus an explicit unlink: on Windows a file cannot be
        # reopened by another process while an NamedTemporaryFile handle is
        # still open, so the handle is closed before the child is started.
        with tempfile.TemporaryDirectory() as scratch:
            out_path = Path(scratch) / "stdout"
            err_path = Path(scratch) / "stderr"
            with open(out_path, "w", encoding="utf-8") as out_handle, \
                 open(err_path, "w", encoding="utf-8") as err_handle:
                subprocess.run(cmd, stdout=out_handle, stderr=err_handle,
                               timeout=TOOL_TIMEOUT, cwd=cwd)
            # errors="replace": a linter may emit bytes that are not valid
            # UTF-8 when echoing a malformed source line, and losing the whole
            # measurement to a decode error would be worse than one bad char.
            text = (out_path.read_text(encoding="utf-8", errors="replace")
                    + err_path.read_text(encoding="utf-8", errors="replace"))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception:
        return None
    return _strip_ansi(text)


def run_gherkin_lint(binary: str, feature_file: Path) -> Tuple[str, int]:
    """Lint one file with gherkin-lint.

    Violation rows look like ``  12   Some message    rule-name`` -- leading
    whitespace, a line number, then the message and rule. Rows are kept verbatim
    so the CSV preserves the tool's own wording rather than our paraphrase of it.
    """
    output = _run([binary, "--config", str(GHERKIN_LINTRC), str(feature_file)])
    if output is None:
        return TOOL_ERROR_TEXT, -1
    rows = [line.strip() for line in output.splitlines()
            if re.match(r"^\s+\d+\s+\S", line)]
    return "\n".join(rows), len(rows)


def run_cuke_linter(binary: str, feature_file: Path) -> Tuple[str, int]:
    """Lint one file with cuke_linter.

    Output groups issues under a linter-class header, then an indented message
    and an indented file:line location, and ends with ``N issues found``. The
    count is taken from that summary line rather than by counting blocks,
    because the tool's own total is the authoritative one.

    Run with cwd set to our config directory: cuke_linter discovers `.cukelinter`
    from the working directory, so the configuration is selected by WHERE it is
    launched, not by a flag. Getting this wrong silently lints with defaults.
    """
    output = _run([binary, "-p", str(feature_file)], cwd=str(CUKE_LINTER_CONFIG_DIR))
    if output is None:
        return TOOL_ERROR_TEXT, -1
    match = re.search(r"(\d+)\s+issues?\s+found", output)
    if not match:
        return output.strip(), 0
    return output[:match.start()].strip(), int(match.group(1))
