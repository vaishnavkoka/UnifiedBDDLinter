"""
cli.py -- the single command-line entry point.

WHY ONE ENTRY POINT
-------------------
v1.0 shipped three scripts that each did part of the job: ``cli.py`` (the
18-rule subset), ``linter.py`` (all 28), and ``auto_fix.py`` (repair).  They
had overlapping flags with different meanings, no shared config handling, and
no way to discover what rules existed.  A user had to know which file to run.

This module replaces all three with subcommands::

    bddlint lint    PATH      report violations
    bddlint fix     PATH      repair what can be safely repaired
    bddlint rules             list the rule catalogue
    bddlint config            show the resolved configuration
    bddlint version           version and provenance

EXIT CODES -- the contract CI depends on
----------------------------------------
    0  success, nothing at or above the failure threshold
    1  violations found at or above the threshold
    2  usage error (bad flag, missing path)
    3  internal error

These are distinct on purpose.  A CI job must be able to tell "the linter found
problems" (1) from "the linter is broken" (3); collapsing both into 1, as v1.0
did, means a crashed linter looks exactly like a failing build and gets
"fixed" by suppressing the wrong thing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, __ported_from__, __origin_engine_md5__
from . import catalogue, reporting
from .config import LinterConfig, emit_warnings
from .engine import UnifiedLinter, disabled_rule_notices
from .fixer import AutoFixer

EXIT_OK, EXIT_VIOLATIONS, EXIT_USAGE, EXIT_ERROR = 0, 1, 2, 3


def _collect(path: Path, linter: UnifiedLinter) -> dict:
    """Lint a file or a directory, returning ``{path: [violations]}``.

    Accepting both shapes at one call site means every subcommand handles a
    file and a directory identically, which is the behaviour users expect from
    every other linter they have used.
    """
    if path.is_file():
        return {str(path): linter.lint_file(str(path))}
    if path.is_dir():
        return linter.lint_directory(str(path))
    raise FileNotFoundError(path)


def _add_common(parser: argparse.ArgumentParser) -> None:
    """Flags shared by ``lint`` and ``fix``, defined once so they cannot drift."""
    parser.add_argument("path", help="feature file or directory to process")
    parser.add_argument("--config", metavar="FILE",
                        help="configuration file (default: nearest "
                             ".unified-lintrc.json, searching upward)")
    parser.add_argument("--no-config", action="store_true",
                        help="ignore any config file and use built-in defaults; "
                             "use this to reproduce published v1.0 numbers")


def build_parser() -> argparse.ArgumentParser:
    """Construct the full argument parser.

    Kept as a function rather than module-level state so the test suite can
    build a fresh parser per test and assert on its behaviour directly.
    """
    parser = argparse.ArgumentParser(
        prog="bddlint",
        description="UnifiedBDDLinter -- detect and safely repair quality "
                    "anti-patterns in Gherkin .feature files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  bddlint lint features/                     report everything, all 28 rules
  bddlint lint f.feature --severity error    only errors and above
  bddlint lint features/ --format sarif      output for a code-review UI
  bddlint fix features/ --dry-run            show what would change
  bddlint fix features/ --output-dir fixed/  repair into a copy, originals kept
  bddlint rules                              what does this tool check?
""")
    parser.add_argument("--version", action="version",
                        version=f"UnifiedBDDLinter {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # -- lint --------------------------------------------------------------
    p_lint = sub.add_parser("lint", help="report violations")
    _add_common(p_lint)
    p_lint.add_argument("--format", choices=["text", "json", "sarif"], default="text",
                        help="output format (default: text)")
    p_lint.add_argument("--severity", choices=["info", "warning", "error", "critical"],
                        default="info",
                        help="minimum severity to report (default: info)")
    p_lint.add_argument("--fail-on", choices=["info", "warning", "error", "critical", "never"],
                        default="error",
                        help="minimum severity that sets exit code 1 "
                             "(default: error). 'never' always exits 0.")
    p_lint.add_argument("--summary", action="store_true",
                        help="print only the summary, not each violation")
    p_lint.add_argument("--no-summary", action="store_true",
                        help="omit the trailing summary block")
    # --spellcheck accepted and ignored: SY001 was removed after finding
    # nothing on a 20,270-file corpus. Kept so existing scripts do not break.
    p_lint.add_argument("--spellcheck", action="store_true", help=argparse.SUPPRESS)
    p_lint.add_argument("--default-rules", action="store_true",
                        help="run only the 18 oracle-checkable rules instead of "
                             "all 28. This is the set the published evaluation "
                             "measures, so use it to reproduce those numbers.")

    # -- fix ---------------------------------------------------------------
    p_fix = sub.add_parser("fix", help="repair what can be safely repaired")
    _add_common(p_fix)
    p_fix.add_argument("--dry-run", action="store_true",
                       help="report what would change without writing anything")
    p_fix.add_argument("--output-dir", metavar="DIR",
                       help="write repaired copies here, leaving originals "
                            "untouched. Strongly recommended.")
    p_fix.add_argument("--quiet", "-q", action="store_true",
                       help="suppress per-file progress; print only the summary")
    p_fix.add_argument("--allow-text-injection", action="store_true",
                       help="ALSO apply the five rules that can only be repaired "
                            "by authoring text (ST001-ST004, ST006), reproducing "
                            "the historical v1.0 behaviour. Writes invented "
                            "feature and scenario names into your files. Not "
                            "used for any reported result.")
    # Older names for the same two states, kept so existing scripts do not break.
    p_fix.add_argument("--safe-fix", action="store_true", help=argparse.SUPPRESS)
    p_fix.add_argument("--legacy-fix", action="store_true", help=argparse.SUPPRESS)

    # -- rules / config / version -----------------------------------------
    p_rules = sub.add_parser("rules", help="list the rule catalogue")
    p_rules.add_argument("--default-rules", action="store_true",
                         help="show only the 18 rules the default mode runs")

    p_config = sub.add_parser("config", help="show the resolved configuration")
    p_config.add_argument("--config", metavar="FILE", help="configuration file to inspect")

    sub.add_parser("version", help="version and provenance")
    return parser


def _load_config(args) -> LinterConfig:
    """Resolve configuration for a subcommand, honouring ``--no-config``."""
    if getattr(args, "no_config", False):
        cfg = LinterConfig()
        cfg.source = "<defaults: --no-config>"
        return cfg
    cfg = LinterConfig.load(path=getattr(args, "config", None),
                            start=str(Path(args.path).resolve().parent)
                            if getattr(args, "path", None) else None)
    emit_warnings(cfg)
    # The compatibility entry points (linter.py, cli.py, auto_fix.py) express
    # their family/fix-class toggles by disabling rule ids. They set this
    # attribute directly; no bddlint subcommand exposes it as a flag.
    for rule_id in getattr(args, "disable", None) or ():
        cfg.disabled.add(rule_id)
    return cfg


def cmd_lint(args) -> int:
    """Run the linter and print a report. Returns the process exit code."""
    cfg = _load_config(args)
    linter = UnifiedLinter(full=not args.default_rules, config=cfg)

    try:
        results = _collect(Path(args.path), linter)
    except FileNotFoundError:
        print(f"bddlint: no such file or directory: {args.path}", file=sys.stderr)
        return EXIT_USAGE

    # Apply the display filter before formatting, so every format shows the
    # same set and `--severity` means the same thing regardless of `--format`.
    results = {path: reporting.filter_by_severity(v, args.severity)
               for path, v in results.items()}

    if args.format == "json":
        print(reporting.format_json(results))
    elif args.format == "sarif":
        print(reporting.format_sarif(results, __version__))
    else:
        if not args.summary:
            body = reporting.format_text(results)
            if body.strip():
                print(body)
        if not args.no_summary:
            print(reporting.summarise(results))

    # A rule that could not run (missing optional dependency) is stated again
    # at the end, because a warning emitted before thousands of lines of report
    # has effectively not been shown to anyone.
    for rule_id in sorted(disabled_rule_notices()):
        print(f"bddlint: note: rule {rule_id} did not run; results are incomplete "
              f"for it", file=sys.stderr)

    if args.fail_on == "never":
        return EXIT_OK
    blocking = [v for group in results.values()
                for v in reporting.filter_by_severity(group, args.fail_on)]
    return EXIT_VIOLATIONS if blocking else EXIT_OK


def cmd_fix(args) -> int:
    """Run the auto-fixer under the configured boundary."""
    # The fixer reads configuration too -- `file_name_style` decides which
    # separator ST007's rename uses. Loading it here rather than only in
    # cmd_lint means a project's policy governs the repair as well as the
    # report, which is the whole point of having a policy file.
    cfg = _load_config(args)
    target = Path(args.path)
    if not target.exists():
        print(f"bddlint: no such file or directory: {args.path}", file=sys.stderr)
        return EXIT_USAGE

    # Refusing in-place repair without an explicit acknowledgement: the fixer
    # rewrites requirements artifacts, and an un-asked-for in-place edit of a
    # directory the user did not expect is not recoverable without version
    # control. --output-dir keeps the originals, so it is the recommended path.
    if not args.output_dir and not args.dry_run:
        print("bddlint: refusing to modify files in place without --output-dir.\n"
              "         Use --output-dir DIR to write repaired copies, or\n"
              "         --dry-run to preview. If you really want in-place edits,\n"
              "         commit your work first and pass --output-dir pointing at\n"
              "         the same tree.", file=sys.stderr)
        return EXIT_USAGE

    # Safe by default. Text injection is opt-in and off the reported route.
    # --legacy-fix is the old spelling of --allow-text-injection.
    inject = (getattr(args, "allow_text_injection", False)
              or getattr(args, "legacy_fix", False))
    # A non-empty output directory is a real trap. The fixer renames files to
    # match their Feature line, so a stale file from an earlier run survives
    # under its own name, the new one is written beside it with a collision
    # suffix, and a subsequent `lint` on that directory silently reports both --
    # roughly double the violations, with nothing to indicate why.
    if args.output_dir:
        existing = list(Path(args.output_dir).glob("**/*.feature")) \
            if Path(args.output_dir).is_dir() else []
        if existing:
            print(f"bddlint: note: {args.output_dir} already contains "
                  f"{len(existing)} .feature file(s). Output will be added "
                  f"alongside them, and repaired files may be renamed, so "
                  f"linting that directory afterwards will include the old "
                  f"ones too. Clear it first for a clean comparison.",
                  file=sys.stderr)

    fixer = AutoFixer(safe=not inject, quiet=args.quiet,
                      file_name_style=cfg.file_name_style)
    if target.is_file():
        fixer.fix_file(str(target), dry_run=args.dry_run, output_dir=args.output_dir)
    else:
        fixer.fix_directory(str(target), dry_run=args.dry_run, output_dir=args.output_dir)

    print(f"\nfiles repaired: {fixer.files_fixed}")
    # State the guarantee rather than leaving it to be inferred: the repair went
    # somewhere else and the input still says exactly what it said.
    if not args.dry_run and args.output_dir:
        print(f"originals unchanged: {target}")
        print(f"repaired copies:     {args.output_dir}")
    # Exactly one of these is non-empty, depending on the mode: either the tool
    # authored text and says what, or it declined to and says what it left.
    for summary in (fixer.injected_summary(), fixer.withheld_summary()):
        if summary:
            print(summary)
    return EXIT_OK


def cmd_rules(args) -> int:
    """Print the rule catalogue."""
    print(catalogue.render_table(full=not args.default_rules))
    return EXIT_OK


def cmd_config(args) -> int:
    """Print the resolved configuration and where each value came from."""
    cfg = LinterConfig.load(path=args.config)
    print(cfg.describe())
    return EXIT_OK


def cmd_version(_args) -> int:
    """Print version and the provenance chain back to the published results."""
    print(f"UnifiedBDDLinter {__version__}")
    print(f"  ported from      {__ported_from__}")
    print(f"  origin engine    md5 {__origin_engine_md5__}")
    print(f"  rules            {len(catalogue.RULES)} "
          f"({len(catalogue.rules_in_mode(False))} in default mode)")
    # The default is form-only. The larger number is reachable only with
    # --allow-text-injection, and stating it first (as this line once did)
    # advertises a mode that writes invented text into requirements files.
    print(f"  auto-fixed       {len(catalogue.fixable(True))} of "
          f"{len(catalogue.RULES)}   ({len(catalogue.RULES) - len(catalogue.fixable(True))} "
          f"reported, never modified)")
    print(f"  python           {sys.version.split()[0]} on {sys.platform}")
    return EXIT_OK


DISPATCH = {"lint": cmd_lint, "fix": cmd_fix, "rules": cmd_rules,
            "config": cmd_config, "version": cmd_version}


def _make_output_encoding_safe() -> None:
    """Ensure stdout and stderr can carry any character a .feature file holds.

    On Windows, Python uses the console encoding when stdout is a terminal but
    the locale encoding -- typically cp1252 -- when it is a pipe or a file. A
    report containing a character cp1252 cannot represent then raises
    UnicodeEncodeError and the command dies.

    That is not hypothetical. `fix --dry-run` prints an arrow, and redirecting
    it to a file on Windows crashed the tool. Feature files in the corpus carry
    Dutch, Russian and Japanese text, so linting them would fail the same way.

    `errors="replace"` rather than "strict": a report that shows one character
    as a substitute is far better than a report that does not appear at all.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue                      # already replaced, or not a text stream
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass                          # detached or closed; nothing to do


def main(argv=None) -> int:
    """Entry point. Returns an exit code rather than calling sys.exit, so the
    test suite can invoke it directly and assert on the result."""
    _make_output_encoding_safe()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_USAGE
    try:
        return DISPATCH[args.command](args)
    except KeyboardInterrupt:
        print("\nbddlint: interrupted", file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:
        # Happens routinely under `bddlint lint ... | head`. Not an error, and
        # the traceback Python would otherwise print is pure noise.
        return EXIT_OK
    except Exception as exc:            # noqa: BLE001 -- top-level guard
        print(f"bddlint: internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("bddlint: this is a bug; please report it with the input file.",
              file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
