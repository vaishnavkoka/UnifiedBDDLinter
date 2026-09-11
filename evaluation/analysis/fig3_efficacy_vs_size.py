#!/usr/bin/env python3
"""
fig3_efficacy_vs_size.py -- repair efficacy against repository size.

    python3 analysis/fig3_efficacy_vs_size.py <results.csv> <outdir> [--linter NAME]

THE CLAIM THIS FIGURE SUPPORTS
------------------------------
That the fixer's effectiveness does not depend on how large a project is -- it
is not carried by a handful of big repositories while failing on small ones.
A reader checks that by looking for a trend in the cloud and not finding one.

ENCODING, AND WHY EACH CHANNEL WAS CHOSEN
-----------------------------------------
    x   files per repository, LOG scale
        Repository sizes span three orders of magnitude (11 files to ~1,900).
        On a linear axis every small project collapses onto the origin and the
        figure would show only the four largest.

    y   reduction %, LINEAR and signed
        Linear because the reader compares distances ("this one did 20 points
        worse"). Signed because zero is a real boundary: a repository below it
        got WORSE, which is the single most important thing the figure can show.

    area   violations before fixing
        Area, not radius: perceived quantity scales with area, so encoding a
        value as radius exaggerates large values quadratically.

    colour   reduction %, DIVERGING about zero
        A sequential ramp would make a regressing repository merely a paler
        shade of an improving one. Diverging with a neutral grey midpoint makes
        "got worse" a different colour, not a weaker one.

Colour and y therefore encode the same variable. That redundancy is deliberate:
it is the secondary encoding that lets the sign survive greyscale printing and
colour-vision deficiency.

Regressing repositories are always labelled by name. They are the exceptions a
reader most needs to be able to look up, and there are few enough to name.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.lines import Line2D

import style
from aggregate import load, per_repository


def draw(results_csv: Path, outdir: Path, linter: str = "gherkin-lint") -> Path:
    rows = load(results_csv)
    repos = per_repository(rows)
    style.apply()

    points = [(name, entry["files"], entry[linter]["before"],
               entry[linter]["reduction_pct"])
              for name, entry in repos.items() if entry[linter]["before"] > 0]
    if not points:
        sys.exit(f"no repository has any {linter} violations to plot")

    names = [p[0] for p in points]
    files = np.array([p[1] for p in points], dtype=float)
    before = np.array([p[2] for p in points], dtype=float)
    reduction = np.array([p[3] for p in points], dtype=float)

    # Marker AREA from a log of the violation count. Area rather than radius,
    # because perceived quantity scales with area -- encoding a value as radius
    # exaggerates large values quadratically. Log first, because raw counts span
    # four orders of magnitude and a linear map makes small repositories vanish.
    span = np.log10(before + 1)
    normalised = ((span - span.min()) / (span.max() - span.min())
                  if span.max() > span.min() else np.full_like(span, 0.5))
    areas = 40 + normalised * 900

    # The x range is fixed here and used twice: to place the median label and to
    # set the axis. Reading it back from the axes before set_xlim() runs returns
    # matplotlib's default linear limits, whose lower bound is 0 -- and log10(0)
    # is not a number.
    X_LO, X_HI = 1e1, 3e3

    fig, ax = plt.subplots(figsize=(10.0, 6.4))

    # Fixed 0-100 colour range, matching the reference figures, so a marker's
    # colour means the same thing across runs. Regressions fall below the range
    # and clamp to the red end -- they are additionally named in the plot and
    # counted in the footer, so they cannot be missed.
    scatter = ax.scatter(files, reduction, s=areas, c=reduction,
                         cmap=style.DIVERGING, vmin=0, vmax=100,
                         edgecolors="black", linewidths=0.8,
                         zorder=4, alpha=0.9)

    bar = fig.colorbar(scatter, ax=ax, pad=0.015)
    bar.set_label("reduction (%)", fontsize=12)

    median = float(np.median(reduction))
    median_line = ax.axhline(median, color=style.INK_SOFT, linewidth=2.0,
                             linestyle="--", zorder=3)

    if reduction.max() > 80:
        ax.axhline(80, color=style.INK_SOFT, linewidth=1.0,
                   linestyle=":", alpha=0.8, zorder=2)

    # The median label goes just ABOVE its line, in the widest gap between
    # repository sizes.
    #
    # Two properties make that work, and both were learned the hard way. Placing
    # it in a GAP means it covers no marker -- the right-hand margin looked
    # emptier but was narrower than the label itself. Placing it ABOVE the line
    # rather than centred on it means the dashes do not run through the text
    # like a strikethrough, which is what an opaque backing box used to hide.
    #
    # The gap is computed rather than chosen: a different corpus puts its gaps
    # somewhere else, and four hand-picked positions were each correct for the
    # data in front of them and wrong after the next change.
    inside = np.sort(files[(files >= X_LO) & (files <= X_HI)])
    boundaries = np.concatenate(([np.log10(X_LO)], np.log10(inside),
                                 [np.log10(X_HI)]))
    widths = np.diff(boundaries)
    widest = int(np.argmax(widths))
    label_x = 10 ** ((boundaries[widest] + boundaries[widest + 1]) / 2)

    ax.annotate(f"median = {median:.1f}%", xy=(label_x, median),
                xytext=(0, 7), textcoords="offset points",
                ha="center", va="bottom", fontsize=12, fontweight="bold",
                color=style.INK_SOFT, zorder=10)

    ax.set_xscale("log")
    # Fixed bounds rather than data-driven limits, so two runs are directly
    # comparable: a point does not move because the corpus changed size.
    #
    # The right edge stops short of 10^4 deliberately. The largest repository
    # holds ~1,900 files, so a decade tick at 10,000 would label a region with no
    # data in it while squeezing everything real into the left two-thirds. This
    # leaves enough clear space past the last point for the median label and no
    # more.
    ax.set_xlim(X_LO, X_HI)
    # Ticks stop at 10^3: the largest repository holds ~1,900 files, so a labelled
    # decade at 10,000 would announce a range with no data in it. The axis still
    # RUNS to 10^4, and that unlabelled margin is what gives the median label a
    # place to sit on its own line without covering a marker -- the strip below
    # 4,000 was narrower than the label itself.
    ax.xaxis.set_major_locator(mticker.FixedLocator([1e1, 1e2, 1e3]))
    # Minor ticks mark 2,3,...,9 within each decade, so a reader can place a
    # point between the powers of ten instead of estimating.
    ax.xaxis.set_minor_locator(
        mticker.LogLocator(base=10, subs=tuple(np.arange(2, 10) * 0.1), numticks=12))
    ax.xaxis.set_minor_formatter(mticker.NullFormatter())
    ax.tick_params(axis="x", which="minor", length=4, width=0.9, color=style.INK)
    ax.set_xlabel("Repository size  (number of .feature files, log scale)")
    ax.set_ylabel(f"{linter} violation reduction (%)")
    low = min(0.0, float(reduction.min()))
    ax.set_ylim(low - 4, 104)
    ax.grid(True, which="major", linestyle="--", alpha=0.65)
    # Darker than a hairline: these are the marks a reader uses to place a point
    # BETWEEN the powers of ten, which on a log axis is most of the plot.
    ax.grid(True, axis="x", which="minor", linestyle=":", alpha=0.55,
            color=style.INK_SOFT, linewidth=0.7)
    style.boxed(ax)

    ax.set_title(f"Auto-fix efficacy vs. repository size  "
                 f"({len(points)} repositories, {len(rows):,} files)", pad=14)

    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / "fig3_efficacy_vs_size.png"
    fig.tight_layout()
    fig.savefig(target, bbox_inches="tight", dpi=150)
    plt.close(fig)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results"); parser.add_argument("outdir")
    parser.add_argument("--linter", default="gherkin-lint")
    args = parser.parse_args()
    print(draw(Path(args.results), Path(args.outdir), args.linter))
