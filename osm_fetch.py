"""
GridGap — pull the Greek transmission network from OpenStreetMap (Overpass).

We need the physical graph to trace each DEDDIE 150/20 substation back to its
upstream ΚΥΤ (400/150 kV center):
  * transmission LINES (power=line / cable) with voltage -> graph edges
  * SUBSTATIONS (node/way/relation) with voltage -> graph nodes (incl. the ΚΥΤ)

Saves raw Overpass JSON to data/raw/ so we never re-hit the API during dev.

Notes learned the hard way on this network:
  * A TLS-intercepting proxy breaks cert verification -> verify=False.
  * Overpass returns 406 without a real User-Agent (mod_security).
  * The ISO area lookup 504s; a bounding box over Greece is far faster.
"""

import json
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

RAW = Path(__file__).parent / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HEADERS = {"User-Agent": "GridGap/1.0 (grid connection research; vlmangidis@gmail.com)"}
BBOX = "34.6,19.2,41.9,29.8"  # south,west,north,east — mainland + islands

# Transmission lines with geometry (filter voltage in Python).
Q_LINES = f"""
[out:json][timeout:180];
(
  way["power"="line"]["voltage"]({BBOX});
  way["power"="cable"]["voltage"]({BBOX});
);
out tags geom;
"""

# Substations with a centroid (nodes/ways/relations).
Q_SUBS = f"""
[out:json][timeout:180];
(
  node["power"="substation"]({BBOX});
  way["power"="substation"]({BBOX});
  relation["power"="substation"]({BBOX});
);
out tags center;
"""


def overpass(query: str, label: str) -> dict:
    last = None
    for base in MIRRORS:
        for attempt in range(3):
            try:
                print(f"[*] {label}: {base} (try {attempt+1})")
                r = requests.post(base, data={"data": query}, timeout=200,
                                  verify=False, headers=HEADERS)
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code}"
                print(f"    {last}")
                if r.status_code in (429, 504, 503):
                    time.sleep(15)
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                print(f"    {last}")
                time.sleep(8)
    raise RuntimeError(f"{label} failed on all mirrors: {last}")


def main():
    lines = overpass(Q_LINES, "lines")
    (RAW / "osm_lines.json").write_text(json.dumps(lines), encoding="utf-8")
    print(f"[+] lines: {len(lines.get('elements', []))} elements -> osm_lines.json")

    subs = overpass(Q_SUBS, "substations")
    (RAW / "osm_substations.json").write_text(json.dumps(subs), encoding="utf-8")
    print(f"[+] substations: {len(subs.get('elements', []))} elements -> osm_substations.json")


if __name__ == "__main__":
    sys.exit(main())
