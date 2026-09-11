"""
reporting.py -- turning violations into output.

Separated from the engine because *finding* a problem and *presenting* it are
different jobs with different audiences.  v1.0 mixed them: ``format_output``
lived on the linter class, so adding an output format meant editing the engine,
and the two entry points (``cli.py``, ``linter.py``) each grew their own
divergent copy of the summary logic.

Three formats, chosen for three consumers:

    text   a person reading a terminal
    json   another program (CI gates, the evaluation harness)
    sarif  a code-review UI -- GitHub, GitLab and VS Code all ingest SARIF,
           so this is what puts findings inline on a pull request

One invariant applies to all of them: **report output goes to stdout, and
diagnostics about the tool itself go to stderr**.  Without that split,
``bddlint lint --format json | jq`` breaks the moment a config warning appears.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List

from .catalogue import BY_ID

# Ordering used wherever severities are ranked, weakest first.
SEVERITY_RANK: Dict[str, int] = {"info": 0, "warning": 1, "error": 2, "critical": 3}

# SARIF has only three levels, so `critical` and `error` both map to "error".
# Losing that distinction is acceptable: the rule id is still carried, and a
# reviewer acts on both the same way.
_SARIF_LEVEL = {"info": "note", "warning": "warning", "error": "error", "critical": "error"}


def filter_by_severity(violations: Iterable, minimum: str) -> List:
    """Keep violations at *minimum* severity or above.

    "or above" rather than "exactly" is what a user means by ``--severity
    error``: show me the serious things, not only the things at one precise
    level.
    """
    floor = SEVERITY_RANK[minimum]
    return [v for v in violations if SEVERITY_RANK[v.severity.value] >= floor]


# Matches a leading "Line 12:" / "Line 12 " that a rule message already carries.
_LINE_PREFIX = re.compile(r"^Line\s+\d+\s*:?\s*")


def _display_path(file_path: str) -> str:
    """Shorten a path against the working directory when that is where it lives.

    An absolute corpus path is often longer than the terminal is wide. Printing
    it relative to the cwd keeps the useful half on screen; a path outside the
    cwd is left alone, because shortening it would be a lie.
    """
    try:
        return str(Path(file_path).resolve().relative_to(Path.cwd()))
    except (ValueError, OSError):
        return file_path


def format_text(results: Dict[str, List], show_suggestions: bool = True) -> str:
    """Human-readable report, grouped by file.

    The file path is printed once as a heading rather than repeated on every
    line. Repeating it pushes the part a reader actually needs -- the rule and
    the message -- off the right-hand edge of the terminal, which is the one
    thing a report must never do.

    Each violation is then three short lines: what rule fired, where, and what
    to do about it. `path:line:column` still appears on the location line, so it
    stays clickable in editors and terminals that recognise that shape.
    """
    out: List[str] = []
    for file_path in sorted(results):
        violations = results[file_path]
        if not violations:
            continue
        shown = _display_path(file_path)
        out.append("")
        out.append(shown)
        out.append("=" * min(len(shown), 78))
        for v in violations:
            name = getattr(BY_ID.get(v.rule_id), "name", "")
            heading = f"[{v.severity.value.upper():<7}] {v.rule_id}"
            out.append(f"{heading}: {name}" if name else heading)
            # Several rules already open their message with "Line N: ". Printing
            # the location as well would say it twice, so drop the duplicate.
            message = _LINE_PREFIX.sub("", v.message, count=1) or v.message
            out.append(f"  Line {v.line}: {message}")
            if show_suggestions and v.suggestion:
                out.append(f"  Suggestion: {v.suggestion}")
    return "\n".join(out)


def format_json(results: Dict[str, List]) -> str:
    """Machine-readable report.

    Sorted keys and a fixed indent make the output diffable: two runs over the
    same input produce byte-identical JSON, so a regression shows up as a real
    change rather than as key reordering.
    """
    payload = {
        "version": 1,
        "files": [
            {
                "path": file_path,
                "violations": [
                    {
                        "line": v.line,
                        "column": v.column,
                        "rule": v.rule_id,
                        "name": v.rule_name,
                        "severity": v.severity.value,
                        "category": v.category,
                        "message": v.message,
                        "suggestion": v.suggestion,
                    }
                    for v in results[file_path]
                ],
            }
            for file_path in sorted(results)
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def format_sarif(results: Dict[str, List], tool_version: str) -> str:
    """SARIF 2.1.0 -- the format code-review platforms consume.

    Rules are declared once in ``driver.rules`` and referenced by index from
    each result, which is what SARIF requires and what keeps the document small
    when one rule fires ten thousand times across a corpus.
    """
    rule_index: Dict[str, int] = {}
    rules: List[dict] = []
    findings: List[dict] = []

    for file_path in sorted(results):
        for v in results[file_path]:
            if v.rule_id not in rule_index:
                rule_index[v.rule_id] = len(rules)
                rules.append({
                    "id": v.rule_id,
                    "name": v.rule_name,
                    "shortDescription": {"text": v.rule_name},
                    "properties": {"category": v.category},
                })
            findings.append({
                "ruleId": v.rule_id,
                "ruleIndex": rule_index[v.rule_id],
                "level": _SARIF_LEVEL[v.severity.value],
                "message": {"text": v.message},
                "locations": [{
                    "physicalLocation": {
                        # SARIF uri fields must use forward slashes on every
                        # platform, so a report produced on Windows is readable
                        # by a Linux CI runner.
                        "artifactLocation": {"uri": file_path.replace("\\", "/")},
                        # SARIF columns are 1-based; the engine records 0-based
                        # offsets, hence the +1. Off-by-one here would point a
                        # reviewer at the wrong character.
                        "region": {"startLine": max(1, v.line),
                                   "startColumn": max(1, v.column + 1)},
                    }
                }],
            })

    return json.dumps({
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "UnifiedBDDLinter",
                "version": tool_version,
                "informationUri": "https://github.com/vaishnavkoka/UnifiedBDDLinter",
                "rules": rules,
            }},
            "results": findings,
        }],
    }, indent=2)


def summarise(results: Dict[str, List]) -> str:
    """Counts by severity, by category and by rule.

    Printed after every run because the per-violation listing is unreadable at
    corpus scale -- twenty thousand files produce output no one scrolls through,
    and the only question that survives is "what is wrong, and how much of it".
    """
    files_total = len(results)
    files_clean = sum(1 for v in results.values() if not v)
    all_violations = [v for group in results.values() for v in group]

    # One file is the interactive case: the listing above is short and already
    # readable, so a full breakdown restates what the reader just saw. A single
    # line closes it off instead. The block earns its place across many files,
    # where nobody scrolls the listing and the counts are the whole answer.
    if files_total == 1:
        errors = sum(1 for v in all_violations
                     if getattr(v.severity, "value", v.severity) == "error")
        return (f"Summary: 1 file, {len(all_violations)} violation(s), "
                f"{errors} error(s)")

    by_severity: Dict[str, int] = {}
    by_category: Dict[str, int] = {}
    by_rule: Dict[str, int] = {}
    for v in all_violations:
        by_severity[v.severity.value] = by_severity.get(v.severity.value, 0) + 1
        by_category[v.category or "uncategorised"] = by_category.get(v.category or "uncategorised", 0) + 1
        by_rule[v.rule_id] = by_rule.get(v.rule_id, 0) + 1

    out = ["", "=" * 62, "SUMMARY", "=" * 62,
           f"  files scanned      {files_total}",
           f"  files clean        {files_clean}",
           f"  violations         {len(all_violations)}"]

    if by_severity:
        out.append("")
        out.append("  by severity")
        # Strongest first: a reader scanning the top of the block should see
        # the things that block a build before the advisory noise.
        for name in sorted(by_severity, key=lambda s: -SEVERITY_RANK[s]):
            out.append(f"    {name:<10} {by_severity[name]}")

    if by_category:
        out.append("")
        out.append("  by category")
        for name in sorted(by_category):
            out.append(f"    {name:<14} {by_category[name]}")

    if by_rule:
        out.append("")
        # Ten is enough to show where the mass is without becoming a table;
        # the full breakdown is available via --format json.
        out.append("  top rules")
        ranked = sorted(by_rule.items(), key=lambda kv: (-kv[1], kv[0]))
        for rule_id, count in ranked[:10]:
            out.append(f"    {rule_id:<8} {count}")
        if len(ranked) > 10:
            out.append(f"    ... and {len(ranked) - 10} more rules (use --format json)")

    out.append("=" * 62)
    return "\n".join(out)
