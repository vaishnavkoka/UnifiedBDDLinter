"""
UnifiedBDDLinter -- detection and form-preserving repair of quality
anti-patterns in Gherkin ``.feature`` files.

A single engine that subsumes the rule sets of the two de facto Gherkin
linters (gherkin-lint, cuke_linter) and adds a Workflow and Quality family
neither of them has, plus an auto-fixer constrained by a *safe-fix boundary*:
it may only make changes recoverable from the file itself, never author text.

Public API -- these five names are the supported surface; everything else is
an implementation detail and may move between versions::

    from unifiedbddlinter import UnifiedLinter, LinterConfig, Violation, RuleSeverity
    from unifiedbddlinter import AutoFixer

    linter = UnifiedLinter(full=True, config=LinterConfig.load())
    for violation in linter.lint_file("login.feature"):
        print(violation.rule_id, violation.line, violation.message)

The package is standard library only.  ``pyspellchecker`` enables rule SY001
and nothing else; its absence is reported, never silently ignored.
"""

__version__ = "1.0"

# The version of the original tool this package was ported from, and the md5 of
# the engine file that produced the published results.  Recorded in code rather
# than only in documentation so that `bddlint version` can state the lineage of
# any installed copy, which is what an artefact evaluation asks for.
__ported_from__ = "UnifiedBDDLinter v1.0"
__origin_engine_md5__ = "e00914cc0c52787466193752cfdd3469"

from .config import LinterConfig, ConfigError            # noqa: E402
from .engine import UnifiedLinter, Violation, RuleSeverity, UnifiedParser  # noqa: E402
from .fixer import AutoFixer                             # noqa: E402

__all__ = [
    "UnifiedLinter",
    "AutoFixer",
    "LinterConfig",
    "ConfigError",
    "Violation",
    "RuleSeverity",
    "UnifiedParser",
    "__version__",
]
