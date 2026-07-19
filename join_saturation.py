"""
GridGap — the join. DEDDIE margins ⊕ ADMIE transmission saturation.

Key insight from the topology step: ΡΑΑΕΥ declares saturation by GEOGRAPHY
("downstream of ΚΥΤ Κουμουνδούρου" = the whole Peloponnese network fed through
it), NOT "the substation nearest that ΚΥΤ". ΚΥΤ Κουμουνδούρου physically also
feeds west Athens, which is NOT saturated. So the saturation flag is driven by
PREFECTURE (matching how the regulator declares it); the ΚΥΤ topology is carried
as corroborating context ("fed by ΚΥΤ X, Y km away").

Output:
  data/substations.geojson  — every substation, DEDDIE margin + ADMIE flag
  prints the headline: DEDDIE-green substations sitting in a saturated zone.
"""

import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower().strip()


def pref_stem(name):
    """Normalize a prefecture name to an accent-free stem for matching.
    Handles 'Περιφερειακή Ενότητα Κορινθίας' / 'Κορινθία' / 'Κορινθίας'."""
    n = strip_accents(name)
    for pfx in ("περιφερειακη ενοτητα ", "μητροπολιτικη ενοτητα ",
                "περιφερειακη ενοτητα", "νομος "):
        if n.startswith(pfx):
            n = n[len(pfx):]
    n = n.strip()
    # collapse Greek genitive/nominative endings to a common stem
    for suf in ("ιας", "ιου", "ων", "ας", "ης", "ος", "ου", "α", "ο", "ς", "η", "υ"):
        if n.endswith(suf) and len(n) - len(suf) >= 4:
            return n[: -len(suf)]
    return n


def main():
    subs = json.loads((RAW / "substation_kyt.json").read_text(encoding="utf-8"))
    sat = json.loads((ROOT / "data" / "saturation_zones.json").read_text(encoding="utf-8"))

    # Build stem -> (zone, confidence) from the saturation zones' prefecture lists.
    stem_zone = {}
    for z in sat["zones"]:
        for p in z.get("prefectures", []):
            stem_zone[pref_stem(p)] = {
                "zone": z["zone_name_el"], "confidence": z["confidence"],
                "decision": z["decision"], "fek": z.get("fek"),
                "mw_margin": z.get("mw_margin"),
            }

    # Match each substation's OSM prefecture stem to a saturated zone.
    matched_prefs = {}
    feats, flagged = [], []
    for r in subs:
        pref = r.get("prefecture")
        zone = None
        if pref:
            zone = stem_zone.get(pref_stem(pref))
            if zone:
                matched_prefs[pref] = zone["zone"]

        deddie_green = r["icons"] == ["Green"]  # DEDDIE says: space available
        admie_saturated = zone is not None
        # The product's punchline set: DEDDIE-green BUT ADMIE-saturated.
        hidden_red = deddie_green and admie_saturated
        if hidden_red:
            flagged.append((r, zone))

        props = {
            "substation": r["substation"],
            "deddie_icons": r["icons"],
            "min_margin_mva": r["min_margin_mva"],
            "prefecture": pref,
            "feeding_kyt": r["kyt"],
            "kyt_distance_km": r["kyt_km"],
            "topology_status": r["status"],
            "admie_zone": zone["zone"] if zone else None,
            "admie_confidence": zone["confidence"] if zone else None,
            "admie_decision": zone["decision"] if zone else None,
            "hidden_red": hidden_red,
        }
        geom = None
        if r["lon"] is not None and r["lat"] is not None:
            geom = {"type": "Point", "coordinates": [r["lon"], r["lat"]]}
        feats.append({"type": "Feature", "properties": props, "geometry": geom})

    fc = {"type": "FeatureCollection", "features": feats}
    (ROOT / "data" / "substations.geojson").write_text(
        json.dumps(fc, ensure_ascii=False), encoding="utf-8")

    # ---------- report ----------
    total = len(subs)
    green = sum(1 for r in subs if r["icons"] == ["Green"])
    in_sat = sum(1 for f in feats if f["properties"]["admie_zone"])
    hi = [f for r, z in flagged for f in [None] if z["confidence"] == "high"]
    hi_n = sum(1 for r, z in flagged if z["confidence"] == "high")
    print(f"Substations: {total}  (DEDDIE-green: {green})")
    print(f"Sitting in an ADMIE-saturated zone: {in_sat}")
    print()
    print(f"===> HIDDEN RED: {len(flagged)} DEDDIE-green substations sit in a")
    print(f"     transmission-saturated zone (green on WebAPE, blocked in reality).")
    print(f"        of which HIGH-confidence (citable ΦΕΚ, Peloponnese+Evia): {hi_n}")
    print()
    print("Matched saturated prefectures (verify these are correct):")
    for p, z in sorted(matched_prefs.items()):
        print(f"   {p:45} -> {z}")
    print()
    print("Hidden-red substations by zone:")
    byzone = Counter(z["zone"] for _, z in flagged)
    for zone, n in byzone.most_common():
        conf = next(z["confidence"] for _, z in flagged if z["zone"] == zone)
        print(f"   [{conf:6}] {n:3}  {zone}")
    print()
    print("HIGH-confidence hidden-red list (Peloponnese + Evia):")
    for r, z in flagged:
        if z["confidence"] == "high":
            print(f"   {r['substation'][:32]:32} {str(r['prefecture'])[:38]:38} "
                  f"margin={r['min_margin_mva']} via {r['kyt']}")


if __name__ == "__main__":
    sys.exit(main())
