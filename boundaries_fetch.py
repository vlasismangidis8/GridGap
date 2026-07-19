"""
GridGap — Greek prefecture (περιφερειακή ενότητα, admin_level=6) boundaries.

geodata.gov.gr is blocked by the local proxy, so we pull admin boundaries from
OSM/Overpass (the proven channel) and polygonize the relation members with
shapely. Two uses:
  1. clip the OSM transmission network to Greece (drop TR/AL/MK noise)
  2. assign each DEDDIE substation to its νομός for the prefecture overlay

Output: data/raw/greece_prefectures.geojson  (name + geometry, EPSG:4326)
"""

import json
import sys
import time
from pathlib import Path

import requests
import urllib3
from shapely.geometry import LineString, mapping
from shapely.ops import linemerge, polygonize, unary_union

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

RAW = Path(__file__).parent / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HEADERS = {"User-Agent": "GridGap/1.0 (grid connection research; vlmangidis@gmail.com)"}

# admin_level=6 = περιφερειακές ενότητες (regional units / prefectures).
# Use the Greece area so we don't drag in Turkish/Albanian districts.
QUERY = """
[out:json][timeout:240];
area["ISO3166-1"="GR"]["admin_level"="2"]->.gr;
relation["boundary"="administrative"]["admin_level"="6"](area.gr);
out geom;
"""


def overpass(query: str) -> dict:
    last = None
    for base in MIRRORS:
        for attempt in range(3):
            try:
                print(f"[*] boundaries: {base} (try {attempt+1})")
                r = requests.post(base, data={"data": query}, timeout=260,
                                  verify=False, headers=HEADERS)
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code}"
                print(f"    {last}")
                time.sleep(15)
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                print(f"    {last}")
                time.sleep(8)
    raise RuntimeError(f"boundaries failed on all mirrors: {last}")


def polygonize_relation(members):
    """Build a (multi)polygon from a relation's outer/inner member ways."""
    def lines(role):
        out = []
        for m in members:
            if m.get("type") != "way" or not m.get("geometry"):
                continue
            if m.get("role", "outer") not in role:
                continue
            coords = [(p["lon"], p["lat"]) for p in m["geometry"]]
            if len(coords) >= 2:
                out.append(LineString(coords))
        return out

    outer = list(polygonize(linemerge(lines(("outer", ""))))) if lines(("outer", "")) else []
    inner = list(polygonize(linemerge(lines(("inner",))))) if lines(("inner",)) else []
    if not outer:
        return None
    geom = unary_union(outer)
    if inner:
        geom = geom.difference(unary_union(inner))
    return geom


def main():
    data = overpass(QUERY)
    rels = [e for e in data.get("elements", []) if e.get("type") == "relation"]
    print(f"[+] {len(rels)} admin_level=6 relations")

    feats = []
    for rel in rels:
        tags = rel.get("tags", {})
        name = tags.get("name:el") or tags.get("name") or f"rel/{rel['id']}"
        geom = polygonize_relation(rel.get("members", []))
        if geom is None or geom.is_empty:
            print(f"    ! skipped (no polygon): {name}")
            continue
        feats.append({
            "type": "Feature",
            "properties": {
                "name": name,
                "name_en": tags.get("name:en", ""),
                "osm_id": rel["id"],
                "wikidata": tags.get("wikidata", ""),
            },
            "geometry": mapping(geom),
        })

    fc = {"type": "FeatureCollection", "features": feats}
    out = RAW / "greece_prefectures.geojson"
    out.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    print(f"[+] wrote {len(feats)} prefectures -> {out.name}")


if __name__ == "__main__":
    sys.exit(main())
