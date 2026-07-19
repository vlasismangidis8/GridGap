"""
GridGap — interactive Plotly map (gridgap_interactive.html), English UI.

Self-contained (no external tiles): everything is drawn from real geometries on
Plotly's geo canvas, so it works offline and anywhere.

Layers (toggle in the legend):
  * Saturated prefectures  — choropleth polygons, shaded by ΑΔΜΗΕ confidence
  * 400 kV backbone + ΚΥΤ  — the transmission spine and its 400/150 centers
  * 150 kV network         — the finer mesh (off by default)
  * Substations            — points by DEDDIE WebAPE colour (green/orange/red)
  * ⚠ Hidden red           — GREEN on WebAPE but inside a saturated zone
"""

import json
import math
import re
import sys
import unicodedata
from pathlib import Path

import geopandas as gpd
import plotly.graph_objects as go
from shapely.geometry import Point
from shapely.prepared import prep

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"

GREEN, ORANGE, RED = "#2e9e5b", "#e8912d", "#d23b3b"
CONF_Z = {None: 0, "low": 1, "medium": 2, "high": 3}
CONF_COLORS = ["#f4f4f2", "#f6d55c", "#ef8a3c", "#c0392b"]


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


def volts(v):
    return [int(p) for p in re.split(r"[;,]", str(v)) if p.strip().isdigit()]


def center(e):
    if e["type"] == "node":
        return e.get("lon"), e.get("lat")
    c = e.get("center")
    return (c["lon"], c["lat"]) if c else (None, None)


def line_coords(elements, greece_prep, want):
    """Return (lons, lats) with None separators for lines whose voltage∈want."""
    lons, lats = [], []
    for e in elements:
        vs = set(volts(e.get("tags", {}).get("voltage", "")))
        if not (want & vs):
            continue
        g = e.get("geometry", [])
        if len(g) < 2:
            continue
        mlon, mlat = g[len(g) // 2]["lon"], g[len(g) // 2]["lat"]
        if not greece_prep.contains(Point(mlon, mlat)):
            continue
        lons += [p["lon"] for p in g] + [None]
        lats += [p["lat"] for p in g] + [None]
    return lons, lats


def main():
    fc = json.loads((ROOT / "data" / "substations.geojson").read_text(encoding="utf-8"))
    sat = json.loads((ROOT / "data" / "saturation_zones.json").read_text(encoding="utf-8"))
    prefs = gpd.read_file(RAW / "greece_prefectures.geojson")
    pref_gj = json.loads((RAW / "greece_prefectures.geojson").read_text(encoding="utf-8"))
    greece_prep = prep(prefs.union_all() if hasattr(prefs, "union_all") else prefs.unary_union)

    # prefecture stem -> zone (en) + confidence
    stem_zone = {}
    for z in sat["zones"]:
        for p in z.get("prefectures", []):
            stem_zone[pref_stem(p)] = (z["zone_name_en"], z["confidence"])

    # ----- prefecture choropleth -----
    locs, zvals, cdata = [], [], []
    for feat in pref_gj["features"]:
        name = feat["properties"]["name"]
        en = feat["properties"].get("name_en") or ""
        zone, conf = stem_zone.get(pref_stem(name), (None, None))
        locs.append(name)
        zvals.append(CONF_Z[conf])
        label = en or name
        cdata.append([label, zone or "not declared saturated", conf or "—"])

    colorscale = []
    for i, c in enumerate(CONF_COLORS):
        colorscale += [[i / 4, c], [(i + 1) / 4, c]]

    fig = go.Figure()
    fig.add_trace(go.Choropleth(
        geojson=pref_gj, locations=locs, z=zvals, featureidkey="properties.name",
        colorscale=colorscale, zmin=0, zmax=4,
        marker_line_color="white", marker_line_width=0.5,
        customdata=cdata,
        hovertemplate="<b>%{customdata[0]}</b><br>ADMIE zone: %{customdata[1]}"
                      "<br>Confidence: %{customdata[2]}<extra></extra>",
        colorbar=dict(title="ADMIE<br>saturation", tickvals=[0.5, 1.5, 2.5, 3.5],
                      ticktext=["none", "low", "medium", "high (ΦΕΚ)"],
                      len=0.5, y=0.78, thickness=14),
        name="Saturated prefectures", showscale=True,
    ))

    # ----- transmission network -----
    lines = json.loads((RAW / "osm_lines.json").read_text(encoding="utf-8"))["elements"]
    lo4, la4 = line_coords(lines, greece_prep, {400000})
    lo1, la1 = line_coords(lines, greece_prep, {150000})
    fig.add_trace(go.Scattergeo(
        lon=lo1, lat=la1, mode="lines", line=dict(width=0.6, color="#9bbcd6"),
        name="150 kV network", visible="legendonly", hoverinfo="skip"))
    fig.add_trace(go.Scattergeo(
        lon=lo4, lat=la4, mode="lines", line=dict(width=1.3, color="#5a6b8c"),
        name="400 kV backbone", hoverinfo="skip"))

    # ----- ΚΥΤ (400/150 centers) -----
    subs_osm = json.loads((RAW / "osm_substations.json").read_text(encoding="utf-8"))["elements"]
    kx, ky, kn = [], [], []
    for e in subs_osm:
        t = e.get("tags", {})
        name = t.get("name", "") or t.get("name:el", "")
        vs = volts(t.get("voltage", ""))
        lon, lat = center(e)
        if lon is None:
            continue
        is_kyt = ("ΚΥΤ" in name or "Υπερυψηλής" in name or (400000 in vs and 150000 in vs))
        if is_kyt and greece_prep.contains(Point(lon, lat)):
            kx.append(lon); ky.append(lat); kn.append(name or "ΚΥΤ")
    fig.add_trace(go.Scattergeo(
        lon=kx, lat=ky, mode="markers", name="ΚΥΤ (400/150 kV)",
        marker=dict(symbol="diamond", size=9, color="#2c3e50",
                    line=dict(width=1, color="white")),
        text=kn, hovertemplate="<b>%{text}</b><br>EHV center (400/150 kV)<extra></extra>"))

    # ----- substations by WebAPE status -----
    buckets = {"Green": ([], [], [], GREEN, "WebAPE green (space available)"),
               "Orange": ([], [], [], ORANGE, "WebAPE orange (marginal)"),
               "Red": ([], [], [], RED, "WebAPE red (no space)")}
    hr_lon, hr_lat, hr_txt = [], [], []
    for f in fc["features"]:
        if not f["geometry"]:
            continue
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        icons = p["deddie_icons"]
        key = "Green" if icons == ["Green"] else ("Red" if "Red" in icons else "Orange")
        zone_txt = (f"<br><b style='color:#c0392b'>ADMIE: {p['admie_zone']} "
                    f"[{p['admie_confidence']}]</b>" if p["admie_zone"] else "")
        kyt_txt = (f"{p['feeding_kyt']}" + (f" ({p['kyt_distance_km']} km)"
                   if p['kyt_distance_km'] else "")) if p["feeding_kyt"] else "—"
        hov = (f"<b>{p['substation']}</b><br>WebAPE: {', '.join(icons)}"
               f"<br>Headroom min(thermal, short-circuit): <b>{p['min_margin_mva']}</b> MVA"
               f"<br>Prefecture: {p['prefecture'] or '—'}"
               f"<br>Upstream ΚΥΤ: {kyt_txt}{zone_txt}<extra></extra>")
        b = buckets[key]
        b[0].append(lon); b[1].append(lat); b[2].append(hov)
        if p["hidden_red"]:
            hr_lon.append(lon); hr_lat.append(lat)
            hr_txt.append(f"<b>⚠ {p['substation']}</b><br>"
                          f"GREEN on WebAPE ({p['min_margin_mva']} MVA free)…<br>"
                          f"…but inside <b>{p['admie_zone']}</b> [{p['admie_confidence']}]"
                          f"<br>→ transmission-blocked in reality<extra></extra>")

    for key, (lo, la, tx, col, label) in buckets.items():
        fig.add_trace(go.Scattergeo(
            lon=lo, lat=la, mode="markers", name=label,
            marker=dict(size=6, color=col, line=dict(width=0.5, color="white")),
            text=tx, hovertemplate="%{text}"))

    fig.add_trace(go.Scattergeo(
        lon=hr_lon, lat=hr_lat, mode="markers", name="⚠ Hidden red",
        marker=dict(size=15, color="rgba(0,0,0,0)",
                    line=dict(width=2.6, color="#7b241c")),
        text=hr_txt, hovertemplate="%{text}"))

    # ----- layout -----
    n_hidden = sum(1 for f in fc["features"] if f["properties"]["hidden_red"])
    n_high = sum(1 for f in fc["features"] if f["properties"]["hidden_red"]
                 and f["properties"]["admie_confidence"] == "high")
    n_green = sum(1 for f in fc["features"] if f["properties"]["deddie_icons"] == ["Green"])

    fig.update_geos(
        scope="world", projection_type="mercator",
        lataxis_range=[34.6, 41.9], lonaxis_range=[19.1, 28.6],
        showland=True, landcolor="#ffffff", showocean=True, oceancolor="#eef4f8",
        showcountries=True, countrycolor="#cccccc", coastlinecolor="#b9c6d0",
        resolution=50,
    )
    fig.update_layout(
        title=dict(
            text=(f"<b>GridGap — Greece</b>  ·  where you can actually connect renewables<br>"
                  f"<span style='font-size:14px;color:#7b241c'>"
                  f"<b>{n_hidden}</b> substations look GREEN on DEDDIE WebAPE but sit in an "
                  f"ADMIE-saturated zone — <b>{n_high}</b> with a citable ΦΕΚ decision "
                  f"(Peloponnese, Evia)</span>"),
            x=0.01, xanchor="left", y=0.97),
        legend=dict(x=0.01, y=0.60, bgcolor="rgba(255,255,255,0.85)",
                    bordercolor="#ccc", borderwidth=1, font=dict(size=11)),
        margin=dict(l=0, r=0, t=70, b=30), height=860, paper_bgcolor="white",
        annotations=[dict(
            x=0.5, y=-0.02, xref="paper", yref="paper",
            showarrow=False, font=dict(size=10, color="#777"),
            text=("Sources: DEDDIE WebAPE (MVA headroom, daily) · ΡΑΑΕΥ/ΑΔΜΗΕ saturation "
                  "(ΡΑΕ 663/2019 ΦΕΚ Β' 3660 Peloponnese; Evia 2025) · OpenStreetMap "
                  "(150/400 kV topology, prefecture borders). "
                  f"MVA as published (no cos φ assumed). {n_green} substations green on WebAPE."))],
    )

    out = ROOT / "gridgap_interactive.html"
    fig.write_html(str(out), include_plotlyjs="inline", full_html=True)
    print(f"[+] wrote {out.name}  ({n_hidden} hidden-red, {n_high} citable, "
          f"400kV pts={len(lo4)}, 150kV pts={len(lo1)}, ΚΥΤ={len(kx)})")


if __name__ == "__main__":
    sys.exit(main())
