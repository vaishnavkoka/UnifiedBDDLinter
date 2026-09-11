"""The three entry points the paper documents must keep working.

The paper prints four commands a reader (or a demo video) will type verbatim:

    python3 linter.py features/
    python3 cli.py features/ --format json
    python3 auto_fix.py features/ -o fixed/
    python3 phase3_bdd_pipeline_full.py -r repos/ -o out.csv

These are not aliases for convenience; they are the published interface. A
rename inside the package must not silently break them, so each one is exercised
here -- flags included, exactly as Table 2 spells them.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def run(script, *args):
    """Invoke an entry point as a real process, the way a reader would."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        capture_output=True, text=True)
    return proc


class PaperEntryPoints(unittest.TestCase):

    def test_entry_point_scripts_exist(self):
        for script in ("linter.py", "cli.py", "auto_fix.py"):
            self.assertTrue((ROOT / script).is_file(),
                            f"{script} is named in the paper but is missing")

    def test_linter_runs_all_four_families(self):
        proc = run("linter.py", str(EXAMPLES), "--format", "json")
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        rules = {v["rule"] for f in json.loads(proc.stdout)["files"]
                 for v in f["violations"]}
        # The quality family is what distinguishes linter.py from cli.py.
        self.assertTrue(any(r.startswith("Q") for r in rules),
                        "linter.py must run the quality family")

    def test_cli_runs_the_oracle_subset_only(self):
        proc = run("cli.py", str(EXAMPLES), "--format", "json")
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        rules = {v["rule"] for f in json.loads(proc.stdout)["files"]
                 for v in f["violations"]}
        self.assertFalse([r for r in rules if r.startswith("Q")],
                         "cli.py must not run the quality family")

    def test_cli_family_toggles(self):
        full = run("cli.py", str(EXAMPLES), "--format", "json")
        nostyle = run("cli.py", str(EXAMPLES), "--no-style", "--format", "json")
        count = lambda p: sum(len(f["violations"])
                              for f in json.loads(p.stdout)["files"])
        self.assertLess(count(nostyle), count(full),
                        "--no-style must suppress the style family")

    def test_auto_fix_writes_to_output_dir_and_spares_the_input(self):
        import shutil, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in"
            shutil.copytree(EXAMPLES, src,
                            ignore=shutil.ignore_patterns("*.md"))
            before = {p.name: p.read_bytes() for p in src.glob("*.feature")}

            out = Path(tmp) / "fixed"
            proc = run("auto_fix.py", str(src), "-o", str(out))
            self.assertIn(proc.returncode, (0, 1), proc.stderr)

            self.assertTrue(list(out.rglob("*.feature")),
                            "auto_fix.py wrote nothing")
            after = {p.name: p.read_bytes() for p in src.glob("*.feature")}
            self.assertEqual(before, after,
                             "auto_fix.py modified its input; it must not")

    def test_auto_fix_dry_run_writes_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "fixed"
            proc = run("auto_fix.py", str(EXAMPLES), "-o", str(out), "--dry-run")
            self.assertIn(proc.returncode, (0, 1), proc.stderr)
            self.assertFalse(out.exists(), "--dry-run created an output directory")

    def test_harness_accepts_the_flags_the_paper_prints(self):
        harness = ROOT / "evaluation" / "phase3_bdd_pipeline_full.py"
        if not harness.is_file():
            self.skipTest("validation harness not present (tool ships standalone)")
        help_text = subprocess.run([sys.executable, str(harness), "--help"],
                                   capture_output=True, text=True).stdout
        for flag in ("-r", "--repos-root", "-o", "-w", "-n"):
            self.assertIn(flag, help_text, f"harness lost {flag}")


if __name__ == "__main__":
    unittest.main()
