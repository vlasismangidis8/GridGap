"""
GridGap — story dashboard (gridgap_story.html), self-contained, English.

One page that explains itself: the thesis, a legend in plain words, the
interactive map (clear land/sea, one red = saturation, dots = DEDDIE signal,
rings = the trap), a date slider over the archived snapshots, and the insights.
"""

import json
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
LAND = "#f7f4ee"
SAT_CITABLE = "#c0392b"     # ΦΕΚ decision
SAT_INDICATIVE = "#edb0a4"  # DEDDIE map (indicative)
URBAN_RING = "#7a8899"      # green but in a dense city core -> no land to build

# Dense urban cores: green here is electrically free but has no land for a park.
URBAN_MARKERS = ("Τομέα Αθηνών", "Πειραιώς", "Μητροπολιτική Ενότητα Θεσσαλονίκης")


def is_urban(p):
    pr = p.get("prefecture") or ""
    return any(u in pr for u in URBAN_MARKERS)


MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def short_date(d):
    y, m, dd = d.split("-")
    return f"{MON[int(m) - 1]} {int(dd)}"


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


def num(s):
    try:
        return float(str(s).replace(",", "."))
    except (ValueError, AttributeError):
        return None


def center(e):
    if e["type"] == "node":
        return e.get("lon"), e.get("lat")
    c = e.get("center")
    return (c["lon"], c["lat"]) if c else (None, None)


def status_of(icons):
    return "Green" if icons == ["Green"] else ("Red" if "Red" in icons else "Orange")


def date_status(path):
    """substation name -> (status, min_margin) for one daily snapshot."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    agg = {}
    for r in rows:
        name = r["PERI_Y"]
        m = min([x for x in (num(r["TTHP"]), num(r["TPBK"])) if x is not None] or [None])
        a = agg.setdefault(name, {"icons": set(), "mm": None})
        a["icons"].add(r["MARGIN_ICON"])
        if m is not None:
            a["mm"] = m if a["mm"] is None else min(a["mm"], m)
    return {n: (status_of(sorted(v["icons"])), v["mm"]) for n, v in agg.items()}


def sub_traces(feats, per_status, saturated_names, colorkey):
    """Build the 3 dot traces + hidden-red ring trace for one date."""
    buckets = {"Green": ([], [], []), "Orange": ([], [], []), "Red": ([], [], [])}
    rlon, rlat, rtxt = [], [], []
    ulon, ulat, utxt = [], [], []
    for f in feats:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        st, mm = per_status.get(p["substation"], (None, None))
        if st is None:
            continue
        zone = p["admie_zone"]
        ztxt = (f"<br><b style='color:{SAT_CITABLE}'>Downstream grid: {zone} "
                f"[{p['admie_confidence']}]</b>" if zone else
                "<br>Downstream grid: not saturated")
        kyt = (f"{p['feeding_kyt']}" + (f" ({p['kyt_distance_km']} km)"
               if p['kyt_distance_km'] else "")) if p['feeding_kyt'] else "n/a"
        terr = (f"<br>Terrain: {p['terrain']} (mean slope {p['slope_mean_deg']}°)"
                if p.get("terrain") else "")
        hov = (f"<b>{p['substation']}</b><br>WebAPE: {st} &nbsp;|&nbsp; headroom "
               f"<b>{mm}</b> MVA<br>Prefecture: {p['prefecture'] or 'n/a'}"
               f"<br>Upstream ΚΥΤ: {kyt}{terr}{ztxt}<extra></extra>")
        b = buckets[st]
        b[0].append(lon); b[1].append(lat); b[2].append(hov)
        if st == "Green" and p["substation"] in saturated_names:
            rlon.append(lon); rlat.append(lat)
            rtxt.append(f"<b>{p['substation']}</b><br>Green on WebAPE "
                        f"({mm} MVA shown free), but the grid downstream is "
                        f"<b>{zone}</b> [{p['admie_confidence']}].<br>"
                        f"You cannot actually connect here.<extra></extra>")
        elif st == "Green" and is_urban(p):
            ulon.append(lon); ulat.append(lat)
            utxt.append(f"<b>{p['substation']}</b><br>Green on WebAPE ({mm} MVA free) "
                        f"and the grid is fine, but it sits in a dense city core "
                        f"({(p['prefecture'] or '').replace('Περιφερειακή Ενότητα ','')}), "
                        f"so there is no land to build a park.<extra></extra>")
    made = []
    for st, col in (("Green", GREEN), ("Orange", ORANGE), ("Red", RED)):
        lo, la, tx = buckets[st]
        made.append(go.Scattergeo(lon=lo, lat=la, mode="markers",
            marker=dict(size=6, color=col, line=dict(width=0.5, color="white")),
            text=tx, hovertemplate="%{text}", showlegend=False))
    made.append(go.Scattergeo(lon=rlon, lat=rlat, mode="markers",
        marker=dict(size=15, color="rgba(0,0,0,0)", line=dict(width=2.6, color="#7b241c")),
        text=rtxt, hovertemplate="%{text}", showlegend=False))
    made.append(go.Scattergeo(lon=ulon, lat=ulat, mode="markers",
        marker=dict(size=13, color="rgba(0,0,0,0)", line=dict(width=2.2, color=URBAN_RING)),
        text=utxt, hovertemplate="%{text}", showlegend=False))
    return made


def main():
    fc = json.loads((ROOT / "data" / "substations.geojson").read_text(encoding="utf-8"))
    sat = json.loads((ROOT / "data" / "saturation_zones.json").read_text(encoding="utf-8"))
    prefs = gpd.read_file(RAW / "greece_prefectures.geojson")
    pref_gj = json.loads((RAW / "greece_prefectures.geojson").read_text(encoding="utf-8"))
    greece_prep = prep(prefs.union_all() if hasattr(prefs, "union_all") else prefs.unary_union)

    stem_zone = {}
    for z in sat["zones"]:
        for p in z.get("prefectures", []):
            stem_zone[pref_stem(p)] = z["confidence"]

    feats = [f for f in fc["features"] if f["geometry"]]
    saturated_names = {f["properties"]["substation"] for f in fc["features"]
                       if f["properties"]["admie_zone"]}

    # ---- prefecture choropleth: 0 land, 1 indicative, 2 citable ----
    locs, zvals = [], []
    for feat in pref_gj["features"]:
        conf = stem_zone.get(pref_stem(feat["properties"]["name"]))
        locs.append(feat["properties"]["name"])
        zvals.append(2 if conf == "high" else (1 if conf in ("medium", "low") else 0))
    cs = [[0.0, LAND], [0.33, LAND], [0.34, SAT_INDICATIVE], [0.66, SAT_INDICATIVE],
          [0.67, SAT_CITABLE], [1.0, SAT_CITABLE]]

    fig = go.Figure()
    fig.add_trace(go.Choropleth(
        geojson=pref_gj, locations=locs, z=zvals, featureidkey="properties.name",
        colorscale=cs, zmin=0, zmax=2, showscale=False,
        marker_line_color="#c9cdd4", marker_line_width=0.5, hoverinfo="skip"))

    # ---- network + ΚΥΤ ----
    lines = json.loads((RAW / "osm_lines.json").read_text(encoding="utf-8"))["elements"]
    def line_xy(want):
        lo, la = [], []
        for e in lines:
            if not (want & set(volts(e.get("tags", {}).get("voltage", "")))):
                continue
            g = e.get("geometry", [])
            if len(g) < 2 or not greece_prep.contains(
                    Point(g[len(g)//2]["lon"], g[len(g)//2]["lat"])):
                continue
            lo += [p["lon"] for p in g] + [None]
            la += [p["lat"] for p in g] + [None]
        return lo, la
    lo4, la4 = line_xy({400000})
    lo1, la1 = line_xy({150000})
    fig.add_trace(go.Scattergeo(lon=lo1, lat=la1, mode="lines",
        line=dict(width=0.5, color="#a9c2d8"), name="150 kV grid",
        visible="legendonly", hoverinfo="skip"))
    fig.add_trace(go.Scattergeo(lon=lo4, lat=la4, mode="lines",
        line=dict(width=1.2, color="#6b7c9c"), name="400 kV backbone", hoverinfo="skip"))

    subs_osm = json.loads((RAW / "osm_substations.json").read_text(encoding="utf-8"))["elements"]
    kx, ky, kn = [], [], []
    for e in subs_osm:
        t = e.get("tags", {}); name = t.get("name", "") or t.get("name:el", "")
        vs = volts(t.get("voltage", "")); lon, lat = center(e)
        if lon is None:
            continue
        if ("ΚΥΤ" in name or "Υπερυψηλής" in name or (400000 in vs and 150000 in vs)) \
                and greece_prep.contains(Point(lon, lat)):
            kx.append(lon); ky.append(lat); kn.append(name or "ΚΥΤ")
    fig.add_trace(go.Scattergeo(lon=kx, lat=ky, mode="markers", name="ΚΥΤ (400/150 kV)",
        marker=dict(symbol="diamond", size=8, color="#2c3e50", line=dict(width=1, color="white")),
        text=kn, hovertemplate="<b>%{text}</b><br>EHV center (400/150 kV)<extra></extra>"))

    # ---- legend proxies (so the dot colours appear in the legend) ----
    for label, col in [("WebAPE green (space)", GREEN), ("WebAPE orange (marginal)", ORANGE),
                       ("WebAPE red (full)", RED)]:
        fig.add_trace(go.Scattergeo(lon=[None], lat=[None], mode="markers", name=label,
            marker=dict(size=8, color=col), hoverinfo="skip"))
    fig.add_trace(go.Scattergeo(lon=[None], lat=[None], mode="markers",
        name="Hidden red: green but grid-blocked",
        marker=dict(size=13, color="rgba(0,0,0,0)", line=dict(width=2.4, color="#7b241c")),
        hoverinfo="skip"))
    fig.add_trace(go.Scattergeo(lon=[None], lat=[None], mode="markers",
        name="Green in a city: no land",
        marker=dict(size=12, color="rgba(0,0,0,0)", line=dict(width=2.2, color=URBAN_RING)),
        hoverinfo="skip"))

    # ---- per-date substation traces + slider frames ----
    snaps = sorted(RAW.glob("margins_2*.json"))
    dates = [p.stem.replace("margins_", "") for p in snaps]
    per = {d: date_status(p) for d, p in zip(dates, snaps)}
    latest = dates[-1]

    base_start = len(fig.data)  # dot traces occupy indices base_start..base_start+4
    for tr in sub_traces(feats, per[latest], saturated_names, latest):
        fig.add_trace(tr)
    dot_idx = list(range(base_start, base_start + 5))

    frames = []
    for d in dates:
        frames.append(go.Frame(name=d, data=sub_traces(feats, per[d], saturated_names, d),
                               traces=dot_idx))
    fig.frames = frames

    steps = [dict(method="animate", label=short_date(d),
                  args=[[d], dict(mode="immediate", frame=dict(duration=0, redraw=True),
                                  transition=dict(duration=0))]) for d in dates]
    fig.update_layout(
        sliders=[dict(active=len(dates)-1, x=0.07, len=0.5, y=0.06, pad=dict(t=6, b=8),
                      currentvalue=dict(prefix="Daily snapshot:  ", font=dict(size=13),
                                        xanchor="left"),
                      tickcolor="#888", ticklen=5, font=dict(size=12), steps=steps)],
        updatemenus=[dict(type="buttons", showactive=False, x=0.63, y=0.055, xanchor="left",
                          buttons=[dict(label="▶ Play", method="animate",
                                        args=[None, dict(frame=dict(duration=900, redraw=True),
                                                         fromcurrent=True)])])])

    fig.update_geos(scope="world", projection_type="mercator",
        lataxis_range=[34.7, 41.9], lonaxis_range=[19.2, 28.5],
        showland=True, landcolor=LAND, showocean=True, oceancolor="#cfe1ee",
        showcountries=False, coastlinecolor="#7d97a8", coastlinewidth=1.1,
        showframe=False, resolution=50, bgcolor="rgba(0,0,0,0)")
    fig.update_layout(margin=dict(l=0, r=0, t=6, b=72), height=780,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(x=0.01, y=0.98, bgcolor="rgba(255,255,255,0.9)", bordercolor="#ddd",
                    borderwidth=1, font=dict(size=11.5)),
        dragmode="pan")

    map_div = fig.to_html(full_html=False, include_plotlyjs="inline",
                          config={"scrollZoom": True, "displayModeBar": False})

    # ---- numbers for the narrative ----
    def mmv(p):
        return p["min_margin_mva"] or 0
    greens = [f["properties"] for f in fc["features"]
              if f["properties"]["deddie_icons"] == ["Green"]]
    traps = [p for p in greens if p["substation"] in saturated_names]
    urban = [p for p in greens if p["substation"] not in saturated_names and is_urban(p)]
    build = [p for p in greens if p["substation"] not in saturated_names and not is_urban(p)]
    # prefer flat land, then bigger headroom
    top_build = sorted(build, key=lambda p: (p.get("terrain") != "gentle", -mmv(p)))[:6]

    k = dict(
        total=len(fc["features"]), green=len(greens),
        hidden=len(traps), high=sum(1 for p in traps if p["admie_confidence"] == "high"),
        phantom=round(sum(mmv(p) for p in traps)),
        phantom_hi=round(sum(mmv(p) for p in traps if p["admie_confidence"] == "high")),
        urban=len(urban), build=len(build), build_mva=round(sum(mmv(p) for p in build)),
        build_gentle=sum(1 for p in build if p.get("terrain") == "gentle"),
        top_build=[(p["substation"], round(mmv(p)),
                    (p["prefecture"] or "").replace("Περιφερειακή Ενότητα ", ""))
                   for p in top_build],
        dates=dates)
    page = build_page(map_div, k)
    out = ROOT / "gridgap_story.html"
    out.write_text(page, encoding="utf-8")
    (ROOT / "gridgap_map.html").write_text(page, encoding="utf-8")  # email attachment name

    # artifact-ready fragment: <style> + body content, no doctype/head/body wrapper
    style = page[page.index("<style>"): page.index("</style>") + len("</style>")]
    body = page[page.index("<body>") + len("<body>"): page.index("</body>")]
    (ROOT / "gridgap_artifact.html").write_text(style + "\n" + body, encoding="utf-8")
    print(f"[+] wrote {out.name}  ({k['hidden']} traps / {k['high']} citable / "
          f"{k['urban']} urban / {k['build']} buildable ({k['build_mva']} MVA) / "
          f"{len(dates)} snapshots)")


def build_page(map_div, k):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GridGap Greece</title>
<style>
  :root{{--ink:#1c2733;--mut:#5c6b7a;--red:#c0392b;--grn:#2e9e5b;--line:#e6e9ed;--bg:#ffffff;--card:#f7f9fb}}
  *{{box-sizing:border-box}} body{{margin:0;font:16px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}}
  .wrap{{max-width:1180px;margin:0 auto;padding:28px 22px 60px}}
  h1{{font-size:30px;margin:0 0 4px}} .sub{{color:var(--mut);font-size:16px;margin:0 0 20px}}
  .lede{{font-size:18px;line-height:1.6;border-left:4px solid var(--red);padding:4px 0 4px 16px;margin:18px 0 26px}}
  .lede b{{color:var(--red)}}
  .stats{{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 26px}}
  .stat{{flex:1;min-width:150px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
  .stat .n{{font-size:32px;font-weight:800;line-height:1;font-variant-numeric:tabular-nums}} .stat .l{{color:var(--mut);font-size:13px;margin-top:4px}}
  .stat.red .n{{color:var(--red)}} .stat.grn .n{{color:var(--grn)}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin:8px 0 26px}}
  @media(max-width:760px){{.grid2{{grid-template-columns:1fr}}}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}}
  .card h3{{margin:0 0 10px;font-size:15px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}}
  .key{{display:flex;align-items:flex-start;gap:10px;margin:9px 0;font-size:14.5px}}
  .dot{{width:14px;height:14px;border-radius:50%;flex:0 0 14px;margin-top:3px}}
  .sw{{width:16px;height:14px;border-radius:3px;flex:0 0 16px;margin-top:3px}}
  .ring{{width:14px;height:14px;border-radius:50%;flex:0 0 14px;margin-top:3px;border:3px solid #7b241c;box-sizing:border-box}}
  .mapbox{{border:1px solid var(--line);border-radius:14px;overflow:hidden;margin:6px 0 10px;box-shadow:0 1px 6px rgba(0,0,0,.05)}}
  .hint{{color:var(--mut);font-size:13px;margin:0 0 26px}}
  ol.ins{{margin:6px 0 0;padding-left:20px}} ol.ins li{{margin:10px 0;font-size:15.5px}}
  ol.ins b{{color:var(--ink)}}
  .foot{{color:var(--mut);font-size:12.5px;border-top:1px solid var(--line);margin-top:30px;padding-top:14px}}
  .tag{{display:inline-block;background:#fbeae8;color:var(--red);font-size:11px;font-weight:700;
        border-radius:20px;padding:2px 9px;vertical-align:middle}}
  .explain{{background:#eef5f0;border:1px solid #cfe3d7;border-radius:12px;padding:16px 20px;margin:0 0 26px}}
  .explain h3{{margin:0 0 8px;font-size:16px}} .explain p{{margin:0;font-size:15.5px;line-height:1.6}}
  .explain b{{color:#1c2733}}
  .uring{{width:14px;height:14px;border-radius:50%;flex:0 0 14px;margin-top:3px;border:3px solid #7a8899;box-sizing:border-box}}
  .arrow{{color:var(--mut);font-weight:800;align-self:center;font-size:22px}}
  .prov{{width:100%;border-collapse:collapse;font-size:14px;margin-top:4px}}
  .prov td{{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
  .prov tr:last-child td{{border-bottom:none}}
  .src{{display:inline-block;font-size:11px;font-weight:700;border-radius:20px;padding:2px 9px;white-space:nowrap}}
  .s-ded{{background:#e4eef7;color:#1f5f8b}} .s-adm{{background:#fbeae8;color:#b0342a}}
  .s-osm{{background:#e6f2e9;color:#2f7d46}} .s-gg{{background:#eceef1;color:#4a5560}}
</style></head><body><div class="wrap">

<h1>GridGap Greece</h1>
<p class="sub">Where a renewable or BESS project can really connect in Greece, measured in MW.</p>

<p class="lede">DEDDIE publishes, every day, how much capacity each substation can take. But by its own terms it leaves
out the transmission grid. So a substation can show 40&nbsp;MW free and still be impossible to connect, because the ΑΔΜΗΕ
grid behind it is full. DEDDIE shows where there is room; ΑΔΜΗΕ is where the real blockage sits; almost nobody looks at
both at once. This page puts them together.</p>

<div class="explain"><h3>What “saturated” means, in plain terms</h3>
<p>Think of the grid as roads. A solar or wind park makes power that has to travel to the cities over wires. DEDDIE runs
the local streets next to the park. ΑΔΜΗΕ runs the national motorways that carry the power away. WebAPE only looks at the
local street and, by its own rules, ignores the motorway. “Saturated” means the motorway is already jammed: there may be
a free parking spot, but nothing new can get onto the highway, so the power has nowhere to go. It looks available on the
tool and isn’t in practice. Someone who trusts that can spend months and real money before ΑΔΜΗΕ says no. This page shows
it beforehand.</p></div>

<h3 style="margin:0 0 8px;color:var(--mut);font-size:14px;text-transform:uppercase;letter-spacing:.04em">
From 229 substations to the ones worth a phone call</h3>
<div class="stats">
  <div class="stat"><div class="n">{k['green']}</div><div class="l">look green on DEDDIE WebAPE, the raw signal</div></div>
  <div class="stat red"><div class="n">{k['hidden']}</div><div class="l">are grid-blocked <b>traps</b> ({k['phantom']:,} MVA phantom, {k['high']} with a ΦΕΚ decision)</div></div>
  <div class="stat"><div class="n">{k['urban']}</div><div class="l">sit in dense city cores, with <b>no land</b> to build a park</div></div>
  <div class="stat grn"><div class="n">{k['build']}</div><div class="l"><b>buildable shortlist</b>: green, grid-free, with land ({k['build_mva']:,} MVA; {k['build_gentle']} on gentle terrain)</div></div>
</div>

<div class="grid2">
  <div class="card"><h3>How to read the dots and rings</h3>
    <div class="key"><span class="dot" style="background:{GREEN}"></span><div><b>Green dot.</b> WebAPE says space is available (the signal everyone sees).</div></div>
    <div class="key"><span class="dot" style="background:{ORANGE}"></span><div><b>Orange</b>, marginal &nbsp;·&nbsp; <span class="dot" style="display:inline-block;background:{RED};vertical-align:middle"></span> <b>Red</b>, full.</div></div>
    <div class="key"><span class="ring"></span><div><b>Red ring, a grid trap.</b> Green on WebAPE, but the transmission grid behind it is saturated, so the capacity is phantom.</div></div>
    <div class="key"><span class="uring"></span><div><b>Grey ring, no land.</b> Green, and the grid is fine, but it sits in a dense city core where there is nowhere to build.</div></div>
  </div>
  <div class="card"><h3>How to read the shaded land</h3>
    <div class="key"><span class="sw" style="background:{SAT_CITABLE}"></span><div><b>Solid red area.</b> Transmission officially saturated by a ΡΑΑΕΥ/ΦΕΚ decision (Peloponnese below ΚΥΤ Κουμουνδούρου, and Evia).</div></div>
    <div class="key"><span class="sw" style="background:{SAT_INDICATIVE}"></span><div><b>Light red area.</b> The grid there is operationally full (ΑΔΜΗΕ ten-year plan and the ΔΕΔΔΗΕ map), but there is no formal ΦΕΚ saturation decision, unlike the solid-red zones.</div></div>
    <div class="key"><span class="sw" style="background:{LAND};border:1px solid #ccc"></span><div><b>Plain land.</b> No declared transmission saturation.</div></div>
    <div class="key"><span class="sw" style="background:#2c3e50"></span><div><b>◆ ΚΥΤ.</b> The 400/150&nbsp;kV centers. Grey lines are the network. Toggle layers in the legend.</div></div>
  </div>
</div>

<div class="card" style="margin-bottom:22px"><h3>Where each thing on the map comes from</h3>
<table class="prov">
  <tr><td><b>Dot colour</b> (green/orange/red) and the MVA headroom</td>
      <td><span class="src s-ded">ΔΕΔΔΗΕ</span></td>
      <td>WebAPE, the operator's own daily figures per substation and transformer.</td></tr>
  <tr><td><b>Solid red zone</b> (Peloponnese, Evia)</td>
      <td><span class="src s-adm">ΑΔΜΗΕ / ΡΑΑΕΥ</span></td>
      <td>Official transmission-saturation decision (ΡΑΕ 663/2019, ΦΕΚ Β' 3660).</td></tr>
  <tr><td><b>Light red zone</b> (Macedonia, Thessaly, Thrace)</td>
      <td><span class="src s-adm">ΑΔΜΗΕ ΔΠΑ / ΔΕΔΔΗΕ</span></td>
      <td>Operationally full per the ΑΔΜΗΕ ten-year plan (ΔΠΑ 2025-2034) and the ΔΕΔΔΗΕ map. No formal ΦΕΚ, so we mark it indicative.</td></tr>
  <tr><td><b>◆ ΚΥΤ</b>, the 150/400 kV lines, the prefecture borders and coastline</td>
      <td><span class="src s-osm">OpenStreetMap</span></td>
      <td>Public geographic data for the physical grid and boundaries.</td></tr>
  <tr><td><b>“Upstream ΚΥΤ”</b> and the <b>red ring</b> (grid trap)</td>
      <td><span class="src s-gg">GridGap</span></td>
      <td>Our computation: we trace each substation through the grid graph, then cross it with the saturation zones.</td></tr>
  <tr><td>The <b>grey ring</b> (no land)</td>
      <td><span class="src s-gg">GridGap</span></td>
      <td>Our flag: a green substation inside a dense urban core (from the OSM administrative boundary).</td></tr>
  <tr><td>The <b>terrain</b> in each substation's hover (slope, flat-enough or not)</td>
      <td><span class="src s-osm">Copernicus DEM</span></td>
      <td>The EU 30 m elevation model. We read the land around each point and measure how flat it is.</td></tr>
  <tr><td>The <b>date slider</b> (daily snapshots)</td>
      <td><span class="src s-gg">GridGap</span></td>
      <td>Our daily archive of the ΔΕΔΔΗΕ figures, the history nobody else keeps.</td></tr>
</table>
<p style="margin:10px 0 0;font-size:13px;color:var(--mut)">In short: the green dots come from ΔΕΔΔΗΕ, the red land from
ΑΔΜΗΕ and ΡΑΑΕΥ, the grid lines and borders from OpenStreetMap, and the rings and the join are GridGap putting them
together.</p></div>

<div class="mapbox">{map_div}</div>
<p class="hint">Drag to pan, scroll to zoom, hover any element for detail, click the legend to toggle layers. Use the
slider to step through the archived daily snapshots (<span class="tag">{len(k['dates'])} days so far</span>). One
substation already changed from red to orange this week.</p>

<div class="card"><h3>What this actually tells you</h3>
<ol class="ins">
  <li><b>About one in five greens is misleading.</b> {k['hidden']} of {k['green']} green substations sit in a saturated
      zone: {k['phantom']:,} MVA that looks free but the grid cannot carry, {k['phantom_hi']:,} MVA of it in ΦΕΚ-certified
      zones. Chasing one can cost months for a connection that will not be granted.</li>
  <li><b>There is a second problem: land.</b> Another {k['urban']} greens sit in dense city cores (central Athens, Piraeus,
      Thessaloniki), where the grid is fine but there is no land for a park. So of {k['green']} greens,
      {k['build']} are genuine opportunities.</li>
  <li><b>The buildable shortlist: {k['build']} substations, {k['build_mva']:,} MVA.</b> The real targets are rural and island.
      Adding terrain from the elevation model, {k['build_gentle']} of the {k['build']} sit on gentle, flat-enough land,
      the easiest for solar and BESS. Top of the list: {', '.join(f"{s} ({v} MVA, {p})" for s, v, p in k['top_build'][:5])}.
      That is who to call, instead of going through 229 by hand.</li>
  <li><b>The clearest trap.</b> ΛΥΓΟΥΡΙΟΥ (Argolida) shows 38&nbsp;MVA free on WebAPE but sits below the saturated
      ΚΥΤ Κουμουνδούρου. No official tool shows this.</li>
  <li><b>The time series matters later.</b> Over eight days only 3 of 229 substations moved at all, so the picture is stable
      week to week and today’s snapshot is enough for the map. Margins do shift over months as projects connect, and no one
      keeps that history. We do.</li>
</ol></div>

<p class="foot">Sources: DEDDIE WebAPE (MVA headroom per substation and transformer, daily) · ΡΑΑΕΥ/ΑΔΜΗΕ saturation
(ΡΑΕ 663/2019, ΦΕΚ Β' 3660 for Peloponnese; Evia joint announcement 2025) · OpenStreetMap (150/400 kV topology and
prefecture boundaries) · Copernicus GLO-30 DEM (terrain slope). Method: each substation is traced to its upstream ΚΥΤ
through a transmission graph; the saturation flag follows the regulator's own geography, by prefecture; “no land” means
the dense Athens, Piraeus and Thessaloniki urban cores; terrain reads the 30 m elevation model around each point.
Values are MVA as published, with no cos&nbsp;φ assumed. {k['hidden']-k['high']} of the {k['hidden']} traps rest on the
operators' electrical-space assessment (ΑΔΜΗΕ ΔΠΑ 2025-2034 and the ΔΕΔΔΗΕ map), operationally real but not a formal
Article-14 ΦΕΚ; the {k['high']} ΦΕΚ ones (Peloponnese, Evia) are firmly sourced. Terrain is the first land
layer; Natura2000 and CORINE land-cover come next and are not yet included. GridGap, snapshots {k['dates'][0]} to
{k['dates'][-1]}.</p>

</div></body></html>"""


if __name__ == "__main__":
    sys.exit(main())
