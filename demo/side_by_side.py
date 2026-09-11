"""Side-by-side before/after view of one repaired feature file.

Built for demonstrating the fixer, where an ordinary diff is a poor witness:
almost every change is whitespace, which a normal diff renders as two lines that
look identical. Here trailing spaces and indentation are drawn explicitly, so a
viewer can see what moved.

    python3 misc/demo/side_by_side.py <original.feature> <repaired.feature>

It also answers the question the demo actually rests on -- did the repair change
any words? -- by comparing the two files' non-whitespace tokens and reporting the
result rather than asserting it.
"""

import argparse
import difflib
import shutil
import sys
from pathlib import Path

# ColorBrewer Dark2, the same pair the figures use: orange for "before", green
# for "after". Bright variants keep them legible on a dark terminal.
ORANGE = "\033[38;5;166m"
GREEN = "\033[38;5;29m"
DIM = "\033[38;5;244m"
BOLD = "\033[1m"
RESET = "\033[0m"
INVERT_WS = "\033[48;5;217m"      # background for trailing whitespace


def visible(line, mark_trailing=True):
    """Render a line with its invisible characters made visible.

    Trailing whitespace is the single most common repair and the one a viewer
    can never see. Tabs are widened to match how the linter counts columns.
    """
    line = line.replace("\t", "→   ")
    if mark_trailing:
        stripped = line.rstrip()
        trailing = line[len(stripped):]
        if trailing:
            return stripped + INVERT_WS + "·" * len(trailing) + RESET
    return line


def clip(text, width):
    """Truncate to *width* visible columns, ignoring ANSI escapes."""
    out, shown, i = [], 0, 0
    while i < len(text) and shown < width:
        if text[i] == "\033":
            j = text.find("m", i)
            if j == -1:
                break
            out.append(text[i:j + 1])
            i = j + 1
            continue
        out.append(text[i])
        shown += 1
        i += 1
    if i < len(text):
        out.append("…")
    # No RESET here: every caller wraps the result in its own colour span and
    # closes it. Adding one as well produced doubled escape sequences.
    return "".join(out)


def render(before_path, after_path, width=None, context=None, max_run=None):
    before = Path(before_path).read_text(encoding="utf-8", errors="replace").splitlines()
    after = Path(after_path).read_text(encoding="utf-8", errors="replace").splitlines()

    width = width or shutil.get_terminal_size((160, 24)).columns
    col = max(28, (width - 14) // 2)

    left_title = Path(before_path).name
    right_title = Path(after_path).name
    print()
    print(f"  {ORANGE}{BOLD}{clip(left_title, col)}{RESET}"
          f"{' ' * max(1, col - len(left_title) + 4)}"
          f"{GREEN}{BOLD}{clip(right_title, col)}{RESET}")
    print("  " + DIM + "─" * min(width - 4, col * 2 + 10) + RESET)

    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    changed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            rows = list(range(i1, i2))
            # Long runs of untouched lines are noise; keep the edges so the
            # reader still sees where they are in the file.
            if context is not None and len(rows) > context * 2:
                rows = rows[:context] + [None] + rows[-context:]
            for r in rows:
                if r is None:
                    print(f"  {DIM}{'':>4} {'⋮':<{col}}  {'':>4} ⋮{RESET}")
                    continue
                text = clip(visible(before[r], mark_trailing=False), col)
                offset = r - i1 + j1
                print(f"  {DIM}{r+1:>4} {text:<{col}}{RESET}"
                      f"  {DIM}{offset+1:>4} {clip(visible(after[offset], False), col)}{RESET}")
        else:
            left = list(range(i1, i2))
            right = list(range(j1, j2))
            span = max(len(left), len(right))
            keep = set(range(span))
            if max_run is not None and span > max_run * 2:
                keep = set(range(max_run)) | set(range(span - max_run, span))
            elided = False
            for k in range(span):
                if k not in keep:
                    if not elided:
                        note = f"⋮ {span - max_run * 2} more changed lines"
                        print(f"  {DIM}{'':>4} {note:<{col}}{RESET}"
                              f"  {DIM}{'':>4} ⋮{RESET}")
                        elided = True
                    changed += 1
                    continue
                changed += 1
                if k < len(left):
                    lno, ltxt = left[k] + 1, clip(visible(before[left[k]]), col)
                    lcell = f"{ORANGE}{lno:>4} {ltxt}{RESET}"
                    pad = col - _visible_len(visible(before[left[k]]))
                else:
                    lcell, pad = f"{DIM}{'':>4} {'':<{col}}{RESET}", 0
                if k < len(right):
                    rno, rtxt = right[k] + 1, clip(visible(after[right[k]]), col)
                    rcell = f"{GREEN}{rno:>4} {rtxt}{RESET}"
                else:
                    rcell = f"{DIM}{'':>4} (removed){RESET}"
                print(f"  {lcell}{' ' * max(1, pad)}  {rcell}")

    print("  " + DIM + "─" * min(width - 4, col * 2 + 10) + RESET)
    return before, after, changed


def _visible_len(text):
    """Length of *text* ignoring ANSI escape sequences."""
    out, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            j = text.find("m", i)
            if j == -1:
                break
            i = j + 1
            continue
        out += 1
        i += 1
    return out


def words_of(lines):
    """Every non-whitespace token, in order. Whitespace-only edits vanish here."""
    return [token for line in lines for token in line.split()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--width", type=int, default=None,
                        help="total columns (default: terminal width)")
    parser.add_argument("--max-run", type=int, default=6,
                        help="show at most this many changed lines at each end "
                             "of a long changed block (default 6; -1 for all)")
    parser.add_argument("--context", type=int, default=3,
                        help="unchanged lines to keep around each edit "
                             "(default 3; use 0 for none, -1 for the whole file)")
    args = parser.parse_args()

    context = None if args.context < 0 else args.context
    args.max_run = None if args.max_run < 0 else args.max_run
    before, after, changed = render(args.before, args.after, args.width,
                                   context, args.max_run)

    wb, wa = words_of(before), words_of(after)
    print(f"  lines changed   {changed}")
    print(f"  words before    {len(wb):,}")
    print(f"  words after     {len(wa):,}")
    if wb == wa:
        print(f"  {GREEN}{BOLD}every word preserved -- the repair changed layout only{RESET}")
        return 0
    # Say exactly what moved rather than only that something did.
    diff = [w for w in difflib.ndiff(wb, wa) if w[0] in "+-"]
    print(f"  {ORANGE}{BOLD}{len(diff)} word-level change(s):{RESET}")
    for entry in diff[:10]:
        print(f"    {entry}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
