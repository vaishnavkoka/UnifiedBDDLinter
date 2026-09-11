# analysis/

Turns one run's `results.csv` into the paper's figures and tables.

| script | produces | paper |
|---|---|---|
| `aggregate.py` | the shared arithmetic; run alone for a text summary | — |
| `fig2_before_after.py` | violations before/after, per linter | **Figure 2** |
| `fig3_efficacy_vs_size.py` | efficacy vs repository size | **Figure 3** |
| `table1_repositories.py` | per-repository results, as CSV | **Table 1** |
| `make_all.py` | all of the above, plus `SUMMARY.md` | — |

```bash
python3 evaluation/analysis/make_all.py <run-dir>
python3 evaluation/analysis/aggregate.py <run-dir>/results.csv   # numbers only
```

Every script takes `<results.csv> <outdir>` and reads nothing but that CSV, so
any figure can be regenerated on its own while you iterate on it.

## Two rules the arithmetic follows

**A count of −1 is "not measured", not zero.** A linter that crashed, timed out
or was not installed did not find the file clean. Those rows are excluded from
that linter's totals and the exclusion count is reported beside every
percentage — a percentage whose denominator is unstated is not a measurement.

**The three linters are never summed.** Adding them would double-count every
defect they agree on. Each is one tool's independent opinion.

## Colour

The palette is validated, not chosen by eye: all-pairs colour-vision-deficiency
separation ΔE 9.2 (target ≥ 8) and normal-vision ΔE 24.0 (floor ≥ 15). One slot
falls below 3:1 contrast against the surface, so anything drawn in it also
carries a visible label — colour is never the only way to read a value.

Signed quantities use a diverging ramp with a neutral grey midpoint at zero, so
a repository that got *worse* is a different colour rather than a paler shade of
better.
