# Hands-on walkthrough

**Run every command in this file from `tool/`.** Paths are relative, so this
works from any checkout on any machine.

> The analysis and pipeline commands in the other documents run from the
> **artifact root** (one level up), not from here. `cd ..` first for those.

`demo.feature` is deliberately bad: it has violations of **both** kinds — some
the tool can repair, some it will only report.

---

## 1. What does the tool check?

```bash
python3 bddlint.py rules
```

28 rules. The `DEFAULT` column says what the fixer does; `CLASS` says why.

## 2. Lint the demo file

```bash
python3 bddlint.py lint examples/demo.feature
```

Each line is `path:line:column  severity  RULE  message`, which editors and
terminals recognise as a jump target.

## 3. Filter what you see

```bash
python3 bddlint.py lint examples/demo.feature --severity error   # serious only
python3 bddlint.py lint examples/demo.feature --summary          # counts only
python3 bddlint.py lint examples/demo.feature --no-summary       # no counts
```

`--severity` means "this level **and above**", not "exactly this level".

The summary lists the top 10 rules and says `... and N more rules (use --format
json)`. That hint is real — see the next section.

## 4. Machine-readable output

```bash
python3 bddlint.py lint examples/demo.feature --format json
python3 bddlint.py lint examples/demo.feature --format sarif
```

JSON is key-sorted, so two runs diff cleanly. SARIF is what GitHub, GitLab and
VS Code ingest to show findings inline on a pull request.

JSON carries **every** violation, with no truncation — this is how to get the
full per-rule breakdown the text summary abbreviates:

```bash
python3 bddlint.py lint examples/demo.feature --format json \
  | python3 -c "import json,sys,collections; \
      v=[x for f in json.load(sys.stdin)['files'] for x in f['violations']]; \
      [print(f'{r:<8}{n}') for r,n in collections.Counter(x['rule'] for x in v).most_common()]"
```

Report goes to **stdout**, diagnostics to **stderr** — so
`… --format json | jq` keeps working even when your config has a problem.

## 5. The two rule sets

```bash
python3 bddlint.py lint examples/demo.feature --summary                  # all 28
python3 bddlint.py lint examples/demo.feature --default-rules --summary  # the 18
```

The 18 are the ones `gherkin-lint` and `cuke_linter` can also be asked about,
so they are what the differential evaluation measures.

## 6. Preview a fix without writing anything

```bash
python3 bddlint.py fix examples/demo.feature --dry-run
```

## 7. Actually fix it

```bash
rm -rf /tmp/demo-fixed        # start from an empty directory
python3 bddlint.py fix examples/demo.feature --output-dir /tmp/demo-fixed
diff examples/demo.feature /tmp/demo-fixed/*.feature
```

Clear the output directory first. The fixer renames a repaired file to match its
`Feature:` line, so a leftover from an earlier run survives under its own name
and the new one lands beside it with a collision suffix — after which linting
that directory reports both files. The tool warns when the directory is not
empty, but starting clean is simpler.

`--output-dir` is **required**. Without it the tool refuses (exit 2) rather than
editing a requirements artifact in place.

Notice the output filename: the file was renamed to match its `Feature:` line
(rule `ST007`). No `.feature` **content** is invented by that — only the name.

### What it repaired

trailing whitespace · indentation · repeated blank lines · a trailing period ·
the filename

### What it refused to repair, and said so

```
Reported but NOT repaired (safe-fix boundary):
  ST006  x1   duplicate scenario name -- a suffix would have to be invented
```

It also left `Q001` ("clicks" is UI detail, not business language), `Q004`
("test" in a scenario name) and `Q007` (double negation) alone. Every one would
need the tool to decide what you meant.

**That is the whole design.** Compare before and after:

```bash
python3 bddlint.py lint examples/demo.feature --summary   # before
python3 bddlint.py lint /tmp/demo-fixed --summary         # after
```

(Pass the *directory*, not `dir/*.feature` — the fixer may rename a file, and
`lint` takes one path.)

| | before | after |
|---|---:|---:|
| style | 13 | **0** |
| structure | 2 | 1 |
| workflow | 2 | 1 |
| quality | 7 | 7 |
| **total** | **24** | **9** |
| exit code | 1 | **0** |

Every style violation is gone. What remains is semantic — `Q001` UI wording,
`Q004` "test" in a name, `Q007` double negation, `Q008` near-duplicate
scenarios, `ST006` duplicate name, `W001` two `When` steps. Each needs someone
who knows what the requirement means.

Exit code drops to 0 because nothing at `error` severity is left: the file is
now CI-clean while still carrying advisory findings for a human. That is the
correct outcome, not a shortfall.

## 8. Configuration

```bash
python3 bddlint.py config                                    # what resolved, and from where
cp ../config/unified-lintrc.default.json .unified-lintrc.json
python3 bddlint.py lint examples/demo.feature --summary      # now governed by that file
rm .unified-lintrc.json
```

The file is discovered by walking **upward**, so one at a repository root
governs every subdirectory. A malformed config warns and falls back to defaults
— it never stops the linter.

## 9. Exit codes — the CI contract

```bash
python3 bddlint.py lint examples/well_formed.feature ; echo "exit $?"   # 0
python3 bddlint.py lint examples/demo.feature        ; echo "exit $?"   # 1
python3 bddlint.py lint no/such/file.feature         ; echo "exit $?"   # 2
python3 bddlint.py fix examples/demo.feature         ; echo "exit $?"   # 2, refuses in place
```

| | |
|---|---|
| `0` | clean, or nothing at/above `--fail-on` |
| `1` | violations at/above the threshold |
| `2` | usage error |
| `3` | internal error |

`1` and `3` are distinct on purpose: a CI job must be able to tell "found
problems" from "the linter is broken".

```bash
python3 bddlint.py lint examples/demo.feature --fail-on never ; echo "exit $?"   # 0
```

Report-only mode, for adopting the linter gradually.

## 10. Provenance

```bash
python3 bddlint.py version
```

Prints the md5 of the v1.0 engine this was ported from — the same hash recorded
in the published run's provenance file.
