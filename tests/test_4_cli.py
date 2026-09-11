"""
test_4_cli.py -- the command-line contract.

Exit codes and output streams are an API. CI pipelines branch on the exit code,
and `--format json` output is piped into other programs. Breaking either is a
breaking change even though no Python signature moved, so both are pinned here.

Every test runs the launcher as a SUBPROCESS rather than calling main() in
process. That is deliberate: it exercises the real path a user takes, including
sys.path setup and stream separation, which an in-process call would bypass.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import FIXTURES, ROOT, add_src_to_path

add_src_to_path()

LAUNCHER = ROOT / "bddlint.py"


def run(*args, cwd=None):
    """Invoke the CLI and return the CompletedProcess."""
    return subprocess.run([sys.executable, str(LAUNCHER), *args],
                          capture_output=True, text=True, cwd=cwd)


class ExitCodes(unittest.TestCase):
    """0 clean, 1 violations, 2 usage, 3 internal -- CI depends on these."""

    def test_clean_file_exits_zero(self):
        proc = run("lint", str(FIXTURES / "clean.feature"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_errors_exit_one(self):
        proc = run("lint", str(FIXTURES / "semantic_defects.feature"))
        self.assertEqual(proc.returncode, 1)

    def test_fail_on_never_exits_zero_despite_errors(self):
        """Report-only mode, for a team adopting the linter gradually."""
        proc = run("lint", str(FIXTURES / "semantic_defects.feature"), "--fail-on", "never")
        self.assertEqual(proc.returncode, 0)

    def test_fail_on_threshold_is_respected(self):
        """--fail-on critical must not trip on merely-error findings."""
        proc = run("lint", str(FIXTURES / "semantic_defects.feature"), "--fail-on", "critical")
        self.assertEqual(proc.returncode, 0)

    def test_missing_path_is_a_usage_error_not_a_crash(self):
        proc = run("lint", "/nonexistent/path/definitely.feature")
        self.assertEqual(proc.returncode, 2,
                         "a missing path must be exit 2, distinguishable from "
                         "both 'violations found' and 'the tool broke'")


class OutputStreams(unittest.TestCase):
    """Report on stdout, diagnostics on stderr -- so pipes keep working."""

    def test_json_output_is_valid_even_with_a_broken_config(self):
        """The regression that motivated the split.

        A config warning printed to stdout would corrupt the JSON document and
        break `bddlint lint --format json | jq` for anyone with an imperfect
        config -- a failure that appears far from its cause.
        """
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".unified-lintrc.json").write_text("{ broken", encoding="utf-8")
            target = Path(tmp) / "a.feature"
            target.write_text("Feature: A\n\n  Scenario: Something happens here\n    Given a step\n",
                              encoding="utf-8")
            proc = run("lint", str(target), "--format", "json", cwd=tmp)
            parsed = json.loads(proc.stdout)      # must not raise
            self.assertIn("files", parsed)
            self.assertIn("config", proc.stderr, "the warning should still be shown, on stderr")

    def test_sarif_is_wellformed(self):
        proc = run("lint", str(FIXTURES / "form_defects.feature"), "--format", "sarif")
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["version"], "2.1.0")
        run_block = doc["runs"][0]
        self.assertEqual(run_block["tool"]["driver"]["name"], "UnifiedBDDLinter")
        for result in run_block["results"]:
            region = result["locations"][0]["physicalLocation"]["region"]
            # SARIF is 1-based on both axes; a 0 here points a reviewer at the
            # wrong place in their diff.
            self.assertGreaterEqual(region["startLine"], 1)
            self.assertGreaterEqual(region["startColumn"], 1)

    def test_severity_filter_applies_to_every_format(self):
        """--severity must mean the same thing regardless of --format."""
        target = str(FIXTURES / "form_defects.feature")
        as_json = json.loads(run("lint", target, "--format", "json",
                                 "--severity", "error").stdout)
        for entry in as_json["files"]:
            for violation in entry["violations"]:
                self.assertIn(violation["severity"], ("error", "critical"))


class FixerSafety(unittest.TestCase):
    def test_fix_refuses_in_place_without_output_dir(self):
        """Repairing requirements in place without being asked is not recoverable."""
        proc = run("fix", str(FIXTURES / "form_defects.feature"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--output-dir", proc.stderr)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "f.feature"
            original = "Feature: Trailing space   \n"
            target.write_text(original, encoding="utf-8")
            proc = run("fix", str(target), "--dry-run")
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), original,
                             "--dry-run modified the file")


class FixModes(unittest.TestCase):
    """The CLI default is the paper's configuration; --safe-fix opts out."""

    def _fix_into(self, tmp, *extra):
        target = Path(tmp) / "semantic.feature"
        target.write_text("Feature:\n\nScenario:\n  Given something\n", encoding="utf-8")
        out = Path(tmp) / "out"
        out.mkdir()
        proc = run("fix", str(target), "--output-dir", str(out), "-q", *extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc, (out / "semantic.feature").read_text(encoding="utf-8")

    def test_default_writes_no_new_text(self):
        """`bddlint fix`, with no flags, must not invent anything."""
        with tempfile.TemporaryDirectory() as tmp:
            proc, result = self._fix_into(tmp)
            self.assertNotIn("Auto-generated", result)
            self.assertNotIn("Scenario 1", result)
            self.assertIn("NOT repaired", proc.stdout,
                          "the tool must say what it declined to fix, or an "
                          "unrepaired violation looks like a missed one")

    def test_injection_is_opt_in(self):
        """The historical behaviour is reachable, and only on request."""
        with tempfile.TemporaryDirectory() as tmp:
            proc, result = self._fix_into(tmp, "--allow-text-injection")
            self.assertIn("Auto-generated Feature", result)
            self.assertIn("AUTHORED", proc.stdout,
                          "when it does write text, it must say so")


class Introspection(unittest.TestCase):
    def test_rules_lists_the_whole_catalogue(self):
        """The printed count must match the catalogue, whatever its size."""
        add_src_to_path()
        from unifiedbddlinter import catalogue
        proc = run("rules")
        self.assertEqual(proc.returncode, 0)
        self.assertIn(f"{len(catalogue.RULES)} rules", proc.stdout)

    def test_default_rules_lists_the_oracle_checkable_subset(self):
        add_src_to_path()
        from unifiedbddlinter import catalogue
        proc = run("rules", "--default-rules")
        self.assertIn(f"{len(catalogue.rules_in_mode(False))} rules", proc.stdout)

    def test_version_states_provenance(self):
        """An artefact evaluator must be able to ask what this copy descends from."""
        proc = run("version")
        self.assertIn("e00914cc0c52787466193752cfdd3469", proc.stdout)

    def test_no_subcommand_prints_help_and_exits_two(self):
        proc = run()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stdout.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
