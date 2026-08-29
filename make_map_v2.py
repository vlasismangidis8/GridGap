"""
GridGap — map v2 (gridgap_map_v2.html).

Everything collected so far, in one page, organised as four reading modes:

  1. Available capacity:      min(TTHP, TPBK) MVA, exactly as published
  2. Short-circuit constraint: substations reported as saturated only because
     the fault-level margin is 0 while thermal capacity remains; a constraint of
     a different nature from thermal exhaustion
  3. Non-visible saturation:  DEDDIE-available sites inside an ADMIE saturated zone
  4. Change over time:        what moved across the daily snapshots

Data: margins_YYYY-MM-DD.json snapshots (DEDDIE WebAPE) + substations.geojson
(KYT topology + slope join) + saturation_zones.json (ADMIE/RAAEY).
Self-contained apart from the Leaflet CDN; polygons are simplified so the page
stays a few hundred KB instead of 10 MB.
"""

import json
import sys
import unicodedata
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "gridgap_map_v2.html"

# Prefecture keywords that mean "dense urban" — no land for utility-scale PV,
# but prime siting for BESS. A stopgap until a CORINE land-use join exists.
URBAN_KEYS = ("Τομέα Αθηνών", "Πειραιώς", "Θεσσαλονίκης")


def num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ".").strip())
    except ValueError:
        return None


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


def load_history():
    """-> dates, {(sub, tx): [ {t, b, i} per date ]}, latest static row per tx."""
    files = sorted(RAW.glob("margins_20*.json"))
    dates = [f.stem.replace("margins_", "") for f in files]
    hist, static = {}, {}
    for di, f in enumerate(files):
        for r in json.loads(f.read_text(encoding="utf-8")):
            k = (r.get("PERI_Y", ""), r.get("PERI_M", ""))
            hist.setdefault(k, [None] * len(dates))[di] = {
                "t": num(r.get("TTHP")), "b": num(r.get("TPBK")),
                "i": r.get("MARGIN_ICON"),
            }
            static[k] = r
    return dates, hist, static


def snapshot(entries):
    """One substation on one date -> (usable MVA, icon, #saturated Μ/Σ, locked MVA).

    usable = best min(thermal, short-circuit) on site: a connection is made on
    one transformer, so the best transformer is the one that matters.
    locked  = thermal headroom sitting behind a zero short-circuit margin —
    capacity the published icon calls saturated, but that is not a thermal wall.
    """
    best, icon, nred, locked = None, "?", 0, 0.0
    for e in entries:
        if not e or (e[0] is None and e[1] is None):
            continue
        t, b, ic = e[0], e[1], (e[2] or "?")
        # A blank margin is "not published", not "zero": 11 transformers ship a
        # blank short-circuit field while the icon says Green, so the published
        # margin is whatever DEDDIE does give. Same convention as validate.py.
        m = min(x for x in (t, b) if x is not None)
        if best is None or m > best:
            best, icon = m, ic
        if ic == "R":
            nred += 1
            if b == 0 and t > 0:
                locked += t
    return best, icon, nred, round(locked, 1)


def main():
    fc = json.loads((ROOT / "data" / "substations.geojson").read_text(encoding="utf-8"))
    sat = json.loads((ROOT / "data" / "saturation_zones.json").read_text(encoding="utf-8"))
    dates, hist, static = load_history()
    nd = len(dates)

    by_sub = {}
    for (sub, tx), h in hist.items():
        by_sub.setdefault(sub, []).append((tx, h))

    subs, changes = [], []
    for f in fc["features"]:
        if not f["geometry"]:
            continue
        p = f["properties"]
        name = p["substation"]
        lon, lat = f["geometry"]["coordinates"]

        txs = []
        for tx, h in sorted(by_sub.get(name, [])):
            s = static[(name, tx)]
            txs.append({
                "n": tx,
                "tot": num(s.get("ISXY_TOT")),
                "nape": num(s.get("APE_C")),
                "ape": num(s.get("ISXY_APE")),
                # per-date [thermal, short-circuit, icon-letter]
                "h": [[e["t"], e["b"], (e["i"] or "?")[0]] if e else None for e in h],
                # ΣΥΝΟΛΟ rows aggregate two transformers — never sum with members
                "agg": "ΣΥΝΟΛΟ" in tx,
            })
            for di in range(1, nd):
                a, b = h[di - 1], h[di]
                if not a or not b:
                    continue
                if a["t"] != b["t"] or a["b"] != b["b"] or a["i"] != b["i"]:
                    changes.append({
                        "d": dates[di], "s": name, "tx": tx,
                        "ua": None if a["t"] is None or a["b"] is None else min(a["t"], a["b"]),
                        "ub": None if b["t"] is None or b["b"] is None else min(b["t"], b["b"]),
                        "ia": a["i"], "ib": b["i"],
                        "flip": a["i"] != b["i"],
                    })

        # per date: [usable MVA, icon, #saturated Μ/Σ, MVA locked behind β/κ]
        series = []
        for di in range(nd):
            best, icon, nred, locked = snapshot([t["h"][di] for t in txs])
            series.append([round(best, 1) if best is not None else None,
                           icon, nred, locked])

        subs.append({
            "n": name, "la": round(lat, 5), "lo": round(lon, 5),
            "pf": p.get("prefecture"), "kyt": p.get("feeding_kyt"),
            "kd": p.get("kyt_distance_km"),
            "z": p.get("admie_zone"), "zc": p.get("admie_confidence"),
            "zd": p.get("admie_decision"), "hr": bool(p.get("hidden_red")),
            "sl": p.get("slope_mean_deg"), "gf": p.get("gentle_frac"),
            "urb": any(k in (p.get("prefecture") or "") for k in URBAN_KEYS),
            "tx": txs, "s": series,
        })

    changes.sort(key=lambda c: (c["d"], c["s"]))

    # ---------- saturated prefecture polygons (simplified) ----------
    stem_info = {}
    for z in sat["zones"]:
        for pr in z.get("prefectures", []):
            stem_info[pref_stem(pr)] = (z["confidence"], z["zone_name_el"])
    prefs = gpd.read_file(RAW / "greece_prefectures.geojson")
    prefs["geometry"] = prefs.geometry.simplify(0.004, preserve_topology=True)
    zone_feats = []
    for _, row in prefs.iterrows():
        info = stem_info.get(pref_stem(row["name"]))
        if not info:
            continue
        conf, zone = info
        zone_feats.append({
            "type": "Feature",
            "properties": {"name": row["name"], "conf": conf, "zone": zone},
            "geometry": json.loads(gpd.GeoSeries([row.geometry]).to_json())
                            ["features"][0]["geometry"],
        })
    zones_gj = {"type": "FeatureCollection", "features": zone_feats}

    # ---------- headline numbers ----------
    def usable_total(di):
        """National usable MVA across the whole published feed, including the few
        substations that carry no coordinates and so are not drawn.

        A ΣΥΝΟΛΟ row gives the combined figure for a named transformer pair, e.g.
        "ΣΥΝΟΛΟ  Ρέθυμνο ΜΣ2-Ρέθυμνο ΜΣ1". Skipping it whenever the substation also
        lists some other transformer was wrong: those four sites publish a ΜΣ3,
        which is not a member of the pair, so 74 MVA of real capacity was dropped.
        Skip an aggregate only when the transformers it names are themselves
        published — which today never happens, but would be double counting if it did."""
        rows = {}
        for (sub, tx), h in hist.items():
            rows.setdefault(sub, []).append((tx.strip(), h[di]))
        tot = 0.0
        for sub, items in rows.items():
            names = {tx for tx, _ in items}
            for tx, e in items:
                if "ΣΥΝΟΛΟ" in tx:
                    members = [m.strip() for m in tx.replace("ΣΥΝΟΛΟ", "", 1).split("-")]
                    if any(m in names for m in members):
                        continue
                if e:
                    present = [x for x in (e["t"], e["b"]) if x is not None]
                    if present:
                        tot += min(present)   # a blank margin does not block
        return round(tot, 1)

    locked_sites = [s for s in subs if s["s"][-1][3] > 0 and s["s"][-1][1] == "R"]
    stats = {
        "subs": len(subs), "tx": sum(len(s["tx"]) for s in subs),
        "dates": nd, "first": dates[0], "last": dates[-1],
        "hidden": sum(1 for s in subs if s["hr"]),
        "hidden_high": sum(1 for s in subs if s["hr"] and s["zc"] == "high"),
        "hidden_mva": round(sum(s["s"][-1][0] or 0 for s in subs if s["hr"]), 1),
        "locked_sites": len(locked_sites),
        "locked_mva": round(sum(s["s"][-1][3] for s in locked_sites), 1),
        "urban_sites": sum(1 for s in subs if s["urb"] and s["s"][-1][1] == "G"),
        "urban_mva": round(sum(s["s"][-1][0] or 0 for s in subs
                               if s["urb"] and s["s"][-1][1] == "G"), 1),
        "changes": len(changes), "flips": sum(1 for c in changes if c["flip"]),
        "nat_first": usable_total(0), "nat_last": usable_total(nd - 1),
    }
    stats["nat_delta"] = round(stats["nat_last"] - stats["nat_first"], 1)

    payload = {"dates": dates, "subs": subs, "zones": zones_gj,
               "changes": changes, "stats": stats}
    OUT.write_text(
        TEMPLATE.replace("__DATA__",
                         json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        encoding="utf-8")
    print(f"[+] {OUT.name}  {OUT.stat().st_size/1e6:.1f} MB - {stats['subs']} substations / "
          f"{stats['tx']} transformers, {nd} snapshots ({dates[0]} .. {dates[-1]})\n"
          f"    locked-by-short-circuit: {stats['locked_mva']} MVA at {stats['locked_sites']} sites | "
          f"hidden-red: {stats['hidden']} | flips: {stats['flips']}/{stats['changes']} changes | "
          f"national usable {stats['nat_first']} -> {stats['nat_last']} MVA")


TEMPLATE = r"""<!doctype html>
<html lang="el"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GridGap: available connection capacity in the Greek distribution grid</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{
  --bg:#0b0e13; --panel:#141922; --panel2:#0e131a; --line:#242c38;
  --fg:#e8eef6; --dim:#93a0b1; --faint:#5d6b7d;
  --green:#3fb06a; --orange:#e8912d; --red:#e05252;
  --locked:#a97bf0; --accent:#5b9bff;
  --shadow:0 6px 22px rgba(0,0,0,.45);
}
*{box-sizing:border-box}
html,body{margin:0;height:100%;overflow:hidden;
  font:13px/1.55 system-ui,"Segoe UI",Roboto,sans-serif;
  background:var(--bg);color:var(--fg);-webkit-font-smoothing:antialiased}
#map{position:absolute;inset:0;left:352px;background:var(--bg)}
#side{position:absolute;top:0;bottom:0;left:0;width:352px;background:var(--panel);
  border-right:1px solid var(--line);overflow-y:auto;z-index:1000;
  scrollbar-width:thin;scrollbar-color:#2c3644 transparent}
#side::-webkit-scrollbar{width:8px}
#side::-webkit-scrollbar-thumb{background:#2c3644;border-radius:4px}
.sec{padding:14px 16px;border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:9px}
h1{font-size:17px;margin:0;letter-spacing:-.2px}
.tag{font-size:10px;color:var(--faint);border:1px solid var(--line);
  border-radius:99px;padding:1px 7px}
.lede{color:var(--dim);font-size:11.5px;margin-top:6px}
h2{font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--faint);
  margin:0 0 9px;font-weight:700}

/* language switch */
#lang{margin-left:auto;display:flex;border:1px solid var(--line);border-radius:99px;
  overflow:hidden}
#lang button{border:0;border-radius:0;background:transparent;color:var(--faint);
  padding:3px 9px;font-size:10.5px;font-weight:700;letter-spacing:.04em}
#lang button.on{background:var(--accent);color:#fff}
#lang button:hover:not(.on){color:var(--fg)}

/* mode switch */
.modes{display:grid;gap:6px}
.mode{display:flex;gap:9px;align-items:flex-start;padding:9px 10px;border-radius:8px;
  border:1px solid var(--line);background:var(--panel2);cursor:pointer;transition:.13s}
.mode:hover{border-color:#3a4757}
.mode.on{border-color:var(--accent);background:#152238;
  box-shadow:inset 0 0 0 1px rgba(91,155,255,.25)}
.mode .sw{width:9px;height:9px;border-radius:50%;margin-top:5px;flex:none;background:var(--faint)}
.mode.on .sw{background:var(--accent);box-shadow:0 0 0 3px rgba(91,155,255,.22)}
.mode b{display:block;font-size:12.5px;font-weight:600}
.mode span{color:var(--dim);font-size:10.5px;line-height:1.35;display:block;margin-top:1px}

/* explainer */
details.help{border:1px solid var(--line);border-radius:8px;background:var(--panel2);
  padding:9px 11px}
details.help summary{cursor:pointer;font-size:12px;font-weight:600;list-style:none;
  display:flex;justify-content:space-between;align-items:center;color:var(--fg)}
details.help summary::-webkit-details-marker{display:none}
details.help summary::after{content:'+';color:var(--faint);font-size:14px}
details.help[open] summary::after{content:'−'}
details.help .hb{color:var(--dim);font-size:11px;line-height:1.5;margin-top:9px;
  padding-top:9px;border-top:1px solid var(--line)}
details.help .hb b{color:var(--fg);font-weight:600}
details.help .hb p{margin:0 0 8px}
details.help .hb p:last-child{margin-bottom:0}

/* readout */
.headline{background:var(--panel2);border:1px solid var(--line);border-radius:9px;
  padding:11px 12px}
.headline .big{font-size:26px;font-weight:650;line-height:1.05;letter-spacing:-.5px}
.headline .cap{color:var(--dim);font-size:11px;margin-top:3px}
.note{color:var(--dim);font-size:11px;margin-top:9px;padding-top:9px;
  border-top:1px solid var(--line)}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:8px}
.kpi{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 9px}
.kpi b{display:block;font-size:16px;line-height:1.15}
.kpi span{color:var(--dim);font-size:10px}
.bar{display:flex;height:7px;border-radius:4px;overflow:hidden;margin:10px 0 5px;
  background:#0a0d12}
.bar div{transition:width .25s}

/* timeline */
#tl{display:flex;align-items:center;gap:10px}
#tl button{width:30px;height:30px;flex:none;border-radius:50%;font-size:11px}
#dlabel{font-variant-numeric:tabular-nums;font-size:12px;font-weight:600;
  min-width:74px;text-align:right}
input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:4px;
  background:#26303d;border-radius:3px;outline:none}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;
  border-radius:50%;background:var(--accent);cursor:pointer;border:2px solid var(--panel)}

/* controls */
label.chk{display:flex;align-items:center;gap:8px;margin:6px 0;cursor:pointer;font-size:12px}
label.chk input{accent-color:var(--accent)}
select,input[type=search]{width:100%;background:var(--panel2);color:var(--fg);
  border:1px solid var(--line);border-radius:7px;padding:7px 9px;font:inherit;outline:none}
select:focus,input:focus{border-color:var(--accent)}
button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
  border-radius:7px;padding:5px 10px;font:inherit;cursor:pointer;transition:.13s}
button:hover{border-color:var(--accent);color:#fff}
.rowlab{display:flex;justify-content:space-between;font-size:11.5px;color:var(--dim);
  margin:10px 0 4px}
.pill{display:inline-block;padding:1px 7px;border-radius:99px;font-size:10px;
  border:1px solid var(--line);color:var(--dim);white-space:nowrap}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex:none}

/* change log */
#chg{max-height:250px;overflow-y:auto;margin:-2px -4px 0;padding:0 4px}
.ch{padding:7px 0;border-bottom:1px solid #1c232d;font-size:11.5px}
.ch:last-child{border-bottom:none}
.ch .t{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.ch b{font-weight:600}
.ch .m{color:var(--dim);font-size:10.5px}
.up{color:var(--green)}.dn{color:var(--red)}
.flip{border-color:#5b3fa0;color:#c9aaff}

/* popup */
.leaflet-popup-content-wrapper{background:var(--panel);color:var(--fg);border-radius:11px;
  box-shadow:var(--shadow);border:1px solid var(--line)}
.leaflet-popup-tip{background:var(--panel);border:1px solid var(--line)}
.leaflet-popup-content{margin:13px 15px;font-size:12px;max-height:64vh;overflow:auto;width:auto!important}
.leaflet-container a.leaflet-popup-close-button{color:var(--faint)}
.pop h3{margin:0 0 2px;font-size:14.5px;letter-spacing:-.2px}
.pop .meta{color:var(--dim);font-size:10.5px}
.pop .big{font-size:22px;font-weight:650;letter-spacing:-.5px;margin-top:7px}
.pop .big small{font-size:11px;font-weight:400;color:var(--dim);letter-spacing:0}
.pop table{border-collapse:collapse;width:100%;margin-top:9px;font-size:11px}
.pop th{color:var(--faint);font-weight:600;font-size:9.5px;text-transform:uppercase;
  letter-spacing:.04em}
.pop th,.pop td{border-top:1px solid var(--line);padding:5px 5px;text-align:right;
  white-space:nowrap}
.pop th:first-child,.pop td:first-child{text-align:left;white-space:normal}
.callout{margin-top:9px;padding:8px 10px;border-radius:8px;font-size:11.5px;line-height:1.45}
.c-lock{background:rgba(169,123,240,.12);border:1px solid rgba(169,123,240,.38)}
.c-hid{background:rgba(224,82,82,.12);border:1px solid rgba(224,82,82,.4)}
.c-lock b,.c-hid b{font-weight:650}

/* legend */
#legend{position:absolute;right:14px;bottom:14px;z-index:1000;background:rgba(20,25,34,.94);
  backdrop-filter:blur(6px);border:1px solid var(--line);border-radius:10px;
  padding:11px 13px;font-size:11.5px;box-shadow:var(--shadow);max-width:290px}
#legend .lt{font-weight:650;margin-bottom:7px;font-size:12px}
#legend .li{display:flex;align-items:center;gap:8px;margin:4px 0}
#legend .lf{color:var(--dim);font-size:10.5px;margin-top:8px;padding-top:7px;
  border-top:1px solid var(--line);line-height:1.4}
.ring{width:13px;height:13px;border-radius:50%;border:2px solid var(--red);flex:none}
.leaflet-control-attribution{background:rgba(11,14,19,.8)!important;color:var(--faint)!important}
.leaflet-control-attribution a{color:var(--dim)!important}
</style></head><body>
<div id="side">
  <div class="sec">
    <div class="brand"><h1>GridGap</h1>
      <div id="lang"><button data-l="el">EL</button><button data-l="en">EN</button></div>
    </div>
    <div class="lede" data-i18n="lede"></div>
    <div style="margin-top:8px"><span class="tag" id="tag"></span></div>
  </div>

  <div class="sec">
    <h2 data-i18n="sec_modes"></h2>
    <div class="modes" id="modes"></div>
    <details class="help" style="margin-top:9px">
      <summary data-i18n="help_t"></summary>
      <div class="hb" data-i18n="help_b"></div>
    </details>
  </div>

  <div class="sec">
    <h2 data-i18n="sec_number"></h2>
    <div class="headline">
      <div class="big" id="hbig"></div>
      <div class="cap" id="hcap"></div>
      <div class="note" id="hnote"></div>
    </div>
    <div class="kpis" id="kpis"></div>
    <div class="bar" id="bar"></div>
    <div class="cap" style="color:var(--dim);font-size:10.5px" id="barlab"></div>
  </div>

  <div class="sec">
    <h2 data-i18n="sec_date"></h2>
    <div id="tl"><button id="play">▶</button>
      <input type="range" id="date" min="0" step="1"><b id="dlabel"></b></div>
    <div class="cap" style="color:var(--faint);font-size:10.5px;margin-top:7px" id="dsub"></div>
  </div>

  <div class="sec">
    <h2 data-i18n="sec_filters"></h2>
    <input type="search" id="q" data-i18n-ph="f_search">
    <div class="rowlab"><span data-i18n="f_icons"></span></div>
    <label class="chk"><input type="checkbox" class="ic" value="G" checked>
      <span class="dot" style="background:var(--green)"></span><span data-i18n="f_green"></span></label>
    <label class="chk"><input type="checkbox" class="ic" value="O" checked>
      <span class="dot" style="background:var(--orange)"></span><span data-i18n="f_orange"></span></label>
    <label class="chk"><input type="checkbox" class="ic" value="R" checked>
      <span class="dot" style="background:var(--red)"></span><span data-i18n="f_red"></span></label>
    <div class="rowlab"><span data-i18n="f_constraints"></span></div>
    <label class="chk"><input type="checkbox" id="fhr"><span data-i18n="f_hidden"></span></label>
    <label class="chk"><input type="checkbox" id="flk"><span data-i18n="f_locked"></span></label>
    <label class="chk"><input type="checkbox" id="fch"><span data-i18n="f_changed"></span></label>
    <div class="rowlab"><span data-i18n="f_siting"></span></div>
    <label class="chk"><input type="checkbox" id="fgt"><span data-i18n="f_gentle"></span></label>
    <label class="chk"><input type="checkbox" id="fru"><span data-i18n="f_rural"></span></label>
    <label class="chk"><input type="checkbox" id="fur"><span data-i18n="f_urban"></span></label>
    <div class="rowlab"><span data-i18n="f_min"></span><b id="mlab"></b></div>
    <input type="range" id="minmva" min="0" max="60" value="0" step="1">
    <select id="reg" style="margin-top:10px"></select>
    <div class="cap" style="color:var(--dim);font-size:11px;margin-top:9px" id="count"></div>
  </div>

  <div class="sec">
    <h2 data-i18n="sec_layers"></h2>
    <label class="chk"><input type="checkbox" id="lz" checked><span data-i18n="l_zones"></span></label>
    <label class="chk"><input type="checkbox" id="ld"><span data-i18n="l_light"></span></label>
  </div>

  <div class="sec">
    <h2 data-i18n="sec_log"></h2>
    <label class="chk"><input type="checkbox" id="onlyflip"><span data-i18n="log_onlyflip"></span></label>
    <div id="chg"></div>
  </div>

  <div class="sec" style="color:var(--faint);font-size:10.5px;line-height:1.5" id="foot"></div>
</div>
<div id="map"></div>
<div id="legend"><div class="lt" id="legtitle"></div><div id="legbody"></div>
  <div class="lf" id="legfoot"></div></div>
<script>
const D = __DATA__, S = D.stats;
const C = {G:'#3fb06a', O:'#e8912d', R:'#e05252', '?':'#4a5462'};
const LOCK = '#a97bf0', CONF = {high:'#c0392b', medium:'#e67e22', low:'#f1c40f'};
let di = D.dates.length - 1, mode = 'space';
let lang = (localStorage.getItem('gg_lang') === 'en') ? 'en' : 'el';

/* ---------------- i18n ----------------
   Values are strings, or functions when a number has to be spliced in.
   Substation, prefecture and KYT names stay in Greek in both languages:
   they are the identifiers DEDDIE/ADMIE publish. */
const I18N = {
el: {
  title:'GridGap: διαθέσιμη ικανότητα σύνδεσης στο δίκτυο διανομής',
  lede:'Διαθέσιμη ικανότητα σύνδεσης ανά υποσταθμό, και διάκριση των περιπτώσεων στις οποίες '
    +'ο δημοσιευμένος κορεσμός δεν αντιστοιχεί σε πραγματικό φυσικό όριο του δικτύου.',
  tag:(a,b,c)=>`${a} ΥΣ · ${b} Μ/Σ · ${c} στιγμιότυπα`,
  na:'μ/δ',
  sec_modes:'Επιλογή ανάλυσης', sec_number:'Βασικό μέγεθος', sec_date:'Ημερομηνία στιγμιότυπου',
  sec_filters:'Φίλτρα', sec_layers:'Επίπεδα χάρτη', sec_log:'Ημερολόγιο μεταβολών',
  m_space:'Διαθέσιμη ικανότητα',
  m_space_d:'Ικανότητα σύνδεσης ανά υποσταθμό σε MVA, όπως δημοσιεύεται από τον ΔΕΔΔΗΕ.',
  m_locked:'Περιορισμός βραχυκύκλωσης',
  m_locked_d:'Υποσταθμοί με μηδενικό δημοσιευμένο περιθώριο που διατηρούν θερμική ικανότητα· '
    +'δεσμευτικό είναι το όριο στάθμης βραχυκύκλωσης.',
  m_hidden:'Μη ορατός κορεσμός',
  m_hidden_d:'Διαθέσιμοι κατά ΔΕΔΔΗΕ, εντός κορεσμένης ζώνης μεταφοράς ΑΔΜΗΕ '
    +'(«κρυφά κόκκινα»).',
  m_change:'Χρονική μεταβολή',
  m_change_d:n=>`Μεταβολή των δημοσιευμένων περιθωρίων στα ${n} διαδοχικά στιγμιότυπα.`,
  play:'Αναπαραγωγή χρονοσειράς',
  dsub:(i,n,a,b)=>`Στιγμιότυπο ${i} από ${n} · αρχείο ${a} έως ${b}`,
  f_search:'Υποσταθμός, περιφερειακή ενότητα ή ΚΥΤ…',
  f_icons:'Εικονίδιο ΔΕΔΔΗΕ', f_green:'Πράσινο', f_orange:'Πορτοκαλί', f_red:'Κόκκινο',
  f_constraints:'Περιορισμοί', f_hidden:'Μόνο μη ορατός κορεσμός (ζώνη ΑΔΜΗΕ)',
  f_locked:'Μόνο δέσμευση από βραχυκύκλωση', f_changed:'Μόνο όσοι μεταβλήθηκαν στο αρχείο',
  f_siting:'Χωροθέτηση', f_gentle:'Ήπιο ανάγλυφο (μέση κλίση < 10°)',
  f_rural:'Εκτός πυκνού αστικού ιστού', f_urban:'Μόνο αστικοί (υποψήφιοι για αποθήκευση)',
  f_min:'Ελάχιστο περιθώριο', f_allregions:'Όλες οι περιφερειακές ενότητες',
  l_zones:'Κορεσμένες ζώνες ΑΔΜΗΕ/ΡΑΑΕΥ', l_light:'Ανοιχτό υπόβαθρο',
  log_onlyflip:'Μόνο μεταβολές εικονιδίου',
  log_empty:'Καμία μεταβολή με τα τρέχοντα φίλτρα.',
  count:(n,tot,acc,u)=>`<b>${n}</b> υποσταθμοί στον χάρτη (από ${tot}) · <b>${acc}</b> ${u}`,
  u_locked:'MVA δεσμευμένα', u_change:'MVA συνολικής μεταβολής', u_green:'MVA σε διαθέσιμους',
  bar:(g,o,r,t)=>`Μετασχηματιστές: ${g} πράσινοι · ${o} πορτοκαλί · ${r} κόκκινοι (${t})`,
  help_t:'Πώς διαβάζεται: τα δύο όρια',
  help_b:`<p>Για να συνδεθεί ένα έργο, ο υποσταθμός πρέπει να περάσει <b>δύο ανεξάρτητους
    ελέγχους</b>. Ο ΔΕΔΔΗΕ δημοσιεύει και τους δύο.</p>
    <p><b>Θερμικό όριο.</b> Πόση ισχύ αντέχει να μεταφέρει ο εξοπλισμός στη διαρκή λειτουργία
    χωρίς υπερθέρμανση. Όταν εξαντληθεί, απαιτείται ενίσχυση εξοπλισμού, δηλαδή νέος
    μετασχηματιστής ή νέα γραμμή.</p>
    <p><b>Όριο βραχυκύκλωσης.</b> Δεν αφορά την κανονική λειτουργία, αλλά τη συνθήκη σφάλματος.
    Σε βραχυκύκλωμα, όλες οι συνδεδεμένες πηγές τροφοδοτούν το σημείο του σφάλματος. Οι
    διακόπτες ισχύος έχουν ονομαστική ικανότητα διακοπής, και κάθε νέα μονάδα αυξάνει τη στάθμη
    βραχυκύκλωσης. Όταν η στάθμη πλησιάσει την ικανότητα των διακοπτών, δεν χορηγούνται νέες
    συνδέσεις, ανεξάρτητα από το αν απομένει θερμική ικανότητα.</p>
    <p><b>Διαθέσιμο περιθώριο</b> είναι το μικρότερο από τα δύο. Ο χάρτης δείχνει ποιο από τα
    δύο δεσμεύει σε κάθε μετασχηματιστή (στήλη «δεσμεύει»), επειδή τα μέτρα αντιμετώπισης
    διαφέρουν: το θερμικό όριο απαιτεί νέο εξοπλισμό, ενώ η στάθμη βραχυκύκλωσης αντιμετωπίζεται
    και με πηνία περιορισμού, διαχωρισμό ζυγών ή διακόπτες υψηλότερης ικανότητας.</p>
    <p>Τα μεγέθη παρατίθενται όπως δημοσιεύονται από τον ΔΕΔΔΗΕ. Η μεθοδολογία υπολογισμού τους
    δεν δημοσιεύεται.</p>`,
  /* headlines */
  h_space_cap:n=>`διαθέσιμα σε ${n} υποσταθμούς με πράσινο εικονίδιο`,
  h_space_note:(n,mva)=>`Το μέγεθος του κύκλου είναι ανάλογο των διαθέσιμων MVA. Η κατάταξη με
    κριτήριο μόνο τα MVA είναι παραπλανητική: <b>${n}</b> από τους υποσταθμούς με τα μεγαλύτερα
    περιθώρια (<b>${mva} MVA</b>) βρίσκονται σε αστικό ιστό, όπου δεν υπάρχει διαθέσιμη έκταση
    για σταθμό ΑΠΕ, είναι όμως κατάλληλοι για συστήματα αποθήκευσης.`,
  k_green:'υποσταθμοί με διαθέσιμο περιθώριο', k_red:'πλήρως κορεσμένοι',
  k_hidden:'περιπτώσεις μη ορατού κορεσμού',
  k_lockshut:'MVA δεσμευμένα σε φαινομενικά κορεσμένους',
  h_locked_cap:n=>`θερμική ικανότητα πίσω από μηδενικό περιθώριο βραχυκύκλωσης, σε ${n} υποσταθμούς`,
  h_locked_note:(mva,n)=>`Τα <b>${mva} MVA</b> εξ αυτών αντιστοιχούν σε <b>${n}</b> υποσταθμούς
    που εμφανίζονται πλήρως κορεσμένοι. Πανελλαδικά η στάθμη βραχυκύκλωσης αποτελεί τον
    δεσμευτικό περιορισμό στο <b>61%</b> των μετασχηματιστών, περιορισμός διαφορετικής φύσης
    από την ανάγκη ενίσχυσης εξοπλισμού.`,
  k_locksites:'υποσταθμοί με δεσμευμένη ικανότητα',
  k_shutsites:'εξ αυτών, φαινομενικά κορεσμένοι',
  k_shutmva:'MVA στους φαινομενικά κορεσμένους', k_avg:'MVA μέση τιμή ανά υποσταθμό',
  h_hidden_big:n=>`${n} <small style="font-size:13px;color:var(--dim)">υποσταθμοί</small>`,
  h_hidden_cap:'διαθέσιμοι κατά ΔΕΔΔΗΕ εντός κορεσμένης ζώνης ΑΔΜΗΕ',
  h_hidden_note:(mva,n)=>`Εμφανίζονται ως διαθέσιμοι χωρίς να είναι αξιοποιήσιμοι:
    <b>${mva} MVA</b> φαινομενικής ικανότητας. Οι <b>${n}</b> τεκμηριώνονται με ρητή απόφαση
    ΡΑΕ/ΦΕΚ· οι υπόλοιποι με εξαντλημένο ηλεκτρικό χώρο κατά το ΔΠΑ του ΑΔΜΗΕ.`,
  k_fek:'με ρητή απόφαση/ΦΕΚ', k_oper:'με επιχειρησιακή μόνο τεκμηρίωση',
  k_hidmva:'MVA φαινομενικής ικανότητας', k_prefs:'περιφερειακές ενότητες σε ζώνη',
  h_change_big:(d,n)=>`${d} <small style="font-size:13px;color:var(--dim)">MVA σε ${n} στιγμιότυπα</small>`,
  h_change_cap:(a,b)=>`συνολικό διαθέσιμο περιθώριο: ${a} → ${b} MVA`,
  h_change_note:(c,f)=>`Τα δημοσιευμένα περιθώρια μεταβάλλονται ελάχιστα: <b>${c}</b> μεταβολές,
    εκ των οποίων <b>${f}</b> μετέβαλαν το εικονίδιο. Συνεπώς η αξία του ημερήσιου αρχείου δεν
    έγκειται στην τάση, αλλά στον έγκαιρο εντοπισμό των σπάνιων γεγονότων απελευθέρωσης
    ικανότητας.`,
  k_flips:'μεταβολές εικονιδίου', k_adj:'αναπροσαρμογές τιμών',
  k_moved:'υποσταθμοί με μεταβολή σε Μ/Σ', k_snaps:'στιγμιότυπα στο αρχείο',
  /* legend */
  lg_space:'Διαθέσιμο περιθώριο', lg_space_g:'≥ 1,4 MVA: επιτρέπεται σύνδεση',
  lg_space_o:'οριακό (< 1,4 MVA)', lg_space_r:'μηδενικό περιθώριο',
  lg_space_f:'Χρώμα και μέγεθος από τον μετασχηματιστή με το μεγαλύτερο περιθώριο, δηλαδή το '
    +'σημείο στο οποίο πραγματοποιείται η σύνδεση.',
  lg_lock:'Δέσμευση από βραχυκύκλωση',
  lg_lock_1:'θερμική ικανότητα πίσω από μηδενικό περιθώριο β/κ',
  lg_rest:'λοιποί υποσταθμοί',
  lg_lock_f:'Μέγεθος ανάλογο των δεσμευμένων MVA. Το θερμικό όριο απαιτεί ενίσχυση εξοπλισμού· '
    +'η στάθμη βραχυκύκλωσης αντιμετωπίζεται και με τεχνικά μέτρα.',
  lg_hid:'Μη ορατός κορεσμός',
  lg_hid_1:'διαθέσιμος κατά ΔΕΔΔΗΕ, εντός κορεσμένης ζώνης ΑΔΜΗΕ',
  lg_hid_f:'Έντονο περίγραμμα: ρητή απόφαση ή ΦΕΚ. Οι σκιασμένες περιοχές είναι οι κορεσμένες '
    +'ζώνες μεταφοράς.',
  lg_chg:'Μεταβολές στο αρχείο', lg_chg_up:'αύξηση περιθωρίου', lg_chg_dn:'μείωση περιθωρίου',
  lg_chg_fl:'μεταβολή εικονιδίου', lg_chg_no:'χωρίς μεταβολή',
  lg_chg_f:'Μέγεθος ανάλογο της μεταβολής από το πρώτο στιγμιότυπο έως την επιλεγμένη ημερομηνία.',
  /* popup */
  p_avail:(n,red)=>`MVA διαθέσιμα (μέγιστο μεταξύ ${n} Μ/Σ${red?`, ${red} κορεσμένοι`:''})`,
  p_since:d=>`έναντι ${d}`, p_flat:d=>`χωρίς μεταβολή από ${d}`,
  p_lock:mva=>`<b>${mva} MVA θερμικής ικανότητας</b> πίσω από μηδενικό περιθώριο βραχυκύκλωσης.
    Δεν απαιτείται ενίσχυση εξοπλισμού· δεσμευτικός είναι ο περιορισμός στάθμης βραχυκύκλωσης,
    ο οποίος αντιμετωπίζεται με τεχνικά μέτρα (πηνία περιορισμού, διαχωρισμός ζυγών,
    συμπεριφορά ρεύματος σφάλματος των αντιστροφέων).`,
  p_hid:(z,c)=>`<b>Μη ορατός κορεσμός.</b> Ο ΔΕΔΔΗΕ δημοσιεύει διαθέσιμο περιθώριο, ωστόσο ο
    υποσταθμός εντάσσεται σε κορεσμένη ζώνη ΑΔΜΗΕ: <b>${z}</b> (τεκμηρίωση: ${c})`,
  p_zone:(z,c)=>`Ζώνη ΑΔΜΗΕ: ${z} (${c})`,
  p_slope:v=>`μέση κλίση ${v}°`, p_urban:'αστικός', p_agg:'συνδυασμένο', p_fed:'ανάντη',
  th_tx:'Μετασχηματιστής', th_mva:'MVA', th_res:'ΑΠΕ', th_th:'θερμ.', th_sc:'β/κ',
  th_av:'διαθ.', th_bind:'δεσμεύει', th_hist:'ιστορικό',
  bind_sc:'β/κ', bind_th:'θερμ.',
  tt_locked:mva=>`${mva} MVA δεσμευμένα`,
  zone_tt:(n,z,c)=>`<b>${n}</b><br>${z} · τεκμηρίωση: ${c}`,
  foot:`<b style="color:var(--dim)">Ορισμοί.</b> Διαθέσιμο περιθώριο: min(θερμικό περιθώριο,
    περιθώριο βραχυκύκλωσης) στον μετασχηματιστή με τη μεγαλύτερη τιμή, σε MVA όπως
    δημοσιεύονται από τον ΔΕΔΔΗΕ, χωρίς υπόθεση συντελεστή ισχύος. Δέσμευση από βραχυκύκλωση:
    θερμική ικανότητα πίσω από μηδενικό περιθώριο βραχυκύκλωσης. Οι γραμμές «ΣΥΝΟΛΟ» δίνουν το
    συνδυασμένο περιθώριο ζεύγους μετασχηματιστών και προσμετρώνται μία φορά, καθώς αποτελούν
    τη μοναδική γραμμή των 23 αυτών υποσταθμών.<br><br>
    <b style="color:var(--dim)">Πηγές.</b> ΔΕΔΔΗΕ WebAPE (ημερήσιο αρχείο) · ΑΔΜΗΕ/ΡΑΑΕΥ
    κορεσμένα δίκτυα · OpenStreetMap (τοπολογία ΚΥΤ) · Copernicus DEM (κλίση). Ο αστικός
    χαρακτηρισμός αποτελεί προσέγγιση βάσει περιφερειακής ενότητας, έως την ενσωμάτωση
    δεδομένων χρήσης γης (CORINE).`,
},
en: {
  title:'GridGap: available connection capacity in the Greek distribution grid',
  lede:'Available connection capacity per substation, and identification of the cases where '
    +'published saturation does not correspond to an actual physical limit of the network.',
  tag:(a,b,c)=>`${a} substations · ${b} transformers · ${c} snapshots`,
  na:'n/a',
  sec_modes:'Analysis view', sec_number:'Headline figure', sec_date:'Snapshot date',
  sec_filters:'Filters', sec_layers:'Map layers', sec_log:'Change log',
  m_space:'Available capacity',
  m_space_d:'Connection capacity per substation in MVA, as published by HEDNO (ΔΕΔΔΗΕ).',
  m_locked:'Short-circuit constraint',
  m_locked_d:'Substations with zero published margin that still retain thermal capacity; the '
    +'binding limit is the fault level.',
  m_hidden:'Non-visible saturation',
  m_hidden_d:'Available per HEDNO, but inside a saturated IPTO (ΑΔΜΗΕ) transmission zone '
    +'("hidden reds").',
  m_change:'Change over time',
  m_change_d:n=>`Movement of published margins across the ${n} successive snapshots.`,
  play:'Play the time series',
  dsub:(i,n,a,b)=>`Snapshot ${i} of ${n} · archive ${a} to ${b}`,
  f_search:'Substation, regional unit or KYT…',
  f_icons:'HEDNO icon', f_green:'Green', f_orange:'Orange', f_red:'Red',
  f_constraints:'Constraints', f_hidden:'Non-visible saturation only (IPTO zone)',
  f_locked:'Short-circuit-constrained only', f_changed:'Changed in the archive only',
  f_siting:'Siting', f_gentle:'Gentle terrain (mean slope < 10°)',
  f_rural:'Outside dense urban fabric', f_urban:'Urban only (storage candidates)',
  f_min:'Minimum margin', f_allregions:'All regional units',
  l_zones:'Saturated IPTO/RAAEY zones', l_light:'Light basemap',
  log_onlyflip:'Icon changes only',
  log_empty:'No changes under the current filters.',
  count:(n,tot,acc,u)=>`<b>${n}</b> substations on the map (of ${tot}) · <b>${acc}</b> ${u}`,
  u_locked:'MVA constrained', u_change:'MVA of total movement', u_green:'MVA at available sites',
  bar:(g,o,r,t)=>`Transformers: ${g} green · ${o} orange · ${r} red (${t})`,
  help_t:'How to read this: the two limits',
  help_b:`<p>For a project to connect, the substation must pass <b>two independent checks</b>.
    HEDNO publishes both.</p>
    <p><b>Thermal limit.</b> How much power the equipment can carry continuously without
    overheating. Once exhausted, it requires equipment reinforcement: a new transformer or a
    new line.</p>
    <p><b>Short-circuit (fault-level) limit.</b> This concerns the fault condition, not normal
    operation. During a short circuit, every connected source feeds the fault point. Circuit
    breakers have a rated breaking capacity, and each new unit raises the fault level. Once the
    level approaches breaker capacity, no further connections are granted, regardless of any
    thermal capacity that remains.</p>
    <p>The <b>available margin</b> is the lower of the two. The map shows which one binds at
    each transformer (the "binds" column), because the remedies differ: a thermal limit requires
    new equipment, whereas a fault-level limit can also be addressed with current-limiting
    reactors, busbar splitting or higher-rated breakers.</p>
    <p>All figures are reproduced as published by HEDNO. The methodology behind their
    calculation is not published.</p>`,
  h_space_cap:n=>`available across ${n} substations with a green icon`,
  h_space_note:(n,mva)=>`Circle size is proportional to available MVA. Ranking on MVA alone is
    misleading: <b>${n}</b> of the substations with the largest margins (<b>${mva} MVA</b>) are
    in urban fabric, where no land is available for a generation plant, although they are well
    suited to storage.`,
  k_green:'substations with available margin', k_red:'fully saturated',
  k_hidden:'cases of non-visible saturation',
  k_lockshut:'MVA constrained at apparently saturated sites',
  h_locked_cap:n=>`thermal capacity behind a zero short-circuit margin, at ${n} substations`,
  h_locked_note:(mva,n)=>`<b>${mva} MVA</b> of that figure sits at <b>${n}</b> substations that
    appear fully saturated. Nationally the fault level is the binding constraint on <b>61%</b>
    of transformers, a constraint of a different nature from the need to reinforce equipment.`,
  k_locksites:'substations with constrained capacity',
  k_shutsites:'of those, apparently saturated',
  k_shutmva:'MVA at apparently saturated sites', k_avg:'MVA mean per substation',
  h_hidden_big:n=>`${n} <small style="font-size:13px;color:var(--dim)">substations</small>`,
  h_hidden_cap:'available per HEDNO, inside a saturated IPTO zone',
  h_hidden_note:(mva,n)=>`They appear available without being usable: <b>${mva} MVA</b> of
    apparent capacity. <b>${n}</b> are supported by an explicit RAE decision published in the
    Government Gazette; the remainder by electrical space exhausted under the IPTO ten-year
    development plan.`,
  k_fek:'with explicit decision or Gazette', k_oper:'with operational evidence only',
  k_hidmva:'MVA of apparent capacity', k_prefs:'regional units within a zone',
  h_change_big:(d,n)=>`${d} <small style="font-size:13px;color:var(--dim)">MVA over ${n} snapshots</small>`,
  h_change_cap:(a,b)=>`total available margin: ${a} → ${b} MVA`,
  h_change_note:(c,f)=>`Published margins move very little: <b>${c}</b> changes, of which
    <b>${f}</b> altered an icon. The value of the daily archive therefore lies not in the trend,
    but in the timely identification of the rare capacity-release events.`,
  k_flips:'icon changes', k_adj:'value readjustments',
  k_moved:'substations with a transformer change', k_snaps:'snapshots in the archive',
  lg_space:'Available margin', lg_space_g:'≥ 1.4 MVA: connection permitted',
  lg_space_o:'marginal (< 1.4 MVA)', lg_space_r:'zero margin',
  lg_space_f:'Colour and size follow the transformer with the largest margin, that is, the '
    +'point at which the connection is made.',
  lg_lock:'Short-circuit constraint',
  lg_lock_1:'thermal capacity behind a zero fault-level margin',
  lg_rest:'other substations',
  lg_lock_f:'Size is proportional to constrained MVA. A thermal limit requires equipment '
    +'reinforcement; a fault-level limit can also be addressed by technical measures.',
  lg_hid:'Non-visible saturation',
  lg_hid_1:'available per HEDNO, inside a saturated IPTO zone',
  lg_hid_f:'Bright outline: explicit decision or Gazette. Shaded areas are the saturated '
    +'transmission zones.',
  lg_chg:'Archive changes', lg_chg_up:'margin increased', lg_chg_dn:'margin decreased',
  lg_chg_fl:'icon changed', lg_chg_no:'no change',
  lg_chg_f:'Size is proportional to the change from the first snapshot to the selected date.',
  p_avail:(n,red)=>`MVA available (maximum across ${n} transformer${n===1?'':'s'}${red?`, ${red} saturated`:''})`,
  p_since:d=>`relative to ${d}`, p_flat:d=>`no change since ${d}`,
  p_lock:mva=>`<b>${mva} MVA of thermal capacity</b> behind a zero short-circuit margin. No
    equipment reinforcement is required; the binding constraint is the fault level, which can be
    addressed by technical measures (current-limiting reactors, busbar splitting, inverter
    fault-current behaviour).`,
  p_hid:(z,c)=>`<b>Non-visible saturation.</b> HEDNO publishes an available margin, yet the
    substation lies within a saturated IPTO zone: <b>${z}</b> (evidence: ${c})`,
  p_zone:(z,c)=>`IPTO zone: ${z} (${c})`,
  p_slope:v=>`mean slope ${v}°`, p_urban:'urban', p_agg:'combined', p_fed:'fed by',
  th_tx:'Transformer', th_mva:'MVA', th_res:'RES', th_th:'thermal', th_sc:'s/c',
  th_av:'avail.', th_bind:'binds', th_hist:'history',
  bind_sc:'s/c', bind_th:'thermal',
  tt_locked:mva=>`${mva} MVA constrained`,
  zone_tt:(n,z,c)=>`<b>${n}</b><br>${z} · evidence: ${c}`,
  foot:`<b style="color:var(--dim)">Definitions.</b> Available margin: min(thermal margin,
    short-circuit margin) at the transformer with the highest value, in MVA exactly as published
    by HEDNO, with no assumed power factor. Short-circuit constraint: thermal capacity behind a
    zero fault-level margin. "ΣΥΝΟΛΟ" rows give the combined margin of a transformer pair and
    are counted once, as they are the only row published for those 23 substations. Substation,
    regional unit and KYT names are retained in Greek, being the published identifiers.<br><br>
    <b style="color:var(--dim)">Sources.</b> HEDNO WebAPE (daily archive) · IPTO/RAAEY saturated
    networks · OpenStreetMap (KYT topology) · Copernicus DEM (slope). The urban flag is a
    regional-unit approximation pending integration of land-use data (CORINE).`,
},
};
const t = (k, ...a) => { const v = I18N[lang][k]; return typeof v === 'function' ? v(...a) : v; };
const fmt = v => v == null ? t('na')
  : v.toLocaleString(lang === 'el' ? 'el-GR' : 'en-GB', {maximumFractionDigits:1});
// Greek-friendly search key: drop accents and fold final sigma (toLowerCase turns
// a trailing Σ into ς, so "ΛΑΡΙΣ" would otherwise never match "ΛΑΡΙΣΑ").
const norm = s => (s||'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'')
  .replace(/ς/g,'σ');

const MODES = ['space','locked','hidden','change'];

const map = L.map('map', {zoomControl:false, preferCanvas:true}).setView([38.4, 24.2], 7);
L.control.zoom({position:'topright'}).addTo(map);
const TA = '© OpenStreetMap, © CARTO';
const dark = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  {attribution:TA, maxZoom:19}).addTo(map);
const light = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
  {attribution:TA, maxZoom:19});
const zoneLayer = L.geoJSON(D.zones, {
  style: f => ({color:CONF[f.properties.conf], fillColor:CONF[f.properties.conf],
                weight:1, fillOpacity:.15}),
  onEachFeature: (f,l) => l.bindTooltip(() =>
    t('zone_tt', f.properties.name, f.properties.zone, f.properties.conf))
}).addTo(map);
const dim = L.layerGroup().addTo(map);      // context markers (de-emphasised)
const main = L.layerGroup().addTo(map);     // the markers this mode is about

const changedSubs = new Set(D.changes.map(c => c.s));
const flipSubs = new Set(D.changes.filter(c => c.flip).map(c => c.s));

/* ---------- popup ---------- */
function spark(tx){
  const pts = tx.h.map(e => e && e[0]!=null && e[1]!=null ? Math.min(e[0],e[1]) : null);
  const ok = pts.filter(v => v!=null);
  if(ok.length < 2) return '';
  const mx = Math.max(...ok, 1), n = pts.length, w = 62, h = 15;
  const flat = Math.max(...ok) - Math.min(...ok) < .05;
  const d = pts.map((v,i) => v==null ? null :
    `${(i/(n-1)*w).toFixed(1)},${(h - v/mx*(h-2) - 1).toFixed(1)}`).filter(Boolean).join(' L');
  return `<svg width="${w}" height="${h}" style="vertical-align:middle"><path d="M${d}"
    fill="none" stroke="${flat?'#3c4757':'#5b9bff'}" stroke-width="1.6"
    stroke-linejoin="round"/></svg>`;
}

function popup(s){
  const [mv, ic, nred, locked] = s.s[di];
  const rows = s.tx.map(tx => {
    const e = tx.h[di];
    const m = e && e[0]!=null && e[1]!=null ? Math.min(e[0],e[1]) : null;
    const bind = e && e[0]!=null && e[1]!=null
      ? (e[1] < e[0] ? `<span class="pill" style="color:${LOCK}">${t('bind_sc')}</span>`
                     : `<span class="pill">${t('bind_th')}</span>`) : '';
    return `<tr><td>${tx.n}${tx.agg?` <span class="pill">${t('p_agg')}</span>`:''}</td>
      <td>${fmt(tx.tot)}</td><td>${fmt(tx.ape)}</td>
      <td>${e?fmt(e[0]):t('na')}</td><td>${e?fmt(e[1]):t('na')}</td>
      <td style="color:${C[e?e[2]:'?']};font-weight:650">${fmt(m)}</td>
      <td>${bind}</td><td>${spark(tx)}</td></tr>`;
  }).join('');
  const first = s.s[0][0];
  const moved = first!=null && mv!=null && Math.abs(mv-first) > .05;
  return `<div class="pop"><h3>${s.n}</h3>
    <div class="meta">${s.pf||t('na')} · ${t('p_fed')} ${s.kyt||t('na')}${s.kd?` (${s.kd} km)`:''}
      ${s.sl!=null?` · ${t('p_slope', s.sl)}`:''}${s.urb?` · ${t('p_urban')}`:''}</div>
    <div class="big"><span class="dot" style="background:${C[ic]};margin-right:6px"></span>${fmt(mv)}
      <small>${t('p_avail', s.tx.length, nred)}</small></div>
    ${moved ? `<div class="meta ${mv>first?'up':'dn'}">${mv>first?'+':''}${fmt(mv-first)} MVA
       ${t('p_since', D.dates[0])}</div>` : `<div class="meta">${t('p_flat', D.dates[0])}</div>`}
    ${locked>0 ? `<div class="callout c-lock">${t('p_lock', fmt(locked))}</div>` : ''}
    ${s.hr ? `<div class="callout c-hid">${t('p_hid', s.z, s.zc)}
       ${s.zd?`<div class="meta" style="margin-top:3px">${s.zd}</div>`:''}</div>`
      : (s.z ? `<div class="meta" style="margin-top:7px">${t('p_zone', s.z, s.zc)}</div>` : '')}
    <table><tr><th>${t('th_tx')}</th><th>${t('th_mva')}</th><th>${t('th_res')}</th>
      <th>${t('th_th')}</th><th>${t('th_sc')}</th><th>${t('th_av')}</th>
      <th>${t('th_bind')}</th><th>${t('th_hist')}</th></tr>${rows}</table></div>`;
}

/* ---------- filtering ---------- */
let ICONS = new Set(['G','O','R']);
const F = {hr:false, lk:false, ch:false, gt:false, ru:false, ur:false, min:0, reg:'', q:''};

function passes(s){
  const [mv, ic, , locked] = s.s[di];
  if(!ICONS.has(ic)) return false;
  if(F.hr && !s.hr) return false;
  if(F.lk && !(locked > 0)) return false;
  if(F.ch && !changedSubs.has(s.n)) return false;
  if(F.gt && !(s.sl != null && s.sl < 10)) return false;
  if(F.ru && s.urb) return false;
  if(F.ur && !s.urb) return false;
  if((mv||0) < F.min) return false;
  if(F.reg && s.pf !== F.reg) return false;
  if(F.q && !norm(s.n+' '+(s.pf||'')+' '+(s.kyt||'')).includes(F.q)) return false;
  return true;
}

/* ---------- drawing ---------- */
const R = (v, k) => 4 + Math.min(9, Math.sqrt(Math.max(v,0)) * k);

function draw(){
  main.clearLayers(); dim.clearLayers();
  let n = 0, acc = 0;

  D.subs.filter(passes).forEach(s => {
    const [mv, ic, , locked] = s.s[di];
    const first = s.s[0][0], delta = (first!=null && mv!=null) ? mv-first : 0;
    let star = false, color = C[ic], r = R(mv||0, 1.0), w = 1, stroke = color;

    if(mode === 'locked'){
      star = locked > 0;
      if(star){ color = LOCK; stroke = '#c9aaff'; r = R(locked, 1.0); }
    } else if(mode === 'hidden'){
      star = s.hr;
      if(star){ color = C.G; stroke = s.zc==='high' ? '#ff5c4d' : '#e0894a'; w = 2.5; }
    } else if(mode === 'change'){
      star = changedSubs.has(s.n) && Math.abs(delta) > .05;
      if(star){ color = delta > 0 ? C.G : C.R; r = R(Math.abs(delta)*6, 1.0);
                stroke = flipSubs.has(s.n) ? LOCK : color; w = flipSubs.has(s.n) ? 2.5 : 1; }
    } else star = true;

    const m = L.circleMarker([s.la, s.lo], star
      ? {radius:r, color:stroke, fillColor:color, fillOpacity:.85, weight:w}
      : {radius:2.5, color:'#37414f', fillColor:'#2a323d', fillOpacity:.55, weight:0});
    m.bindPopup(() => popup(s), {maxWidth:520, minWidth:330});
    m.bindTooltip(() => `<b>${s.n}</b>: ${fmt(mv)} MVA`
      + (locked>0 ? ` · ${t('tt_locked', fmt(locked))}` : ''),
      {direction:'top', offset:[0,-4]});
    (star ? main : dim).addLayer(m);
    if(star){ n++; acc += mode==='locked' ? locked
      : mode==='change' ? Math.abs(delta) : (ic==='G' ? (mv||0) : 0); }
  });

  const unit = mode==='locked' ? t('u_locked') : mode==='change' ? t('u_change') : t('u_green');
  document.getElementById('count').innerHTML = t('count', n, D.subs.length, fmt(acc), unit);
  readout();
}

/* ---------- headline + legend per mode ---------- */
const kpi = (v,l) => `<div class="kpi"><b>${v}</b><span>${l}</span></div>`;
const MVA = v => `${v} <small style="font-size:13px;color:var(--dim)">MVA</small>`;

function readout(){
  const cur = {G:0,O:0,R:0,'?':0}, tx = {G:0,O:0,R:0};
  // locked = thermal headroom behind β/κ=0, anywhere; "shut" = the subset in
  // substations that show no usable margin at all, i.e. look entirely closed.
  let green = 0, locked = 0, lsites = 0, shut = 0, ssites = 0;
  D.subs.forEach(s => {
    const [mv, ic, , lk] = s.s[di];
    cur[ic] = (cur[ic]||0)+1;
    if(ic === 'G') green += mv||0;
    if(lk > 0){ locked += lk; lsites++; if(ic === 'R'){ shut += lk; ssites++; } }
    s.tx.forEach(x => { const e = x.h[di]; if(e) tx[e[2]] = (tx[e[2]]||0)+1; });
  });

  const H = document.getElementById('hbig'), Cp = document.getElementById('hcap'),
        N = document.getElementById('hnote'), K = document.getElementById('kpis');
  if(mode === 'locked'){
    H.innerHTML = MVA(fmt(locked));
    Cp.textContent = t('h_locked_cap', lsites);
    N.innerHTML = t('h_locked_note', fmt(shut), ssites);
    K.innerHTML = kpi(lsites, t('k_locksites')) + kpi(ssites, t('k_shutsites'))
      + kpi(fmt(shut), t('k_shutmva')) + kpi(fmt(locked/Math.max(lsites,1)), t('k_avg'));
  } else if(mode === 'hidden'){
    H.innerHTML = t('h_hidden_big', S.hidden);
    Cp.textContent = t('h_hidden_cap');
    N.innerHTML = t('h_hidden_note', fmt(S.hidden_mva), S.hidden_high);
    K.innerHTML = kpi(S.hidden_high, t('k_fek')) + kpi(S.hidden - S.hidden_high, t('k_oper'))
      + kpi(fmt(S.hidden_mva), t('k_hidmva')) + kpi(D.zones.features.length, t('k_prefs'));
  } else if(mode === 'change'){
    H.innerHTML = t('h_change_big',
      `${S.nat_delta > 0 ? '+' : ''}${fmt(S.nat_delta)}`, D.dates.length);
    Cp.textContent = t('h_change_cap', fmt(S.nat_first), fmt(S.nat_last));
    N.innerHTML = t('h_change_note', S.changes, S.flips);
    K.innerHTML = kpi(S.flips, t('k_flips')) + kpi(S.changes - S.flips, t('k_adj'))
      + kpi(changedSubs.size, t('k_moved')) + kpi(D.dates.length, t('k_snaps'));
  } else {
    H.innerHTML = MVA(fmt(green));
    Cp.textContent = t('h_space_cap', cur.G);
    N.innerHTML = t('h_space_note', S.urban_sites, fmt(S.urban_mva));
    K.innerHTML = kpi(cur.G, t('k_green')) + kpi(cur.R, t('k_red'))
      + kpi(S.hidden, t('k_hidden')) + kpi(fmt(S.locked_mva), t('k_lockshut'));
  }

  const tot = tx.G+tx.O+tx.R || 1;
  document.getElementById('bar').innerHTML = ['G','O','R']
    .map(k => `<div style="width:${tx[k]/tot*100}%;background:${C[k]}"></div>`).join('');
  document.getElementById('barlab').textContent = t('bar', tx.G, tx.O, tx.R, tot);
  legend();
}

function legend(){
  const T = document.getElementById('legtitle'), B = document.getElementById('legbody'),
        Fo = document.getElementById('legfoot');
  const li = (sw,txt) => `<div class="li">${sw}<span>${txt}</span></div>`;
  const d = c => `<span class="dot" style="background:${c}"></span>`;
  const rest = li('<span class="dot" style="background:#2a323d"></span>', t('lg_rest'));
  if(mode === 'locked'){
    T.textContent = t('lg_lock');
    B.innerHTML = li(d(LOCK), t('lg_lock_1')) + rest;
    Fo.textContent = t('lg_lock_f');
  } else if(mode === 'hidden'){
    T.textContent = t('lg_hid');
    B.innerHTML = li('<span class="ring"></span>', t('lg_hid_1')) + rest;
    Fo.textContent = t('lg_hid_f');
  } else if(mode === 'change'){
    T.textContent = t('lg_chg');
    B.innerHTML = li(d(C.G), t('lg_chg_up')) + li(d(C.R), t('lg_chg_dn'))
      + li(`<span class="ring" style="border-color:${LOCK}"></span>`, t('lg_chg_fl'))
      + li('<span class="dot" style="background:#2a323d"></span>', t('lg_chg_no'));
    Fo.textContent = t('lg_chg_f');
  } else {
    T.textContent = t('lg_space');
    B.innerHTML = li(d(C.G), t('lg_space_g')) + li(d(C.O), t('lg_space_o'))
      + li(d(C.R), t('lg_space_r'));
    Fo.textContent = t('lg_space_f');
  }
}

/* ---------- change log ---------- */
function renderLog(){
  const only = document.getElementById('onlyflip').checked;
  const list = D.changes.filter(c => !only || c.flip).slice().reverse();
  document.getElementById('chg').innerHTML = list.map(c => {
    const cls = (c.ub!=null && c.ua!=null) ? (c.ub>c.ua ? 'up' : (c.ub<c.ua ? 'dn' : '')) : '';
    return `<div class="ch"><div class="t"><b>${c.s}</b><span class="pill">${c.d}</span></div>
      <div class="m">${c.tx}</div>
      <div><span class="${cls}">${fmt(c.ua)} → ${fmt(c.ub)} MVA</span>
      ${c.flip?` <span class="pill flip">${c.ia} → ${c.ib}</span>`:''}</div></div>`;
  }).join('') || `<div class="ch m">${t('log_empty')}</div>`;
}

/* ---------- language ---------- */
function applyLang(){
  document.documentElement.lang = lang;
  document.title = t('title');
  document.querySelectorAll('[data-i18n]').forEach(el => el.innerHTML = t(el.dataset.i18n));
  document.querySelectorAll('[data-i18n-ph]').forEach(el => el.placeholder = t(el.dataset.i18nPh));
  document.querySelectorAll('#lang button').forEach(b =>
    b.classList.toggle('on', b.dataset.l === lang));
  document.getElementById('tag').textContent = t('tag', S.subs, S.tx, S.dates);
  document.getElementById('play').title = t('play');
  document.getElementById('mlab').textContent = F.min + ' MVA';
  document.getElementById('modes').innerHTML = MODES.map(k =>
    `<div class="mode${k===mode?' on':''}" data-k="${k}"><span class="sw"></span>
       <div><b>${t('m_'+k)}</b><span>${t('m_'+k+'_d', D.dates.length)}</span></div></div>`).join('');
  document.querySelectorAll('.mode').forEach(el => el.onclick = () => {
    mode = el.dataset.k;
    document.querySelectorAll('.mode').forEach(x => x.classList.toggle('on', x === el));
    draw();
  });
  const sel = document.getElementById('reg'), keep = F.reg;
  sel.innerHTML = `<option value="">${t('f_allregions')}</option>`
    + REGIONS.map(r => `<option${r===keep?' selected':''}>${r}</option>`).join('');
  document.getElementById('foot').innerHTML = t('foot');
  renderLog(); setDate();
}
const REGIONS = [...new Set(D.subs.map(s => s.pf).filter(Boolean))]
  .sort((a,b) => a.localeCompare(b,'el'));
document.querySelectorAll('#lang button').forEach(b => b.onclick = () => {
  lang = b.dataset.l; localStorage.setItem('gg_lang', lang); applyLang();
});

/* ---------- wiring ---------- */
const dr = document.getElementById('date');
dr.max = D.dates.length - 1; dr.value = di;
function setDate(){
  di = +dr.value;
  document.getElementById('dlabel').textContent = D.dates[di];
  document.getElementById('dsub').textContent =
    t('dsub', di+1, D.dates.length, D.dates[0], D.dates[D.dates.length-1]);
  draw();
}
dr.oninput = setDate;
let timer = null;
document.getElementById('play').onclick = e => {
  if(timer){ clearInterval(timer); timer = null; e.target.textContent = '▶'; return; }
  e.target.textContent = '❚❚';
  timer = setInterval(() => { dr.value = (+dr.value + 1) % D.dates.length; setDate(); }, 950);
};

document.querySelectorAll('.ic').forEach(cb => cb.onchange = () => {
  ICONS = new Set([...document.querySelectorAll('.ic')].filter(x => x.checked)
    .map(x => x.value));
  draw();
});
const bind = (id, key, fn) => document.getElementById(id).oninput =
  e => { F[key] = fn(e.target); draw(); };
bind('fhr','hr', x=>x.checked); bind('flk','lk', x=>x.checked);
bind('fch','ch', x=>x.checked); bind('fgt','gt', x=>x.checked);
bind('q','q', x=>norm(x.value.trim()));
// urban / non-urban are mutually exclusive
document.getElementById('fru').oninput = e => {
  F.ru = e.target.checked;
  if(F.ru){ F.ur = false; document.getElementById('fur').checked = false; }
  draw(); };
document.getElementById('fur').oninput = e => {
  F.ur = e.target.checked;
  if(F.ur){ F.ru = false; document.getElementById('fru').checked = false; }
  draw(); };
document.getElementById('minmva').oninput = e => {
  F.min = +e.target.value;
  document.getElementById('mlab').textContent = F.min + ' MVA'; draw(); };
document.getElementById('reg').onchange = e => { F.reg = e.target.value; draw(); };
document.getElementById('lz').onchange = e =>
  e.target.checked ? zoneLayer.addTo(map) : map.removeLayer(zoneLayer);
document.getElementById('ld').onchange = e => {
  if(e.target.checked){ map.removeLayer(dark); light.addTo(map); }
  else { map.removeLayer(light); dark.addTo(map); } };
document.getElementById('onlyflip').onchange = renderLog;

applyLang();
</script></body></html>
"""


if __name__ == "__main__":
    sys.exit(main())
