# -*- coding: utf-8 -*-
"""Emit the inline-SVG maps used by gridgap_presentation.html.

Three panels, all on the same projection and geometry:
  A  DEDDIE   — 229 substations coloured by the published icon
  B  ADMIE    — prefectures shaded by transmission-saturation zone
  C  GridGap  — the join: traps ringed red, buildable shortlist in copper

Writes data/raw/_deck_maps.html (paste-ready fragment).
"""
import json
import math
import sys
import unicodedata
from pathlib import Path

from shapely.geometry import shape

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
sys.stdout.reconfigure(encoding="utf-8")

LON0, LON1, LAT0, LAT1 = 19.2, 28.4, 34.6, 41.9
S = 100.0                       # svg units per degree of latitude
K = math.cos(math.radians(38.5))  # lon compression
W = round((LON1 - LON0) * K * S, 1)
H = round((LAT1 - LAT0) * S, 1)


def px(lon, lat):
    return (lon - LON0) * K * S, (LAT1 - lat) * S


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower().strip()


def pref_stem(name):
    n = strip_accents(name)
    for pfx in ("περιφερειακη ενοτητα ", "μητροπολιτικη ενοτητα ",
                "περιφερειακη ενοτητα", "νομος "):
        if n.startswith(pfx):
            n = n[len(pfx):]
    n = n.strip()
    for suf in ("ιας", "ιου", "ων", "ας", "ης", "ος", "ου", "α", "ο", "ς", "η", "υ"):
        if n.endswith(suf) and len(n) - len(suf) >= 4:
            n = n[: -len(suf)]
            break
    # nominative 'Κορινθία' stems to 'κορινθι', genitive 'Κορινθίας' to 'κορινθ'
    if n.endswith("ι") and len(n) >= 6:
        n = n[:-1]
    return n


def path_d(geom, min_area=0.0012, tol=0.012):
    """Polygon exteriors -> one SVG path string, small islands dropped."""
    g = geom.simplify(tol, preserve_topology=True)
    polys = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
    out = []
    for p in polys:
        if p.is_empty or p.area < min_area:
            continue
        pts = []
        for lon, lat in p.exterior.coords:
            x, y = px(lon, lat)
            pts.append(f"{x:.1f},{y:.1f}")
        if len(pts) > 3:
            out.append("M" + "L".join(pts) + "Z")
    return "".join(out)


def main():
    prefs = json.loads((RAW / "greece_prefectures.geojson").read_text(encoding="utf-8"))
    sat = json.loads((ROOT / "data" / "saturation_zones.json").read_text(encoding="utf-8"))
    subs = json.loads((ROOT / "data" / "substations.geojson").read_text(encoding="utf-8"))

    stem_conf = {}
    for z in sat["zones"]:
        for p in z.get("prefectures", []):
            stem_conf[pref_stem(p)] = z["confidence"]

    # --- land + saturation shading -------------------------------------------
    land, sat_hi, sat_lo = [], [], []
    for f in prefs["features"]:
        d = path_d(shape(f["geometry"]))
        if not d:
            continue
        land.append(d)
        conf = stem_conf.get(pref_stem(f["properties"]["name"]))
        if conf == "high":
            sat_hi.append(d)
        elif conf in ("medium", "low"):
            sat_lo.append(d)

    # --- substations ----------------------------------------------------------
    URBAN = ("Τομέα Αθηνών", "Πειραιώς", "Μητροπολιτική Ενότητα Θεσσαλονίκης")
    dots = []
    for f in subs["features"]:
        p = f["properties"]
        if not f.get("geometry"):
            continue
        lon, lat = f["geometry"]["coordinates"]
        x, y = px(lon, lat)
        icons = p["deddie_icons"]
        icon = "Green" if icons == ["Green"] else ("Red" if "Red" in icons else "Orange")
        urban = any(u in (p.get("prefecture") or "") for u in URBAN)
        dots.append(dict(x=round(x, 1), y=round(y, 1), icon=icon,
                         trap=bool(p["hidden_red"]),
                         urban=icons == ["Green"] and not p["hidden_red"] and urban,
                         build=icons == ["Green"] and not p["hidden_red"] and not urban,
                         mva=p["min_margin_mva"] or 0))

    C = {"Green": "var(--green)", "Orange": "var(--amber)", "Red": "var(--red)"}

    def frame(inner, cls=""):
        return (f'<svg class="map {cls}" viewBox="0 0 {W} {H}" role="img" '
                f'preserveAspectRatio="xMidYMid meet">{inner}</svg>')

    land_path = f'<path class="land" d="{"".join(land)}"/>'
    sat_hi_path = f'<path class="sat-hi" d="{"".join(sat_hi)}"/>' if sat_hi else ""
    sat_lo_path = f'<path class="sat-lo" d="{"".join(sat_lo)}"/>' if sat_lo else ""

    # A — what DEDDIE publishes
    a = land_path + "".join(
        f'<circle cx="{d["x"]}" cy="{d["y"]}" r="4" fill="{C[d["icon"]]}" '
        f'fill-opacity=".9" stroke="#0b1216" stroke-width=".8"/>' for d in dots)

    # B — what ADMIE constrains
    b = land_path + sat_lo_path + sat_hi_path

    # C — the join
    c = (land_path + sat_lo_path + sat_hi_path
         + "".join(f'<circle cx="{d["x"]}" cy="{d["y"]}" r="2.2" fill="#5d7280"/>'
                   for d in dots if not (d["trap"] or d["build"] or d["urban"]))
         + "".join(f'<circle cx="{d["x"]}" cy="{d["y"]}" r="3" fill="#7a8899"/>'
                   for d in dots if d["urban"])
         + "".join(f'<circle cx="{d["x"]}" cy="{d["y"]}" r="{2.6 + min(d["mva"], 50) / 14:.1f}" '
                   f'fill="var(--copper)" fill-opacity=".92" stroke="#0b1216" stroke-width=".7"/>'
                   for d in dots if d["build"])
         + "".join(f'<circle cx="{d["x"]}" cy="{d["y"]}" r="{3.4 + min(d["mva"], 40) / 12:.1f}" '
                   f'fill="none" stroke="var(--red)" stroke-width="2.2"/>'
                   f'<circle cx="{d["x"]}" cy="{d["y"]}" r="1.8" fill="var(--green)"/>'
                   for d in dots if d["trap"]))

    # inject between the markers in the deck (idempotent — safe to re-run)
    deck = ROOT / "gridgap_presentation.html"
    html = deck.read_text(encoding="utf-8")
    for key, svg in (("A", frame(a)), ("B", frame(b)), ("C", frame(c))):
        start, end = f"<!--MAP-{key}:START-->", f"<!--MAP-{key}:END-->"
        i, j = html.find(start), html.find(end)
        if i < 0 or j < 0:
            sys.exit(f"marker MAP-{key} missing from {deck.name}")
        html = html[: i + len(start)] + svg + html[j:]
    deck.write_text(html, encoding="utf-8")
    print(f"injected 3 maps into {deck.name}  ({len(html) / 1024:.0f} KB total)")
    print(f"viewBox 0 0 {W} {H} · {len(dots)} dots · "
          f"{sum(d['trap'] for d in dots)} traps · {sum(d['build'] for d in dots)} buildable · "
          f"{sum(d['urban'] for d in dots)} urban · {len(sat_hi)} citable prefs / {len(sat_lo)} indicative")


if __name__ == "__main__":
    main()
