"""
test_7_filenames.py -- the feature-name to file-stem contract.

TWO BUGS THIS PINS, BOTH FOUND BY RUNNING THE REAL CORPUS
---------------------------------------------------------
v1.0 derived a file stem from a feature name in THREE places with THREE
different rules: the ST007 check in the engine, and two write paths in the
fixer. The check kept punctuation, the fixer stripped it, and neither capped
length. Measured on the 20,270-file corpus:

  1. ST007 was UNFIXABLE on 6,687 files (34.4% of those with a named Feature).
     The fixer renamed the file correctly; the checker then reported the same
     violation against the new name, forever. Any punctuation triggered it --
     even a hyphen.

  2. 15 files produced a stem over Linux's 255-byte limit and crashed the fixer
     with [Errno 36]. That is exactly the "auto-fix failures = 15" in the
     published run's provenance. On Windows (MAX_PATH 260 for the whole path)
     1,162 files, 5.7% of the corpus, would have failed.

The invariant that prevents both: **one function, and fixing ST007 must
actually resolve ST007.** That round-trip is the real test -- it would catch any
future divergence between the checker and the repairer, whatever its cause.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from _support import add_src_to_path

add_src_to_path()
from unifiedbddlinter.config import LinterConfig                        # noqa: E402
from unifiedbddlinter.engine import (MAX_FILE_STEM, UnifiedLinter,      # noqa: E402
                                     slugify_feature_name)
from unifiedbddlinter.fixer import AutoFixer                            # noqa: E402


class Slugify(unittest.TestCase):

    def test_is_idempotent(self):
        """Slugifying a slug must return it unchanged.

        This is the property the ST007 check relies on: it normalises both the
        feature name and the existing file stem and compares. Without
        idempotence, a correctly named file would fail its own check.
        """
        for name in ["User Login (v2.0)", "Payments: refunds & credits",
                     "Search - advanced filters", "simple_login", "Already-Kebab"]:
            once = slugify_feature_name(name)
            self.assertEqual(once, slugify_feature_name(once), f"not idempotent: {name!r}")

    def test_respects_the_length_cap(self):
        stem = slugify_feature_name("A " * 400)
        self.assertLessEqual(len(stem), MAX_FILE_STEM)
        self.assertFalse(stem.endswith("-"), "truncated mid-separator")

    def test_snake_and_kebab_agree_apart_from_the_separator(self):
        name = "User Login (v2.0)"
        self.assertEqual(slugify_feature_name(name, "kebab").replace("-", "_"),
                         slugify_feature_name(name, "snake"))

    def test_undrivable_names_produce_an_empty_stem(self):
        """Pure punctuation or non-Latin script yields nothing, not garbage.

        The callers must then fall back to the original filename. v1.0 wrote a
        hidden file literally named `.feature` in this case.
        """
        for name in ["!!!", "***", "ログイン"]:
            self.assertEqual(slugify_feature_name(name), "")


class FixingST007ResolvesST007(unittest.TestCase):
    """The round-trip. This is the test that actually matters."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bddlint-names-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _roundtrip(self, feature_name):
        source = self.tmp / "input.feature"
        source.write_text(
            f"Feature: {feature_name}\n\nScenario: A scenario with a real name\n"
            f"  Given a step\n", encoding="utf-8")
        out = self.tmp / f"out{abs(hash(feature_name))}"
        out.mkdir()
        fixed = Path(AutoFixer(quiet=True).fix_file(str(source), output_dir=str(out)))
        violations = UnifiedLinter(full=True, config=LinterConfig()).lint_file(str(fixed))
        return fixed, [v for v in violations if v.rule_id == "ST007"]

    def test_punctuated_names_are_repairable(self):
        """The 34.4% case: any punctuation used to make ST007 permanent."""
        for name in ["User Login (v2.0)", "Payments: refunds & credits",
                     "Search - advanced filters", "Order #42 processing",
                     "Reports [monthly]", "A/B testing flow"]:
            with self.subTest(feature=name):
                _fixed, remaining = self._roundtrip(name)
                self.assertEqual(remaining, [],
                                 f"ST007 survived its own fix for {name!r}")

    def test_very_long_names_do_not_crash_the_fixer(self):
        """The 15-file case: [Errno 36] File name too long."""
        fixed, remaining = self._roundtrip("The system shall " + "handle this case " * 40)
        self.assertTrue(fixed.is_file(), "the fixer produced no file")
        self.assertLessEqual(len(fixed.name.encode("utf-8")), 255,
                             "filename exceeds the Linux limit")
        self.assertEqual(remaining, [], "ST007 survived its own fix")

    def test_no_hidden_dotfile_for_an_underivable_name(self):
        """A name that slugifies to nothing must not become '.feature'."""
        fixed, _remaining = self._roundtrip("!!!")
        self.assertNotEqual(fixed.name, ".feature")
        self.assertFalse(fixed.name.startswith("."), f"wrote a hidden file: {fixed.name}")

    def test_underivable_name_is_still_reported(self):
        """Declining to rename is not the same as declaring the file fine."""
        _fixed, remaining = self._roundtrip("!!!")
        self.assertTrue(remaining, "ST007 should still be reported when the name "
                                   "cannot be derived -- a human must fix it")


class WindowsPathBudget(unittest.TestCase):

    def test_cap_leaves_room_for_a_deep_output_tree(self):
        """A capped stem must still fit inside Windows' default MAX_PATH.

        260 characters for the WHOLE path. With a 150-character stem plus
        '.feature', a reviewer still has ~100 characters of directory prefix --
        enough for a checkout under a user's home directory.
        """
        budget = 260 - (MAX_FILE_STEM + len(".feature"))
        self.assertGreaterEqual(budget, 100,
                                "MAX_FILE_STEM leaves too little room for a "
                                "realistic Windows directory prefix")


if __name__ == "__main__":
    unittest.main(verbosity=2)
