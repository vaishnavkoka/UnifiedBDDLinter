"""
test_1_boundary.py -- the safe-fix boundary.

WHAT THIS PINS

The fixer has two modes, and this module pins the contract of BOTH.

  DEFAULT (`safe=False`, `bddlint fix`) reproduces the published v1.0
  behaviour: eleven auto-fixable rules, five of which author text the tool
  cannot derive from the input. This is the mode the tool demonstration paper
  measured, so it must keep injecting exactly what it injected -- if it
  stopped, the artifact would no longer reproduce its own paper.

  SAFE (`safe=True`, `bddlint fix --safe-fix`) restricts repair to changes
  recoverable from the file itself. Here the fixer must never add a word, a
  number or a symbol that was not already there. This is the mode to point at
  a real project, and the guarantee it offers is the reason it exists.

Both directions are asserted. A test suite that only checked safe mode would
let the default silently stop reproducing the paper; one that only checked the
default would let the safe boundary silently leak.

The tests are written as PROPERTIES over the file's content rather than as
assertions about specific rules, because a property survives someone adding a
new fix method -- which is exactly the moment the boundary is most likely to be
breached by accident.
"""

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from _support import FIXTURES, add_src_to_path

add_src_to_path()
from unifiedbddlinter.fixer import AutoFixer          # noqa: E402
from unifiedbddlinter import catalogue                # noqa: E402


def word_bag(text: str):
    """Multiset of alphanumeric tokens in *text*.

    A multiset, not a set: duplicating an existing word is still injection.
    Punctuation and whitespace are excluded because reflowing them is precisely
    what safe mode is allowed to do.
    """
    from collections import Counter
    return Counter(re.findall(r"[0-9A-Za-z_]+", text))


class SafeFixInjectsNothing(unittest.TestCase):
    """No token may appear in the output that was not in the input."""

    def setUp(self):
        # Each test works on a throwaway copy: the fixer writes to disk, and a
        # test that mutates its own fixtures passes once and then lies forever.
        self.tmp = Path(tempfile.mkdtemp(prefix="bddlint-boundary-"))
        self.out = self.tmp / "out"
        self.out.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fix(self, fixture_name, safe=True):
        """Copy a fixture, fix it into `out/`, return (before, after) text."""
        source = FIXTURES / fixture_name
        working = self.tmp / fixture_name
        shutil.copy(source, working)
        before = working.read_text(encoding="utf-8", errors="replace")
        # quiet: the suite's own output is the result, not the fixer's log.
        fixer = AutoFixer(safe=safe, quiet=True)
        result = fixer.fix_file(str(working), output_dir=str(self.out))
        after = Path(result).read_text(encoding="utf-8", errors="replace")
        return before, after

    def test_semantic_defects_gain_no_new_words(self):
        """A file of purely semantic defects must come back word-identical.

        semantic_defects.feature has an unnamed Feature and two unnamed
        Scenarios -- exactly the defects v1.0 repaired by inventing
        "Auto-generated Feature" and "Scenario 1". If any of those strings
        appear, the boundary has been breached.
        """
        before, after = self._fix("semantic_defects.feature")
        self.assertEqual(word_bag(before), word_bag(after),
                         "safe mode introduced or removed words")
        for invented in ("Auto", "generated", "Scenario1"):
            self.assertNotIn(invented, after.replace(" ", ""),
                             f"fixer invented text containing {invented!r}")

    def test_every_fixture_is_word_preserving(self):
        """The property must hold for every fixture, not just the crafted one."""
        for fixture in sorted(FIXTURES.glob("*.feature")):
            with self.subTest(fixture=fixture.name):
                before, after = self._fix(fixture.name)
                self.assertEqual(
                    word_bag(before), word_bag(after),
                    f"{fixture.name}: safe mode changed the word content")

    def test_safe_mode_still_repairs_form(self):
        """The boundary must not be satisfied by doing nothing at all.

        A test that only proves "nothing changed" would also pass if the fixer
        were broken. So: form defects MUST be repaired even while words are
        preserved -- that combination is the whole claim.
        """
        before, after = self._fix("form_defects.feature")
        self.assertNotEqual(before, after, "fixer made no change at all")
        self.assertNotIn("   \n", after, "trailing whitespace was not removed")
        self.assertNotIn("\n\n\n", after, "multiple blank lines were not collapsed")
        self.assertTrue(after.endswith("\n"), "missing final newline")
        self.assertEqual(word_bag(before), word_bag(after),
                         "form repair must not alter words")

    def test_injection_mode_still_injects(self):
        """Negative control for the property above.

        `safe=False` is not part of any reported result -- it exists so the
        historical v1.0 behaviour can be reproduced for provenance. It is
        exercised here for one reason: if it ALSO came back word-identical, the
        safe-mode property above would be vacuous, measuring nothing at all.
        """
        before, after = self._fix("semantic_defects.feature", safe=False)
        self.assertNotEqual(
            word_bag(before), word_bag(after),
            "injection mode did not inject, so the safe-mode test above proves "
            "nothing -- investigate before trusting this suite")

    def test_default_is_form_only(self):
        """`AutoFixer()` with no arguments must write no invented text.

        This is the project's central safety property, so it is asserted on the
        constructor itself rather than only through behaviour: a future edit
        that flips the default would fail here immediately, with a message
        saying why it matters.
        """
        self.assertTrue(AutoFixer().safe,
                        "the no-argument default must be form-only -- the tool "
                        "must never invent text in a requirements artifact "
                        "unless explicitly asked to")

    def test_injection_mode_reports_what_it_authored(self):
        """Injecting is acceptable only if the tool says that it did.

        A silent rewrite of a requirements artifact is the failure mode; a
        reported one the user can review is not.
        """
        source = FIXTURES / "semantic_defects.feature"
        working = self.tmp / "reported.feature"
        shutil.copy(source, working)
        fixer = AutoFixer(safe=False, quiet=True)
        fixer.fix_file(str(working), output_dir=str(self.out))
        self.assertTrue(fixer.injected, "the fixer authored text but recorded nothing")
        summary = fixer.injected_summary()
        self.assertIn("AUTHORED", summary)
        self.assertIn("--safe-fix", summary, "the summary must name the way out")


class CatalogueMatchesBoundary(unittest.TestCase):
    """The documented fixability must match what the fixer actually does."""

    def test_unsafe_rules_are_exactly_the_gated_methods(self):
        """catalogue.UNSAFE entries and AutoFixer.UNSAFE_FIXES must agree.

        These are two independent statements of the same fact -- one for the
        user, one for the code. Drift between them means the documentation is
        lying about what the tool does.
        """
        gated = {name for name, _reason in AutoFixer.UNSAFE_FIXES}
        self.assertEqual(len(gated), 4, "expected four gated injecting fixes")
        unsafe_rules = {r.rule_id for r in catalogue.RULES
                        if r.fixability == catalogue.UNSAFE}
        self.assertEqual(unsafe_rules, {"ST001", "ST002", "ST003", "ST004", "ST006"})

    def test_safe_fixable_set_is_documented(self):
        """The fixable set is pinned, so a change to it has to be deliberate.

        W005 was in this list until a `mvn test` run showed that removing a
        step's trailing period breaks any step definition containing it. It is
        detect-only now. If a rule is added or removed here, the reason belongs
        in the commit and in docs/FINDINGS.md.
        """
        self.assertEqual(sorted(catalogue.fixable(safe_mode=True)),
                         ["S001", "S002", "S003", "S004", "ST007",
                          "ST013", "T001", "T006"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class StepTextIsNeverAltered(unittest.TestCase):
    """No repair may change a string the Cucumber runtime binds on.

    A step definition is matched against the step's TEXT. Change one character
    of it -- even punctuation -- and a previously bound step becomes UNDEFINED
    at run time, while every linter still reports the file as improved.

    This is stricter than the word-preservation property above, and it was added
    because the word-preservation property was NOT sufficient: removing a
    trailing full stop invents no word, passes that test, and still breaks a real
    test suite. Demonstrated with `mvn test` in misc/binding-experiment/:
    BUILD SUCCESS before the repair, BUILD FAILURE after.

    Feature and scenario NAMES are compared too. Cucumber does not bind on them,
    but tag filters and reports reference them, and a repair has no business
    changing them either.
    """

    STEP = re.compile(r"^\s*(Given|When|Then|And|But)\s+(.*)$")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bddlint-steptext-"))
        self.out = self.tmp / "out"
        self.out.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _texts(self, content):
        """Step texts and element names, in order, stripped of indentation.

        A leading byte-order mark is removed first. ST013's repair deletes it,
        and without this the mark would make line 1 fail the keyword match
        before the repair and succeed after -- reporting a "name change" that
        is really the mark being gone.
        """
        content = content.lstrip("\ufeff")
        steps, names = [], []
        for line in content.splitlines():
            match = self.STEP.match(line)
            if match:
                steps.append(match.group(2).strip())
            else:
                bare = line.strip()
                for keyword in ("Feature:", "Scenario Outline:", "Scenario:"):
                    if bare.startswith(keyword):
                        names.append(bare[len(keyword):].strip())
                        break
        return steps, names

    def test_no_fixture_has_its_step_text_changed(self):
        for fixture in sorted(FIXTURES.glob("*.feature")):
            with self.subTest(fixture=fixture.name):
                working = self.tmp / fixture.name
                shutil.copy(fixture, working)
                before = working.read_text(encoding="utf-8", errors="replace")
                result = AutoFixer(quiet=True).fix_file(
                    str(working), output_dir=str(self.out))
                after = Path(result).read_text(encoding="utf-8", errors="replace")

                steps_before, names_before = self._texts(before)
                steps_after, names_after = self._texts(after)
                self.assertEqual(
                    steps_before, steps_after,
                    f"{fixture.name}: a repair altered step text. Cucumber "
                    f"matches step definitions against exactly this string, so "
                    f"a bound step can become UNDEFINED.")
                self.assertEqual(names_before, names_after,
                                 f"{fixture.name}: a repair altered an element name")

    def test_a_trailing_period_is_reported_not_removed(self):
        """The specific case that made this test necessary."""
        source = self.tmp / "period.feature"
        source.write_text(
            "Feature: Period\n\nScenario: A customer completes an order\n"
            "  Given a customer has items in the cart\n"
            "  Then the order is recorded.\n", encoding="utf-8")
        result = AutoFixer(quiet=True).fix_file(str(source), output_dir=str(self.out))
        after = Path(result).read_text(encoding="utf-8")
        self.assertIn("the order is recorded.", after,
                      "the trailing period was removed; this breaks any step "
                      "definition that includes it")

        from unifiedbddlinter.config import LinterConfig
        from unifiedbddlinter.engine import UnifiedLinter
        violations = UnifiedLinter(full=True, config=LinterConfig()).lint_file(str(source))
        self.assertIn("W005", {v.rule_id for v in violations},
                      "W005 must still be REPORTED even though it is not fixed")
