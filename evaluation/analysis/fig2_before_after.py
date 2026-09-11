#!/usr/bin/env python3
"""
fig2_before_after.py -- total violations before and after fixing, per linter.

    python3 analysis/fig2_before_after.py <results.csv> <outdir>

WHAT THE FIGURE HAS TO SHOW, AND WHY IT IS SHAPED THIS WAY
----------------------------------------------------------
Three linters, each measured twice. The reader's question is "did the fixer
work, and does an independent tool agree?", so the comparison that must be easy
is before-vs-after WITHIN a linter -- which is why before/after are the two
colour series and the linter is the axis, rather than the reverse.

The three totals are NEVER summed. Adding them would double-count every defect
the tools agree on and present the sum as a corpus total, which it is not. Each
bar is one tool's own opinion, reported separately.

Every bar is directly labelled. Partly because six bars can each carry a number
without clutter, and partly because it is required: one palette slot falls below
the 3:1 contrast bar, and the rule for that case is that colour may not be the
only way to read a value.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter

import style
from aggregate import LINTERS, corpus_totals, file_outcomes, load


def draw(results_csv: Path, outdir: Path) -> Path:
    rows = load(results_csv)
    totals = corpus_totals(rows)
    outcomes = file_outcomes(rows)
    style.apply()

    labels = [label for label, _b, _a in LINTERS]
    # "BDD-lint" is what the reference figures call our own linter. Kept so the
    # two are directly comparable side by side.
    display = ["gherkin-lint", "cuke_linter", "BDD-lint"]
    before = [totals[l]["before"] for l in labels]
    after = [totals[l]["after"] for l in labels]

    x = np.arange(len(labels))
    width = 0.38

    fig, ax = plt.subplots(figsize=(10.5, 6.5))
    bars_before = ax.bar(x - width/2, before, width, label="Before",
                         color=style.ORANGE, zorder=3)
    bars_after = ax.bar(x + width/2, after, width, label="After",
                        color=style.GREEN, zorder=3)

    # Every bar carries its value: at these magnitudes the axis alone cannot be
    # read to the precision a reader wants, and a figure that has to be
    # estimated from gridlines is not evidence.
    for bars in (bars_before, bars_after):
        for bar in bars:
            ax.annotate(style.human(bar.get_height()),
                        (bar.get_x() + bar.get_width()/2, bar.get_height()),
                        textcoords="offset points", xytext=(0, 6),
                        ha="center", fontsize=12, fontweight="bold",
                        color=style.INK)

    # The reduction, in the "after" colour, placed above the bar it describes.
    # Signed as a change in violations: negative means fewer, which is the
    # direction the bar itself shows.
    for index, label in enumerate(labels):
        pct = totals[label]["reduction_pct"]
        ax.annotate(f"{-pct:+.1f}%",
                    (index + width/2, after[index]),
                    textcoords="offset points", xytext=(0, 34),
                    ha="center", fontsize=15, fontweight="bold",
                    color=style.GREEN if pct >= 0 else "#c0392b")

    ax.set_xticks(x)
    ax.set_xticklabels(display, fontsize=13)
    ax.set_ylabel("Total violations")
    ax.set_ylim(0, max(max(before), max(after)) * 1.22)
    ax.yaxis.set_major_formatter(FuncFormatter(style.thousands))
    ax.yaxis.grid(True, linestyle="--", alpha=0.85)
    ax.xaxis.grid(False)
    ax.legend(loc="upper right", fontsize=13, edgecolor=style.GRID)
    style.boxed(ax)

    excluded = max(totals[l]["excluded"] for l in labels)
    mode = "corpus" if len(rows) > 10000 else "sample"
    ax.set_title(f"Before vs After Auto-Fix \u2014 {mode}  "
                 f"({len(rows):,} .feature files)", pad=16)

    # The footer carries the denominators and the failure counts -- a reduction
    # percentage without them is not a measurement.
    #
    # It carries NOTHING ELSE. A line reading "each linter reports
    # independently; totals are never pooled" used to sit below it and was
    # removed: the paper's caption already says what the figure shows, and two
    # texts saying the same thing leave a reader reconciling them -- with the
    # one baked into the image being the one they cannot edit.
    footer = (f"{len(rows):,} files  ·  {outcomes['improved_pct']:.1f}% improved  ·  "
              f"{outcomes['regressed']} regressed  ·  "
              f"{outcomes['excluded']} unmeasured")
    if excluded:
        footer += f"  ·  {excluded} excluded by a linter"
    fig.text(0.5, -0.005, footer, ha="center", fontsize=11,
             color=style.INK_SOFT, style="italic")

    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / "fig2_before_after.png"
    fig.tight_layout()
    fig.savefig(target, bbox_inches="tight", dpi=150)
    plt.close(fig)
    return target


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: fig2_before_after.py <results.csv> <outdir>")
    print(draw(Path(sys.argv[1]), Path(sys.argv[2])))
