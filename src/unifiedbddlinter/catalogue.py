"""
catalogue.py -- the rule catalogue: what each of the 28 rules is and what
can be done about it.

WHY A SEPARATE TABLE
--------------------
The engine knows a rule's identity only at the moment it fires: id, name,
severity and category are arguments to a ``Violation`` constructor buried
inside a check.  That means v1.0 could not answer "what rules exist?" without
running the linter over a file crafted to trigger every one of them, and could
not answer "which of these can you fix?" at all.

This table answers both questions without running anything.  It drives
``bddlint rules``, the generated ``docs/RULES.md``, and the test that asserts
the catalogue and the engine have not drifted apart.

FIXABILITY IS THE INTERESTING COLUMN
------------------------------------
It encodes the safe-fix boundary described in ``fixer.py``:

  ``SAFE``     repairable from the file's own content.
  ``UNSAFE``   repairable only by authoring text the tool cannot derive from
               the input.
  ``NONE``     no automatic repair exists or should exist -- these are
               judgements about what a requirement means, which is a human's
               call, not a linter's.

EIGHT of twenty-eight are auto-fixed. The five ``UNSAFE`` rules are detect-only:
repairing them means authoring text the tool cannot derive from the input, and a
.feature file is a requirements artifact. They remain reachable behind
``--allow-text-injection`` only so the historical v1.0 behaviour can be
reproduced for provenance.

That six is not a shortfall. On the evaluation corpus the six form rules account
for roughly 69% of all violations detected: the repair that matters was never
the semantic one.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple

SAFE, UNSAFE, NONE = "SAFE", "UNSAFE", "NONE"


class Rule(NamedTuple):
    """One catalogue entry. Mirrors what the engine emits, plus intent."""
    rule_id: str
    name: str
    severity: str      # declared severity; a config override can change it
    category: str      # style | structure | workflow | quality
    fixability: str    # SAFE | UNSAFE | NONE
    summary: str       # what the rule detects, and why it matters


# Ordered by family, then by number -- the order `bddlint rules` prints, which
# groups related checks together instead of scattering them alphabetically.
RULES: List[Rule] = [
    # -- Style: surface form. Cheap to detect, cheap to fix, genuinely safe. --
    Rule("S001", "No trailing spaces", "warning", "style", SAFE,
         "Whitespace at end of line. Invisible, produces noisy diffs."),
    Rule("S002", "No multiple empty lines", "info", "style", SAFE,
         "More than one consecutive blank line."),
    Rule("S003", "EOF newline", "info", "style", SAFE,
         "File does not end with a newline; POSIX tools mishandle the last line."),
    Rule("S004", "Indentation", "error", "style", SAFE,
         "Indentation inconsistent with Gherkin nesting."),
    Rule("S005", "File name format", "error", "style", NONE,
         "File name is not snake_case. Renaming may break external references, "
         "so this is reported for a human to action."),
    Rule("S006", "Name length", "warning", "style", NONE,
         "Feature, scenario or step name exceeds its configured limit. "
         "Shortening requires knowing what may be dropped."),

    # -- Structure: required elements. Mostly UNSAFE -- fixing means inventing. --
    Rule("ST001", "Unnamed feature", "error", "structure", UNSAFE,
         "'Feature:' with no name. A name cannot be derived from the file."),
    Rule("ST002", "Unnamed scenario", "error", "structure", UNSAFE,
         "'Scenario:' with no name. Naming it requires reading its intent."),
    Rule("ST003", "Empty file", "error", "structure", UNSAFE,
         "No content at all. Repair would mean authoring an entire scenario."),
    Rule("ST004", "No feature", "error", "structure", UNSAFE,
         "No 'Feature:' header anywhere in the file."),
    Rule("ST006", "Duplicate scenario name", "warning", "structure", UNSAFE,
         "Two scenarios share a name. v1.0 appended a numeric suffix, which "
         "MASKED the Q008 'similar scenarios' finding underneath."),
    Rule("ST013", "Byte-order mark", "error", "structure", SAFE,
         "File begins with a UTF-8 BOM. The official Gherkin parser rejects the "
         "file outright -- it looks perfect and the suite will not start. "
         "Repaired by deleting three bytes that carry no content; every "
         "character of Gherkin is byte-identical afterwards."),
    Rule("T001", "Duplicate tag", "warning", "structure", SAFE,
         "A tag is repeated on one element. Removing the repeat cannot change "
         "which scenarios a tag filter selects: the tag SET is unchanged, and "
         "Cucumber collapses duplicates anyway."),
    Rule("T006", "Tag indentation mismatch", "info", "style", SAFE,
         "A tag line is not aligned with the element beneath it. Whitespace only."),
    Rule("ST007", "Feature/file name match", "error", "structure", SAFE,
         "Feature name disagrees with file name. Repaired by renaming the FILE, "
         "so no .feature content changes. Safe with Cucumber, which resolves "
         "step definitions by annotation, never by filename."),

    # -- Workflow: Given/When/Then discipline. Where BDD intent actually lives. --
    Rule("W001", "Only one When", "warning", "workflow", NONE,
         "Multiple 'When' steps: the scenario tests more than one action."),
    Rule("W002", "GWT order", "error", "workflow", NONE,
         "Given/When/Then out of order. Reordering could change meaning."),
    Rule("W003", "No verification step", "error", "workflow", NONE,
         "No 'Then'. The scenario asserts nothing, so it cannot fail for the "
         "right reason. Only the author knows what should be asserted."),
    Rule("W004", "No action step", "error", "workflow", NONE,
         "No 'When'. The scenario exercises no behaviour."),
    Rule("W005", "Step with period", "info", "workflow", NONE,
         "Step ends with '.'. DETECT-ONLY: removing the period changes the step "
         "TEXT, which is the string Cucumber matches a step definition against. "
         "A project whose definition includes the period stops binding, and the "
         "step becomes UNDEFINED. Demonstrated with a real `mvn test` run in "
         "misc/binding-experiment/: BUILD SUCCESS before the repair, BUILD "
         "FAILURE after."),
    Rule("W006", "Too many steps", "warning", "workflow", NONE,
         "Step count exceeds the configured maximum; the scenario is doing too "
         "much. Splitting it is a design decision."),

    # -- Quality: is this a specification or a script? The novel family. --
    Rule("Q001", "Implementation detail", "warning", "quality", NONE,
         "Steps name UI mechanics ('click the button') rather than business "
         "intent. v1.0 rewrote these words automatically, which changed what "
         "the test asserted. Now detect-only."),
    Rule("Q002", "Vague language", "info", "quality", NONE,
         "Words like 'properly', 'correctly' that assert nothing checkable."),
    Rule("Q003", "Step count", "info", "quality", NONE,
         "Scenario length signalling a readability problem."),
    Rule("Q004", "Test jargon in name", "info", "quality", NONE,
         "Names containing 'test', 'verify', 'check' -- describes the activity "
         "rather than the behaviour."),
    Rule("Q005", "Vague scenario name", "info", "quality", NONE,
         "Name too short to convey which behaviour is verified ('login')."),
    Rule("Q006", "Mock data", "info", "quality", NONE,
         "Placeholder data ('foo', 'test123') obscuring the real case."),
    Rule("Q008", "Similar scenarios", "info", "quality", NONE,
         "Near-duplicate scenarios that likely should be a Scenario Outline."),
]

# Fast lookup by id, built once at import.
BY_ID: Dict[str, Rule] = {rule.rule_id: rule for rule in RULES}

# The subset the default (non-full) engine runs -- the oracle-checkable rules
# that gherkin-lint and cuke_linter can also be asked about, and therefore the
# set the differential evaluation measures. ST007 and the Quality family are
# excluded because no external tool can corroborate them.
DEFAULT_MODE_EXCLUDED = frozenset(
    ["ST007"] + [r.rule_id for r in RULES if r.category == "quality"]
)


def rules_in_mode(full: bool) -> List[Rule]:
    """Rules the engine actually runs for the given mode."""
    if full:
        return list(RULES)
    return [r for r in RULES if r.rule_id not in DEFAULT_MODE_EXCLUDED]


def fixable(safe_mode: bool = True) -> List[str]:
    """Rule ids the fixer will repair under the given boundary."""
    wanted = {SAFE} if safe_mode else {SAFE, UNSAFE}
    return [r.rule_id for r in RULES if r.fixability in wanted]


# What each fixability class means in each of the fixer's two modes. Stated as
# data rather than prose so `bddlint rules` and the docs cannot disagree.
FIX_BEHAVIOUR = {
    SAFE:   ("repair", "repair"),
    UNSAFE: ("report", "REWRITE"),
    NONE:   ("report", "report"),
}


def render_table(full: bool = True) -> str:
    """Plain-text catalogue, printed by ``bddlint rules``.

    Two behaviour columns rather than one fixability label, because the tool has
    two modes and a single column cannot state what it does without naming which
    mode it means. The column that says ``REWRITE`` is the one a reader needs to
    notice: those rules are repaired by AUTHORING text into the file.
    """
    rows = rules_in_mode(full)
    width = max(len(r.name) for r in rows)
    lines = [
        f"{'ID':<7} {'NAME':<{width}} {'SEV':<8} {'CATEGORY':<10} "
        f"{'DEFAULT':<9} {'CLASS':<10}",
        "-" * (7 + width + 8 + 10 + 21),
    ]
    current = None
    for rule in rows:
        if rule.category != current:
            current = rule.category
            lines.append("")
        default_action, _injecting = FIX_BEHAVIOUR[rule.fixability]
        # The class says WHY the tool behaves that way, which is the part a
        # reader needs in order to trust the behaviour rather than just observe it.
        klass = {SAFE: "form", UNSAFE: "semantic", NONE: "semantic"}[rule.fixability]
        lines.append(
            f"{rule.rule_id:<7} {rule.name:<{width}} {rule.severity:<8} "
            f"{rule.category:<10} {default_action:<9} {klass:<10}"
        )

    safe_count = sum(1 for r in rows if r.fixability == SAFE)
    lines += [
        "",
        f"{len(rows)} rules: {safe_count} form, {len(rows) - safe_count} semantic.",
        "",
        "  repair    the change is derivable from the file's own content, and",
        "            touches nothing the Cucumber runtime binds on",
        "  report    detected, never modified -- because repairing it would",
        "            either invent content only a human can supply, or alter",
        "            step text that a step definition is matched against",
        "",
        f"  The tool repairs {safe_count} rules and reports {len(rows) - safe_count}.",
        "  It never invents text, and never edits a string the runtime binds on.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# How the Quality family decides
# ---------------------------------------------------------------------------
# The Quality family is the novel part of this tool, so the question a reader
# asks first is "on what basis?". The catalogue summaries say what each rule
# means; these say how it is actually computed, because "vague scenario name"
# and "len(name) < 10" invite very different levels of trust.
#
# Every one is a keyword or shape heuristic over the raw lines. There is no
# parsing of meaning, no lexicon and no NLP -- that is the cost of the tool
# having no dependencies, and it bounds what these rules can see.
#
# HAND-MAINTAINED: these strings describe code in QualityRules.check_all().
# Change one and you must change the other. A test checks they stay in step.

QUALITY_MECHANISM = {
    "Q001": (
        "A specification should say what the business needs, not which buttons "
        "to press. Case-insensitive substring match on step lines against 19 UI "
        "terms (clicks, selects, types, button, link, checkbox, ...). First hit "
        "wins, at most one violation per step."),
    "Q002": (
        "Words that sound like a requirement but cannot be tested. "
        "Word-boundary regex on step lines against 12 terms (basic, simple, "
        "some, various, stuff, etc, maybe, probably, might, ...). The word "
        "boundary matters: 'some' must not fire inside 'something'."),
    "Q003": (
        "A scenario with two steps usually has not finished explaining itself, "
        "and one with fifteen is doing several things at once. Counts step "
        "lines between Scenario headers and flags anything outside 3-7. "
        "Configurable via limits.min_steps_per_scenario / "
        "max_steps_per_scenario."),
    "Q004": (
        "The name should describe the behaviour, not the fact that it is being "
        "tested. Word-boundary regex on the scenario name for test, verify, "
        "check, validate, should."),
    "Q005": (
        "A name too short to say which behaviour is checked -- 'Login' does not "
        "distinguish success from lockout. Implemented purely as "
        "len(name) < 10 characters. This is the crudest rule in the family: a "
        "short precise name is flagged, and a long vague one is not."),
    "Q006": (
        "Placeholder values hide the real case -- 'password123' does not say "
        "whether the rule concerns length, expiry or reuse. Five case-"
        "insensitive regexes on step lines: user123, test@test.com, "
        "password123, john[ ._-]?doe, 999-99-9999."),
    "Q008": (
        "Two scenarios opening the same way are usually one scenario with "
        "different data, which Gherkin already expresses as a Scenario "
        "Outline. Keys each scenario by its first three words, lowercased, and "
        "reports a repeat against the line of the first occurrence."),
}
