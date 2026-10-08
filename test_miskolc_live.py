"""Live source check for the official Miskolc plan; no zoning assertion from proximity."""
import io
import os
import re
import sys
import urllib.request

import fitz
from plan_labels import native_hrsz_hits

URL = "https://njt.jog.gov.hu/document/b4/b45dLL_EJR_127797597-Belteruleti_szabalyozasi_tervlapok_modositasa.pdf"
MAX_BYTES = 110 * 1024 * 1024


def main():
    request = urllib.request.Request(URL, headers={"User-Agent": "TelekEloirasAI-regression/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise RuntimeError("Official PDF exceeds 110 MB test limit")
    if not data.startswith(b"%PDF"):
        raise RuntimeError("Official source did not return a PDF")
    with fitz.open(stream=data, filetype="pdf") as doc:
        print("PDF pages:", len(doc), "bytes:", len(data), flush=True)
        if len(doc) < 31:
            raise AssertionError("The official PDF has fewer than 31 pages")
        # Browser page 31 is zero-indexed page 30.
        page = doc[30]
        page_text = page.get_text("text")
        print("Page 31 text chars:", len(page_text), flush=True)
        print("Page 31 exact text search:", len(page.search_for("4755/11")), flush=True)
        matches = native_hrsz_hits(doc, "4755/11")
        print("Exact native HRSZ matches:", [(m["page_number"] + 1) for m in matches], flush=True)
        if not matches:
            raise AssertionError("No exact native text match for Miskolc 4755/11")
        if not any(m["page_number"] == 30 for m in matches):
            raise AssertionError("Miskolc 4755/11 not found on PDF page 31")
        print("PASS: official PDF page 31 contains native HRSZ 4755/11", flush=True)
        # Zoning remains unverified without parcel-to-zone geometry.
        nearby = page.get_text("text")
        print("Gipe code visible in text layer:", bool(re.search(r"Gipe\\s*-\\s*60\\.63\\.5", nearby, re.I)), flush=True)


if __name__ == "__main__":
    main()
