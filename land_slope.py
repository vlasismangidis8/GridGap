"""
GridGap — land layer v2 (first cut): terrain slope from Copernicus GLO-30 DEM.

For every substation we read a ~3 km window of the 30 m DEM (windowed COG reads
over HTTP, no full-tile download) and compute how flat the surrounding land is.
Flat land is what a solar / BESS park actually needs; steep terrain is a real
siting constraint the grid maps can't see.

Adds to data/substations.geojson:  slope_mean_deg, gentle_frac (<10°), terrain.

Note: this is terrain only. Natura2000 and CORINE land-cover are the other two
land constraints and need their own (large / access-restricted) datasets; they
are the next iteration, deliberately not faked from patchy OSM data here.
"""

import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("GDAL_HTTP_UNSAFESSL", "YES")          # proxy intercepts TLS
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("VSI_CACHE", "TRUE")

import numpy as np
import rasterio
from rasterio.windows import Window

ROOT = Path(__file__).parent
GEOJSON = ROOT / "data" / "substations.geojson"
BASE = "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com"
HALF = 50  # pixels each side of the point (~3 km window at 30 m)


def tile_url(lon, lat):
    la, lo = int(math.floor(lat)), int(math.floor(lon))
    ns = f"N{la:02d}" if la >= 0 else f"S{-la:02d}"
    ew = f"E{lo:03d}" if lo >= 0 else f"W{-lo:03d}"
    name = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"
    return name, f"/vsicurl/{BASE}/{name}/{name}.tif"


def slope_stats(ds_cache, lon, lat):
    name, url = tile_url(lon, lat)
    if name in ds_cache and ds_cache[name] is None:
        return None
    try:
        ds = ds_cache.get(name) or rasterio.open(url)
        ds_cache[name] = ds
    except Exception:
        ds_cache[name] = None
        return None
    row, col = ds.index(lon, lat)
    c0, r0 = max(0, col - HALF), max(0, row - HALF)
    w = min(2 * HALF, ds.width - c0)
    h = min(2 * HALF, ds.height - r0)
    if w < 5 or h < 5:
        return None
    dem = ds.read(1, window=Window(c0, r0, w, h)).astype(float)
    dem[dem < -1000] = np.nan
    if np.isnan(dem).all():
        return None
    xres, yres = ds.res
    mx = xres * 111320 * math.cos(math.radians(lat))
    my = yres * 111320
    dzdy, dzdx = np.gradient(dem, my, mx)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    mean = float(np.nanmean(slope))
    gentle = float(np.nanmean(slope < 10))
    return round(mean, 1), round(gentle, 2)


def terrain_class(gentle):
    if gentle is None:
        return None
    if gentle >= 0.6:
        return "gentle"
    if gentle >= 0.3:
        return "mixed"
    return "steep"


def main():
    fc = json.loads(GEOJSON.read_text(encoding="utf-8"))
    cache = {}
    done = 0
    for f in fc["features"]:
        g = f.get("geometry")
        if not g:
            f["properties"].update(slope_mean_deg=None, gentle_frac=None, terrain=None)
            continue
        lon, lat = g["coordinates"]
        st = slope_stats(cache, lon, lat)
        if st is None:
            f["properties"].update(slope_mean_deg=None, gentle_frac=None, terrain=None)
        else:
            mean, gentle = st
            f["properties"].update(slope_mean_deg=mean, gentle_frac=gentle,
                                   terrain=terrain_class(gentle))
        done += 1
        if done % 25 == 0:
            print(f"    {done}/{len(fc['features'])} …")

    GEOJSON.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")

    # summary over the buildable shortlist
    def is_urban(p):
        pr = p.get("prefecture") or ""
        return any(u in pr for u in ("Τομέα Αθηνών", "Πειραιώς",
                                     "Μητροπολιτική Ενότητα Θεσσαλονίκης"))
    build = [f["properties"] for f in fc["features"]
             if f["properties"]["deddie_icons"] == ["Green"]
             and not f["properties"]["admie_zone"] and not is_urban(f["properties"])]
    from collections import Counter
    tc = Counter(p.get("terrain") for p in build)
    print(f"[+] slope written for {done} substations")
    print(f"    buildable ({len(build)}) terrain: {dict(tc)}")
    gentle = [p for p in build if p.get("terrain") == "gentle"]
    print(f"    buildable AND gentle terrain: {len(gentle)}")


if __name__ == "__main__":
    sys.exit(main())
