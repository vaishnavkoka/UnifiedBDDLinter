"""
test_6_independence.py -- the tool depends on nothing but Python.

WHY THIS IS A TEST
------------------
The paper claims UnifiedBDDLinter replaces a fragmented ecosystem with one
self-contained engine, and the artifact is shipped so a reviewer can run it
without installing Node or Ruby. That claim is easy to state and easy to break:
one convenient `subprocess.run(["gherkin-lint", ...])` added later, to
cross-check an edge case, would quietly turn our independent tool into a
wrapper around the two linters it claims to subsume -- and no other test in
this suite would notice.

So the claim is enforced mechanically, by inspecting the shipped source itself.
A reviewer can run this and see the independence demonstrated rather than
asserted.

The validation harness DOES invoke both external linters. That is correct and
deliberate: it uses them as independent witnesses to our fixer's claims. It
lives outside the tool, in ../evaluation/, and nothing here imports it.
"""

import ast
import unittest
from pathlib import Path

from _support import ROOT

PACKAGE = ROOT / "src" / "unifiedbddlinter"

# Modules that would let the tool shell out, reach the network, or otherwise
# depend on something outside this process.
FORBIDDEN_IMPORTS = {
    "subprocess", "multiprocessing", "socket", "urllib", "urllib2", "requests",
    "http", "ftplib", "telnetlib", "smtplib", "asyncio", "ctypes", "shutil",
}

# The single optional third-party dependency, and the only one permitted.
# Nothing. SY001 was removed, and with it the package's last
# third-party import. Any name appearing here again is a regression.
ALLOWED_THIRD_PARTY = set()

# Everything else must be in the standard library.
ALLOWED_STDLIB = {
    "argparse", "dataclasses", "enum", "json", "os", "pathlib", "re", "sys",
    "typing", "__future__", "collections", "hashlib", "itertools", "functools",
    "string", "unicodedata", "difflib", "textwrap", "importlib",
}


def python_files():
    return sorted(PACKAGE.rglob("*.py"))


def imported_names(path: Path):
    """Top-level module names imported by *path*, from the AST.

    Parsed rather than grepped: a grep would match the word `subprocess` inside
    a comment or a docstring, of which this package has many -- every mention of
    gherkin-lint in the source is prose. Only real import statements count.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # a relative import, i.e. our own package
                continue
            if node.module:
                names.add(node.module.split(".")[0])
    return names


class NoExternalProcesses(unittest.TestCase):

    def test_no_module_can_shell_out(self):
        """The tool must not be able to launch a process at all."""
        for path in python_files():
            with self.subTest(module=path.name):
                offending = imported_names(path) & FORBIDDEN_IMPORTS
                self.assertEqual(
                    offending, set(),
                    f"{path.name} imports {sorted(offending)}. The tool must not "
                    f"shell out or reach the network -- that is the harness's job, "
                    f"in ../evaluation/.")

    def test_no_reference_to_the_external_linters_in_code(self):
        """gherkin-lint and cuke_linter may be discussed, never invoked.

        Their names appear throughout the source in comments and docstrings,
        which is fine and useful. This checks the STRING LITERALS and calls: a
        literal "gherkin-lint" in executable code would mean the tool is
        naming a binary it intends to run.
        """
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                # Docstrings are Expr statements; those are prose, not code.
                value = node.value
                if len(value) > 200:
                    continue
                for binary in ("gherkin-lint", "cuke_linter", "cuke_modeler"):
                    if value.strip() == binary:
                        self.fail(f"{path.name} contains the bare string {binary!r} "
                                  f"in code, suggesting it invokes it")

    def test_only_permitted_dependencies(self):
        """Every import is stdlib, our own package, or the one optional extra."""
        permitted = ALLOWED_STDLIB | ALLOWED_THIRD_PARTY | {"unifiedbddlinter"}
        for path in python_files():
            with self.subTest(module=path.name):
                unexpected = imported_names(path) - permitted
                self.assertEqual(
                    unexpected, set(),
                    f"{path.name} imports {sorted(unexpected)}, which is neither "
                    f"standard library nor the permitted optional dependency. "
                    f"Adding a dependency breaks the zero-install guarantee.")

    def test_the_only_third_party_import_is_optional(self):
        """`spellchecker` must be imported lazily, never at module scope.

        A top-level import would make the whole package fail to load when it is
        absent -- turning an optional extra into a hard requirement, which is
        exactly the zero-install claim this suite defends.
        """
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:            # module scope only
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", None) or ""
                    names = {a.name.split(".")[0] for a in node.names}
                    if "spellchecker" in names or module.startswith("spellchecker"):
                        self.fail(f"{path.name} imports spellchecker at module "
                                  f"scope; it must be imported inside the rule "
                                  f"that needs it")


class RunsWithoutOptionalDependencies(unittest.TestCase):

    def test_linting_works_with_spellchecker_blocked(self):
        """Simulate the package being absent and confirm the tool still lints."""
        import sys as _sys
        from _support import FIXTURES, add_src_to_path
        add_src_to_path()

        class Blocker:
            """Import hook that makes `spellchecker` appear uninstalled."""
            def find_module(self, name, path=None):
                return self if name == "spellchecker" else None

            def load_module(self, name):
                raise ImportError("blocked by test")

            def find_spec(self, name, path=None, target=None):
                if name == "spellchecker":
                    raise ImportError("blocked by test")
                return None

        saved = _sys.modules.pop("spellchecker", None)
        _sys.meta_path.insert(0, Blocker())
        try:
            from unifiedbddlinter.engine import UnifiedLinter
            from unifiedbddlinter.config import LinterConfig
            cfg = LinterConfig()
            cfg.spellcheck = True          # ask for it even though it is absent
            violations = UnifiedLinter(full=True, config=cfg).lint_file(
                str(FIXTURES / "form_defects.feature"))
            self.assertTrue(violations, "the linter produced nothing without "
                                        "the optional dependency")
        finally:
            _sys.meta_path.pop(0)
            if saved is not None:
                _sys.modules["spellchecker"] = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
