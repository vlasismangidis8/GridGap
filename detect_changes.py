"""
GridGap — turn the daily archive into events.

DEDDIE publishes only the current state and announces nothing when it changes.
Two snapshots are all it takes to see what moved, but only if someone kept
yesterday's copy — which is exactly what the archive is for.

This compares snapshots and reports:
  * FLIP    — the availability icon changed (Red/Orange/Green). The events that matter.
  * MARGIN  — a margin moved without flipping the icon (early warning).
  * NEW/GONE— a transformer appeared in or vanished from the feed.

A watchlist (one substation name per line, accents and case ignored) marks the
rows a specific developer cares about, so a portfolio holder sees their own
sites first. `data/watchlist.example.txt` shows the format.

Usage:
    python detect_changes.py                     # latest two snapshots
    python detect_changes.py --all               # the whole archive
    python detect_changes.py --since 2026-08-01  # from a date onwards
    python detect_changes.py --watchlist data/watchlist.txt
    python detect_changes.py --all --out data/changes/history.md

In GitHub Actions the report is also written to the job summary, and
`changed=true|false` is emitted so a later step can notify only when something
actually happened.
"""

import argparse
import json
import os
import sys
import unicodedata
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"

# A margin moving by less than this is rounding noise in a feed that publishes
# one decimal; below it we would cry wolf every day.
DEFAULT_MIN_DELTA = 0.1

RANK = {"Red": 0, "Orange": 1, "Green": 2}


def num(s):
    """Parse a DEDDIE numeric cell ('40,1' -> 40.1). Empty -> 0.0."""
    if s in (None, ""):
        return 0.0
    try:
        return float(str(s).replace(",", "."))
    except ValueError:
        return 0.0


def gr(x):
    """Format a number the way the feed does, with a Greek decimal comma."""
    return f"{x:.1f}".replace(".", ",")


def fold(s):
    """Accent- and case-insensitive form, so 'Λάρισα' matches 'ΛΑΡΙΣΑ'."""
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn").upper().strip()


def snapshots(since=None):
    """Archived snapshots in date order, as (date, {key: row})."""
    out = []
    for f in sorted(RAW.glob("margins_20??-??-??.json")):
        day = f.stem.replace("margins_", "")
        if since and day < since:
            continue
        rows = json.loads(f.read_text(encoding="utf-8"))
        out.append((day, {(r["PERI_Y"], r["PERI_M"]): r for r in rows}))
    return out


def load_watchlist(path):
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        sys.exit(f"watchlist not found: {p}")
    return [fold(l) for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def watched(key, watchlist):
    if not watchlist:
        return False
    hay = fold(key[0]) + " " + fold(key[1])
    return any(w in hay for w in watchlist)


def diff(prev, cur, day, min_delta, watchlist):
    """Everything that changed between two consecutive snapshots."""
    events = []
    for key, row in cur.items():
        old = prev.get(key)
        star = watched(key, watchlist)
        if old is None:
            events.append(dict(day=day, kind="NEW", key=key, star=star,
                               text=f"εμφανίστηκε στη ροή ({row['MARGIN_ICON']})"))
            continue

        o_icon, n_icon = old["MARGIN_ICON"], row["MARGIN_ICON"]
        o_th, n_th = num(old["TTHP"]), num(row["TTHP"])
        o_sc, n_sc = num(old["TPBK"]), num(row["TPBK"])

        if o_icon != n_icon:
            better = RANK.get(n_icon, 0) > RANK.get(o_icon, 0)
            bits = []
            if abs(n_th - o_th) >= 0.05:
                bits.append(f"θερμικό {gr(o_th)}→{gr(n_th)}")
            if abs(n_sc - o_sc) >= 0.05:
                bits.append(f"β/κ {gr(o_sc)}→{gr(n_sc)}")
            events.append(dict(day=day, kind="FLIP", key=key, star=star, better=better,
                               text=f"{o_icon} → {n_icon}" + (f" · {' · '.join(bits)}" if bits else "")))
        else:
            bits = []
            if abs(n_th - o_th) >= min_delta:
                bits.append(f"θερμικό {gr(o_th)}→{gr(n_th)} ({n_th - o_th:+.1f})".replace(".", ","))
            if abs(n_sc - o_sc) >= min_delta:
                bits.append(f"β/κ {gr(o_sc)}→{gr(n_sc)} ({n_sc - o_sc:+.1f})".replace(".", ","))
            if bits:
                events.append(dict(day=day, kind="MARGIN", key=key, star=star,
                                   text=" · ".join(bits)))

    for key in prev.keys() - cur.keys():
        events.append(dict(day=day, kind="GONE", key=key, star=watched(key, watchlist),
                           text="έπαψε να δημοσιεύεται"))
    return events


def render(events, days, watchlist):
    """Markdown report — the thing a person actually reads."""
    flips = [e for e in events if e["kind"] == "FLIP"]
    margins = [e for e in events if e["kind"] == "MARGIN"]
    other = [e for e in events if e["kind"] in ("NEW", "GONE")]
    starred = [e for e in events if e["star"]]

    span = f"{days[0]} → {days[-1]}" if len(days) > 1 else days[0]
    out = [f"# GridGap — μεταβολές ΔΕΔΔΗΕ", "",
           f"Διάστημα: **{span}** ({len(days)} στιγμιότυπα)", ""]

    if not events:
        out += ["Καμία μεταβολή.", ""]
        return "\n".join(out)

    out += [f"**{len(flips)}** αλλαγές κατάστασης · **{len(margins)}** μεταβολές τιμών · "
            f"**{len(other)}** προσθήκες/αποσύρσεις", ""]

    if watchlist:
        out += ["## Σημεία παρακολούθησης", ""]
        out += ([f"- `{e['day']}` **{e['key'][1]}** — {e['text']}" for e in starred]
                or ["- Καμία μεταβολή στα σημεία σας.", ""])
        out += [""]

    if flips:
        out += ["## Αλλαγές κατάστασης", ""]
        for e in flips:
            arrow = "🟢" if e.get("better") else "🔴"
            out.append(f"- `{e['day']}` {arrow} **{e['key'][1]}** ({e['key'][0]}) — {e['text']}")
        out += [""]

    if other:
        out += ["## Προσθήκες και αποσύρσεις", ""]
        out += [f"- `{e['day']}` **{e['key'][1]}** — {e['text']}" for e in other] + [""]

    if margins:
        out += ["## Μεταβολές τιμών χωρίς αλλαγή κατάστασης", ""]
        for e in margins:
            out.append(f"- `{e['day']}` {e['key'][1]} — {e['text']}")
        out += [""]

    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Detect changes between DEDDIE snapshots.")
    ap.add_argument("--all", action="store_true", help="scan the whole archive")
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="scan from this date onwards")
    ap.add_argument("--watchlist", help="file with substation names to flag")
    ap.add_argument("--min-delta", type=float, default=DEFAULT_MIN_DELTA,
                    help=f"ignore margin moves below this many MVA (default {DEFAULT_MIN_DELTA})")
    ap.add_argument("--out", help="also write the report to this file")
    args = ap.parse_args()

    snaps = snapshots(args.since)
    if len(snaps) < 2:
        sys.exit("need at least two archived snapshots to compare")
    if not (args.all or args.since):
        snaps = snaps[-2:]          # default: what changed since yesterday

    watchlist = load_watchlist(args.watchlist)
    events, days = [], [d for d, _ in snaps]
    for (_, prev), (day, cur) in zip(snaps, snaps[1:]):
        events += diff(prev, cur, day, args.min_delta, watchlist)

    report = render(events, days, watchlist)
    sys.stdout.reconfigure(encoding="utf-8")
    print(report)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"\n→ {out}", file=sys.stderr)

    # GitHub Actions: surface the report and let a later step notify on change.
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).write_text(report, encoding="utf-8")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        important = [e for e in events if e["kind"] in ("FLIP", "NEW", "GONE") or e["star"]]
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if important else 'false'}\n")
            fh.write(f"flips={sum(1 for e in events if e['kind'] == 'FLIP')}\n")


if __name__ == "__main__":
    main()
