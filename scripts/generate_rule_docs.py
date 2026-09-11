#!/usr/bin/env python3
"""
generate_rule_docs.py -- regenerate docs/RULES.md from the rule catalogue.

    python3 scripts/generate_rule_docs.py            write docs/RULES.md
    python3 scripts/generate_rule_docs.py --check    fail if it is out of date

The catalogue in `src/unifiedbddlinter/catalogue.py` is the single source of
truth. Documentation that is maintained by hand alongside code drifts from it,
and rule documentation that is wrong is worse than none -- a reader trusts it
and stops checking.

`--check` is for CI: it regenerates into memory and compares, so a pull request
that adds a rule without regenerating the docs fails rather than merging a lie.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifiedbddlinter import catalogue as c   # noqa: E402

OUTPUT = ROOT / "docs" / "RULES.md"

FAMILIES = [
    ("style", "Style — surface form"),
    ("structure", "Structure — required elements"),
    ("workflow", "Workflow — Given/When/Then discipline"),
    ("quality", "Quality — is this a specification or a script?"),
]


def render() -> str:
    """Build the complete Markdown document as a string."""
    out = ["# Rule reference", ""]
    out += ["Generated from `src/unifiedbddlinter/catalogue.py`. Do not edit by hand —",
            "regenerate with `python3 scripts/generate_rule_docs.py`.", ""]
    out += [f"**{len(c.RULES)} rules.** {len(c.rules_in_mode(False))} run in default "
            f"mode; {len(c.fixable(True))} are safely fixable.", ""]
    out += ["## Fixability", "",
            "| | meaning |", "|---|---|",
            "| `SAFE` | repairable from the file's own content; applied by default |",
            "| `UNSAFE` | repairable only by authoring text the tool cannot derive; "
            "**detect-only** |",
            "| `NONE` | no automatic repair exists or should exist — a human judgement |",
            ""]

    for family, title in FAMILIES:
        out += ["", f"## {title}", "",
                "| ID | Rule | Severity | Fix | Detects |",
                "|---|---|---|---|---|"]
        for rule in c.RULES:
            if rule.category != family:
                continue
            # Mark rules the default (18-rule) run excludes, so a reader
            # comparing against published numbers knows which are absent there.
            note = " *(full mode only)*" if rule.rule_id in c.DEFAULT_MODE_EXCLUDED else ""
            out.append(f"| `{rule.rule_id}` | {rule.name}{note} | {rule.severity} "
                       f"| `{rule.fixability}` | {rule.summary} |")

    # The Quality family is this tool's novel claim, so its mechanism is spelled
    # out rather than left in the source. "Vague scenario name" and
    # "len(name) < 10" earn very different amounts of trust, and a reader
    # deserves the second one.
    out += ["", "## How the Quality family decides", "",
            "Every quality rule is a keyword or shape heuristic over the raw lines.",
            "There is no parsing of meaning, no lexicon and no NLP. That is the cost",
            "of the tool having no dependencies, and it bounds what these rules can",
            "see.", ""]
    for rule in c.RULES:
        if rule.category != "quality":
            continue
        out += [f"**`{rule.rule_id}` — {rule.name}**", "",
                c.QUALITY_MECHANISM.get(rule.rule_id, "_(undocumented)_"), ""]

    return "\n".join(out) + "\n"


def main(argv) -> int:
    content = render()
    if "--check" in argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != content:
            print("docs/RULES.md is out of date; run "
                  "python3 scripts/generate_rule_docs.py", file=sys.stderr)
            return 1
        print("docs/RULES.md is up to date")
        return 0
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}  ({len(c.RULES)} rules)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
