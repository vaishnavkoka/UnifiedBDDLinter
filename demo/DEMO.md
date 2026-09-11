# UnifiedBDDLinter — demo script

Everything in one file: setup, commands, expected output, and what to say.
Repository: **bcgov/onRouteBCSpecification**, 230 feature files.

*Spoken lines are in quotes. `(cues)` are for you — never read them aloud.*

Every command and number below was executed against this artifact on
**2026-09-09** and verified against the run's own `summary.csv`.

---

# Before you record

## Off camera

    npm ls -g gherkin-lint        # gherkin-lint@4.2.4
    cuke_linter --version         # 1.4.0

> `gherkin-lint` has **no version flag** — neither `--version` nor `-V`. It
> prints `error: unknown option` and still exits 0, so a script cannot detect the
> failure. `npm ls -g` is the only way to read its version.

Check these **silently**. Only step 8 needs them, and step 8 introduces them on
camera as independent witnesses. Verifying them at the top would teach the viewer
they are prerequisites — the opposite of true. Steps 1–7 run on a bare Python
3.8+ with nothing installed. That ordering mirrors the artifact itself, where
`tool/` is standard-library only and `validation-harness/` is separate.

## VS Code settings — step 7 is dead without the first one

```json
"diffEditor.ignoreTrimWhitespace": false,
"editor.renderWhitespace": "all",
"diffEditor.renderSideBySide": true
```

`ignoreTrimWhitespace` defaults to **true**, which hides the trailing-space
repair on line 1 — your `Feature:` line would appear unchanged. Reload the
window after editing (`Ctrl+Shift+P` → *Developer: Reload Window*).

## Terminal

~150 columns. Reset between takes:

    cd /home/vaishnavkoka/RE4BDD/UnifiedBDDLinter-Artifact && rm -rf demo

Only steps 1 and 8 take real time — the clone, and ~30 seconds with a progress
bar. Everything else is instant, so don't script pauses that aren't there.

---

# Opening

> "Hi. I'm going to show you **UnifiedBDDLinter** — a tool that checks BDD feature
> files for quality problems and automatically repairs the safe ones, without ever
> changing what a test means.
>
> Everything I'm about to run uses nothing but Python's standard library. No
> install, no dependencies. Let me show you on a real public codebase."

---

# Step 1 — Clone

```bash
rm -rf demo && mkdir -p demo && cd demo
git clone --depth 1 https://github.com/bcgov/onRouteBCSpecification.git
find onRouteBCSpecification -name '*.feature' | wc -l
cd ..
```

→ **230**

> "This is the British Columbia government's onRouteBC specification — a real
> commercial vehicle permitting system, and its requirements are written in
> Gherkin. Let me count the feature files.
>
> 230 files. Let's zoom into one of them so you can see what the tool sees."

*(Upstream drifts — read whatever number appears rather than committing to one.)*

```bash
F="demo/onRouteBCSpecification/Applying for Permits/Single Trip Oversize/Staff Apply For STOS.feature"
```

⚠ The path contains spaces. Keep the quotes on every use of `$F`.

---

# Step 2 — Look at the file first

```bash
head -5 "$F"
```

→ `Feature: staff apply for single trip oversize (STOS) permit`

> "Here's the feature name — *staff apply for single trip oversize permit*. Keep
> an eye on that; it comes back twice."

---

# Step 3 — Lint: the subset the oracles corroborate

```bash
python3 tool/cli.py "$F"
```

→ `Summary: 1 file, 11 violation(s), 9 error(s)`

> "That's our first entry point, and it finds **11 issues** — indentation, trailing
> whitespace, a filename that doesn't follow convention. This is the subset that
> established linters also check, so it's the fair comparison set.
>
> But our tool goes further."

---

# Step 4 — Lint: the full 28-rule engine  ← your thesis

```bash
python3 tool/linter.py "$F"
```

→ `Summary: 1 file, 13 violation(s), 10 error(s)`

The two additions:

```
[ERROR ] ST007: Feature/file name match
  Line 1: Feature name "staff apply for single trip oversize (STOS) permit"
          does not match file name "Staff Apply For STOS"

[INFO  ] Q003: Step count
  Line 20: Scenario "Permit duration accepted" has 2 step(s) (ideal 3-7)
```

> "**13 now.** The same 11, plus two more, and these two are the point of the whole
> tool.
>
> The first is a structure rule: the feature is *named* one thing and the *file* is
> called another. Notice it doesn't just complain — it tells us the exact filename
> it wants.
>
> The second is from our quality family: this scenario has only two steps, where a
> readable scenario usually has three to seven. That's a **business readability**
> check — is this specification actually clear to the person who has to read it?
> No existing Gherkin linter asks that question. That's what we added."

*(Point at the filename `ST007` asks for. It reappears in step 5.)*

---

# Step 5 — Repair

```bash
python3 tool/auto_fix.py "$F" -o demo/fixed
ls demo/fixed/
```

→ `staff_apply_for_single_trip_oversize_stos_permit.feature`
→ `Changes: 39 line(s) modified`
→ `originals unchanged: demo/onRouteBCSpecification/.../Staff Apply For STOS.feature`

> "Now the fixer. **39 lines changed**, and look at the filename — exactly the one
> the tool told us it wanted a moment ago. It derived that from the feature text.
> It didn't invent a name.
>
> And notice this line: *originals unchanged*. The repair went to a separate
> folder. Your files are never touched."

---

# Step 6 — Re-lint: the safe-fix boundary

```bash
python3 tool/linter.py demo/fixed/staff_apply_for_single_trip_oversize_stos_permit.feature
```

→ `Summary: 1 file, 1 violation(s), 0 error(s)`

> "Re-lint the repaired file, and we've gone from **13 issues to 1**. Every
> formatting problem is gone.
>
> What's left is that two-step scenario. And the tool *deliberately* leaves it
> alone — it can't know whether two steps is wrong here. Only the author does. Add
> a step and you might change what the test asserts. That line, between what's safe
> to fix mechanically and what needs a human, is what we designed the entire tool
> around."

---

# Step 7 — Side by side, then proof

```bash
code --diff "$F" demo/fixed/staff_apply_for_single_trip_oversize_stos_permit.feature
```

> "Let me put them next to each other. On the left the original, on the right the
> repair.
>
> You can see the indentation was drifting — four spaces, then five, then six —
> and it's now consistently two. There's a trailing space on the first line that's
> gone. And the feature text itself is character for character identical.
>
> But 'looks the same' isn't proof, so let's actually check."

*(Point at: line 1 trailing space · lines 9–12 indentation `4,5,5,6` → `2` ·
the `Feature:` text unchanged.)*

```bash
python3 misc/demo/side_by_side.py "$F" \
    demo/fixed/staff_apply_for_single_trip_oversize_stos_permit.feature | tail -4
```

```
lines changed   39
words before    230
words after     230
every word preserved -- the repair changed layout only
```

> "It pulls every word out of both files and compares them. **230 words before, 230
> words after, every one preserved.** 39 lines changed, and not a single word.
> That's the guarantee, verified rather than asserted."

*(Exit code 1 if any word differs — it's a check, not a claim.)*

---

# Step 8 — The whole repository, with independent witnesses

> "That was one file. Let's do all 230.
>
> And here's a fair question: everything you've seen so far is our tool marking its
> own homework. So for this part I'm bringing in two independent linters —
> **gherkin-lint**, which is the Node.js standard, and **cuke_linter**, from the
> Ruby Cucumber ecosystem."

```bash
npm ls -g gherkin-lint        # gherkin-lint@4.2.4  -- Node.js
cuke_linter --version         # 1.4.0               -- Ruby
```

> "Neither of these is needed to *run* our tool — everything so far ran without
> them. They're here purely as external referees for this measurement.
>
> The pipeline lints every file with all three, repairs them with our fixer, then
> lints all three again and compares."

```bash
python3 validation-harness/phase3_bdd_pipeline_full.py \
    -r demo/onRouteBCSpecification -o demo/out -w 8 --open
```

> "While that runs — it's processing all 230 files in parallel… and done."

⚠ Watch for `repository onRouteBCSpecification (single repository, not a corpus
tree)`. If it says **168 repositories**, something regressed.

`--open` puts the before/after chart on screen — that figure only, so the answer
appears rather than whichever file sorted last. `--open-all` shows the rest too,
with before/after still opened last so it ends up in front.

| linter | before | after | change |
|---|--:|--:|--:|
| **UnifiedBDDLinter** | **12,895** | **758** | **−94.1%** |
| cuke_linter | 598 | 413 | −30.9% |
| gherkin-lint | 1,830 | 1,495 | −18.3% |

> "Orange is before the repair, green is after.
>
> Our own count drops from **12,895 to 758** — a **94% reduction**. gherkin-lint's
> count falls too, and cuke_linter's falls by **31%**. So all three linters agree
> the files got better. That's the part that matters: this isn't only our tool's
> opinion of its own work.
>
> 229 of the 230 files improved, and **not one file got worse**."

---

# Close

> "So: UnifiedBDDLinter checks feature files across style, structure, workflow and
> business readability. It repairs what can be repaired mechanically, refuses to
> touch anything else, and it never changes a single word of a specification.
>
> We ran this across **20,270 feature files from 38 public repositories**, with
> zero regressions anywhere.
>
> Thanks for watching."

---

# Reference card

| beat | number |
|---|---|
| files in the repo | 230 |
| `cli.py` | 11 violations, 9 errors |
| `linter.py` | 13 violations, 10 errors |
| after repair | 1 violation, 0 errors |
| lines changed | 39 |
| words before → after | 230 → 230 |
| repo, ours | 12,895 → 758 (−94.1%) |
| repo, cuke_linter | 598 → 413 (−30.9%) |
| repo, gherkin-lint | 1,830 → 1,495 (−18.3%) |
| files improved | 229 of 230, zero regressions |
| corpus | 20,270 files, 38 repositories |

## Do not say

**"We detect more issues than gherkin-lint."** True here, but 81% of our count is
indentation and someone will ask. Claim *coverage* — four families, one of which
nobody else has — which the 11→13 moment already proved on camera.

**Don't show `summary.csv`.** Its `aggregate_reduction_pct` is computed against
gherkin-lint and reads **18.31** on a single-repository run. It will look like it
contradicts the 94% you just said. Show `fig2_before_after.png`.

## If asked

**"Isn't most of that just indentation?"** Yes — `S004` is 81% of our findings
here, and it's the top rule in 37 of the 38 corpus repositories, median share
80%. That's what's actually wrong with Gherkin in the wild. This repository is
typical, not cherry-picked.

**"You detect more but reduce less overall."** Corpus-wide, gherkin-lint resolves
86.2% to our 79.5%. Per repository the picture reverses: our median is 88.2%
against their 87.4%, and we beat them in 22 of 35 repositories. The aggregate is
carried by a few very large repositories. Both numbers are true; report both.

**"Doesn't renaming break step definitions?"** No. Cucumber binds step
definitions on **step text**, never on filenames. We verified this on a real
Maven project — `mvn test` passes before and after the rename. The inverse *is*
true and it's why `W005` was withdrawn: removing a step's trailing period changes
the string the runtime matches on, and that turned BUILD SUCCESS into BUILD
FAILURE with the step reported UNDEFINED.

## Why this repository

Chosen from all 38 corpus repositories by margin over gherkin-lint: 94.1% against
18.3%, a **73-point gap**. Nothing else in the corpus comes within 30 points. See
`candidates/_contact_sheet.png` for the comparison that settled it.
