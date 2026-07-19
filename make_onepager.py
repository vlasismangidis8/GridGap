"""
GridGap — one-page PDF (GridGap_Greece.pdf), English, for BD / permitting teams.

A clean A4: the thesis, the funnel from 229 substations to the buildable
shortlist, a map of Greece, the top buildable opportunities, and honest sources.
"""

import json
import sys
import textwrap
import unicodedata
from datetime import date
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
GREEN, ORANGE, RED = "#2e9e5b", "#e8912d", "#d23b3b"
LAND, SAT_CITABLE, SAT_INDICATIVE = "#f7f4ee", "#c0392b", "#edb0a4"
INK, MUT = "#1c2733", "#5c6b7a"


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower().strip()


def pref_stem(name):
    n = strip_accents(name)
    for pfx in ("περιφερειακη ενοτητα ", "μητροπολιτικη ενοτητα ", "νομος "):
        if n.startswith(pfx):
            n = n[len(pfx):]
    n = n.strip()
    for suf in ("ιας", "ιου", "ων", "ας", "ης", "ος", "ου", "α", "ο", "ς", "η", "υ"):
        if n.endswith(suf) and len(n) - len(suf) >= 4:
            return n[: -len(suf)]
    return n


def is_urban(p):
    pr = p.get("prefecture") or ""
    return any(u in pr for u in ("Τομέα Αθηνών", "Πειραιώς",
                                 "Μητροπολιτική Ενότητα Θεσσαλονίκης"))


def main():
    fc = json.loads((ROOT / "data" / "substations.geojson").read_text(encoding="utf-8"))
    sat = json.loads((ROOT / "data" / "saturation_zones.json").read_text(encoding="utf-8"))
    prefs = gpd.read_file(RAW / "greece_prefectures.geojson")

    stem_conf = {}
    for z in sat["zones"]:
        for p in z.get("prefectures", []):
            stem_conf[pref_stem(p)] = z["confidence"]
    prefs["conf"] = prefs["name"].map(lambda n: stem_conf.get(pref_stem(n)))

    def mmv(p):
        return p["min_margin_mva"] or 0
    P = [f["properties"] for f in fc["features"]]
    feats = [f for f in fc["features"] if f["geometry"]]
    greens = [p for p in P if p["deddie_icons"] == ["Green"]]
    traps = [p for p in greens if p["admie_zone"]]
    urban = [p for p in greens if not p["admie_zone"] and is_urban(p)]
    build = [p for p in greens if not p["admie_zone"] and not is_urban(p)]
    build_gentle = [p for p in build if p.get("terrain") == "gentle"]
    n_high = sum(1 for p in traps if p["admie_confidence"] == "high")
    phantom = round(sum(mmv(p) for p in traps))
    top_build = sorted(build, key=lambda p: (p.get("terrain") != "gentle", -mmv(p)))[:12]

    plt.rcParams["font.family"] = "DejaVu Sans"
    fig = plt.figure(figsize=(8.27, 11.69))
    fig.subplots_adjust(left=0.055, right=0.945, top=0.965, bottom=0.03)
    gs = fig.add_gridspec(4, 1, height_ratios=[1.35, 2.95, 1.75, 0.62], hspace=0.16)

    # ---------- header + funnel ----------
    ax = fig.add_subplot(gs[0]); ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.text(0, 0.99, "GridGap Greece", fontsize=27, fontweight="bold", va="top", color=INK)
    ax.text(0, 0.75, "Where a renewable or BESS project can actually connect, measured in MW.",
            fontsize=11.5, va="top", color=MUT)
    ax.text(0, 0.56,
            f"{len(traps)} substations look green on DEDDIE WebAPE but sit in an ADMIE-saturated zone.\n"
            f"{n_high} of them in a zone with a formal ΦΕΚ decision.",
            fontsize=11.5, va="top", color=SAT_CITABLE, fontweight="bold", linespacing=1.35)
    funnel = [(f"{len(greens)}", "green on WebAPE", INK),
              (f"{len(traps)}", f"grid-blocked traps\n({phantom:,} MVA phantom)", SAT_CITABLE),
              (f"{len(urban)}", "in city cores\n(no land)", MUT),
              (f"{len(build)}", f"buildable shortlist\n({len(build_gentle)} on gentle terrain)", GREEN)]
    for i, (n, lab, col) in enumerate(funnel):
        x = 0.005 + i * 0.252
        ax.text(x, 0.12, n, fontsize=23, fontweight="bold", color=col, va="center")
        ax.text(x + 0.088, 0.12, lab, fontsize=8.3, color=MUT, va="center")

    # ---------- map ----------
    axm = fig.add_subplot(gs[1])
    prefs.boundary.plot(ax=axm, color="#cfcfcf", linewidth=0.4)
    prefs.plot(ax=axm, color=LAND, edgecolor="none", zorder=0)
    for conf, sub in prefs.dropna(subset=["conf"]).groupby("conf"):
        c = SAT_CITABLE if conf == "high" else SAT_INDICATIVE
        sub.plot(ax=axm, facecolor=c, edgecolor="none", alpha=0.55, zorder=1)
    for f in feats:
        lon, lat = f["geometry"]["coordinates"]
        icons = f["properties"]["deddie_icons"]
        c = GREEN if icons == ["Green"] else (RED if "Red" in icons else ORANGE)
        axm.plot(lon, lat, "o", ms=3.1, mfc=c, mec="none", zorder=3)
    for f in feats:
        p = f["properties"]
        if p["hidden_red"]:
            lon, lat = f["geometry"]["coordinates"]
            hi = p["admie_confidence"] == "high"
            axm.plot(lon, lat, "o", ms=8.5, mfc="none",
                     mec="#7b241c" if hi else "#b03a2e", mew=1.5, zorder=4)
    axm.set_xlim(19.0, 29.8); axm.set_ylim(34.6, 41.9)
    axm.set_aspect(1.24); axm.axis("off")
    axm.legend(handles=[
        Line2D([], [], marker="o", ls="", mfc=GREEN, mec="none", label="WebAPE green"),
        Line2D([], [], marker="o", ls="", mfc=ORANGE, mec="none", label="orange"),
        Line2D([], [], marker="o", ls="", mfc=RED, mec="none", label="red"),
        Line2D([], [], marker="o", ls="", mfc="none", mec="#7b241c", mew=1.5,
               label="green but grid-blocked"),
        Patch(facecolor=SAT_CITABLE, alpha=0.55, label="saturated (ΦΕΚ)"),
        Patch(facecolor=SAT_INDICATIVE, alpha=0.55, label="saturated (indicative)"),
    ], loc="upper left", fontsize=8, frameon=True, framealpha=0.92, borderpad=0.7)

    # ---------- table ----------
    axt = fig.add_subplot(gs[2]); axt.axis("off"); axt.set_xlim(0, 1); axt.set_ylim(0, 1)
    axt.text(0, 0.98, "Buildable shortlist: green on WebAPE, grid clear, with land",
             fontsize=12, fontweight="bold", va="top", color=INK)
    axt.text(0, 0.87, "The genuine targets, ranked by flat terrain then headroom.",
             fontsize=9, va="top", color=MUT)
    rows = [["Substation", "Prefecture", "MVA", "Terrain"]]
    for p in top_build:
        pref = (p["prefecture"] or "").replace("Περιφερειακή Ενότητα ", "")
        rows.append([p["substation"][:24], pref[:20], f"{mmv(p):.0f}", p.get("terrain") or "n/a"])
    t = axt.table(cellText=rows, cellLoc="left", loc="upper left", bbox=[0, 0.02, 1, 0.80])
    t.auto_set_font_size(False); t.set_fontsize(8.4)
    for (r, c), cell in t.get_celld().items():
        cell.set_edgecolor("#e6e6e6")
        if r == 0:
            cell.set_facecolor("#f3f3f3"); cell.set_text_props(fontweight="bold")
        cell.set_height(0.062)

    # ---------- footer ----------
    axf = fig.add_subplot(gs[3]); axf.axis("off"); axf.set_xlim(0, 1); axf.set_ylim(0, 1)
    paras = [
        "Sources: DEDDIE WebAPE (per-substation MVA headroom, daily) · ΡΑΑΕΥ/ΑΔΜΗΕ saturation "
        "(ΡΑΕ 663/2019, ΦΕΚ Β' 3660 for the Peloponnese; Evia joint announcement 2025) · "
        "OpenStreetMap (150/400 kV topology and prefecture boundaries) · Copernicus GLO-30 DEM (terrain).",
        "Method: each substation is traced to its upstream ΚΥΤ through a transmission graph; the saturation "
        "flag follows the regulator's geography, by prefecture. Values are MVA as published, no cos φ assumed. "
        f"Only the Peloponnese and Evia carry a formal ΦΕΚ decision ({n_high} substations); the other saturated "
        "zones rest on the ΑΔΜΗΕ ten-year plan and the ΔΕΔΔΗΕ map, and are marked indicative.",
        f"GridGap · Vlasis Mangidis · {date.today().isoformat()}"]
    foot = "\n".join(textwrap.fill(p, width=155) for p in paras)
    axf.text(0, 0.95, foot, fontsize=6.8, va="top", color=MUT, linespacing=1.5)

    out = ROOT / "GridGap_Greece.pdf"
    fig.savefig(str(out), dpi=200)
    fig.savefig(str(RAW / "_onepager_preview.png"), dpi=110)
    print(f"[+] wrote {out.name}  (traps {len(traps)}/{n_high} ΦΕΚ, "
          f"buildable {len(build)} ({len(build_gentle)} gentle))")


if __name__ == "__main__":
    sys.exit(main())
