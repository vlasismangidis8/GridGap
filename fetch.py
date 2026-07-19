"""
GridGap — daily fetch of DEDDIE WebAPE substation margins.

Pulls the single public REST endpoint discovered during discovery and archives
one timestamped snapshot per day. THIS IS THE ASSET: DEDDIE updates daily and
keeps no history anywhere. Every day we don't fetch is lost forever.

    GET services/views/margins?klpe=-1&klno=-1&klot=-1&status=111
    -> 454 rows (transformers), JSON, no auth.

Output:
    data/raw/margins_YYYY-MM-DD.json   (one snapshot per calendar day, UTC)
    data/raw/margins_latest.json       (convenience copy of the newest pull)
    data/raw/fetch.log                 (append-only audit trail)

Design notes:
  * Writes to a temp file first, validates, then renames. A bad pull (network
    blip, HTML error page, empty body) never clobbers a good snapshot.
  * SSL: DEDDIE's cert chain has a broken CA (Basic Constraints not marked
    critical) that OpenSSL 3.x rejects. We try verified first and fall back to
    unverified ONLY for this host, so if they ever fix the cert we upgrade
    automatically. Data is public and read-only, so the integrity risk is low.
"""

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

URL = (
    "https://apps.deddie.gr/WebAPE/services/views/margins"
    "?klpe=-1&klno=-1&klot=-1&status=111"
)
EXPECTED_KEYS = {
    "PERI_Y", "PERI_M", "ISXY_TOT", "APE_C", "ISXY_APE", "TTHP", "TPBK",
    "PERIFEREIES", "DHMOI", "SYNP", "SYNM", "MARGIN_ICON",
}
MIN_ROWS = 300  # sanity floor; today it's 454. A big drop = suspicious pull.

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)
LOG = RAW / "fetch.log"


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()}  {msg}"
    print(line)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def download() -> str:
    """Return response text. Verify TLS if possible; fall back for this host."""
    headers = {"User-Agent": "GridGap/1.0 (+data archival; contact vlmangidis@gmail.com)"}
    try:
        r = requests.get(URL, timeout=60, headers=headers)
        r.raise_for_status()
        return r.text
    except requests.exceptions.SSLError:
        log("WARN  TLS verification failed (DEDDIE broken cert chain); "
            "retrying unverified for apps.deddie.gr")
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.get(URL, timeout=60, headers=headers, verify=False)
        r.raise_for_status()
        return r.text


def validate(text: str):
    """Parse and sanity-check. Raises ValueError on anything suspicious."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"response is not JSON (got HTML error page?): {e}")
    if not isinstance(data, list):
        raise ValueError(f"expected a JSON list, got {type(data).__name__}")
    if len(data) < MIN_ROWS:
        raise ValueError(f"only {len(data)} rows (< {MIN_ROWS}); likely a bad pull")
    missing = EXPECTED_KEYS - set(data[0].keys())
    if missing:
        raise ValueError(f"row is missing expected keys: {sorted(missing)}")
    return data


def main() -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest = RAW / f"margins_{day}.json"
    latest = RAW / "margins_latest.json"

    try:
        text = download()
        data = validate(text)
    except Exception as e:
        log(f"FAIL  {type(e).__name__}: {e}")
        return 1

    # atomic-ish write: temp file -> rename (never clobber a good file with junk)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)
    latest.write_text(text, encoding="utf-8")

    flags = Counter(r.get("MARGIN_ICON") for r in data)
    log(f"OK    {dest.name}  rows={len(data)}  "
        f"flags={{G:{flags.get('Green',0)} O:{flags.get('Orange',0)} R:{flags.get('Red',0)}}}  "
        f"{len(text)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
