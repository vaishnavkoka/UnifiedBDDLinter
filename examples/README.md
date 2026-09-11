# Examples

Three files that demonstrate the distinction the tool is built around.

| file | what it shows |
|---|---|
| `well_formed.feature` | passes all 28 rules — exit code 0 |
| `form_defects.feature` | defects that **can** be repaired from the file itself |
| `semantic_defects.feature` | defects that **cannot** — reported, never rewritten |

## Try it

```bash
# 1. Clean file: nothing to say.
python3 ../tools/bddlint.py lint well_formed.feature

# 2. Form defects: see them, then repair them.
python3 ../tools/bddlint.py lint form_defects.feature
python3 ../tools/bddlint.py fix  form_defects.feature --output-dir /tmp/fixed
diff form_defects.feature /tmp/fixed/*.feature

# 3. Semantic defects: reported, and deliberately NOT repaired.
python3 ../tools/bddlint.py lint semantic_defects.feature
python3 ../tools/bddlint.py fix  semantic_defects.feature --output-dir /tmp/fixed2
diff semantic_defects.feature /tmp/fixed2/semantic_defects.feature
```

Step 3 is the point. The linter reports an unnamed Feature and two unnamed
Scenarios; the fixer changes **only** whitespace and leaves every word alone,
then tells you what it withheld and why. Only you know what those scenarios
were meant to assert.

To see what v1.0 did instead — and what this tool refuses to do by default:

```bash
python3 ../tools/bddlint.py fix semantic_defects.feature --output-dir /tmp/legacy --allow-text-injection
cat /tmp/legacy/*.feature
```

That writes `Feature: Auto-generated Feature` and `Scenario: Scenario 1` into
your specification.
