"""
GridGap — DEDDIE WebAPE discovery script (NO scraping yet).

Goal: open https://apps.deddie.gr/WebAPE/index.html, accept the terms gate,
let the app load, and capture EVERY network request. Dump all JSON/XML
responses to data/raw/ and print a summary of the endpoints + data shapes so
we can understand how the site serves the substation capacity data.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

URL = "https://apps.deddie.gr/WebAPE/index.html"
ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

# Terms of use is usually a checkbox + button. We try a broad set of
# Greek/English texts and roles to click through the gate.
ACCEPT_TEXTS = [
    "Αποδοχή", "Αποδέχομαι", "Συμφωνώ", "Αποδοχή όρων",
    "Συνέχεια", "Είσοδος", "Accept", "I agree", "Agree", "Continue",
    "ΟΚ", "OK",
]

# Data-ish content types worth saving to disk.
SAVE_TYPES = ("json", "xml", "javascript", "text/plain")

captured = []  # request/response log
saved_files = []
seq = 0


def safe_name(url: str, ext: str) -> str:
    global seq
    seq += 1
    p = urlparse(url)
    stem = (p.path.strip("/").replace("/", "_") or "root")
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem)[:80]
    q = re.sub(r"[^A-Za-z0-9._-]", "_", p.query)[:40]
    return f"{seq:03d}_{stem}{('_' + q) if q else ''}.{ext}"


def looks_like_data(ctype: str, url: str) -> bool:
    ctype = (ctype or "").lower()
    if any(t in ctype for t in ("json", "xml")):
        return True
    # sometimes servers return JSON with a generic content-type
    if any(url.lower().endswith(e) for e in (".json", ".xml", ".geojson")):
        return True
    return False


def ext_for(ctype: str, url: str) -> str:
    ctype = (ctype or "").lower()
    if "json" in ctype or url.lower().endswith((".json", ".geojson")):
        return "json"
    if "xml" in ctype or url.lower().endswith(".xml"):
        return "xml"
    if "javascript" in ctype or url.lower().endswith(".js"):
        return "js"
    return "txt"


def on_response(resp):
    try:
        req = resp.request
        ctype = resp.headers.get("content-type", "")
        rec = {
            "url": resp.url,
            "method": req.method,
            "status": resp.status,
            "content_type": ctype,
            "resource_type": req.resource_type,
        }

        # Only try to read bodies for data-ish or api-ish responses to avoid
        # dumping images/fonts. Save JSON/XML always; also save small text.
        is_data = looks_like_data(ctype, resp.url)
        is_api = req.resource_type in ("xhr", "fetch")

        if is_data or is_api or any(t in (ctype or "").lower() for t in SAVE_TYPES):
            try:
                body = resp.body()
            except Exception as e:
                body = None
                rec["body_error"] = str(e)

            if body is not None:
                rec["size"] = len(body)
                if is_data or is_api:
                    fn = safe_name(resp.url, ext_for(ctype, resp.url))
                    (RAW / fn).write_bytes(body)
                    rec["saved_as"] = fn
                    saved_files.append(fn)

        captured.append(rec)
    except Exception as e:
        captured.append({"url": getattr(resp, "url", "?"), "handler_error": str(e)})


def try_accept(page) -> bool:
    """Attempt to click through the terms gate. Returns True if something clicked."""
    clicked = False
    # 1) check any checkboxes first
    try:
        boxes = page.query_selector_all("input[type=checkbox]")
        for b in boxes:
            try:
                if not b.is_checked():
                    b.check(timeout=1000)
                    clicked = True
            except Exception:
                pass
    except Exception:
        pass

    # 2) try buttons / links / inputs by text
    for txt in ACCEPT_TEXTS:
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=re.compile(txt, re.I))
                if loc.count() > 0:
                    loc.first.click(timeout=1500)
                    return True
            except Exception:
                pass
        # generic text match
        try:
            loc = page.get_by_text(re.compile(txt, re.I))
            if loc.count() > 0:
                loc.first.click(timeout=1500)
                return True
        except Exception:
            pass
        # input[type=button/submit] with value
        try:
            loc = page.locator(f"input[value*='{txt}']")
            if loc.count() > 0:
                loc.first.click(timeout=1500)
                return True
        except Exception:
            pass
    return clicked


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="el-GR")
        page = ctx.new_page()
        page.on("response", on_response)

        print(f"[*] Opening {URL}")
        page.goto(URL, wait_until="networkidle", timeout=60000)
        print(f"[*] Title: {page.title()!r}")

        # dump the pre-accept DOM for inspection
        (RAW / "_page_before_accept.html").write_text(page.content(), encoding="utf-8")

        # Known gate: check #acceptUsageTerms -> enables #continueButton ->
        # location.href='main.html'. Do it explicitly; fall back to generic.
        print("[*] Accepting terms gate...")
        try:
            page.check("#acceptUsageTerms", timeout=3000)
            page.click("#continueButton", timeout=3000)
            print("    checked acceptUsageTerms + clicked continueButton")
        except Exception as e:
            print(f"    specific gate failed ({e}); trying generic accept")
            try_accept(page)

        # wait for main.html (the actual map app) to load and fire data reqs
        try:
            page.wait_for_load_state("networkidle", timeout=45000)
        except Exception:
            pass
        page.wait_for_timeout(6000)
        print(f"[*] Now at: {page.url}")

        # a second accept attempt in case a modal appeared after load
        try_accept(page)
        page.wait_for_timeout(4000)

        (RAW / "_page_after_accept.html").write_text(page.content(), encoding="utf-8")

        # Look for iframes (the map app might live in one)
        frames = [f.url for f in page.frames]
        print(f"[*] Frames: {frames}")

        browser.close()

    # ---- write the network log ----
    (RAW / "_network_log.json").write_text(
        json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summarize()


def summarize():
    print("\n" + "=" * 70)
    print("DISCOVERY SUMMARY")
    print("=" * 70)
    print(f"Total responses captured: {len(captured)}")
    print(f"Files saved to data/raw/: {len(saved_files)}")

    xhr = [c for c in captured if c.get("resource_type") in ("xhr", "fetch")]
    print(f"\nXHR/fetch requests: {len(xhr)}")
    for c in xhr:
        print(f"  [{c.get('status')}] {c.get('method')} {c.get('content_type','')[:30]:30} {c['url']}")

    # Inspect saved data files
    print("\n--- Saved data files (shape probe) ---")
    for fn in saved_files:
        fp = RAW / fn
        try:
            raw = fp.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"  {fn}: read error {e}")
            continue
        if fn.endswith(".json"):
            try:
                data = json.loads(raw)
            except Exception:
                print(f"  {fn}: not valid JSON ({len(raw)} bytes)")
                continue
            probe_json(fn, data)
        else:
            print(f"  {fn}: {len(raw)} bytes, head: {raw[:120]!r}")


def probe_json(fn, data):
    def shape(v, depth=0):
        if isinstance(v, dict):
            return "{" + ", ".join(list(v.keys())[:15]) + ("...}" if len(v) > 15 else "}")
        if isinstance(v, list):
            return f"[list len={len(v)}]"
        return type(v).__name__

    print(f"\n  >>> {fn}")
    if isinstance(data, list):
        print(f"      top-level: list, len={len(data)}")
        if data:
            first = data[0]
            print(f"      item shape: {shape(first)}")
            if isinstance(first, dict):
                for k, v in first.items():
                    print(f"         {k}: {shape(v)} = {str(v)[:50]}")
    elif isinstance(data, dict):
        print(f"      top-level: dict keys = {list(data.keys())[:20]}")
        # find biggest list inside (likely the substation array)
        for k, v in data.items():
            if isinstance(v, list) and v:
                print(f"      list field '{k}': len={len(v)}, item={shape(v[0])}")
                if isinstance(v[0], dict):
                    for kk, vv in list(v[0].items())[:20]:
                        print(f"         {kk}: {str(vv)[:50]}")
    else:
        print(f"      top-level: {type(data).__name__}")


if __name__ == "__main__":
    main()
