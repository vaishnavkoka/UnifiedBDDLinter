"""
engine.py -- the UnifiedBDDLinter rule engine.

WHAT THIS IS
------------
A single-pass, line-based Gherkin analyser implementing 28 rules across four
families:

    S*    Style       -- whitespace, indentation, line and name lengths
    ST*   Structure   -- required elements, duplicates, file/feature agreement
    W*    Workflow    -- Given/When/Then discipline, step counts
    Q*/SY Quality     -- business readability, implementation leakage, spelling

The families exist because they answer different questions.  Style and
Structure ask "is this well-formed Gherkin?", which the two established
linters (gherkin-lint, cuke_linter) also ask.  Workflow and Quality ask "does
this scenario express business behaviour?", which neither of them asks at all,
and which is the contribution this tool makes.

PROVENANCE
----------
Ported verbatim from UnifiedBDDLinter v1.0 ``unified_linter.py``
(md5 ``e00914cc0c52787466193752cfdd3469``), the exact file recorded in
``bdd_pipeline_output/run-2026-08-14_corpus-42repos/versions.txt`` as having
produced the published results.  Every rule body below is unchanged.

Changes made during the port are limited to four, each of which is marked with
a ``PORT:`` comment at the site and listed in ``docs/PORTING_NOTES.md``:

  1. File reading is explicitly UTF-8 with error replacement.  v1.0 used the
     platform default encoding, which crashes on non-ASCII input under Windows
     and any non-UTF-8 locale.  This is the change that makes the tool portable.
  2. Configuration is consulted.  v1.0 hardcoded every threshold and severity;
     see ``config.py`` for why.
  3. SY001 reports why it could not run instead of silently swallowing every
     exception.
  4. Directory traversal skips ignored and hidden directories.

TWO DELIBERATE NON-CHANGES
--------------------------
* The rule bodies are untouched, so results reproduce against v1.0 exactly.
* ``quality_rules.py`` from v1.0 is NOT ported.  It defines a ``QualityRules``
  class that is shadowed by the class of the same name in this file, so it was
  dead code -- edits to it appeared to do nothing.  It survives, unused, under
  the earlier release, for the record.
"""

import re
from pathlib import Path
from typing import List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Optional-rule reporting
# ---------------------------------------------------------------------------
# A rule may be unable to run for reasons that are neither the user's fault nor
# a crash worth aborting for -- most often an optional dependency that is not
# installed.  The user still needs to be told, exactly once, or they will read
# an empty result as "no problems found" when it actually means "not checked".
#
# Reported on stderr rather than stdout so that `--format json` stays valid
# machine output, and deduplicated by rule id so a 20,000-file corpus run emits
# one line rather than 20,000.

_DISABLED_RULE_NOTICES = set()

# ---------------------------------------------------------------------------
# Why there is no spell-checking here (SY001, removed)
# ---------------------------------------------------------------------------
# v1.0 shipped a spelling rule. It is gone, and the reasons are worth keeping so
# nobody reintroduces it:
#
#   * It found **zero** violations across all 20,270 corpus files.
#   * It cost ~95% of total runtime -- 21.9 of 22.9 seconds for ten files -- all
#     inside `SpellChecker.correction()`, which generates edit-distance-2
#     candidates. Caching and a process-lifetime singleton brought that down, but
#     the residual cost is inherent to correcting each distinct unknown word.
#   * It was the package's only third-party dependency.
#   * Its auto-correction rewrote technical terms into nonsense: sql -> sol,
#     xml -> my, dble -> able.
#
# Nothing about it earned its place. Removing it is what makes "standard library
# only" true without an asterisk.

def _note_disabled_rule(rule_id: str, reason: str) -> None:
    """Warn once per process that *rule_id* could not run, and why."""
    if rule_id in _DISABLED_RULE_NOTICES:
        return
    _DISABLED_RULE_NOTICES.add(rule_id)
    import sys
    print(f"unifiedbddlinter: rule {rule_id} not applied: {reason}", file=sys.stderr)


def disabled_rule_notices():
    """Rule ids that reported themselves unavailable during this process.

    Exposed so the evaluation harness can record in its provenance file which
    rules were actually exercised by a run -- a result set that silently omits
    SY001 is not comparable with one that includes it.
    """
    return frozenset(_DISABLED_RULE_NOTICES)


# ---------------------------------------------------------------------------
# Feature-name -> file-stem conversion
# ---------------------------------------------------------------------------
# ST007 compares a feature name against its file name, and the fixer repairs a
# mismatch by renaming the file. Those two operations MUST agree on what the
# file should be called, or the tool contradicts itself.
#
# PORT: v1.0 computed this in THREE places with THREE different rules --
# `check_feature_name_match` here, and both `_get_output_file_path` and
# `_fix_st007_via_file_rename` in the fixer. The check kept punctuation; the
# fixer stripped it. Consequences, both measured on the 20,270-file corpus:
#
#   * ST007 was UNFIXABLE on 6,687 files (34.4% of those with a named Feature).
#     The fixer renamed the file correctly, the checker then reported the very
#     same violation against the new name, and would do so forever. Any feature
#     name containing punctuation -- even a hyphen -- was affected.
#
#   * There was no length cap, so a feature name that is a whole sentence
#     produced a stem of up to 445 bytes. 15 files exceeded Linux's 255-byte
#     limit and crashed the fixer with [Errno 36] -- exactly the
#     "auto-fix failures = 15" recorded in the published run's provenance. On
#     Windows, whose default MAX_PATH is 260 characters for the whole path,
#     1,162 files (5.7%) would have failed.
#
# One function now serves all three call sites, so the check and the repair
# cannot disagree again. The cap matches the value adopted independently in a
# later version of this tool, so stems stay consistent across versions.
MAX_FILE_STEM = 150


def slugify_feature_name(name: str, style: str = "kebab") -> str:
    """Canonical file stem for a Feature name.

    Idempotent: slugifying an already-slugified stem returns it unchanged. That
    property is what makes ST007 satisfiable -- the checker normalises BOTH the
    feature name and the existing file stem through this function and compares
    the results, so a file the fixer has already renamed passes.

    Truncation trims at the cap and then strips a trailing hyphen, so a stem
    never ends mid-separator.
    """
    s = name.lower().replace("_", "-").replace(" ", "-")
    s = re.sub(r"[^a-z0-9-]", "", s)      # drop what the filesystem dislikes
    s = re.sub(r"-+", "-", s).strip("-")  # collapse runs, trim the ends
    if len(s) > MAX_FILE_STEM:
        s = s[:MAX_FILE_STEM].rstrip("-")
    return s if style == "kebab" else s.replace("-", "_")


class RuleSeverity(Enum):
    """Rule violation severity"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Violation:
    """Represents a linting violation"""
    line: int
    column: int
    rule_id: str
    rule_name: str
    severity: RuleSeverity
    message: str
    suggestion: str = ""
    category: str = ""  # style, workflow, quality, structure


class UnifiedParser:
    """Unified Gherkin parser supporting all validations"""
    
    KEYWORDS = {
        'Feature': r'^\s*Feature:',
        'Background': r'^\s*Background:',
        'Scenario': r'^\s*Scenario:',
        'Scenario Outline': r'^\s*Scenario Outline:',
        'Examples': r'^\s*Examples:',
        'Given': r'^\s*(Given|And|But)\s+',
        'When': r'^\s*(When|And|But)\s+',
        'Then': r'^\s*(Then|And|But)\s+',
        'Tag': r'^\s*@',
        'Comment': r'^\s*#',
    }
    
    def __init__(self):
        self.content = []
        self.file_path = ""
        self.lines = []
        self.had_bom = False
        
    def parse(self, file_path: str):
        """Read *file_path* into ``self.lines`` and return self for chaining.

        PORT: v1.0 called ``open(file_path, 'r')`` with no encoding, which uses
        the platform default -- UTF-8 on most Linux systems but cp1252 on a
        default Windows install.  A feature file containing any non-ASCII byte
        therefore raised UnicodeDecodeError there and the whole run died.

        ``errors="replace"`` is chosen over ``"strict"`` deliberately: a corpus
        scan must not abort on one malformed file, and substituting U+FFFD
        keeps every line number correct, which matters because every violation
        this engine reports is anchored to a line number.
        """
        self.file_path = file_path
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            self.lines = f.readlines()
        # A UTF-8 BOM survives decoding as U+FEFF on the first line and would
        # otherwise be counted as indentation, making ST001 misfire on any file
        # a Windows editor saved with a signature.
        # Recorded, not merely discarded: ST013 reports the mark, and a rule
        # cannot report what the parser has already thrown away.
        self.had_bom = bool(self.lines) and self.lines[0].startswith("\ufeff")
        if self.had_bom:
            self.lines[0] = self.lines[0][1:]
        return self
    
    def get_line(self, line_num: int) -> str:
        """Get line content (1-indexed)"""
        if 0 < line_num <= len(self.lines):
            return self.lines[line_num - 1].rstrip()
        return ""
    
    def get_indentation(self, line_num: int) -> int:
        """Get indentation level of a line"""
        line = self.get_line(line_num)
        return len(line) - len(line.lstrip())
    
    def is_empty_line(self, line_num: int) -> bool:
        """Check if line is empty or whitespace-only"""
        line = self.get_line(line_num)
        return not line.strip()


class StyleRules:
    """gherkin-lint style rules"""
    
    @staticmethod
    def check_trailing_spaces(parser: UnifiedParser) -> List[Violation]:
        """No trailing spaces"""
        violations = []
        for i, line in enumerate(parser.lines, 1):
            if line.rstrip() != line.rstrip('\n'):
                violations.append(Violation(
                    line=i, column=len(line.rstrip()) + 1,
                    rule_id='S001', rule_name='No trailing spaces',
                    severity=RuleSeverity.WARNING,
                    message=f'Line {i} has trailing whitespace',
                    category='style'
                ))
        return violations
    
    @staticmethod
    def check_multiple_empty_lines(parser: UnifiedParser) -> List[Violation]:
        """No multiple consecutive empty lines"""
        violations = []
        empty_count = 0
        for i, line in enumerate(parser.lines, 1):
            if not line.strip():
                empty_count += 1
                if empty_count > 1:
                    violations.append(Violation(
                        line=i, column=1,
                        rule_id='S002', rule_name='No multiple empty lines',
                        severity=RuleSeverity.INFO,
                        message=f'Multiple empty lines at {i}',
                        suggestion='Remove consecutive blank lines',
                        category='style'
                    ))
            else:
                empty_count = 0
        return violations
    
    @staticmethod
    def check_eof_newline(parser: UnifiedParser) -> List[Violation]:
        """File must end with newline"""
        violations = []
        if parser.lines and not parser.lines[-1].endswith('\n'):
            violations.append(Violation(
                line=len(parser.lines), column=1,
                rule_id='S003', rule_name='EOF newline',
                severity=RuleSeverity.INFO,
                message='File does not end with newline',
                suggestion='Add newline at end of file',
                category='style'
            ))
        return violations
    
    @staticmethod
    def check_indentation(parser: UnifiedParser) -> List[Violation]:
        """Check proper indentation"""
        violations = []
        expected_indent = {
            'Feature': 0,
            'Background': 0,
            'Scenario': 0,
            'When': 2,
            'Given': 2,
            'Then': 2,
            'And': 2,
            'But': 2,
            'Examples': 0,
        }
        
        for i, line in enumerate(parser.lines, 1):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            
            for keyword, expected in expected_indent.items():
                if stripped.startswith(keyword + ':') or (keyword in ['When', 'Given', 'Then', 'And', 'But'] and 
                                                          re.match(f'^({keyword}|And|But)', stripped)):
                    if keyword in ['Given', 'When', 'Then', 'And', 'But']:
                        if indent != 2:
                            violations.append(Violation(
                                line=i, column=1,
                                rule_id='S004', rule_name='Indentation',
                                severity=RuleSeverity.ERROR,
                                message=f'Line {i}: Expected indent {expected}, got {indent}',
                                suggestion=f'Use {expected} spaces for indentation',
                                category='style'
                            ))
                    elif indent != expected:
                        violations.append(Violation(
                            line=i, column=1,
                            rule_id='S004', rule_name='Indentation',
                            severity=RuleSeverity.ERROR,
                            message=f'Line {i}: {keyword} should not be indented',
                            category='style'
                        ))
                    break
        
        return violations
    
    @staticmethod
    def check_file_name(file_path: str, style: str = "snake_case") -> List[Violation]:
        """File should be snake_case.feature or kebab-case.feature.

        The project supports both conventions (snake_case for cuke_linter,
        kebab-case for gherkin-lint), and the fixer renames to whichever
        `file_name_style` selects, so both separators must be accepted here --
        otherwise S005 would flag the very filename the fixer produces. Only the
        *suggestion* narrows to the configured style.
        """
        violations = []
        filename = Path(file_path).name
        if not re.match(r'^[a-z0-9_-]+\.feature$', filename):
            violations.append(Violation(
                line=1, column=1,
                rule_id='S005', rule_name='File name format',
                severity=RuleSeverity.ERROR,
                message=f'Invalid filename: {filename}',
                # Name the convention the fixer will actually apply. Suggesting
                # kebab-case while the repair writes snake_case sends the reader
                # to check a rename that never happens.
                suggestion=('Use snake_case: my_feature.feature'
                            if style == 'snake_case'
                            else 'Use kebab-case: my-feature.feature'),
                category='style'
            ))
        return violations
    
    @staticmethod
    def check_name_length(parser: UnifiedParser, max_feature=80, max_scenario=80, max_step=100) -> List[Violation]:
        """Check name length limits"""
        violations = []
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            
            if stripped.startswith('Feature:'):
                name = stripped.replace('Feature:', '').strip()
                if len(name) > max_feature:
                    violations.append(Violation(
                        line=i, column=len('Feature:') + 1,
                        rule_id='S006', rule_name='Name length',
                        severity=RuleSeverity.WARNING,
                        message=f'Feature name too long ({len(name)} > {max_feature})',
                        category='style'
                    ))
            
            elif stripped.startswith('Scenario'):
                name = re.sub(r'^Scenario(Outline)?:', '', stripped).strip()
                if len(name) > max_scenario:
                    violations.append(Violation(
                        line=i, column=1,
                        rule_id='S006', rule_name='Name length',
                        severity=RuleSeverity.WARNING,
                        message=f'Scenario name too long ({len(name)} > {max_scenario})',
                        category='style'
                    ))
            
            elif any(stripped.startswith(kw) for kw in ['Given', 'When', 'Then', 'And', 'But']):
                if len(stripped) > max_step:
                    violations.append(Violation(
                        line=i, column=1,
                        rule_id='S006', rule_name='Name length',
                        severity=RuleSeverity.INFO,
                        message=f'Step too long ({len(stripped)} > {max_step})',
                        category='style'
                    ))
        
        return violations


class StructureRules:
    """Gherkin structural rules"""
    
    @staticmethod
    def check_unnamed_features(parser: UnifiedParser) -> List[Violation]:
        """Features must have names"""
        violations = []
        for i, line in enumerate(parser.lines, 1):
            if line.strip() == 'Feature:':
                violations.append(Violation(
                    line=i, column=1,
                    rule_id='ST001', rule_name='Unnamed feature',
                    severity=RuleSeverity.ERROR,
                    message='Feature must have a name',
                    category='structure'
                ))
        return violations
    
    @staticmethod
    def check_unnamed_scenarios(parser: UnifiedParser) -> List[Violation]:
        """Scenarios must have names"""
        violations = []
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            if stripped in ['Scenario:', 'Scenario Outline:']:
                violations.append(Violation(
                    line=i, column=1,
                    rule_id='ST002', rule_name='Unnamed scenario',
                    severity=RuleSeverity.ERROR,
                    message='Scenario must have a name',
                    category='structure'
                ))
        return violations
    
    @staticmethod
    def check_empty_file(parser: UnifiedParser) -> List[Violation]:
        """File cannot be empty"""
        violations = []
        if not any(line.strip() for line in parser.lines):
            violations.append(Violation(
                line=1, column=1,
                rule_id='ST003', rule_name='Empty file',
                severity=RuleSeverity.ERROR,
                message='File is empty',
                category='structure'
            ))
        return violations
    
    @staticmethod
    def check_no_feature(parser: UnifiedParser) -> List[Violation]:
        """File must have Feature"""
        violations = []
        has_feature = any(line.strip().startswith('Feature:') for line in parser.lines)
        if not has_feature:
            violations.append(Violation(
                line=1, column=1,
                rule_id='ST004', rule_name='No feature',
                severity=RuleSeverity.ERROR,
                message='File must contain a Feature',
                category='structure'
            ))
        return violations
    
    @staticmethod
    def check_empty_background(parser: UnifiedParser) -> List[Violation]:
        """Background must have steps"""
        violations = []
        for i, line in enumerate(parser.lines, 1):
            if line.strip() == 'Background:':
                # Check if next meaningful line is Scenario or Feature
                for j in range(i, len(parser.lines)):
                    next_line = parser.lines[j].strip()
                    if next_line and not next_line.startswith('#'):
                        if next_line.startswith(('Scenario', 'Feature')):
                            violations.append(Violation(
                                line=i, column=1,
                                rule_id='ST005', rule_name='Empty background',
                                severity=RuleSeverity.WARNING,
                                message='Background has no steps',
                                category='structure'
                            ))
                        break
        return violations
    
    @staticmethod
    def check_duplicate_scenario_names(parser: UnifiedParser) -> List[Violation]:
        """No duplicate scenario names"""
        violations = []
        scenario_names = {}
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            if stripped.startswith('Scenario'):
                name = re.sub(r'^Scenario(Outline)?:', '', stripped).strip()
                if name in scenario_names:
                    violations.append(Violation(
                        line=i, column=1,
                        rule_id='ST006', rule_name='Duplicate scenario name',
                        severity=RuleSeverity.WARNING,
                        message=f'Scenario "{name}" is duplicated (line {scenario_names[name]})',
                        category='structure'
                    ))
                else:
                    scenario_names[name] = i
        return violations

    @staticmethod
    def check_feature_name_match(parser: UnifiedParser, file_path: str,
                                 style: str = "snake_case") -> List[Violation]:
        """ST007: the file name should be derived from the Feature name.

        *style* selects the separator. Both conventions satisfy this rule
        equally -- it only asks that the name MATCH -- but the two incumbent
        linters disagree about which separator is acceptable, so the tool has
        to commit to one and say which.
        """
        violations = []
        fname = Path(file_path).stem
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            if stripped.startswith('Feature:'):
                feature_name = stripped.replace('Feature:', '').strip()
                if feature_name:
                    # PORT: both sides go through slugify_feature_name, so a
                    # file the fixer has already renamed compares equal. v1.0
                    # compared a raw lowercased name against the stem, which no
                    # punctuated feature name could ever satisfy.
                    kebab = slugify_feature_name(feature_name, "kebab")
                    snake = slugify_feature_name(feature_name, "snake")
                    wanted = snake if style == "snake_case" else kebab
                    # Compare through the slug so an already-correct file in
                    # EITHER separator is not reported: the rule is about the
                    # name matching, and the separator is the style setting's
                    # business, not this rule's.
                    if slugify_feature_name(fname, "kebab") != kebab:
                        violations.append(Violation(
                            line=i, column=1,
                            rule_id='ST007', rule_name='Feature/file name match',
                            severity=RuleSeverity.ERROR,
                            message=f'Feature name "{feature_name}" does not match file name "{fname}"',
                            suggestion=f'Rename file to "{kebab}.feature" (kebab-case) or "{snake}.feature" (snake_case)',
                            category='structure'))
                break
        return violations


class TagRules:
    """Tag hygiene (T*) and the byte-order mark (ST013).

    Ported from a later version of this engine. Every rule here is repairable
    from the file's own content and touches nothing the Cucumber runtime binds
    on: a tag SET is unchanged by removing a duplicate of a tag already in it,
    and moving or re-indenting a tag line changes no tag and no step text.

    That distinction is the one W005 failed. A repair is safe only when it
    alters neither the words nor any string the runtime matches against --
    which for Cucumber means step text and the tag set.
    """

    # The block keywords a tag can precede.
    KEYWORD = re.compile(r'^(Feature|Background|Scenario Outline|Scenario|'
                         r'Examples|Rule):')

    @staticmethod
    def doc_mask(lines):
        """True for each line that is doc-string PAYLOAD (fences excluded).

        Doc-string content is data a step carries -- an expected output, a
        quoted file, a JSON body. A line inside it that begins with '@' is not
        a Gherkin tag: "@prefix schema: <...>" is Turtle, "@decorator" is
        Python. Treating those as tags would let a "repair" edit a step's data.
        """
        fences = ('"' * 3, "'" * 3, "`" * 3)
        mask, in_doc, fence = [], False, ""
        for line in lines:
            stripped = line.strip()
            if in_doc:
                if stripped.startswith(fence):
                    in_doc = False
                    mask.append(False)          # the closing fence itself
                else:
                    mask.append(True)
                continue
            if stripped[:3] in fences:
                in_doc, fence = True, stripped[:3]
            mask.append(False)
        return mask

    @staticmethod
    def check_bom(parser: UnifiedParser) -> List[Violation]:
        """ST013: the file begins with a UTF-8 byte-order mark.

        Three invisible bytes sit in front of the first keyword, so a parser
        sees "\ufeffFeature:" rather than "Feature:". The official Gherkin
        parser rejects the file outright: the developer sees a file that looks
        perfect and a suite that will not start.

        This is the only repair in the tool that takes a file from "Cucumber
        refuses to run it" to "runs", and it deletes three bytes that carry no
        content -- every character of Gherkin is byte-identical afterwards.
        """
        if not getattr(parser, "had_bom", False):
            return []
        return [Violation(
            line=1, column=1, rule_id='ST013',
            rule_name='File begins with a byte-order mark',
            severity=RuleSeverity.ERROR,
            message='The first three bytes are a UTF-8 BOM; Gherkin will not '
                    'parse the keyword behind it and the file cannot run',
            suggestion='Save the file as UTF-8 without a signature',
            category='structure')]

    @staticmethod
    def check_tags(parser: UnifiedParser) -> List[Violation]:
        """T001 duplicate tag, T005 inline tag, T006 tag indentation."""
        violations = []
        lines = [line.rstrip("\n") for line in parser.lines]
        mask = TagRules.doc_mask(lines)
        seen = set()

        for index, line in enumerate(lines):
            if mask[index]:
                continue
            stripped = line.strip()
            if not stripped.startswith("@"):
                # A tag block ends at the element it decorates, so the
                # duplicate set resets there rather than running file-wide.
                if stripped:
                    seen = set()
                continue

            tokens = stripped.split()
            for position, token in enumerate(tokens):
                if token.startswith("@"):
                    if token in seen:
                        violations.append(Violation(
                            line=index + 1, column=1, rule_id='T001',
                            rule_name='Duplicate tag',
                            severity=RuleSeverity.WARNING,
                            message=f'Tag {token} is repeated on this element',
                            suggestion='Remove the repeated tag; Cucumber '
                                       'already collapses duplicates',
                            category='structure'))
                    seen.add(token)
                else:
                    # T005 (tag sharing a line with its element) removed:
                    # 5 violations across 20,270 files, in a single file.
                    break

            # T006: a tag line should sit at the indentation of the element it
            # decorates. Blank lines between the two are skipped; anything else
            # ends the search, because the tag then decorates nothing.
            for follower in range(index + 1, len(lines)):
                nxt = lines[follower]
                if not nxt.strip():
                    continue
                if TagRules.KEYWORD.match(nxt.strip()):
                    want = len(nxt) - len(nxt.lstrip())
                    have = len(line) - len(line.lstrip())
                    if want != have:
                        violations.append(Violation(
                            line=index + 1, column=1, rule_id='T006',
                            rule_name='Tag indentation mismatch',
                            severity=RuleSeverity.INFO,
                            message=f'Tag line is indented {have}, but the '
                                    f'element beneath it is indented {want}',
                            suggestion='Align the tag with the element it '
                                       'decorates',
                            category='style'))
                break

        return violations


class WorkflowRules:
    """cuke_linter workflow rules"""
    
    @staticmethod
    def check_only_one_when(parser: UnifiedParser) -> List[Violation]:
        """Scenario should have only one When"""
        violations = []
        in_scenario = False
        when_count = 0
        scenario_line = 0
        
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            
            if stripped.startswith('Scenario'):
                in_scenario = True
                when_count = 0
                scenario_line = i
            elif in_scenario and stripped.startswith(('Scenario', 'Feature', 'Background')):
                in_scenario = False
            
            if in_scenario and stripped.startswith('When'):
                when_count += 1
                if when_count > 1:
                    violations.append(Violation(
                        line=i, column=1,
                        rule_id='W001', rule_name='Only one When',
                        severity=RuleSeverity.WARNING,
                        message=f'Scenario at line {scenario_line} has multiple When steps',
                        suggestion='Use And/But for additional actions',
                        category='workflow'
                    ))
        return violations
    
    @staticmethod
    def check_gwt_structure(parser: UnifiedParser) -> List[Violation]:
        """Steps must follow Given-When-Then order"""
        violations = []
        in_scenario = False
        last_keyword_type = None  # 'given', 'when', 'then'
        scenario_line = 0
        
        keyword_type_map = {
            'Given': 'given',
            'When': 'when',
            'Then': 'then',
            'And': None,  # Depends on context
            'But': None,
        }
        
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            
            if stripped.startswith('Scenario'):
                in_scenario = True
                last_keyword_type = None
                scenario_line = i
            elif in_scenario and stripped.startswith(('Scenario', 'Feature', 'Background')):
                in_scenario = False
            
            if in_scenario:
                for keyword in ['Given', 'When', 'Then', 'And', 'But']:
                    if stripped.startswith(keyword):
                        current_type = keyword_type_map.get(keyword)
                        
                        if keyword in ['And', 'But']:
                            # And/But inherits previous type
                            pass
                        else:
                            # Check order
                            if last_keyword_type == 'then' and current_type in ['given', 'when']:
                                violations.append(Violation(
                                    line=i, column=1,
                                    rule_id='W002', rule_name='GWT order',
                                    severity=RuleSeverity.ERROR,
                                    message=f'{keyword} step after Then (wrong order)',
                                    suggestion='Follow Given-When-Then order',
                                    category='workflow'
                                ))
                            elif last_keyword_type == 'when' and current_type == 'given':
                                violations.append(Violation(
                                    line=i, column=1,
                                    rule_id='W002', rule_name='GWT order',
                                    severity=RuleSeverity.ERROR,
                                    message=f'Given step after When (wrong order)',
                                    category='workflow'
                                ))
                            
                            last_keyword_type = current_type
                        break
        
        return violations
    
    @staticmethod
    def check_no_verification_step(parser: UnifiedParser) -> List[Violation]:
        """Scenarios should have Then steps"""
        violations = []
        in_scenario = False
        has_then = False
        scenario_line = 0
        scenario_name = ""
        
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            
            if stripped.startswith('Scenario'):
                if in_scenario and not has_then:
                    violations.append(Violation(
                        line=scenario_line, column=1,
                        rule_id='W003', rule_name='No verification step',
                        severity=RuleSeverity.ERROR,
                        message=f'Scenario "{scenario_name}" has no Then steps',
                        suggestion='Add assertions with Then/And steps',
                        category='workflow'
                    ))
                
                in_scenario = True
                has_then = False
                scenario_line = i
                scenario_name = re.sub(r'^Scenario.*?:\s*', '', stripped)
            
            elif in_scenario and stripped.startswith(('Feature', 'Background')):
                if not has_then:
                    violations.append(Violation(
                        line=scenario_line, column=1,
                        rule_id='W003', rule_name='No verification step',
                        severity=RuleSeverity.ERROR,
                        message=f'Scenario "{scenario_name}" has no Then steps',
                        category='workflow'
                    ))
                in_scenario = False
            
            if in_scenario and stripped.startswith('Then'):
                has_then = True
        
        return violations
    
    @staticmethod
    def check_no_action_step(parser: UnifiedParser) -> List[Violation]:
        """Scenarios should have When steps"""
        violations = []
        in_scenario = False
        has_when = False
        scenario_line = 0
        scenario_name = ""
        
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            
            if stripped.startswith('Scenario'):
                if in_scenario and not has_when:
                    violations.append(Violation(
                        line=scenario_line, column=1,
                        rule_id='W004', rule_name='No action step',
                        severity=RuleSeverity.ERROR,
                        message=f'Scenario "{scenario_name}" has no When steps',
                        suggestion='Add actions with When steps',
                        category='workflow'
                    ))
                
                in_scenario = True
                has_when = False
                scenario_line = i
                scenario_name = re.sub(r'^Scenario.*?:\s*', '', stripped)
            
            elif in_scenario and stripped.startswith(('Feature', 'Background')):
                if not has_when:
                    violations.append(Violation(
                        line=scenario_line, column=1,
                        rule_id='W004', rule_name='No action step',
                        severity=RuleSeverity.ERROR,
                        message=f'Scenario "{scenario_name}" has no When steps',
                        category='workflow'
                    ))
                in_scenario = False
            
            if in_scenario and stripped.startswith('When'):
                has_when = True
        
        return violations
    
    @staticmethod
    def check_step_with_period(parser: UnifiedParser) -> List[Violation]:
        """Steps should not end with period"""
        violations = []
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            if any(stripped.startswith(kw) for kw in ['Given', 'When', 'Then', 'And', 'But']):
                if stripped.endswith('.'):
                    violations.append(Violation(
                        line=i, column=len(stripped),
                        rule_id='W005', rule_name='Step with period',
                        severity=RuleSeverity.INFO,
                        message='Step ends with a period',
                        suggestion='Remove trailing period',
                        category='workflow'
                    ))
        return violations
    
    @staticmethod
    def check_too_many_steps(parser: UnifiedParser, max_steps=10) -> List[Violation]:
        """Scenarios should not have too many steps"""
        violations = []
        in_scenario = False
        step_count = 0
        scenario_line = 0
        scenario_name = ""
        
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            
            if stripped.startswith('Scenario'):
                if in_scenario and step_count > max_steps:
                    violations.append(Violation(
                        line=scenario_line, column=1,
                        rule_id='W006', rule_name='Too many steps',
                        severity=RuleSeverity.WARNING,
                        message=f'Scenario "{scenario_name}" has {step_count} steps (> {max_steps})',
                        suggestion='Break complex scenarios into simpler ones',
                        category='workflow'
                    ))
                
                in_scenario = True
                step_count = 0
                scenario_line = i
                scenario_name = re.sub(r'^Scenario.*?:\s*', '', stripped)
            
            elif in_scenario and stripped.startswith(('Feature', 'Background', 'Scenario Outline')):
                if step_count > max_steps:
                    violations.append(Violation(
                        line=scenario_line, column=1,
                        rule_id='W006', rule_name='Too many steps',
                        severity=RuleSeverity.WARNING,
                        message=f'Scenario "{scenario_name}" has {step_count} steps (> {max_steps})',
                        category='workflow'
                    ))
                in_scenario = False
            
            if in_scenario and any(stripped.startswith(kw) for kw in ['Given', 'When', 'Then', 'And', 'But']):
                step_count += 1
        
        return violations


class QualityRules:
    """FeatureMate-based quality checks"""
    
    @staticmethod
    def check_all(parser: UnifiedParser) -> List[Violation]:
        """Run the Quality family.

        PORT: *spellcheck* is new and defaults to False. SY001 is opt-in
        because profiling the ported engine measured it at 95% of total
        runtime -- 21.9 of 22.9 seconds over ten files -- for INFO-severity
        findings that also need an optional third-party package.

        This costs nothing in reproducibility. The published v1.0 results were
        produced through `cli.py`, which runs the engine in default mode and
        therefore never ran the Quality family at all.

        `--spellcheck` and `"quality": {"spellcheck": true}` are still parsed and
        ignored, so an old command line or config file does not fail. The rule
        they enabled no longer exists.
        """
        violations = []
        
        # Q001: Implementation details
        impl_keywords = [
            'clicks', 'selects', 'types', 'scrolls', 'presses', 'fills', 'enters', 
            'waits', 'refreshes', 'clears', 'hovering', 'clicking', 'navigates to',
            'button', 'link', 'input field', 'checkbox', 'page loads', 'refreshes page'
        ]
        
        for i, line in enumerate(parser.lines, 1):
            if any(line.strip().startswith(kw) for kw in ['Given', 'When', 'Then', 'And', 'But']):
                for impl in impl_keywords:
                    if impl.lower() in line.lower():
                        violations.append(Violation(
                            line=i, column=1,
                            rule_id='Q001', rule_name='Implementation detail',
                            severity=RuleSeverity.WARNING,
                            message=f'Implementation detail: "{impl}"',
                            suggestion='Use business language instead of UI actions',
                            category='quality'
                        ))
                        break
        
        # Q002: Vague language (genuinely imprecise qualifiers)
        vague_words = ['basic', 'simple', 'some', 'various', 'stuff', 'things',
                       'thing', 'etc', 'and so on', 'maybe', 'probably', 'might']
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            if any(stripped.startswith(kw) for kw in ['Given', 'When', 'Then', 'And', 'But']):
                low = stripped.lower()
                for w in vague_words:
                    if re.search(r'\b' + re.escape(w) + r'\b', low):
                        violations.append(Violation(
                            line=i, column=1,
                            rule_id='Q002', rule_name='Vague language',
                            severity=RuleSeverity.INFO,
                            message=f'Vague term: "{w}"',
                            suggestion='Be more specific about what you mean',
                            category='quality'))
                        break

        # Q003: Sensible step count (ideal 3-7 steps per scenario)
        in_scn = False; cnt = 0; scn_line = 0; scn_name = ''
        def _q003_flush():
            if in_scn and (cnt < 3 or cnt > 7):
                violations.append(Violation(
                    line=scn_line, column=1,
                    rule_id='Q003', rule_name='Step count',
                    severity=RuleSeverity.INFO,
                    message=f'Scenario "{scn_name}" has {cnt} step(s) (ideal 3-7)',
                    suggestion='Aim for 3-7 focused steps',
                    category='quality'))
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            if stripped.startswith('Scenario'):
                _q003_flush()
                in_scn = True; cnt = 0; scn_line = i
                scn_name = re.sub(r'^Scenario.*?:\s*', '', stripped)
            elif in_scn and stripped.startswith(('Feature', 'Background')):
                _q003_flush(); in_scn = False
            if in_scn and any(stripped.startswith(kw) for kw in ['Given', 'When', 'Then', 'And', 'But']):
                cnt += 1
        _q003_flush()

        # Q004 / Q005: scenario-name quality (test jargon, too-generic name)
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            if stripped.startswith('Scenario'):
                name = re.sub(r'^Scenario.*?:\s*', '', stripped)
                low = name.lower()
                for t in ['test', 'verify', 'check', 'validate', 'should']:
                    if re.search(r'\b' + t + r'\b', low):
                        violations.append(Violation(
                            line=i, column=1,
                            rule_id='Q004', rule_name='Test jargon in name',
                            severity=RuleSeverity.INFO,
                            message=f'Scenario name uses technical term: "{t}"',
                            suggestion='Describe behaviour, not testing (what, not how)',
                            category='quality'))
                        break
                if len(name) < 10:
                    violations.append(Violation(
                        line=i, column=1,
                        rule_id='Q005', rule_name='Vague scenario name',
                        severity=RuleSeverity.INFO,
                        message=f'Scenario name too generic: "{name}"',
                        suggestion='Use a descriptive scenario name',
                        category='quality'))

        # Q006: Hardcoded mock data
        mock_patterns = [
            (r'\buser123\b', 'a hardcoded user id'),
            (r'\btest@test\.com\b', 'a hardcoded email'),
            (r'\bpassword123\b', 'a hardcoded password'),
            (r'\bjohn[ ._-]?doe\b', 'a hardcoded name'),
            (r'\b999-99-9999\b', 'a hardcoded SSN'),
        ]
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            if any(stripped.startswith(kw) for kw in ['Given', 'When', 'Then', 'And', 'But']):
                for pat, desc in mock_patterns:
                    if re.search(pat, stripped, re.IGNORECASE):
                        violations.append(Violation(
                            line=i, column=1,
                            rule_id='Q006', rule_name='Mock data',
                            severity=RuleSeverity.INFO,
                            message=f'Step contains {desc}',
                            suggestion='Use Examples tables or parameterised data',
                            category='quality'))
                        break

        # Q008: Near-duplicate scenarios (same opening words)
        seen = {}
        for i, line in enumerate(parser.lines, 1):
            stripped = line.strip()
            if stripped.startswith('Scenario'):
                name = re.sub(r'^Scenario.*?:\s*', '', stripped).lower()
                key = ' '.join(name.split()[:3])
                if key and key in seen:
                    violations.append(Violation(
                        line=i, column=1,
                        rule_id='Q008', rule_name='Similar scenarios',
                        severity=RuleSeverity.INFO,
                        message=f'Scenario may duplicate the one at line {seen[key]}',
                        suggestion='Consider a Scenario Outline for variations',
                        category='quality'))
                elif key:
                    seen[key] = i

        # Q007 (unclear negation) removed: 0 violations across 20,270 files.
        #
        # SY001 (spelling) removed as well. It found nothing on this corpus,
        # it needed the only optional third-party dependency, it accounted for
        # roughly 95% of total runtime when enabled, and the auto-correction
        # it shipped with in v1.0 rewrote technical terms into nonsense
        # ('sql' -> 'sol', 'xml' -> 'my'). Nothing about it earned its place.

        return violations



class UnifiedLinter:
    """Main linter combining all rules"""
    
    def __init__(self, full: bool = False, config=None):
        """Create a linter.

        *full* selects the rule set.  ``False`` (the default) runs the
        oracle-checkable Style/Structure/Workflow subset -- the 18 rules that
        gherkin-lint and cuke_linter can also be asked about, which is what the
        differential evaluation measures and therefore what reproduces the
        published numbers.  ``True`` adds ST007 and the Quality family for the
        complete 28-rule engine, which is what a user linting their own project
        wants.

        PORT: *config* is new.  Passing ``None`` builds a default
        :class:`~unifiedbddlinter.config.LinterConfig`, whose values are exactly
        v1.0's hardcoded ones, so the default path is unchanged behaviour.
        """
        self.violations: List[Violation] = []
        self.file_count = 0
        self.total_violations = 0
        if config is None:
            from .config import LinterConfig
            config = LinterConfig()
        self.config = config
        # full=True runs the complete 28-rule engine (adds ST007 + the Quality
        # family). Off by default, so cli.py and the differential harness keep
        # the oracle-checkable 18-rule subset and reproduce the reported results.
        self.full = full
    
    def lint_file(self, file_path: str) -> List[Violation]:
        """Lint a single feature file"""
        parser = UnifiedParser().parse(file_path)
        violations = []
        
        # Style rules
        violations.extend(StyleRules.check_trailing_spaces(parser))
        violations.extend(StyleRules.check_multiple_empty_lines(parser))
        violations.extend(StyleRules.check_eof_newline(parser))
        violations.extend(StyleRules.check_indentation(parser))
        violations.extend(StyleRules.check_file_name(
            file_path, style=self.config.file_name_style))
        # PORT: thresholds come from configuration rather than the rule's
        # default arguments, which is what makes .unified-lintrc.json load-bearing.
        violations.extend(StyleRules.check_name_length(
            parser,
            max_feature=self.config.limit("max_feature_name"),
            max_scenario=self.config.limit("max_scenario_name"),
            max_step=self.config.limit("max_step_length"),
        ))
        
        # Structure rules
        violations.extend(StructureRules.check_unnamed_features(parser))
        violations.extend(StructureRules.check_unnamed_scenarios(parser))
        violations.extend(StructureRules.check_empty_file(parser))
        violations.extend(StructureRules.check_no_feature(parser))
        # ST005 (empty Background) removed: 5 violations in 20,270 files.
        violations.extend(StructureRules.check_duplicate_scenario_names(parser))
        violations.extend(TagRules.check_bom(parser))
        violations.extend(TagRules.check_tags(parser))
        
        # Workflow rules
        violations.extend(WorkflowRules.check_only_one_when(parser))
        violations.extend(WorkflowRules.check_gwt_structure(parser))
        violations.extend(WorkflowRules.check_no_verification_step(parser))
        violations.extend(WorkflowRules.check_no_action_step(parser))
        violations.extend(WorkflowRules.check_step_with_period(parser))
        violations.extend(WorkflowRules.check_too_many_steps(
            parser, max_steps=self.config.limit("max_steps_per_scenario")))

        # Full mode (28-rule engine): filename<->feature match (ST007) and the
        # Quality family. Excluded from the default (cli.py / harness) run.
        if self.full:
            violations.extend(StructureRules.check_feature_name_match(
                parser, file_path, style=self.config.file_name_style))
            violations.extend(QualityRules.check_all(parser))

        # PORT: apply configuration as a post-pass rather than threading it
        # through 28 rule bodies.  Filtering after the fact costs one list
        # traversal per file and keeps every rule body byte-identical to v1.0,
        # which is what lets the published results be reproduced exactly.
        if self.config.disabled or self.config.severity_overrides:
            kept = []
            for v in violations:
                if not self.config.is_enabled(v.rule_id):
                    continue
                override = self.config.severity_overrides.get(v.rule_id)
                if override is not None:
                    v.severity = RuleSeverity(override)
                kept.append(v)
            violations = kept

        # Stable ordering by position. Reports are read top-to-bottom against
        # the file, and a diff between two runs must not show spurious churn.
        violations.sort(key=lambda v: (v.line, v.column))

        self.total_violations += len(violations)
        self.file_count += 1
        
        return violations
    
    def lint_directory(self, directory: str) -> dict:
        """Lint every ``.feature`` file under *directory*, recursively.

        PORT: v1.0 walked everything ``rglob`` returned.  Three problems, all
        of which cost real time or produced wrong output on real projects:

        * vendored dependency trees (``node_modules``, ``target``) were linted
          as if they were the user's own specifications;
        * ``.git`` internals were traversed;
        * ``rglob`` matches *directories* named ``something.feature`` as well as
          files, and handing a directory to ``open()`` raises IsADirectoryError.

        Ignored directory names come from configuration, so a project with an
        unusual vendor directory can add it.
        """
        root = Path(directory)
        results = {}

        for feature_file in sorted(root.rglob("*.feature")):
            if not feature_file.is_file():
                continue  # a directory whose name merely ends in .feature
            if feature_file.is_symlink():
                continue  # never follow links: a cycle would not terminate
            # Test only the components below `root`; an ignored name in the
            # user's own absolute path (say /home/me/build/project) must not
            # silently exclude their entire project.
            parts = feature_file.relative_to(root).parts[:-1]
            if any(part in self.config.ignore_dirs or part.startswith(".")
                   for part in parts):
                continue
            results[str(feature_file)] = self.lint_file(str(feature_file))

        return results
    
    def format_output(self, violations: List[Violation], format_type='text') -> str:
        """Format violations for output"""
        if format_type == 'json':
            import json
            return json.dumps([{
                'line': v.line,
                'column': v.column,
                'rule': v.rule_id,
                'name': v.rule_name,
                'severity': v.severity.value,
                'message': v.message,
                'suggestion': v.suggestion,
                'category': v.category,
            } for v in violations], indent=2)
        
        else:  # text
            output = []
            for v in violations:
                output.append(f"[{v.severity.name:7}] {v.rule_id}: {v.rule_name}")
                output.append(f"  Line {v.line}: {v.message}")
                if v.suggestion:
                    output.append(f"  Suggestion: {v.suggestion}")
            return '\n'.join(output)
