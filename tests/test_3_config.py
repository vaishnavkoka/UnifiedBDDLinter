"""
test_3_config.py -- configuration actually takes effect.

v1.0 shipped a config file that no code read. These tests exist so that can
never silently become true again: each one changes a setting and asserts the
linter's OUTPUT changes, rather than asserting the value was parsed. Parsing a
setting nobody consults is exactly the bug being guarded against.
"""

import json
import tempfile
import unittest
from pathlib import Path

from _support import FIXTURES, add_src_to_path

add_src_to_path()
from unifiedbddlinter.config import LinterConfig, ConfigError, DEFAULT_LIMITS  # noqa: E402
from unifiedbddlinter.engine import UnifiedLinter                              # noqa: E402


def write_config(directory: Path, payload: dict) -> Path:
    path = directory / ".unified-lintrc.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class Defaults(unittest.TestCase):
    def test_defaults_match_v1_hardcoded_values(self):
        """If a default here drifts, every published number silently moves.

        These four values were the default arguments of v1.0's rule signatures.
        They are asserted literally rather than compared to the source, because
        the whole point is to catch a change to the source.
        """
        cfg = LinterConfig()
        self.assertEqual(cfg.limit("max_feature_name"), 80)
        self.assertEqual(cfg.limit("max_scenario_name"), 80)
        self.assertEqual(cfg.limit("max_step_length"), 100)
        self.assertEqual(cfg.limit("max_steps_per_scenario"), 10)

    def test_spellcheck_is_off_by_default(self):
        self.assertFalse(LinterConfig().spellcheck)

    def test_missing_config_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = LinterConfig.load(start=tmp)
            self.assertEqual(cfg.warnings, [])
            self.assertEqual(cfg.source, "<defaults>")


class TakesEffect(unittest.TestCase):
    """The point of the module: settings must change observable behaviour."""

    def test_disabling_a_rule_removes_its_violations(self):
        cfg = LinterConfig()
        before = UnifiedLinter(full=True, config=cfg).lint_file(
            str(FIXTURES / "form_defects.feature"))
        fired = {v.rule_id for v in before}
        self.assertIn("S001", fired, "fixture no longer triggers S001; fix the fixture")

        cfg2 = LinterConfig()
        cfg2.disabled = {"S001"}
        after = UnifiedLinter(full=True, config=cfg2).lint_file(
            str(FIXTURES / "form_defects.feature"))
        self.assertNotIn("S001", {v.rule_id for v in after})
        self.assertLess(len(after), len(before))

    def test_severity_override_changes_reported_severity(self):
        cfg = LinterConfig()
        cfg.severity_overrides = {"S001": "critical"}
        violations = UnifiedLinter(full=True, config=cfg).lint_file(
            str(FIXTURES / "form_defects.feature"))
        s001 = [v for v in violations if v.rule_id == "S001"]
        self.assertTrue(s001, "fixture no longer triggers S001")
        for v in s001:
            self.assertEqual(v.severity.value, "critical")

    def test_lowering_a_limit_creates_violations(self):
        """A threshold the engine ignores would leave this count unchanged."""
        clean = str(FIXTURES / "clean.feature")
        baseline = UnifiedLinter(full=True, config=LinterConfig()).lint_file(clean)
        strict = LinterConfig()
        strict.limits["max_step_length"] = 5      # every step now exceeds it
        tightened = UnifiedLinter(full=True, config=strict).lint_file(clean)
        self.assertGreater(len(tightened), len(baseline),
                           "max_step_length is not consulted by the engine")


class Discovery(unittest.TestCase):
    def test_config_is_found_by_walking_upward(self):
        """A repository-root config must govern a nested directory."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root, {"limits": {"max_step_length": 42}})
            nested = root / "features" / "auth"
            nested.mkdir(parents=True)
            cfg = LinterConfig.load(start=str(nested))
            self.assertEqual(cfg.limit("max_step_length"), 42)

    def test_explicit_path_wins_over_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root, {"limits": {"max_step_length": 42}})
            other = root / "other.json"
            other.write_text(json.dumps({"limits": {"max_step_length": 7}}), encoding="utf-8")
            cfg = LinterConfig.load(path=str(other), start=str(root))
            self.assertEqual(cfg.limit("max_step_length"), 7)


class Robustness(unittest.TestCase):
    """A bad config warns and degrades; it never stops the linter."""

    def test_malformed_json_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".unified-lintrc.json").write_text("{ not json", encoding="utf-8")
            cfg = LinterConfig.load(start=tmp)
            self.assertTrue(cfg.warnings)
            self.assertEqual(cfg.limit("max_step_length"), DEFAULT_LIMITS["max_step_length"])

    def test_strict_mode_raises_for_ci(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".unified-lintrc.json").write_text("{ not json", encoding="utf-8")
            with self.assertRaises(ConfigError):
                LinterConfig.load_strict(start=tmp)

    def test_boolean_is_rejected_as_a_limit(self):
        """bool subclasses int in Python, so `true` would pass a naive check."""
        with tempfile.TemporaryDirectory() as tmp:
            write_config(Path(tmp), {"limits": {"max_step_length": True}})
            cfg = LinterConfig.load(start=tmp)
            self.assertTrue(any("integer" in w for w in cfg.warnings))
            self.assertEqual(cfg.limit("max_step_length"), 100)

    def test_unknown_severity_is_reported_with_the_rule_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_config(Path(tmp), {"rules": {"severity_overrides": {"S001": "catastrophic"}}})
            cfg = LinterConfig.load(start=tmp)
            self.assertTrue(any("S001" in w for w in cfg.warnings))

    def test_v1_config_file_still_loads(self):
        """A configuration written for the earlier release must keep working.

        The fixture is that release's own file, unedited. Projects that already
        have one should not have to rewrite it to use this version.
        """
        legacy = FIXTURES / "legacy-unified-lintrc.json"
        cfg = LinterConfig.load(path=str(legacy))
        self.assertEqual(cfg.warnings, [], f"v1.0 config produced warnings: {cfg.warnings}")
        self.assertEqual(cfg.limit("max_steps_per_scenario"), 10)
        self.assertIn("S002", cfg.severity_overrides)



class FileNameStyle(unittest.TestCase):
    """The filename convention is a project's choice, not the tool's.

    gherkin-lint's `file-name` rule demands kebab-case; cuke_linter's
    `feature_file_with_invalid_name` demands snake_case. They cannot both be
    satisfied -- measured at exactly one violation each way, per file. The tool
    therefore exposes the convention rather than silently picking a side.

    Default is snake_case because it is the cheaper side on the evaluation
    corpus: cuke_linter carries ~68k violations in total, so the ~19k it
    attributes to filenames dominates its result, while the same count is noise
    against gherkin-lint's ~1.49M.
    """

    FEATURE = ("Feature: Customer Checkout Flow\n\n"
               "Scenario: A customer completes a purchase\n  Given a cart\n")

    def _rename_to(self, style):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "whatever.feature"
            source.write_text(self.FEATURE, encoding="utf-8")
            out = Path(tmp) / "out"
            out.mkdir()
            from unifiedbddlinter.fixer import AutoFixer
            result = AutoFixer(quiet=True, file_name_style=style).fix_file(
                str(source), output_dir=str(out))
            return Path(result).name

    def test_default_is_snake_case(self):
        self.assertEqual(LinterConfig().file_name_style, "snake_case")
        self.assertEqual(self._rename_to("snake_case"),
                         "customer_checkout_flow.feature")

    def test_kebab_case_is_available(self):
        self.assertEqual(self._rename_to("kebab-case"),
                         "customer-checkout-flow.feature")

    def test_the_setting_is_read_from_the_policy_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_config(Path(tmp), {"formatting": {"file_name_style": "kebab-case"}})
            cfg = LinterConfig.load(start=tmp)
            self.assertEqual(cfg.warnings, [])
            self.assertEqual(cfg.file_name_style, "kebab-case")

    def test_an_unknown_style_is_rejected_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_config(Path(tmp), {"formatting": {"file_name_style": "CamelCase"}})
            cfg = LinterConfig.load(start=tmp)
            self.assertTrue(any("file_name_style" in w for w in cfg.warnings))
            self.assertEqual(cfg.file_name_style, "snake_case")


if __name__ == "__main__":
    unittest.main(verbosity=2)
