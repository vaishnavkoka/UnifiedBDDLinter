"""
test_2_portability.py -- the properties that make this tool shippable.

v1.0 ran on exactly one machine. These tests encode what had to become true
for it to run on any machine: text decoding must not depend on the host's
locale, line endings must not be rewritten, and paths must not assume a
separator. Each test corresponds to a concrete way the original failed or would
have failed off its home system.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import FIXTURES, ROOT, add_src_to_path

add_src_to_path()
from unifiedbddlinter.engine import UnifiedLinter, UnifiedParser   # noqa: E402
from unifiedbddlinter.fixer import AutoFixer                       # noqa: E402


class Decoding(unittest.TestCase):
    """Reading a file must not depend on the platform's default encoding."""

    def test_non_ascii_file_is_read_without_error(self):
        """The exact failure v1.0 had on Windows.

        v1.0 called open() with no encoding, so the interpreter used the locale
        codepage -- cp1252 on a default Windows install -- and any accented
        character raised UnicodeDecodeError, killing the whole run.
        """
        linter = UnifiedLinter(full=True)
        violations = linter.lint_file(str(FIXTURES / "unicode_accents.feature"))
        self.assertIsInstance(violations, list)

    def test_undecodable_bytes_do_not_crash(self):
        """A corpus scan must survive a genuinely malformed file.

        errors="replace" is a deliberate choice over "strict": one broken file
        in twenty thousand must not cost the other 19,999 results.
        """
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "broken.feature"
            # Lone continuation bytes: not valid UTF-8 under any interpretation.
            bad.write_bytes(b"Feature: Broken \xff\xfe bytes\n\n  Scenario: One\n    Given a step\n")
            violations = UnifiedLinter(full=True).lint_file(str(bad))
            self.assertIsInstance(violations, list)

    def test_line_numbers_survive_replacement(self):
        """Replacement characters must not shift any line number.

        Every violation this tool reports is anchored to a line. If decoding
        changed the line count, every report on a malformed file would point
        somewhere wrong -- silently.
        """
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "lines.feature"
            bad.write_bytes(b"Feature: A\n\xff\xfe\nline three\nline four\n")
            parser = UnifiedParser().parse(str(bad))
            self.assertEqual(len(parser.lines), 4)

    def test_bom_is_not_read_as_indentation(self):
        """A UTF-8 BOM must not make the first line look indented.

        Windows editors write a BOM by default. Decoded, it survives as U+FEFF
        at the start of line 1, where an indentation check counts it as leading
        content -- so every BOM file reported a spurious violation.
        """
        parser = UnifiedParser().parse(str(FIXTURES / "with_bom.feature"))
        self.assertTrue(parser.lines[0].startswith("Feature:"),
                        f"BOM leaked into line 1: {parser.lines[0]!r}")


class LineEndings(unittest.TestCase):
    """CRLF input must be handled, and never introduced by the fixer."""

    def test_crlf_file_lints(self):
        violations = UnifiedLinter(full=True).lint_file(str(FIXTURES / "crlf.feature"))
        self.assertIsInstance(violations, list)

    def test_fixer_does_not_translate_newlines(self):
        """The write path must not silently convert every line ending.

        Without newline="" on the output file, Python's text mode translates
        "\\n" to the platform separator. On Windows that rewrites every line of
        every file, so a "no changes needed" file still comes out different and
        the before/after comparison measures the platform, not the tool.
        """
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "clean.feature"
            out = Path(tmp) / "out"
            out.mkdir()
            shutil.copy(FIXTURES / "clean.feature", work)
            before = work.read_bytes()
            result = AutoFixer(safe=True, quiet=True).fix_file(str(work), output_dir=str(out))
            after = Path(result).read_bytes()
            self.assertNotIn(b"\r\n", after,
                             "fixer introduced CRLF line endings")
            self.assertEqual(before.count(b"\n"), after.count(b"\n"),
                             "line count changed")


class Traversal(unittest.TestCase):
    """Directory scanning must be safe and must not wander."""

    def test_directory_named_dot_feature_is_skipped(self):
        """rglob('*.feature') matches DIRECTORIES too.

        Handing one to open() raises IsADirectoryError. This actually happened
        in the evaluation harness and had to be patched out of a completed run.
        """
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "trap.feature").mkdir()
            (Path(tmp) / "real.feature").write_text(
                "Feature: Real\n\n  Scenario: A scenario that exists\n    Given a step\n",
                encoding="utf-8")
            results = UnifiedLinter(full=True).lint_directory(tmp)
            self.assertEqual(len(results), 1)
            self.assertTrue(list(results)[0].endswith("real.feature"))

    def test_ignored_directories_are_pruned(self):
        """Vendored trees are not the user's specifications."""
        with tempfile.TemporaryDirectory() as tmp:
            vendored = Path(tmp) / "node_modules" / "pkg"
            vendored.mkdir(parents=True)
            (vendored / "vendor.feature").write_text("Feature: Vendored\n", encoding="utf-8")
            (Path(tmp) / "mine.feature").write_text("Feature: Mine\n", encoding="utf-8")
            results = UnifiedLinter(full=True).lint_directory(tmp)
            self.assertEqual(len(results), 1, f"expected only mine.feature, got {list(results)}")

    def test_ignored_name_in_parent_path_does_not_exclude_project(self):
        """Only components BELOW the scan root may be tested.

        A user whose project genuinely lives in /home/me/build/specs must not
        find that their entire project is silently skipped because an ancestor
        directory happens to be named 'build'.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "build" / "specs"
            root.mkdir(parents=True)
            (root / "a.feature").write_text("Feature: A\n", encoding="utf-8")
            results = UnifiedLinter(full=True).lint_directory(str(root))
            self.assertEqual(len(results), 1)


class Launcher(unittest.TestCase):
    """The zero-install entry point must work from any working directory."""

    def test_launcher_runs_from_an_unrelated_cwd(self):
        """Invoked by absolute path from elsewhere, the tool must still start.

        This is the concrete thing v1.0 could not do: its flat imports required
        the process to be started from inside its own directory.
        """
        with tempfile.TemporaryDirectory() as elsewhere:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "bddlint.py"), "version"],
                cwd=elsewhere, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("UnifiedBDDLinter", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ParseabilityIsPreserved(unittest.TestCase):
    """A repair must never make a parseable file unparseable.

    This is the strongest safety property the tool has, and it is stronger than
    "changes no words": a file can keep every word and still stop parsing.

    Found by comparing all 20,270 repaired corpus files against their originals
    with Cucumber's own parser. Three files had been broken by the trailing-
    whitespace rule, and NO linter reported it -- all three reported the
    repaired files as improved.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bddlint-parse-"))
        self.out = self.tmp / "out"
        self.out.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bare_keyword_keeps_its_trailing_space(self):
        """`When ` parses; `When` does not. The space is load-bearing.

        Some real files contain a step written as a keyword with no text. The
        Gherkin parser accepts "When " as a step with an empty description and
        rejects a bare "When" outright, so stripping that one space turns a
        parseable file into an unparseable one.
        """
        source = self.tmp / "empty-step.feature"
        source.write_text(
            "Feature: Empty Step\n\nScenario: A scenario with an empty step\n"
            "  Given a precondition   \n  When \n  Then \n", encoding="utf-8")
        result = AutoFixer(quiet=True).fix_file(str(source), output_dir=str(self.out))
        after = Path(result).read_text(encoding="utf-8")

        self.assertIn("  When \n", after,
                      "the trailing space after a bare 'When' was removed; the "
                      "file no longer parses as Gherkin")
        self.assertIn("  Then \n", after)
        # Ordinary trailing whitespace must still be removed.
        self.assertIn("  Given a precondition\n", after,
                      "the guard is too broad: normal trailing whitespace "
                      "should still be stripped")

    def test_empty_step_is_still_reported(self):
        """Declining to repair is not the same as declaring the file fine."""
        source = self.tmp / "empty-step.feature"
        source.write_text(
            "Feature: Empty Step\n\nScenario: A scenario with an empty step\n"
            "  Given a precondition\n  When \n", encoding="utf-8")
        violations = UnifiedLinter(full=True).lint_file(str(source))
        self.assertTrue(violations, "an empty step should still produce findings")


class QualityMechanismDocs(unittest.TestCase):
    """The prose describing how the Quality family works must track the code.

    `QUALITY_MECHANISM` is hand-written: it says Q005 is `len(name) < 10` and
    that Q006 uses five regexes. Nothing forces those sentences to stay true
    when the implementation changes, so these checks tie the claims that carry
    a number back to the source they describe.
    """

    def test_every_quality_rule_is_documented(self):
        from unifiedbddlinter.catalogue import RULES, QUALITY_MECHANISM
        quality = {r.rule_id for r in RULES if r.category == "quality"}
        self.assertEqual(quality, set(QUALITY_MECHANISM),
                         "QUALITY_MECHANISM and the quality family disagree")

    def test_documented_counts_match_the_implementation(self):
        import re
        from pathlib import Path
        from unifiedbddlinter.catalogue import QUALITY_MECHANISM
        engine = (Path(__file__).resolve().parent.parent
                  / "src" / "unifiedbddlinter" / "engine.py").read_text(encoding="utf-8")

        import ast
        tree = ast.parse(engine)

        def listed(name):
            """Number of elements in the list literal assigned to *name*.

            Parsed rather than counted with a regex: mock_patterns holds tuples,
            so counting commas gives 7 for a five-element list.
            """
            for node in ast.walk(tree):
                if (isinstance(node, ast.Assign)
                        and any(getattr(t, "id", None) == name for t in node.targets)
                        and isinstance(node.value, ast.List)):
                    return len(node.value.elts)
            self.fail(f"{name} not found as a list literal in engine.py")

        # Q001 says "19 UI terms", Q002 says "12 terms", Q006 says "Five".
        self.assertIn("19", QUALITY_MECHANISM["Q001"])
        self.assertEqual(listed("impl_keywords"), 19,
                         "Q001 prose says 19 UI terms; impl_keywords disagrees")
        self.assertIn("12", QUALITY_MECHANISM["Q002"])
        self.assertEqual(listed("vague_words"), 12,
                         "Q002 prose says 12 terms; vague_words disagrees")
        self.assertEqual(listed("mock_patterns"), 5,
                         "Q006 prose says five regexes; mock_patterns disagrees")
        # Q005's whole point is that it is a bare length threshold.
        self.assertIn("len(name) < 10", QUALITY_MECHANISM["Q005"])
        self.assertIn("len(name) < 10", engine,
                      "Q005 prose cites len(name) < 10; engine.py disagrees")
