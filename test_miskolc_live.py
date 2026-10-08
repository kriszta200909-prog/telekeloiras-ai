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
        zone_in_text = bool(re.search(r"Gipe\s*-\s*60\.63\.5", nearby, re.I))
        print("Gipe code visible in text layer:", zone_in_text, flush=True)
        if not zone_in_text:
            raise AssertionError("Expected Gipe-60.63.5 label is missing from official PDF page 31")
        import ast
        # Extract only the pure zone matching helpers without importing Streamlit.
        from pathlib import Path
        source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {"words_near_hit", "zone_candidates"}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        pattern_node = next(n for n in tree.body if isinstance(n, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == "ZONE_PATTERN" for t in n.targets))
        namespace = {"re": re, "fitz": fitz, "clean_text": lambda x: str(x).strip()}
        namespace["ZONE_PATTERN"] = re.compile(ast.literal_eval(pattern_node.value.args[0]), re.I)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
        zone_candidates = namespace["zone_candidates"]
        parcel_hit = next(m for m in matches if m["page_number"] == 30)
        candidates = zone_candidates(page, fitz.Rect(parcel_hit["pdf_rect"]))
        print("Nearby zoning candidates:", candidates, flush=True)
        expected_candidate = any(c["Övezeti kód"].casefold() == "gipe-60.63.5" for c in candidates)
        print("Expected Gipe label among nearby candidates:", expected_candidate, flush=True)
        if not expected_candidate:
            raise AssertionError("Actual zoning candidate search missed Gipe-60.63.5 near 4755/11")
        if candidates[0]["Övezeti kód"].casefold() != "gipe-60.63.5":
            raise AssertionError("Nearest zoning candidate changed; inspect actual map geometry")
        # Verify that the application keeps nearby labels unverified.
        locate_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "locate_parcel")
        locate_ns = {"find_hrsz": lambda document, hrsz: [
                         {**m, "pdf_rect": fitz.Rect(m["pdf_rect"])} for m in matches],
                     "zone_candidates": zone_candidates, "fitz": fitz}
        exec(compile(ast.Module(body=[locate_fn], type_ignores=[]), "app.py", "exec"), locate_ns)
        result = locate_ns["locate_parcel"](doc, "4755/11")
        assert result["status"] == "candidate_unverified", result
        assert result["zone"] == "", result
        assert result["candidates"][0]["Övezeti kód"] == "Gipe-60.63.5", result
        assert 0 < result["candidates"][0]["Felirat távolsága (PDF-egység)"] < 40, result
        print("PASS: application reports unverified Gipe candidate, not proven zoning", flush=True)

        # Also inspect actual PDF word geometry; this is evidence, not a parcel overlay.
        target = next(m for m in matches if m["page_number"] == 30)
        rect = fitz.Rect(target["pdf_rect"])
        center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        zone_words = [w for w in page.get_text("words")
                      if "Gipe" in w[4] or "60.63.5" in w[4]]
        zone_words.sort(key=lambda w: ((w[0]+w[2])/2-center.x)**2 + ((w[1]+w[3])/2-center.y)**2)
        print("Closest zone-like PDF words:", [(w[4], round(((w[0]+w[2])/2-center.x)**2 + ((w[1]+w[3])/2-center.y)**2, 1)) for w in zone_words[:8]], flush=True)



if __name__ == "__main__":
    main()
