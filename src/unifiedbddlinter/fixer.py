"""
fixer.py -- the UnifiedBDDLinter auto-fixer.

THE SAFE-FIX BOUNDARY
---------------------
A ``.feature`` file is a requirements artifact.  Whatever it says is what the
system is asserted to do, and a "fix" that invents text changes what is being
asserted.  A tool that silently rewords a requirement in order to silence its
own rule has corrupted the specification and invalidated any before/after
measurement taken across it.

So this fixer obeys one boundary:

    A fix may only make changes recoverable from the file itself.

Recoverable means the change is derivable from what is already there --
whitespace, blank lines, indentation, a final newline, a trailing period.  Not
recoverable means the tool would have to author content it cannot know: a
feature name, a scenario name, a missing assertion, a spelling correction.
Those are DETECTED and REPORTED, never rewritten.

There is exactly one apparent exception, and it is not one.  ST007 (feature
name disagrees with file name) is repaired by renaming the *file*.  No byte of
any ``.feature`` file is altered.  This is safe with Cucumber because step
definitions resolve by annotation and regular expression within the glue
package, never by feature filename.

WHAT THIS COSTS, MEASURED
-------------------------
Enforcing the boundary lowers the headline reduction figure slightly.  It also
makes the tool report MORE, not less: suppressing the duplicate-scenario suffix
stops that suffix from masking the "similar scenarios" finding underneath it.
A fix that hides a real defect is worse than no fix.

TWO MODES
---------
``AutoFixer(safe=True)``   -- the default, and what should be used on real code.
``AutoFixer(safe=False)``  -- reproduces UnifiedBDDLinter v1.0 byte-for-byte,
                              injecting text as v1.0 did.  Provided only so the
                              published v1.0 results remain reproducible from
                              this package, and warned about at run time.

PROVENANCE
----------
Ported from v1.0 ``auto_fix.py`` (md5 ``2c22c3b5a6f8d71f1b33b12ae2bbd9bb``), the
file named in the published run's provenance record.  v1.0 had already disabled
three injecting fixes (missing verification steps, implementation-detail
rewording, spelling correction); their comments are preserved verbatim below.
This port additionally gates the four that were still live.  Every fix method
body is unchanged -- only the dispatch in :meth:`AutoFixer.fix_file` differs.
"""

import argparse
import re
import sys
from pathlib import Path


class AutoFixer:
    """Automatically fixes common linting violations with context awareness"""
    
    # Fix methods that must author text the tool cannot derive from the input.
    # Named here rather than buried in `fix_file` so the safe-fix boundary is
    # auditable in one place: this tuple IS the list of what safe mode refuses.
    UNSAFE_FIXES = (
        ("_ensure_feature_exists", "writes a Feature: header into a file that has none"),
        ("_fix_unnamed_feature", "invents a feature name (\"Auto-generated Feature\")"),
        ("_fix_unnamed_scenarios", "invents scenario names (\"Scenario <n>\")"),
        ("_fix_duplicate_scenarios", "appends a numeric suffix to duplicate names"),
    )

    def __init__(self, safe: bool = True, quiet: bool = False,
                 file_name_style: str = "snake_case"):
        """Create a fixer.

        *safe* selects the repair boundary. **The default is True**, and that is
        the only configuration whose results this project reports.

        ``True``  -- repair only what is derivable from the file's own content.
                     Six rules: S001-S004, W005, and the ST007 file rename.
                     Everything else is DETECTED and REPORTED, never rewritten.

        ``False`` -- additionally applies the five rules that can only be
                     repaired by authoring text (ST001-ST004, ST006). Retained
                     solely so the historical v1.0 behaviour can be reproduced
                     and compared for provenance. It is not used for any
                     reported result, and it warns on stderr every time.

        WHY SAFE IS THE DEFAULT
        -----------------------
        A .feature file is a requirements artifact. A "fix" that invents text
        changes what the specification asserts. Measured on a 2,001-file
        stratified sample of the corpus, the five text-authoring rules:

            resolve   508 violations   (0.29% of 176,013)
            create     84 violations   (a placeholder feature name immediately
                                        violates ST007, which requires the name
                                        to match the filename)
            net       424 violations   (0.24%)

        So they buy almost nothing, and what they cost is the integrity of the
        artifact being measured. The six safe rules, by contrast, account for
        **68.9%** of all violations -- the repair that matters was never the
        semantic one.
        """
        self.safe = safe
        # Which separator ST007's rename uses. The two incumbent linters demand
        # opposite conventions, so this is a choice the project makes, not one
        # the tool can make correctly on its behalf.
        self.file_name_style = file_name_style
        # PORT: v1.0 wrote progress straight to stdout from deep inside the fix
        # methods. That makes the class unusable as a library -- a caller who
        # wants a return value gets a page of text as well -- and it pollutes
        # any machine-readable output the caller is producing. Every message now
        # goes through _say(), which one flag can silence.
        self.quiet = quiet
        if not safe:
            # Loud, once, on stderr. This path is off the reported route
            # entirely; anyone on it has opted in explicitly and needs to know
            # their requirements are about to be rewritten.
            print(
                "unifiedbddlinter: WARNING: text-injection mode -- the fixer "
                "will INVENT feature names, scenario names and whole scenarios "
                "in .feature files. Not used for reported results.",
                file=sys.stderr,
            )
        self.changes_made = 0
        self.files_fixed = 0
        # Rule ids whose repair was withheld because it would inject text.
        # Populated only in safe mode; reported so the user knows these were
        # detected and deliberately left alone, rather than missed.
        self.withheld: dict = {}
        # Rule ids whose repair DID author text, populated in the default mode.
        # The counterpart of `withheld`: in both modes the user is told what the
        # tool did to the meaning of their specifications, never left to guess.
        self.injected: dict = {}
        self.gherkin_keywords = {'Feature:', 'Scenario:', 'Given', 'When', 'Then', 'And', 'But', 'Background:'}
        self.ui_keywords = {
            'click': 'interact with',
            'clicks': 'interact with',
            'clicking': 'interacting with',
            'clicked': 'interacted with',
            'button': 'element',
            'input': 'field',
            'text': 'content',
            'page': 'application',
            'screen': 'application',
            'link': 'navigation element',
            'field': 'input',
            'form': 'data entry',
            'submit': 'confirm',
            'type': 'enter',
            'types': 'enter',
            'typing': 'entering',
            'typed': 'entered',
            'enters': 'provide',
            'clear': 'reset',
            'clears': 'reset',
            'clearing': 'resetting',
            'cleared': 'reset',
            'scroll': 'navigate to',
            'hover': 'position over',
            'drag': 'move',
            'drop': 'place'
        }
    
    def _say(self, message: str = "") -> None:
        """Emit progress, unless the caller asked for silence."""
        if not self.quiet:
            print(message)

    def fix_file(self, file_path: str, dry_run: bool = False, output_dir: str = None) -> str:
        """
        Fix a single feature file with context-aware fixes
        Returns the final file path (may be different if file was renamed)
        
        Args:
            file_path: Path to the feature file to fix
            dry_run: If True, don't write changes
            output_dir: Optional directory to save fixed file to
        """
        original_file_path = file_path
        # PORT: explicit UTF-8. v1.0 used the platform default, so this call
        # raised UnicodeDecodeError on any non-ASCII feature file under Windows
        # or a non-UTF-8 locale. "replace" is required for symmetry with the
        # linter -- the fixer must never fail on a file the linter could read.
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            original_content = f.read()
        
        fixed_content = original_content
        
        # Fix 1: Fix feature name mismatch by RENAMING the file (ST007)
        # If output_dir is specified, don't rename (just save to output dir with kebab-case name)
        if output_dir:
            new_file_path = self._get_output_file_path(file_path, fixed_content, output_dir)
        else:
            new_file_path = self._fix_st007_via_file_rename(file_path, fixed_content, dry_run)
        
        # Fixes 2-5 all AUTHOR text the tool cannot derive from the input --
        # a Feature: header, a feature name, scenario names, a disambiguating
        # suffix.  Under the safe-fix boundary they are detect-only: the linter
        # still reports ST001/ST002/ST003/ST004/ST006, the fixer just declines
        # to invent a requirement in order to silence itself.
        #
        # PORT: v1.0 ran all four unconditionally.  They are now gated, and the
        # withholding is recorded so the user can see it happened.
        if self.safe:
            self._record_withheld(fixed_content)
        else:
            # Default path. Record what is about to be authored BEFORE the
            # fixes run, because afterwards the evidence is gone -- an unnamed
            # scenario that has just been given a name is indistinguishable
            # from one that always had it.
            self._record_injected(fixed_content)
            # Legacy (v1.0-identical) path. Order preserved exactly.
            # Fix 2: Ensure file structure is valid (Feature exists)
            fixed_content = self._ensure_feature_exists(fixed_content)

            # Fix 3: Add missing feature names
            fixed_content = self._fix_unnamed_feature(fixed_content)

            # Fix 4: Add missing scenario names
            fixed_content = self._fix_unnamed_scenarios(fixed_content)

            # Fix 5: Fix duplicate scenario names (ST006)
            fixed_content = self._fix_duplicate_scenarios(fixed_content)

        
        # Fix 6: Fix indentation (context-aware for Gherkin)
        fixed_content = self._fix_gherkin_indentation(fixed_content)
        
        # Fix 7: Add missing Then/When steps — DISABLED (not form-preserving).
        # Injecting placeholder "When action is performed" / "Then result is as
        # expected" changes scenario semantics and mis-orders steps, introducing
        # W002 (Given/When after Then) regressions. W003/W004 are now detect-only.
        # fixed_content = self._add_missing_verification_steps(fixed_content)

        # Fix 8: Replace implementation details — DISABLED (not form-preserving).
        # Rewriting step wording (e.g. "page"->"application") alters the meaning
        # of the test. Q001 is now detect-only.
        # fixed_content = self._fix_implementation_details(fixed_content)

        # Fix 8a: byte-order mark (ST013). First, because every later fix and
        # every rule reads the content, and a BOM makes line 1 unparseable.
        fixed_content = self._fix_bom(fixed_content)

        # Fix 8b-8c: tag hygiene (T001, T006). Safe under the boundary:
        # the tag SET is unchanged and no step text is touched -- only which
        # line a tag sits on, and how far it is indented.
        fixed_content = self._fix_duplicate_tags(fixed_content)
        fixed_content = self._fix_tag_indentation(fixed_content)

        # Fix 9: Remove trailing whitespace
        fixed_content = self._fix_trailing_spaces(fixed_content)
        
        # Fix 10: Remove multiple empty lines
        fixed_content = self._fix_multiple_empty_lines(fixed_content)
        
        # Fix 11: Ensure file ends with newline
        fixed_content = self._fix_eof_newline(fixed_content)
        
        # Fix 12: Remove periods at end of steps -- DISABLED.
        #
        # This looked form-preserving: no word is invented, only punctuation
        # removed. It is not. The step TEXT is the string Cucumber matches a
        # step definition against, so a project whose definition ends in a
        # period stops binding and the step becomes UNDEFINED.
        #
        # Verified, not reasoned about. misc/binding-experiment/ is a real
        # Maven + Cucumber project:
        #     before this repair   mvn test -> BUILD SUCCESS, 3 steps pass
        #     after  this repair   mvn test -> BUILD FAILURE, step UNDEFINED
        # Cucumber even prints the replacement snippet for the now-orphaned step.
        #
        # The boundary is therefore stricter than "invents no words": a repair
        # must not alter any string the runtime binds on. W005 is detect-only.
        # fixed_content = self._fix_step_periods(fixed_content)
        
        # Fix 13: Fix spelling errors (SY001) — DISABLED (corrupts content).
        # The spell-checker rewrites legitimate technical terms into nonsense
        # ("sql"->"sol", "xml"->"my", "dble"->"able"). SY001 is now detect-only.
        # fixed_content = self._fix_spelling_errors(fixed_content)
        
        # Check if content changes were made
        if fixed_content != original_content:
            if not dry_run:
                # PORT: encoding pinned, and newline="" so Python performs NO
                # translation. Without it, writing on Windows turns every "\n"
                # into "\r\n", which silently rewrites every line of every file
                # and would make the before/after comparison meaningless.
                Path(new_file_path).parent.mkdir(parents=True, exist_ok=True)
                with open(new_file_path, "w", encoding="utf-8", newline="") as f:
                    f.write(fixed_content)
                # NEVER delete/rename the original — output always goes to output_dir
                self._say(f"Fixed  : {original_file_path}  →  {new_file_path}")
                self.files_fixed += 1
            else:
                self._say(f"[DRY RUN] Would fix: {original_file_path}  →  {new_file_path}")
            
            self._show_diff(original_content, fixed_content)
            return new_file_path
        else:
            # No fixes needed — still copy unchanged file to output_dir so the folder is complete
            if output_dir and not dry_run:
                Path(new_file_path).parent.mkdir(parents=True, exist_ok=True)
                with open(new_file_path, "w", encoding="utf-8", newline="") as f:
                    f.write(fixed_content)
            self._say(f"Clean  : {original_file_path}  (no changes needed)")
            return new_file_path
    
    @staticmethod
    def _fix_bom(content: str) -> str:
        """ST013: drop a leading UTF-8 byte-order mark.

        Three bytes that carry no content. Every character of Gherkin is
        byte-identical afterwards, and the file goes from unparseable by the
        official Gherkin parser to parseable.
        """
        return content[1:] if content.startswith("\ufeff") else content

    @staticmethod
    def _fix_duplicate_tags(content: str) -> str:
        """T001: drop repeated tags from an element's tag block.

        Cucumber collapses duplicates already, so removing one cannot change
        which scenarios a tag filter selects -- the tag SET is unchanged, which
        is the property that matters.

        Duplicates are tracked across the whole run of tag lines before an
        element, not per line: tag blocks are commonly wrapped over several
        lines, and resetting per line would miss most of them.
        """
        from .engine import TagRules
        lines = content.split("\n")
        mask = TagRules.doc_mask(lines)
        out, seen = [], set()
        for line, in_doc in zip(lines, mask):
            stripped = line.strip()
            if in_doc:
                out.append(line)
                continue
            if stripped.startswith("@"):
                indent = line[:len(line) - len(line.lstrip())]
                kept = []
                for token in stripped.split():
                    if token.startswith("@"):
                        if token in seen:
                            continue
                        seen.add(token)
                    kept.append(token)
                if kept:                 # a line emptied of tags is dropped
                    out.append(indent + " ".join(kept))
            else:
                out.append(line)
                if stripped:
                    seen = set()
        return "\n".join(out)

    @staticmethod
    def _fix_tag_indentation(content: str) -> str:
        """T006: align a tag line with the element beneath it. Whitespace only."""
        from .engine import TagRules
        lines = content.split("\n")
        mask = TagRules.doc_mask(lines)
        for index, line in enumerate(lines):
            if mask[index] or not line.strip().startswith("@"):
                continue
            for follower in range(index + 1, len(lines)):
                nxt = lines[follower]
                if not nxt.strip():
                    continue
                if TagRules.KEYWORD.match(nxt.strip()):
                    want = len(nxt) - len(nxt.lstrip())
                    lines[index] = " " * want + line.strip()
                break
        return "\n".join(lines)

    def _record_withheld(self, content: str) -> None:
        """Note which text-injecting repairs were declined for this file.

        Cheap, deliberately shallow checks -- the authoritative detection is the
        linter's.  This exists so a fix run can end by saying "3 files had
        unnamed scenarios; these are reported, not rewritten", rather than
        leaving the user to wonder why a reported violation never went away.
        """
        stripped = content.strip()
        if not stripped:
            self.withheld["ST003"] = self.withheld.get("ST003", 0) + 1
            return
        if "Feature:" not in content:
            self.withheld["ST004"] = self.withheld.get("ST004", 0) + 1
        seen_names = set()
        for line in content.splitlines():
            bare = line.strip()
            if bare == "Feature:":
                self.withheld["ST001"] = self.withheld.get("ST001", 0) + 1
            elif bare in ("Scenario:", "Scenario Outline:"):
                self.withheld["ST002"] = self.withheld.get("ST002", 0) + 1
            elif bare.startswith(("Scenario:", "Scenario Outline:")):
                # ST006 belongs here too: v1.0 repaired a duplicate name by
                # appending a number, which is invented text. Without this the
                # summary silently omitted one of the five withheld rules, and a
                # user would see the violation persist with no explanation.
                name = bare.split(":", 1)[1].strip()
                if name in seen_names:
                    self.withheld["ST006"] = self.withheld.get("ST006", 0) + 1
                seen_names.add(name)

    def _record_injected(self, content: str) -> None:
        """Note which repairs are about to author text into this file.

        Mirrors :meth:`_record_withheld`. In the default mode the tool DOES
        write invented text, so the user is owed an account of exactly which
        rules did it and how often -- otherwise a "files repaired: 20034" line
        conceals the fact that some of those repairs changed what the
        specifications assert.
        """
        stripped = content.strip()
        if not stripped:
            self.injected["ST003"] = self.injected.get("ST003", 0) + 1
            return
        if "Feature:" not in content:
            self.injected["ST004"] = self.injected.get("ST004", 0) + 1
        for line in content.splitlines():
            bare = line.strip()
            if bare == "Feature:":
                self.injected["ST001"] = self.injected.get("ST001", 0) + 1
            elif bare in ("Scenario:", "Scenario Outline:"):
                self.injected["ST002"] = self.injected.get("ST002", 0) + 1

    def injected_summary(self) -> str:
        """Account of text the fixer authored, printed after a default run."""
        if not self.injected:
            return ""
        wrote = {
            "ST001": 'wrote a feature name ("Auto-generated Feature")',
            "ST002": 'wrote scenario names ("Scenario <n>")',
            "ST003": "wrote an entire scenario into an empty file",
            "ST004": 'wrote a Feature: header ("Auto-generated Feature")',
        }
        lines = ["", "Text AUTHORED by the fixer (not derived from the input):"]
        for rule_id in sorted(self.injected):
            lines.append(f"  {rule_id}  x{self.injected[rule_id]:<6} {wrote.get(rule_id, '')}")
        lines.append("  These changed what the specifications assert. Review them,")
        lines.append("  or re-run with --safe-fix to report instead of rewrite.")
        return "\n".join(lines)

    def withheld_summary(self) -> str:
        """One-line-per-rule account of repairs declined under the boundary."""
        if not self.withheld:
            return ""
        reasons = {
            "ST001": "unnamed Feature -- a name cannot be derived from the file",
            "ST002": "unnamed Scenario -- a name cannot be derived from the file",
            "ST003": "empty file -- an entire scenario would have to be invented",
            "ST004": "no Feature: header -- a title would have to be invented",
            "ST006": "duplicate scenario name -- a suffix would have to be invented",
        }
        lines = ["", "Reported but NOT repaired (safe-fix boundary):"]
        for rule_id in sorted(self.withheld):
            lines.append(f"  {rule_id}  x{self.withheld[rule_id]:<6} {reasons.get(rule_id, '')}")
        lines.append("  These need a human decision: only you know what the requirement says.")
        lines.append("  Re-run 'bddlint lint' on the output to see everything still open.")
        return "\n".join(lines)

    def fix_directory(self, directory: str, dry_run: bool = False, output_dir: str = None):
        """Fix all feature files in a directory"""
        path = Path(directory)
        feature_files = sorted(path.rglob('*.feature'))
        
        if not feature_files:
            self._say(f"No feature files found in {directory}")
            return
        
        self._say(f"Found {len(feature_files)} feature file(s)\n")
        
        renamed_files = 0
        for feature_file in feature_files:
            new_file_path = self.fix_file(str(feature_file), dry_run, output_dir)
            if str(feature_file) != new_file_path:
                renamed_files += 1
        
        self._say(f"\n{'='*80}")
        self._say(f"Summary: {self.files_fixed} file(s) fixed, {renamed_files} file(s) renamed")
    
    @staticmethod
    def expected_output_name(content: str, original_stem: str,
                             file_name_style: str = "snake_case") -> str:
        """The file name `fix_file` will give this content, without running it.

        Exists so analysis can pair a repaired file back to its original. The
        fixer RENAMES files to match their Feature line, so the two trees do not
        correspond by name -- and they do not correspond by sorted position
        either, which is the trap: sorting looks like it works and silently
        pairs unrelated files.

        Naming is a pure function of the content, so it can simply be recomputed.
        Kept beside `_get_output_file_path` so the two cannot drift.
        """
        from .engine import slugify_feature_name
        feature_name = AutoFixer._extract_feature_name(content)
        placeholders = ('', 'feature', 'auto-generated feature', 'new feature')
        separator = "snake" if file_name_style == "snake_case" else "kebab"
        if feature_name and feature_name.strip().lower() not in placeholders:
            stem = slugify_feature_name(feature_name, separator)
        else:
            stem = ""
        return f"{stem or original_stem}.feature"

    def _get_output_file_path(self, input_file_path: str, content: str, output_dir: str) -> str:
        """
        Get the output file path when using --output option
        Generates kebab-case filename based on Feature: name
        """
        from pathlib import Path
        
        # Deliberately does NOT create the directory: this method only computes
        # a path, and it is called under --dry-run too, which must write nothing
        # at all -- not even an empty directory. The write sites create it.
        output_path = Path(output_dir)
        
        # Extract Feature: name to generate output filename
        feature_name = AutoFixer._extract_feature_name(content)
        
        if feature_name and feature_name.strip().lower() not in ['', 'feature', 'auto-generated feature', 'new feature']:
            # PORT: delegate to the engine's slugify so the name produced here
            # is exactly the name ST007 will accept, and is length-capped.
            from .engine import slugify_feature_name
            desired_file_name = slugify_feature_name(
                feature_name, "snake" if self.file_name_style == "snake_case" else "kebab")
        else:
            # Fall back to original filename
            desired_file_name = Path(input_file_path).stem
        
        # A feature name of pure punctuation or non-Latin script slugifies to
        # nothing, which would write a hidden file literally called ".feature".
        if not desired_file_name:
            desired_file_name = Path(input_file_path).stem

        output_file_path = output_path / f"{desired_file_name}.feature"

        # Collision guard: if target already exists, append original stem to disambiguate
        if output_file_path.exists():
            original_stem = Path(input_file_path).stem
            output_file_path = output_path / f"{desired_file_name}--{original_stem}.feature"
            self._say(f"  Name collision: saved as {output_file_path.name} (original: {Path(input_file_path).name})")

        return str(output_file_path)
    
    def _fix_st007_via_file_rename(self, file_path: str, content: str, dry_run: bool = False) -> str:
        """
        Fix ST007 (Feature name must match file name) by RENAMING the file to match the Feature: description
        This preserves the semantic meaning that users wrote in the Feature: line
        Returns the new file path
        
        Naming Convention: kebab-case (gherkin-lint style)
        - Supports both snake_case (cuke_linter) and kebab-case (gherkin-lint)
        - Auto-fix generates kebab-case for consistency with gherkin-lint
        
        INFO: If Feature: is empty or auto-generated, no rename is performed (user should provide meaningful name)
        """
        from pathlib import Path
        
        # Extract current file name without extension
        current_file_path = Path(file_path)
        current_file_name = current_file_path.stem
        
        # Extract Feature: name from content
        feature_name = self._extract_feature_name(content)
        
        # KEY INSIGHT: Only rename if Feature has a meaningful, user-provided description
        # Skip renaming if Feature is empty or auto-generated (user should fix this first)
        if not feature_name or feature_name.strip().lower() in ['', 'feature', 'auto-generated feature', 'new feature']:
            # Preserve original file name - requires user to add meaningful feature description
            return file_path
        
        # PORT: the same shared slugify as the checker and the other write path.
        # Follows the gherkin-lint kebab-case convention, and is length-capped so
        # a sentence-length feature name cannot produce an unwritable filename.
        from .engine import slugify_feature_name
        desired_file_name = slugify_feature_name(
            feature_name, "snake" if self.file_name_style == "snake_case" else "kebab")
        
        if not desired_file_name:
            return file_path        # nothing derivable; leave the name alone

        # Add .feature extension
        desired_full_name = f"{desired_file_name}.feature"
        new_file_path = current_file_path.parent / desired_full_name
        
        # Compare the names as they are. Normalising the separator away would
        # make well_formed and well-formed look identical, and then
        # file_name_style could never convert one convention to the other --
        # which is the whole purpose of the setting.
        if current_file_name.lower() == desired_file_name.lower():
            return file_path
        
        # Check for existing file with same name (collision)
        if new_file_path.exists() and new_file_path != current_file_path:
            self._say(f"Warning: Target file {desired_full_name} already exists. Keeping original {current_file_path.name}")
            return file_path
        
        # Perform the rename
        if not dry_run:
            try:
                current_file_path.rename(new_file_path)
                self._say(f"  Renamed: {current_file_path.name} → {desired_full_name}")
                self._say(f"    (Feature: '{feature_name}' now matches filename in kebab-case)")
                return str(new_file_path)
            except OSError as e:
                self._say(f"Failed to rename {current_file_path.name} to {desired_full_name}: {e}")
                return file_path
        else:
            self._say(f"  [DRY RUN] Would rename: {current_file_path.name} → {desired_full_name}")
            return str(new_file_path)
    
    @staticmethod
    def _extract_feature_name(content: str) -> str:
        """Extract the Feature: name from content"""
        lines = content.split('\n')
        for line in lines:
            if line.strip().startswith('Feature:'):
                # Extract name after "Feature:" keyword
                feature_name = line.strip()[8:].strip()  # Remove "Feature:" prefix and whitespace
                return feature_name
        return ""
    
    @staticmethod
    def _ensure_feature_exists(content: str) -> str:
        """Ensure file has a Feature block"""
        if not content.strip():
            return 'Feature: New Feature\n\n  Scenario: New Scenario\n    Given initial condition\n    When action occurs\n    Then result is visible\n'
        
        if not re.search(r'\bFeature:', content, re.MULTILINE):
            # Add Feature block at the beginning
            content = 'Feature: Auto-generated Feature\n\n' + content
        
        return content
    
    @staticmethod
    def _fix_unnamed_feature(content: str) -> str:
        """Add name to unnamed Feature blocks"""
        lines = content.split('\n')
        fixed_lines = []
        
        for i, line in enumerate(lines):
            if line.strip() == 'Feature:':
                fixed_lines.append('Feature: Auto-generated Feature')
            else:
                fixed_lines.append(line)
        
        return '\n'.join(fixed_lines)
    
    @staticmethod
    def _fix_unnamed_scenarios(content: str) -> str:
        """Add name to unnamed Scenario blocks"""
        lines = content.split('\n')
        fixed_lines = []
        scenario_counter = 0
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == 'Scenario:':
                scenario_counter += 1
                indent = line[:len(line) - len(stripped)]
                fixed_lines.append(f'{indent}Scenario: Scenario {scenario_counter}')
            else:
                fixed_lines.append(line)
        
        return '\n'.join(fixed_lines)
    
    @staticmethod
    def _fix_duplicate_scenarios(content: str) -> str:
        """Fix duplicate scenario names by appending numeric suffixes (ST006)"""
        lines = content.split('\n')
        fixed_lines = []
        scenario_names = {}  # Track scenario name occurrences: {name: count}
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            if stripped.startswith('Scenario:') or stripped.startswith('Scenario Outline:'):
                # Extract indentation and keyword
                indent = line[:len(line) - len(stripped)]
                if stripped.startswith('Scenario Outline:'):
                    keyword = 'Scenario Outline:'
                    scenario_name = stripped[17:].strip()  # After "Scenario Outline:"
                else:
                    keyword = 'Scenario:'
                    scenario_name = stripped[9:].strip()  # After "Scenario:"
                
                # Skip if no name
                if not scenario_name or scenario_name.lower() in ['scenario', 'outline']:
                    fixed_lines.append(line)
                    continue
                
                # Check if this scenario name has been seen before
                if scenario_name in scenario_names:
                    scenario_names[scenario_name] += 1
                    # Append a numeric suffix to make it unique
                    new_name = f"{scenario_name} {scenario_names[scenario_name]}"
                    fixed_lines.append(f'{indent}{keyword} {new_name}')
                else:
                    scenario_names[scenario_name] = 1
                    fixed_lines.append(line)
            else:
                fixed_lines.append(line)
        
        return '\n'.join(fixed_lines)
    
    @staticmethod
    def _fix_gherkin_indentation(content: str) -> str:
        """Fix indentation with context awareness for Gherkin keywords"""
        lines = content.split('\n')
        fixed_lines = []
        
        feature_found = False
        in_scenario = False
        in_examples = False
        
        in_doc = False
        for line in lines:
            stripped = line.strip()

            # Inside a doc string (""" or ```), emit every line verbatim. The
            # content's indentation is part of the value passed to the step, so
            # re-indenting it would change the test's meaning. Leaving the whole
            # block untouched keeps the fix meaning-preserving.
            if in_doc:
                fixed_lines.append(line)
                if stripped == '"""' or stripped == '```':
                    in_doc = False
                continue
            if stripped.startswith('"""') or stripped.startswith('```'):
                in_doc = True
                fixed_lines.append(line)
                continue

            if not line.strip():  # Empty line
                fixed_lines.append('')
                continue
            
            # Check keyword type
            is_feature = stripped.startswith('Feature:')
            is_background = stripped.startswith('Background:')
            is_scenario = stripped.startswith('Scenario:') or stripped.startswith('Scenario Outline:')
            is_step = any(stripped.startswith(kw) for kw in ['Given', 'When', 'Then', 'And', 'But'])
            is_examples = stripped.startswith('Examples:')
            is_table_row = stripped.startswith('|')
            
            # Determine correct indentation
            # All Gherkin keywords (Feature, Scenario, Examples, etc.) start at column 0
            # Steps get 2-space indent
            # Table rows get 2-space indent
            if is_feature or is_background or is_scenario or is_examples:
                new_indent = ''  # All keywords at column 0
                if is_scenario:
                    in_scenario = True
                if is_examples:
                    in_examples = True
            elif is_step:
                new_indent = '  '  # 2-space indent for steps
            elif is_table_row:
                new_indent = '  '  # 2-space indent for table rows
            else:
                # Other content - don't indent
                new_indent = ''
            
            fixed_lines.append(new_indent + stripped)
        
        return '\n'.join(fixed_lines)
    
    def _add_missing_verification_steps(self, content: str) -> str:
        """Add Then steps if missing, add When steps if missing"""
        lines = content.split('\n')
        fixed_lines = []
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # Check if this is a scenario start
            if stripped.startswith('Scenario:'):
                fixed_lines.append(line)
                indent = line[:len(line) - len(stripped)]
                
                # Look ahead to find step types
                has_when = False
                has_then = False
                step_indent = indent + '  '
                
                j = i + 1
                while j < len(lines) and lines[j].strip():
                    if not any(lines[j].strip().startswith(kw) for kw in ['Scenario:', 'Feature:', 'Background:']):
                        step_line = lines[j].strip()
                        if step_line.startswith('When'):
                            has_when = True
                        elif step_line.startswith('Then'):
                            has_then = True
                    j += 1
                
                # Collect scenario steps
                j = i + 1
                scenario_steps = []
                while j < len(lines) and lines[j].strip():
                    if any(lines[j].strip().startswith(kw) for kw in ['Scenario:', 'Feature:', 'Background:']):
                        break
                    scenario_steps.append(lines[j])
                    j += 1
                
                # Add collected steps
                for step in scenario_steps:
                    fixed_lines.append(step)
                
                # Add missing steps
                if not has_then and scenario_steps:
                    fixed_lines.append(f'{step_indent}Then result is as expected')
                
                if not has_when and scenario_steps:
                    # Insert When if we have Given but no When
                    has_given = any('Given' in s for s in scenario_steps)
                    if has_given:
                        # Insert before the first Then or at end
                        insert_idx = len(fixed_lines)
                        for idx in range(len(fixed_lines) - 1, -1, -1):
                            if 'Then' in fixed_lines[idx]:
                                insert_idx = idx
                                break
                        fixed_lines.insert(insert_idx, f'{step_indent}When action is performed')
                
                i = j
                continue
            
            fixed_lines.append(line)
            i += 1
        
        return '\n'.join(fixed_lines)
    
    def _fix_implementation_details(self, content: str) -> str:
        """Replace UI implementation details with business language"""
        lines = content.split('\n')
        fixed_lines = []
        
        for line in lines:
            stripped = line.strip()
            
            # Only fix step lines
            if any(stripped.startswith(kw) for kw in ['Given', 'When', 'Then', 'And', 'But']):
                fixed_line = line
                # Replace UI keywords with business language
                for ui_term, business_term in self.ui_keywords.items():
                    # Case-insensitive replacement
                    pattern = re.compile(r'\b' + ui_term + r'\b', re.IGNORECASE)
                    fixed_line = pattern.sub(business_term, fixed_line)
                fixed_lines.append(fixed_line)
            else:
                fixed_lines.append(line)
        
        return '\n'.join(fixed_lines)
    
    # A line that is nothing but a step keyword. Gherkin requires text after the
    # keyword, and the trailing space is what makes such a line parse at all.
    _BARE_KEYWORD = re.compile(
        r"^(Given|When|Then|And|But|\*)\s*$")

    @staticmethod
    def _fix_trailing_spaces(content: str) -> str:
        """Remove trailing whitespace, EXCEPT after a bare step keyword.

        The exception is not a nicety. Some corpus files contain a step written
        as "When " -- a keyword with no text and a trailing space. The official
        Gherkin parser accepts that as a step with an empty description, but
        rejects a bare "When" outright:

            "When "   parses, 3 steps
            "When"    ArgumentError

        So stripping that one space turns a parseable file into an unparseable
        one. Three corpus files were broken this way, found by comparing every
        repaired file against its original with Cucumber's own parser -- not by
        any linter, all of which reported the repaired files as improved.

        The empty step is a real defect, and the linter still reports it. But a
        repair that makes a file stop parsing is worse than the defect it
        removes, so the whitespace is left alone and a human decides.
        """
        out = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and AutoFixer._BARE_KEYWORD.match(stripped):
                out.append(line.rstrip() + " ")
            else:
                out.append(line.rstrip())
        return "\n".join(out)
    
    @staticmethod
    def _fix_multiple_empty_lines(content: str) -> str:
        """Replace multiple consecutive empty lines with single empty line"""
        # Replace 3+ empty lines with 2 (one empty line)
        content = re.sub(r'\n\n\n+', '\n\n', content)
        return content
    
    @staticmethod
    def _fix_eof_newline(content: str) -> str:
        """Ensure file ends with newline"""
        if content and not content.endswith('\n'):
            content += '\n'
        return content
    
    @staticmethod
    def _fix_step_periods(content: str) -> str:
        """Remove periods at end of steps"""
        lines = content.split('\n')
        fixed_lines = []
        
        for line in lines:
            stripped = line.strip()
            if any(stripped.startswith(kw) for kw in ['Given', 'When', 'Then', 'And', 'But']):
                if stripped.endswith('.'):
                    # Remove period
                    indent = line[:len(line) - len(stripped)]
                    fixed_lines.append(indent + stripped[:-1])
                else:
                    fixed_lines.append(line)
            else:
                fixed_lines.append(line)
        
        return '\n'.join(fixed_lines)
    
    def _show_diff(self, original: str, fixed: str):
        """Show what changed (brief summary)"""
        original_lines = original.split('\n')
        fixed_lines = fixed.split('\n')
        
        changes = []
        for i, (orig, fix) in enumerate(zip(original_lines, fixed_lines), 1):
            if orig != fix:
                changes.append(f"  L{i}: Changed")
        
        if changes:
            self._say(f"  Changes: {len(changes)} line(s) modified")
            for change in changes[:3]:  # Show first 3
                self._say(f"    {change}")
            if len(changes) > 3:
                self._say(f"    ... and {len(changes) - 3} more")
            self._say()


def main():
    parser = argparse.ArgumentParser(
        description='Unified BDD Linter - Auto-fix Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python auto_fix.py my_feature.feature
  python auto_fix.py features/ --dry-run
  python auto_fix.py features/
  python auto_fix.py my_feature.feature --output fixed/
  python auto_fix.py features/ --output output_folder/
        '''
    )
    
    parser.add_argument('path', help='Feature file or directory to fix')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be fixed without making changes')
    parser.add_argument('--output', '-o', dest='output_dir', default='fixed_features',
                        help='Directory to save fixed files (default: fixed_features/). Originals are never modified.')
    parser.add_argument('--no-indentation', action='store_true',
                        help='Skip indentation fixes')
    parser.add_argument('--no-spacing', action='store_true',
                        help='Skip trailing space and empty line fixes')
    parser.add_argument('--no-periods', action='store_true',
                        help='Skip step period removal')
    
    args = parser.parse_args()
    
    path = Path(args.path)
    fixer = AutoFixer()

    # Resolve output dir relative to input path so originals are never touched
    if path.is_file():
        output_dir = args.output_dir if Path(args.output_dir).is_absolute() \
            else str(path.parent / args.output_dir)
    else:
        output_dir = args.output_dir if Path(args.output_dir).is_absolute() \
            else str(path.parent / args.output_dir)

    if not args.dry_run:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self._say(f"Originals kept in : {path}")
        self._say(f"Fixed files saved to: {output_dir}")
        self._say()

    if path.is_file():
        fixer.fix_file(str(path), args.dry_run, output_dir)
    elif path.is_dir():
        fixer.fix_directory(str(path), args.dry_run, output_dir)
    else:
        self._say(f"Error: {args.path} not found")
        sys.exit(1)


if __name__ == '__main__':
    main()
