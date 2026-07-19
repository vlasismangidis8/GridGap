"""
GridGap — trace each DEDDIE substation to its upstream ΚΥΤ (400/150 kV).

Method:
  1. Load OSM transmission lines; keep 150 kV + 400 kV; clip to Greece
     (drops Turkish 154 kV noise and AL/MK/TR features).
  2. Build a physical wire graph: every line vertex is a node (coords rounded
     to ~1 m so shared towers merge); edges = consecutive vertices, weighted by
     geodesic length (km).
  3. Snap the ~22 Greek ΚΥΤ and the 229 DEDDIE substations onto the nearest
     graph vertex.
  4. Multi-source Dijkstra FROM all ΚΥΤ at once -> every node learns its nearest
     ΚΥΤ and the network distance to it. Each DEDDIE substation reads off its
     feeding ΚΥΤ.
  5. Attach prefecture (point-in-polygon) for the coarse saturation overlay.

Output: data/raw/substation_kyt.json  (one record per unique DEDDIE substation)
"""

import json
import math
import re
import sys
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
from shapely.geometry import Point
from shapely.prepared import prep

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"

KEEP_V = {150000, 400000}
SNAP_KM_KYT = 5.0   # ΚΥΤ centroid -> nearest 150/400 vertex
SNAP_KM_SUB = 4.0   # DEDDIE substation -> nearest 150/400 vertex


def volts(v):
    return [int(p) for p in re.split(r"[;,]", str(v)) if p.strip().isdigit()]


def num(s):
    try:
        return float(str(s).replace(",", "."))
    except (ValueError, AttributeError):
        return None


def hav(lon1, lat1, lon2, lat2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def center(e):
    if e["type"] == "node":
        return e.get("lon"), e.get("lat")
    c = e.get("center")
    return (c["lon"], c["lat"]) if c else (None, None)


def main():
    # --- Greece polygon for clipping ---
    prefs = gpd.read_file(RAW / "greece_prefectures.geojson")
    greece = prefs.union_all() if hasattr(prefs, "union_all") else prefs.unary_union
    pg = prep(greece)

    # --- lines -> graph ---
    lines = json.loads((RAW / "osm_lines.json").read_text(encoding="utf-8"))["elements"]
    G = nx.Graph()
    kept = 0
    endpoints = []  # (lon,lat) of every kept line's two ends -> bridge to substations
    for e in lines:
        vs = volts(e.get("tags", {}).get("voltage", ""))
        if not (KEEP_V & set(vs)):
            continue
        coords = [(p["lon"], p["lat"]) for p in e.get("geometry", [])]
        if len(coords) < 2:
            continue
        mlon, mlat = coords[len(coords) // 2]
        if not pg.contains(Point(mlon, mlat)):
            continue  # foreign line
        kept += 1
        maxv = max(v for v in vs if v in KEEP_V)
        for (alon, alat), (blon, blat) in zip(coords, coords[1:]):
            ka = (round(alon, 5), round(alat, 5))
            kb = (round(blon, 5), round(blat, 5))
            if ka == kb:
                continue
            w = hav(alon, alat, blon, blat)
            if G.has_edge(ka, kb):
                if w < G[ka][kb]["w"]:
                    G[ka][kb]["w"] = w
            else:
                G.add_edge(ka, kb, w=w)
            G.nodes[ka]["v"] = max(G.nodes[ka].get("v", 0), maxv)
            G.nodes[kb]["v"] = max(G.nodes[kb].get("v", 0), maxv)
        endpoints.append((round(coords[0][0], 5), round(coords[0][1], 5)))
        endpoints.append((round(coords[-1][0], 5), round(coords[-1][1], 5)))
    print(f"[*] kept {kept} Greek 150/400kV lines; raw graph: "
          f"{G.number_of_nodes()} nodes / {G.number_of_edges()} edges "
          f"/ {nx.number_connected_components(G)} components")

    verts = list(G.nodes)
    va = np.array(verts)  # (N,2) lon,lat

    def nearest_vertex(lon, lat):
        dlon = (va[:, 0] - lon) * math.cos(math.radians(lat))
        dlat = va[:, 1] - lat
        i = int((dlon * dlon + dlat * dlat).argmin())
        return verts[i], math.hypot(dlon[i], dlat[i]) * 111.0

    # --- substations: the JUNCTIONS that bridge separate lines ---
    # In OSM, lines don't share vertices at a station; each terminates at its
    # own bay. So we add every Greek transmission substation as a hub node and
    # wire every line endpoint to the nearest substation within BRIDGE_KM.
    subs = json.loads((RAW / "osm_substations.json").read_text(encoding="utf-8"))["elements"]
    stations = []  # {name, lon, lat, is_kyt}
    for e in subs:
        t = e.get("tags", {})
        name = t.get("name", "") or t.get("name:el", "")
        vs = volts(t.get("voltage", ""))
        lon, lat = center(e)
        if lon is None:
            continue
        is_trans = bool({150000, 400000} & set(vs)) or "ΚΥΤ" in name or "Υπερυψηλής" in name
        if not is_trans or not pg.contains(Point(lon, lat)):
            continue
        is_kyt = ("ΚΥΤ" in name or "Υπερυψηλής" in name or (400000 in vs and 150000 in vs))
        stations.append({"name": name or f"(sub {e['id']})", "lon": lon, "lat": lat,
                         "is_kyt": is_kyt, "id": e["id"]})

    sta_xy = np.array([[s["lon"], s["lat"]] for s in stations])

    def nearest_station(lon, lat):
        dlon = (sta_xy[:, 0] - lon) * math.cos(math.radians(lat))
        dlat = sta_xy[:, 1] - lat
        i = int((dlon * dlon + dlat * dlat).argmin())
        return i, math.hypot(dlon[i], dlat[i]) * 111.0

    BRIDGE_KM = 3.0
    for s in stations:
        G.add_node(("STA", s["id"]), name=s["name"], is_kyt=s["is_kyt"])
    bridged = 0
    for ep in set(endpoints):
        i, d = nearest_station(ep[0], ep[1])
        if d <= BRIDGE_KM:
            G.add_edge(("STA", stations[i]["id"]), ep, w=d)
            bridged += 1
    print(f"[*] Greek transmission substations: {len(stations)} "
          f"(ΚΥΤ: {sum(s['is_kyt'] for s in stations)}); bridged endpoints: {bridged}")
    print(f"    connected graph now: {nx.number_connected_components(G)} components")

    added_kyt = [("STA", s["id"]) for s in stations if s["is_kyt"]
                 and ("STA", s["id"]) in G and G.degree(("STA", s["id"])) > 0]
    kyt_name = {("STA", s["id"]): s["name"] for s in stations}
    print(f"[*] ΚΥΤ sources wired into graph: {len(added_kyt)}")

    # --- DEDDIE substations (dedupe by PERI_Y) ---
    rows = json.loads((RAW / "margins_latest.json").read_text(encoding="utf-8"))
    dsub = {}
    for r in rows:
        name = r["PERI_Y"]
        lat, lon = num(r["SYNP"]), num(r["SYNM"])
        m = min([x for x in (num(r["TTHP"]), num(r["TPBK"])) if x is not None] or [None])
        rec = dsub.setdefault(name, {"name": name, "lon": lon, "lat": lat,
                                     "icons": set(), "min_margin": None})
        rec["icons"].add(r["MARGIN_ICON"])
        if m is not None:
            rec["min_margin"] = m if rec["min_margin"] is None else min(rec["min_margin"], m)
        if rec["lon"] is None and lon is not None:
            rec["lon"], rec["lat"] = lon, lat

    added_sub = {}
    for name, rec in dsub.items():
        if rec["lon"] is None:
            continue
        node = ("SUB", name)
        vtx, d = nearest_vertex(rec["lon"], rec["lat"])
        if d <= SNAP_KM_SUB:
            G.add_edge(node, vtx, w=d)
            added_sub[name] = (node, d)

    # --- multi-source Dijkstra from all ΚΥΤ ---
    dist, paths = nx.multi_source_dijkstra(G, set(added_kyt), weight="w")

    # --- prefecture lookup ---
    def prefecture(lon, lat):
        p = Point(lon, lat)
        hit = prefs[prefs.contains(p)]
        return hit.iloc[0]["name"] if len(hit) else None

    out = []
    for name, rec in dsub.items():
        r = {"substation": name, "lon": rec["lon"], "lat": rec["lat"],
             "icons": sorted(rec["icons"]), "min_margin_mva": rec["min_margin"],
             "kyt": None, "kyt_km": None, "prefecture": None, "status": None}
        if rec["lon"] is not None:
            r["prefecture"] = prefecture(rec["lon"], rec["lat"])
        if name not in added_sub:
            r["status"] = "no_snap"  # >4km from any 150/400kV line
        else:
            node = added_sub[name][0]
            if node in dist:
                r["kyt"] = kyt_name.get(paths[node][0], str(paths[node][0]))
                r["kyt_km"] = round(dist[node], 1)
                r["status"] = "assigned"
            else:
                r["status"] = "unreachable"        # snapped but isolated subgraph
        out.append(r)

    (RAW / "substation_kyt.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- summary ---
    from collections import Counter
    st = Counter(r["status"] for r in out)
    print(f"[+] {len(out)} substations -> substation_kyt.json")
    print(f"    status: {dict(st)}")
    kc = Counter(r["kyt"] for r in out if r["kyt"])
    print(f"    distinct feeding ΚΥΤ: {len(kc)}")
    for k, n in kc.most_common():
        print(f"       {n:3}  {k}")


if __name__ == "__main__":
    sys.exit(main())
