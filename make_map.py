"""
GridGap — interactive map (map.html).

Layers:
  * saturated prefectures (shaded, by confidence) — the ADMIE transmission constraint
  * every DEDDIE substation, coloured by its published availability icon
  * HIDDEN RED: DEDDIE-green substations inside a saturated zone — ringed, on top
"""

import json
import sys
import unicodedata
from pathlib import Path

import folium
import geopandas as gpd

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"

ICON_COLOR = {"Green": "#2e9e5b", "Orange": "#e8912d", "Red": "#d23b3b"}


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


def main():
    fc = json.loads((ROOT / "data" / "substations.geojson").read_text(encoding="utf-8"))
    sat = json.loads((ROOT / "data" / "saturation_zones.json").read_text(encoding="utf-8"))
    prefs = gpd.read_file(RAW / "greece_prefectures.geojson")

    # stem -> confidence for shading the saturated prefectures
    stem_conf = {}
    for z in sat["zones"]:
        for p in z.get("prefectures", []):
            stem_conf[pref_stem(p)] = (z["confidence"], z["zone_name_el"])
    conf_fill = {"high": "#c0392b", "medium": "#e67e22", "low": "#f1c40f"}

    m = folium.Map(location=[38.6, 23.5], zoom_start=7, tiles="CartoDB positron",
                   control_scale=True)

    # --- saturated prefecture polygons ---
    sat_layer = folium.FeatureGroup(name="Κορεσμένες ζώνες ΑΔΜΗΕ (ανά νομό)", show=True)
    for _, row in prefs.iterrows():
        info = stem_conf.get(pref_stem(row["name"]))
        if not info:
            continue
        conf, zone = info
        gj = folium.GeoJson(
            row.geometry.__geo_interface__,
            style_function=lambda _f, c=conf_fill[conf]: {
                "fillColor": c, "color": c, "weight": 1, "fillOpacity": 0.18},
            tooltip=f"{row['name']} — {zone} [{conf}]",
        )
        gj.add_to(sat_layer)
    sat_layer.add_to(m)

    # --- substation markers ---
    subs_layer = folium.FeatureGroup(name="Υποσταθμοί ΔΕΔΔΗΕ (χρώμα = WebAPE)", show=True)
    hidden_layer = folium.FeatureGroup(name="⚠ Κρυφά κόκκινα (🟢 WebAPE σε κορεσμένη ζώνη)", show=True)

    for f in fc["features"]:
        if not f["geometry"]:
            continue
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        icons = p["deddie_icons"]
        color = ICON_COLOR["Green"] if icons == ["Green"] else (
            ICON_COLOR["Red"] if "Red" in icons else ICON_COLOR["Orange"])
        popup = folium.Popup(html=(
            f"<b>{p['substation']}</b><br>"
            f"WebAPE: {', '.join(icons)}<br>"
            f"Περιθώριο min(θερμ.,β/κ): <b>{p['min_margin_mva']}</b> MVA<br>"
            f"Νομός: {p['prefecture'] or '—'}<br>"
            f"Ανάντη ΚΥΤ: {p['feeding_kyt'] or '—'}"
            + (f" ({p['kyt_distance_km']} km)" if p['kyt_distance_km'] else "")
            + (f"<br><span style='color:#c0392b'><b>ΑΔΜΗΕ: {p['admie_zone']}</b> "
               f"[{p['admie_confidence']}]</span>" if p['admie_zone'] else "")
        ), max_width=300)

        folium.CircleMarker(
            [lat, lon], radius=5, color=color, fill=True, fill_color=color,
            fill_opacity=0.9, weight=1, popup=popup,
        ).add_to(subs_layer)

        if p["hidden_red"]:
            ring = "#7b241c" if p["admie_confidence"] == "high" else "#b03a2e"
            folium.CircleMarker(
                [lat, lon], radius=11, color=ring, fill=False, weight=3,
                popup=popup,
            ).add_to(hidden_layer)

    subs_layer.add_to(m)
    hidden_layer.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    # --- legend + title ---
    n_hidden = sum(1 for f in fc["features"] if f["properties"]["hidden_red"])
    n_high = sum(1 for f in fc["features"]
                 if f["properties"]["hidden_red"] and f["properties"]["admie_confidence"] == "high")
    legend = f"""
    <div style="position:fixed;bottom:24px;left:24px;z-index:9999;background:white;
      padding:12px 14px;border:1px solid #bbb;border-radius:8px;font:13px/1.5 sans-serif;
      box-shadow:0 2px 8px rgba(0,0,0,.15);max-width:290px">
      <div style="font-weight:700;font-size:14px;margin-bottom:6px">GridGap — Ελλάδα</div>
      <div><span style="color:{ICON_COLOR['Green']}">●</span> WebAPE πράσινο &nbsp;
           <span style="color:{ICON_COLOR['Orange']}">●</span> πορτοκαλί &nbsp;
           <span style="color:{ICON_COLOR['Red']}">●</span> κόκκινο</div>
      <div style="margin-top:4px"><span style="color:#7b241c">◯</span> κρυφό κόκκινο —
           🟢 WebAPE αλλά σε κορεσμένη ζώνη ΑΔΜΗΕ</div>
      <div style="margin-top:8px;padding-top:8px;border-top:1px solid #eee">
        <b>{n_hidden}</b> κρυφά κόκκινα &nbsp;(<b>{n_high}</b> με ΦΕΚ)</div>
      <div style="color:#888;margin-top:6px;font-size:11px">
        MVA όπως δημοσιεύεται από ΔΕΔΔΗΕ · κορεσμός: ΡΑΑΕΥ/ΑΔΜΗΕ</div>
    </div>"""
    m.get_root().html.add_child(folium.Element(legend))

    out = ROOT / "map.html"
    m.save(str(out))
    print(f"[+] wrote {out.name}  ({n_hidden} hidden-red, {n_high} high-confidence)")


if __name__ == "__main__":
    sys.exit(main())
