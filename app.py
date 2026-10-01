import re
import math

import fitz
import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageOps, ImageFilter
import io

try:
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    pytesseract = None
    OCR_AVAILABLE = False


# =========================================================
# TELEKELŐÍRÁS AI v2.3
# RASZTERES HRSZ-KERESÉSI TESZT
# =========================================================


ZONE_RE = re.compile(
    r"\b(?:Lk|Lke|Ln|Vt|Vi|Gksz|Gip|Ge|Gá|K|Kb|KÖu|KÖk|"
    r"Zkp|Zkk|Z|Ev|Ek|Má|Mk|V|Ve|Lf|Üü)"
    r"(?:[-/][A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9]+){0,5}\b"
)


def ncode(s):
    s = (s or "").strip()
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s*([/-])\s*", r"\1", s)


# ---------------------------------------------------------
# HELYRAJZI SZÁM KERESÉSE
# ---------------------------------------------------------

def hrsz_hits(page, hrsz):

    variants = [
        hrsz,
        f"({hrsz})",
        hrsz.replace("/", " / "),
        hrsz.replace("/", "/ "),
        hrsz.replace("/", " /"),
    ]

    results = []

    for variant in variants:
        results.extend(
            page.search_for(variant)
        )

    unique = []
    seen = set()

    for r in results:

        key = (
            round(r.x0, 1),
            round(r.y0, 1),
            round(r.x1, 1),
            round(r.y1, 1),
        )

        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique


# ---------------------------------------------------------
# PDF SZÖVEGKOORDINÁTA -> LÁTHATÓ OLDAL KOORDINÁTA
# ---------------------------------------------------------

def visual_rect_from_pdf(page, rect):
    """
    A keresési találatot a PDF oldal saját transzformációs mátrixával
    vetíti a látható oldal koordinátarendszerére.

    Ez nem becsült, teljes szövegréteg-alapú skálázás: a PyMuPDF által
    megadott page.transformation_matrix kezeli a PDF / MuPDF koordináta-
    rendszer, a MediaBox/CropBox és az oldalgeometria eltéréseit.
    """
    pr = page.rect
    r = fitz.Rect(rect)

    # A search_for() találata normál esetben már MuPDF-oldalkoordináta.
    # Ha benne van a látható oldalon, nincs szükség transzformációra.
    if pr.intersects(r):
        clipped = r & pr
        if not clipped.is_empty:
            return clipped, False, "search_for koordináta"

    # CAD/PDF esetekben a szöveg pozíciója PDF-koordinátaként viselkedhet.
    # Ilyenkor a dokumentum saját PDF->MuPDF transzformációját használjuk.
    try:
        mapped = r * page.transformation_matrix
        mapped = mapped & pr
        if not mapped.is_empty:
            return mapped, True, "page.transformation_matrix"
    except Exception:
        pass

    return None, False, "nem vetíthető megbízhatóan"


def marked_page_image(page, rect=None, zoom=1.0):
    """
    Teljes tervoldal renderelése; ha van megbízható vizuális koordináta,
    piros kerettel megjelöli a hrsz. helyét.
    """
    work = fitz.open()
    work.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    p = work[0]

    if rect is not None:
        r = fitz.Rect(rect)
        pad = max(18, min(p.rect.width, p.rect.height) * 0.006)
        mark = fitz.Rect(r.x0-pad, r.y0-pad, r.x1+pad, r.y1+pad) & p.rect
        if not mark.is_empty:
            p.draw_rect(mark, color=(1, 0, 0), width=8)

    pix = p.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    data = pix.tobytes("png")
    work.close()
    return data



# ---------------------------------------------------------
# v1.5: VIZUÁLIS HRSZ-HELY KERESÉSE RENDERELT OLDALON
# ---------------------------------------------------------

def normalized_hrsz_text(s):
    """Hrsz összehasonlításhoz szóközök és zárójelek eltávolítása."""
    s = (s or "").strip()
    s = s.replace("(", "").replace(")", "")
    s = re.sub(r"\s+", "", s)
    return s


def visual_hrsz_candidates(page, hrsz):
    """
    A teljes szövegstruktúrából gyűjt hrsz-jelölteket, majd csak olyan
    bboxot fogad el, amely ténylegesen a látható page.rect területére esik.

    Fontos: itt nem a search_for() hibásan kívülre kerülő koordinátáját
    transzformáljuk tovább. A page.get_text('dict') látható span-jeiből
    indulunk, ezért ez külön ellenőrzési útvonal.
    """
    target = normalized_hrsz_text(hrsz)
    pr = page.rect
    candidates = []

    data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE)

    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            # Egy span önmagában
            for span in spans:
                txt = normalized_hrsz_text(span.get("text", ""))
                if target and target in txt:
                    r = fitz.Rect(span["bbox"])
                    if pr.intersects(r):
                        r = r & pr
                        if not r.is_empty:
                            candidates.append({
                                "rect": r,
                                "text": span.get("text", ""),
                                "source": "text-span"
                            })

            # Több spanból összerakott sor (pl. 2200 / 8)
            if spans:
                joined = "".join(s.get("text", "") for s in spans)
                if target and target in normalized_hrsz_text(joined):
                    rects = [fitz.Rect(s["bbox"]) for s in spans]
                    r = rects[0]
                    for rr in rects[1:]:
                        r |= rr
                    if pr.intersects(r):
                        r = r & pr
                        if not r.is_empty:
                            candidates.append({
                                "rect": r,
                                "text": joined,
                                "source": "text-line"
                            })

    # Duplikátumok kiszűrése
    unique = []
    seen = set()
    for item in candidates:
        r = item["rect"]
        key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


def render_visual_candidate(page, rect, scale_factor=0.055, zoom=2.4):
    """Nagyított kivágás egy vizuális jelölt körül, piros kerettel."""
    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2
    hw = page.rect.width * scale_factor
    hh = page.rect.height * scale_factor

    clip = fitz.Rect(
        max(page.rect.x0, cx - hw),
        max(page.rect.y0, cy - hh),
        min(page.rect.x1, cx + hw),
        min(page.rect.y1, cy + hh),
    )

    work = fitz.open()
    work.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    p = work[0]
    mark = fitz.Rect(rect)
    pad = max(5, min(p.rect.width, p.rect.height) * 0.0015)
    mark = fitz.Rect(mark.x0-pad, mark.y0-pad, mark.x1+pad, mark.y1+pad) & p.rect
    p.draw_rect(mark, color=(1, 0, 0), width=5)

    pix = p.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    out = pix.tobytes("png")
    work.close()
    return out, clip



# ---------------------------------------------------------
# v1.6: RASZTERES / OCR-ALAPÚ HRSZ-HELYMEGHATÁROZÁS
# ---------------------------------------------------------

def _ocr_norm(s):
    """OCR-szöveg normalizálása helyrajzi szám összehasonlításához."""
    s = (s or "").strip()
    s = s.replace("\\", "/").replace("|", "/")
    s = s.replace("O", "0").replace("o", "0")
    s = re.sub(r"[^0-9/]", "", s)
    return s


def _target_parts(hrsz):
    h = re.sub(r"\s+", "", hrsz or "")
    if "/" in h:
        a, b = h.split("/", 1)
        return a, b
    return h, ""


def _ocr_variants(img):
    """v2.2: több előfeldolgozás apró, vékony CAD helyrajzi számokhoz."""
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray)
    sharp = gray.filter(ImageFilter.SHARPEN).filter(ImageFilter.SHARPEN)
    variants = [("gray", gray), ("sharp", sharp)]
    for thr in (145, 175, 205, 225):
        bw = sharp.point(lambda p, t=thr: 255 if p > t else 0)
        variants.append((f"bw{thr}", bw))
    return variants


def _levenshtein(a, b):
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j-1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _close_numeric(a, b, max_dist=1):
    a = _ocr_norm(a); b = _ocr_norm(b)
    return bool(a and b and _levenshtein(a, b) <= max_dist)


def _find_target_in_ocr_data(data, hrsz, tile_rect, zoom, source_prefix):
    """v2.2: teljes és részleges OCR-találatok pontozása, geometriai összerakással."""
    target = _ocr_norm(hrsz)
    a, b = _target_parts(target)
    rows = []
    n = len(data.get("text", []))
    for i in range(n):
        txt = data["text"][i] or ""
        norm = _ocr_norm(txt)
        if not norm:
            continue
        try: conf = float(data["conf"][i])
        except Exception: conf = -1
        rows.append({
            "text": txt, "norm": norm, "conf": conf,
            "left": int(data["left"][i]), "top": int(data["top"][i]),
            "width": int(data["width"][i]), "height": int(data["height"][i]),
            "block": int(data.get("block_num", [0]*n)[i]),
            "par": int(data.get("par_num", [0]*n)[i]),
            "line": int(data.get("line_num", [0]*n)[i]),
        })

    def make_rect(items):
        x0=min(r["left"] for r in items)/zoom+tile_rect.x0
        y0=min(r["top"] for r in items)/zoom+tile_rect.y0
        x1=max(r["left"]+r["width"] for r in items)/zoom+tile_rect.x0
        y1=max(r["top"]+r["height"] for r in items)/zoom+tile_rect.y0
        return fitz.Rect(x0,y0,x1,y1)

    hits=[]
    # A) teljes token: exact vagy legfeljebb 1 OCR-karakterhiba
    for r in rows:
        if r["norm"] == target or target in r["norm"]:
            hits.append({"rect":make_rect([r]),"text":r["text"],"source":source_prefix+" / teljes","confidence":r["conf"],"score":100})
        elif len(r["norm"]) >= max(4, len(target)-1) and _close_numeric(r["norm"], target, 1):
            hits.append({"rect":make_rect([r]),"text":r["text"],"source":source_prefix+" / fuzzy teljes","confidence":r["conf"],"score":88})

    # B) azonos soron 2..6 token összefűzése
    grouped={}
    for r in rows:
        grouped.setdefault((r["block"],r["par"],r["line"]),[]).append(r)
    for group in grouped.values():
        group.sort(key=lambda r:r["left"])
        for i in range(len(group)):
            for j in range(i+1,min(len(group),i+6)):
                items=group[i:j+1]
                joined="".join(r["norm"] for r in items)
                if joined == target or target in joined:
                    hits.append({"rect":make_rect(items),"text":" ".join(r["text"] for r in items),"source":source_prefix+" / összefűzött","confidence":min(r["conf"] for r in items),"score":96})
                elif len(joined) >= max(4,len(target)-1) and _close_numeric(joined,target,1):
                    hits.append({"rect":make_rect(items),"text":" ".join(r["text"] for r in items),"source":source_prefix+" / fuzzy összefűzött","confidence":min(r["conf"] for r in items),"score":82})

    # C) 2200 és 8 (vagy nagyon közeli OCR-változataik) geometriai párként
    if a and b:
        lefts=[r for r in rows if r["norm"]==a or (len(a)>=3 and _close_numeric(r["norm"],a,1))]
        rights=[r for r in rows if r["norm"]==b]
        for r1 in lefts:
            cy1=r1["top"]+r1["height"]/2
            for r2 in rights:
                if r1 is r2: continue
                cy2=r2["top"]+r2["height"]/2
                gap=r2["left"]-(r1["left"]+r1["width"])
                mh=max(r1["height"],r2["height"],1)
                if abs(cy1-cy2)<=1.2*mh and -0.8*mh<=gap<=5.0*mh:
                    exact_left=(r1["norm"]==a)
                    hits.append({"rect":make_rect([r1,r2]),"text":f'{r1["text"]} / {r2["text"]}',"source":source_prefix+" / rész-találatok összerakva","confidence":min(r1["conf"],r2["conf"]),"score":90 if exact_left else 76})
    return hits


def raster_ocr_hrsz(page, hrsz, pdf_rect=None, progress_cb=None):
    """
    v2.3 – gyors előszűrés + célzott nagyfelbontású OCR.

    1) A teljes oldalt csak egyszer, közepes felbontásban pásztázza.
       Itt NEM várunk teljes 2200/8 egyezést: a számtörzs (pl. 2200)
       vagy annak 1 karakteres OCR-változata elegendő jelöltnek.
    2) Csak a jelölt területeket rendereli újra nagy felbontásban.
    3) A részletes fázisban teljes / összefűzött / geometriailag
       összerakott találatot keres.
    4) Minden fázis visszajelzést ad, és kivétel esetén hibaüzenettel tér vissza.
    """
    if not OCR_AVAILABLE:
        return [], "A Tesseract OCR nem érhető el a futtatási környezetben."

    target = _ocr_norm(hrsz)
    stem, suffix = _target_parts(target)
    if not target:
        return [], "Üres helyrajzi szám."

    # ---------- 1. GYORS ELŐSZŰRÉS ----------
    # Nagyobb csempék, egyetlen OCR-változat, egyetlen PSM.
    coarse_zoom = 2.0
    tile_w = 1250.0
    tile_h = 900.0
    overlap = 120.0

    def make_tiles(w, h, ov):
        step_x = max(200.0, w - ov)
        step_y = max(200.0, h - ov)
        xs, ys = [], []

        x = page.rect.x0
        while True:
            x0 = min(x, max(page.rect.x0, page.rect.x1 - w))
            if not xs or abs(x0 - xs[-1]) > 1:
                xs.append(x0)
            if x0 + w >= page.rect.x1 - 1:
                break
            x += step_x

        y = page.rect.y0
        while True:
            y0 = min(y, max(page.rect.y0, page.rect.y1 - h))
            if not ys or abs(y0 - ys[-1]) > 1:
                ys.append(y0)
            if y0 + h >= page.rect.y1 - 1:
                break
            y += step_y

        return [(x0, y0) for y0 in ys for x0 in xs]

    coarse_tiles = make_tiles(tile_w, tile_h, overlap)
    candidate_rects = []
    coarse_checked = 0

    try:
        for idx, (x0, y0) in enumerate(coarse_tiles, 1):
            if progress_cb:
                progress_cb(idx - 1, len(coarse_tiles), "Gyors előszűrés")

            clip = fitz.Rect(
                x0, y0,
                min(x0 + tile_w, page.rect.x1),
                min(y0 + tile_h, page.rect.y1),
            )

            pix = page.get_pixmap(
                matrix=fitz.Matrix(coarse_zoom, coarse_zoom),
                clip=clip,
                alpha=False,
            )
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            gray = ImageOps.autocontrast(ImageOps.grayscale(img))

            config = "--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789/"
            data = pytesseract.image_to_data(
                gray,
                config=config,
                output_type=pytesseract.Output.DICT,
            )

            n = len(data.get("text", []))
            for i in range(n):
                raw = data["text"][i] or ""
                norm = _ocr_norm(raw)
                if not norm:
                    continue

                # A gyors fázisban a számtörzs a fő jel.
                stem_hit = (
                    norm == stem
                    or stem in norm
                    or (len(norm) >= max(3, len(stem) - 1) and _close_numeric(norm, stem, 1))
                )
                full_hit = (
                    norm == target
                    or target in norm
                    or (len(norm) >= max(4, len(target) - 1) and _close_numeric(norm, target, 1))
                )

                if not (stem_hit or full_hit):
                    continue

                left = int(data["left"][i])
                top = int(data["top"][i])
                width = int(data["width"][i])
                height = int(data["height"][i])

                # Nem csak a token dobozát, hanem bőséges környezetét visszük tovább.
                rx0 = clip.x0 + left / coarse_zoom
                ry0 = clip.y0 + top / coarse_zoom
                rx1 = clip.x0 + (left + width) / coarse_zoom
                ry1 = clip.y0 + (top + height) / coarse_zoom

                token_rect = fitz.Rect(rx0, ry0, rx1, ry1)
                pad_x = max(90.0, token_rect.width * 12)
                pad_y = max(65.0, token_rect.height * 10)

                candidate_rects.append(
                    fitz.Rect(
                        max(page.rect.x0, token_rect.x0 - pad_x),
                        max(page.rect.y0, token_rect.y0 - pad_y),
                        min(page.rect.x1, token_rect.x1 + pad_x),
                        min(page.rect.y1, token_rect.y1 + pad_y),
                    )
                )

            coarse_checked = idx

    except Exception as exc:
        if progress_cb:
            progress_cb(coarse_checked, max(1, len(coarse_tiles)), "hiba")
        return [], f"Gyors OCR-előszűrés közben hiba történt: {type(exc).__name__}: {exc}"

    # Átfedő jelöltek összevonása.
    merged = []
    for r in candidate_rects:
        joined = False
        for j in range(len(merged)):
            m = merged[j]
            expanded = fitz.Rect(m.x0 - 35, m.y0 - 35, m.x1 + 35, m.y1 + 35)
            if expanded.intersects(r):
                merged[j] = m | r
                joined = True
                break
        if not joined:
            merged.append(fitz.Rect(r))

    # Újra összevonjuk, mert az első kör láncolt átfedéseket hagyhat.
    changed = True
    while changed:
        changed = False
        out = []
        while merged:
            base = merged.pop(0)
            rest = []
            for r in merged:
                expanded = fitz.Rect(base.x0 - 35, base.y0 - 35, base.x1 + 35, base.y1 + 35)
                if expanded.intersects(r):
                    base |= r
                    changed = True
                else:
                    rest.append(r)
            out.append(base)
            merged = rest
        merged = out

    # Ne engedjük elszállni a részletes fázist.
    candidates = merged[:12]

    if progress_cb:
        progress_cb(
            len(coarse_tiles),
            len(coarse_tiles),
            f"Gyors előszűrés kész – {len(candidates)} jelölt"
        )

    if not candidates:
        return [], (
            f"Gyors előszűrés kész: {len(coarse_tiles)} területet vizsgáltam meg, "
            f"de a {stem} számtörzsre sem találtam használható képi jelöltet. "
            "Nagyfelbontású OCR ezért nem indult."
        )

    # ---------- 2. CÉLZOTT NAGYFELBONTÁSÚ ELLENŐRZÉS ----------
    detailed_hits = []
    detailed_zoom = 5.0
    total_detail = len(candidates)

    try:
        for idx, clip in enumerate(candidates, 1):
            if progress_cb:
                progress_cb(idx - 1, total_detail, f"Részletes OCR – {idx}/{total_detail} jelölt")

            pix = page.get_pixmap(
                matrix=fitz.Matrix(detailed_zoom, detailed_zoom),
                clip=clip,
                alpha=False,
            )
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

            # Csak három célzott változat – a v2.2-höz képest lényegesen olcsóbb.
            gray = ImageOps.autocontrast(ImageOps.grayscale(img))
            sharp = gray.filter(ImageFilter.SHARPEN).filter(ImageFilter.SHARPEN)
            bw = sharp.point(lambda p: 255 if p > 205 else 0)

            variants = [
                ("gray", gray),
                ("sharp", sharp),
                ("bw205", bw),
            ]

            for variant_name, prepared in variants:
                for psm in (11, 6):
                    config = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789/"
                    data = pytesseract.image_to_data(
                        prepared,
                        config=config,
                        output_type=pytesseract.Output.DICT,
                    )
                    detailed_hits.extend(
                        _find_target_in_ocr_data(
                            data,
                            hrsz,
                            clip,
                            detailed_zoom,
                            f"v2.3 {variant_name} psm{psm}",
                        )
                    )

            # Korai leállás: egy nagyon erős exact/összefűzött találat elég.
            strong_now = [h for h in detailed_hits if h.get("score", 0) >= 96]
            if strong_now:
                if progress_cb:
                    progress_cb(idx, total_detail, "találat")
                break

    except Exception as exc:
        if progress_cb:
            progress_cb(idx - 1 if 'idx' in locals() else 0, total_detail, "hiba")
        return [], f"Részletes OCR közben hiba történt: {type(exc).__name__}: {exc}"

    # ---------- 3. TALÁLATOK ÖSSZEVONÁSA / PONTOZÁSA ----------
    clusters = []
    for hit in detailed_hits:
        r = hit["rect"]
        cx = (r.x0 + r.x1) / 2
        cy = (r.y0 + r.y1) / 2
        placed = False

        for c in clusters:
            cr = c["rect"]
            ccx = (cr.x0 + cr.x1) / 2
            ccy = (cr.y0 + cr.y1) / 2
            tol = max(12.0, 2.2 * max(r.height, cr.height))
            if math.hypot(cx - ccx, cy - ccy) <= tol:
                c["members"].append(hit)
                c["rect"] |= r
                placed = True
                break

        if not placed:
            clusters.append({"rect": fitz.Rect(r), "members": [hit]})

    scored = []
    for c in clusters:
        members = c["members"]
        methods = len(set(m["source"] for m in members))
        best = max(
            members,
            key=lambda m: (m.get("score", 0), m.get("confidence", -1)),
        )
        cluster_score = (
            best.get("score", 0)
            + min(18, 4 * (len(members) - 1))
            + min(12, 3 * (methods - 1))
        )
        scored.append((cluster_score, c, best))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for cluster_score, c, best in scored:
        if cluster_score < 94:
            continue
        results.append({
            "rect": c["rect"],
            "text": best.get("text", hrsz),
            "source": best.get("source", "v2.3 célzott OCR"),
            "confidence": best.get("confidence"),
            "confirmations": len(c["members"]),
            "score": cluster_score,
            "coarse_tiles": len(coarse_tiles),
            "candidate_areas": len(candidates),
            "was_corrected": False,
        })

    if not results:
        if progress_cb:
            progress_cb(total_detail, total_detail, "kész")
        return [], (
            f"Gyors előszűrés: {len(coarse_tiles)} terület → {len(candidates)} jelölt. "
            f"A célzott nagyfelbontású OCR ezeken sem talált elég biztos {hrsz} egyezést."
        )

    if progress_cb:
        progress_cb(total_detail, total_detail, "találat")

    return results, None

def ocr_debug_tokens(page, hrsz, zoom=1.5):
    """v1.8: a memóriaigényes teljes oldalas OCR-diagnosztika kikapcsolva."""
    return [], None


def render_ocr_candidate(page, rect, zoom=3.0, margin_factor=0.045):
    """OCR-találat környezetének nagyítása, piros kerettel."""
    cx=(rect.x0+rect.x1)/2; cy=(rect.y0+rect.y1)/2
    hw=max(page.rect.width*margin_factor, rect.width*8)
    hh=max(page.rect.height*margin_factor, rect.height*8)
    clip=fitz.Rect(max(0,cx-hw), max(0,cy-hh),
                   min(page.rect.width,cx+hw), min(page.rect.height,cy+hh))
    pix=page.get_pixmap(matrix=fitz.Matrix(zoom,zoom), clip=clip, alpha=False)
    img=Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    draw=ImageDraw.Draw(img)
    sx=zoom
    bx0=(rect.x0-clip.x0)*sx; by0=(rect.y0-clip.y0)*sx
    bx1=(rect.x1-clip.x0)*sx; by1=(rect.y1-clip.y0)*sx
    draw.rectangle((bx0,by0,bx1,by1), outline="red", width=max(4,int(2*zoom)))
    bio=io.BytesIO(); img.save(bio, format="PNG")
    return bio.getvalue(), clip


# ---------------------------------------------------------
# HÉSZ KÓDOK
# ---------------------------------------------------------

@st.cache_data(show_spinner=False)
def legal_codes(data):

    doc = fitz.open(
        stream=data,
        filetype="pdf",
    )

    codes = set()

    for page in doc:

        text = page.get_text()

        for m in ZONE_RE.finditer(text):
            codes.add(
                ncode(m.group(0))
            )

    doc.close()

    return sorted(codes)


# ---------------------------------------------------------
# ÖVEZETI FELIRATOK
# ---------------------------------------------------------

def zone_labels(page):

    result = []

    for w in page.get_text("words"):

        raw = w[4].strip(
            "()[]{}.,;:"
        )

        code = ncode(raw)

        if ZONE_RE.fullmatch(code):

            result.append(
                {
                    "code": code,
                    "x": (w[0] + w[2]) / 2,
                    "y": (w[1] + w[3]) / 2,
                }
            )

    return result


def nearby_labels(
    page,
    rect,
    legal,
    radius,
):

    cx = (
        rect.x0 + rect.x1
    ) / 2

    cy = (
        rect.y0 + rect.y1
    ) / 2

    legal_set = set(legal)

    rows = []

    for item in zone_labels(page):

        distance = math.hypot(
            item["x"] - cx,
            item["y"] - cy,
        )

        if distance <= radius:

            rows.append(
                {
                    "code":
                        item["code"],

                    "distance":
                        round(distance, 1),

                    "hesz_match":
                        item["code"]
                        in legal_set,
                }
            )

    rows.sort(
        key=lambda x: x["distance"]
    )

    result = []
    seen = set()

    for row in rows:

        if row["code"] not in seen:
            seen.add(row["code"])
            result.append(row)

    return result[:20]


# ---------------------------------------------------------
# TELJES OLDAL RENDER
# ---------------------------------------------------------

def render_full_page(page):

    # Kis felbontás elég a diagnosztikához.
    matrix = fitz.Matrix(
        0.7,
        0.7,
    )

    pix = page.get_pixmap(
        matrix=matrix,
        alpha=False,
    )

    return pix.tobytes("png")


# ---------------------------------------------------------
# KIVÁGÁS A HRSZ KÖRÜL
# ---------------------------------------------------------

def render_crop(
    page,
    rect,
    scale_factor,
):

    cx = (
        rect.x0 + rect.x1
    ) / 2

    cy = (
        rect.y0 + rect.y1
    ) / 2

    # Az oldal méretének arányában dolgozunk.
    half_width = (
        page.rect.width
        * scale_factor
    )

    half_height = (
        page.rect.height
        * scale_factor
    )

    clip = fitz.Rect(
        max(
            page.rect.x0,
            cx - half_width,
        ),
        max(
            page.rect.y0,
            cy - half_height,
        ),
        min(
            page.rect.x1,
            cx + half_width,
        ),
        min(
            page.rect.y1,
            cy + half_height,
        ),
    )

    pix = page.get_pixmap(
        matrix=fitz.Matrix(
            1.5,
            1.5,
        ),
        clip=clip,
        alpha=False,
    )

    return (
        pix.tobytes("png"),
        clip,
    )


# ---------------------------------------------------------
# STREAMLIT
# ---------------------------------------------------------

st.set_page_config(
    page_title="TelekElőírás AI v2.3",
    page_icon="🏗️",
    layout="wide",
)


st.title(
    "TelekElőírás AI"
)

st.caption(
    "v2.3 • gyors előszűrés + célzott nagyfelbontású OCR • kötelező futási diagnosztika"
)


with st.sidebar:

    st.header(
        "Tesztforrások"
    )

    st.info(
        "A program a kereshető PDF-szöveget csak a megfelelő tervoldal kiválasztására használja. "
        "A helyrajzi szám tényleges helyét ezután a renderelt tervlapon, koordinátafüggetlen képi OCR-rel keresi meg."
    )

    plan = st.file_uploader(
        "Szabályozási terv (PDF)",
        type=["pdf"],
        key="plan",
    )

    hesz = st.file_uploader(
        "HÉSZ/TÉSZ vagy övezeti melléklet (PDF)",
        type=["pdf"],
        key="hesz",
    )

    radius = st.slider(
        "Környezeti vizsgálati sugár",
        100,
        1800,
        600,
        100,
    )


c1, c2 = st.columns(2)

town = c1.text_input(
    "Település",
    "Tiszaújváros",
)

hrsz = c2.text_input(
    "Helyrajzi szám",
    "2200/8",
)


if st.button(
    "v2.3 gyorsított képi telekhely keresés indítása",
    type="primary",
    use_container_width=True,
):

    if not plan or not hesz:

        st.error(
            "Töltsd fel mindkét PDF-et."
        )

        st.stop()


    clean_hrsz = re.sub(
        r"\s+",
        "",
        hrsz,
    )


    with st.spinner(
        "PDF vizsgálata…"
    ):

        plan_bytes = plan.getvalue()
        hesz_bytes = hesz.getvalue()

        legal = legal_codes(
            hesz_bytes
        )

        doc = fitz.open(
            stream=plan_bytes,
            filetype="pdf",
        )

        candidates = []
        page_status = st.empty()
        page_progress = st.progress(0)

        for pno in range(
            len(doc)
        ):
            page_progress.progress(int(100 * (pno + 1) / max(1, len(doc))))
            page_status.caption(
                f"Tervoldal keresése: {pno + 1}/{len(doc)} oldal"
            )

            page = doc[pno]

            found = hrsz_hits(
                page,
                clean_hrsz,
            )

            for rect in found:

                candidates.append(
                    {
                        "page_number":
                            pno,

                        "rect":
                            rect,
                    }
                )

        page_progress.empty()
        page_status.empty()


    if not candidates:

        st.error(
            f"A {clean_hrsz} helyrajzi szám "
            "nem található."
        )

        doc.close()

        st.stop()


    target = candidates[0]

    pno = target[
        "page_number"
    ]

    rect = target[
        "rect"
    ]

    page = doc[pno]

    # v2.3: a PDF-szöveg csak a tervoldalt választja ki; a helyet gyors előszűrés + célzott OCR adja.
    st.markdown("### Képi helymeghatározás")
    ocr_status = st.empty()
    ocr_progress = st.progress(0)

    def update_ocr_progress(done, total, phase):
        pct = int(100 * done / max(1, total))
        ocr_progress.progress(min(100, max(0, pct)))
        if phase == "találat":
            ocr_status.success(
                f"Vizuális helyrajzi szám megtalálva • {done}/{total}."
            )
        elif phase == "kész":
            ocr_status.info(
                f"OCR-vizsgálat befejezve • {done}/{total}."
            )
        elif phase == "hiba":
            ocr_status.error(
                f"OCR-feldolgozási hiba • {done}/{total}."
            )
        else:
            ocr_status.info(
                f"{phase} • {done}/{total}"
            )

    visual_candidates, ocr_error = raster_ocr_hrsz(
        page,
        clean_hrsz,
        pdf_rect=None,
        progress_cb=update_ocr_progress,
    )
    ocr_debug, ocr_debug_error = ocr_debug_tokens(page, clean_hrsz, zoom=1.5)

    if visual_candidates:
        best_candidate = visual_candidates[0]
        visual_rect = best_candidate["rect"]
        was_corrected = best_candidate.get("was_corrected", False)
        coordinate_method = best_candidate["source"]
        ocr_confidence = best_candidate.get("confidence")
        ocr_text = best_candidate.get("text", "")
    else:
        visual_rect = None
        was_corrected = False
        coordinate_method = "nincs megbízható OCR-találat"
        ocr_confidence = None
        ocr_text = ""


    st.success(
        f"{clean_hrsz} megtalálva • "
        f"{pno + 1}. oldal • "
        f"találatok száma: {len(candidates)}"
    )


    # =====================================================
    # 1. VIZUÁLIS KERESÉSI DIAGNOSZTIKA
    # =====================================================

    st.subheader("1. Vizuális keresési diagnosztika")

    page_width = page.rect.width
    page_height = page.rect.height

    d1, d2 = st.columns(2)
    d1.metric("Azonosított tervoldal", pno + 1)
    d2.metric("PDF-szöveges oldaltalálatok", len(candidates))

    st.info(
        "v2.3-ban a PDF kereshető szövegrétege kizárólag a megfelelő tervoldalt választja ki. "
        "A helyrajzi szám tényleges helyét a program a renderelt tervlapon, képi OCR-rel keresi meg; "
        "a PDF-szöveg hibás CAD-koordinátáját nem használja."
    )

    st.write("**OCR diagnosztika:**")
    if ocr_debug_error:
        st.code(ocr_debug_error)
    elif ocr_debug:
        st.dataframe(
            pd.DataFrame(ocr_debug),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "Ez a táblázat azokat az OCR-tokeneket mutatja, amelyek a keresett "
            "helyrajzi szám egészére vagy valamely részére hasonlítanak."
        )
    else:
        st.warning(
            "v2.3-ban először gyors, közepes felbontású előszűrés fut; nagyfelbontású OCR csak a jelölt területeken indul."
        )

    st.write("**Képi/OCR hrsz.-keresés eredménye:**")

    if visual_rect is None:
        st.error(
            "A megfelelő tervoldalt megtaláltuk, de a PDF szövegrétegének találati "
            "koordinátáját nem sikerült megbízhatóan a látható oldalra vetíteni. "
            "A program ezért nem készít telek-központú kivágást."
        )
        if ocr_error:
            st.code(ocr_error)
        vcx = vcy = None
    else:
        vcx = (visual_rect.x0 + visual_rect.x1) / 2
        vcy = (visual_rect.y0 + visual_rect.y1) / 2

        st.success(
            f"A hrsz. helyét a renderelt tervlapon a képi OCR megtalálta. "
            f"Találatok száma: {len(visual_candidates)}."
        )

        st.code(
            f"x0 = {visual_rect.x0:.2f}\n"
            f"y0 = {visual_rect.y0:.2f}\n"
            f"x1 = {visual_rect.x1:.2f}\n"
            f"y1 = {visual_rect.y1:.2f}\n"
            f"középpont X = {vcx:.2f}\n"
            f"középpont Y = {vcy:.2f}\n"
            f"relatív X = {(vcx / page_width) * 100:.2f}%\n"
            f"relatív Y = {(vcy / page_height) * 100:.2f}%\n"
            f"forrás = {coordinate_method}\n"
            f"OCR szöveg = {ocr_text}\n"
            f"OCR biztonság = {ocr_confidence}"
        )

        st.subheader("2. Hrsz.-találat nagyítása")
        st.caption(
            "A piros keretnek közvetlenül a keresett helyrajzi szám feliratát kell körülvennie. "
            "Ezzel ellenőrizhető, hogy a PDF-szövegréteg és a renderelt tervlap fedésben van-e."
        )
        try:
            candidate_img, candidate_clip = render_ocr_candidate(
                page, visual_rect
            )
            st.image(
                candidate_img,
                caption=f"{clean_hrsz} – vizuális találat",
                use_container_width=True,
            )
        except Exception as e:
            st.error("A vizuális találat nagyítása sikertelen.")
            st.code(repr(e))

    # =====================================================
    # 2. TELJES OLDAL
    # =====================================================

    st.subheader(
        "3. Teljes tervoldal"
    )

    st.caption(
        "Ha a képi OCR bizonyított helyrajziszám-találatot ad, azt a teljes tervlapon piros keret jelöli. "
        "A jelölést a nagyított kivágással együtt kell ellenőrizni."
    )


    try:

        full_image = marked_page_image(
            page,
            visual_rect,
            zoom=0.7,
        )

        st.image(
            full_image,
            caption=(
                f"{town} – szabályozási terv – "
                f"{pno + 1}. oldal"
            ),
            use_container_width=True,
        )

    except Exception as e:

        st.error(
            "A teljes oldal renderelése sikertelen."
        )

        st.code(
            repr(e)
        )


    if visual_rect is None:
        st.subheader("4. További vizsgálat")
        st.info(
            "A helymeghatározás nem bizonyított, ezért a program itt megáll. "
            "Nagy/szűk kivágás és közeli övezeti keresés csak érvényes koordináta után készül."
        )
        doc.close()
        st.stop()

    # =====================================================
    # 3. NAGY KIVÁGÁS
    # =====================================================

    st.subheader(
        "4. Vizuális nagy környezet"
    )


    try:

        if visual_rect is None:
            raise ValueError("Nincs megbízható vizuális hrsz-koordináta.")

        img_large, clip_large = render_crop(
            page,
            visual_rect,
            0.25,
        )

        st.image(
            img_large,
            caption=(
                f"{clean_hrsz} környezete – "
                "nagy kivágás"
            ),
            use_container_width=True,
        )

        st.caption(
            "Kivágási koordináták: "
            f"{clip_large.x0:.1f}, "
            f"{clip_large.y0:.1f}, "
            f"{clip_large.x1:.1f}, "
            f"{clip_large.y1:.1f}"
        )

    except Exception as e:

        st.error(
            "A nagy kivágás renderelése sikertelen."
        )

        st.code(
            repr(e)
        )


    # =====================================================
    # 4. SZŰK KIVÁGÁS
    # =====================================================

    st.subheader(
        "5. Vizuális szűk hrsz-környezet"
    )


    try:

        if visual_rect is None:
            raise ValueError("Nincs megbízható vizuális hrsz-koordináta.")

        img_small, clip_small = render_crop(
            page,
            visual_rect,
            0.08,
        )

        st.image(
            img_small,
            caption=(
                f"{clean_hrsz} – "
                "szűk környezet"
            ),
            use_container_width=True,
        )

        st.caption(
            "Kivágási koordináták: "
            f"{clip_small.x0:.1f}, "
            f"{clip_small.y0:.1f}, "
            f"{clip_small.x1:.1f}, "
            f"{clip_small.y1:.1f}"
        )

    except Exception as e:

        st.error(
            "A szűk kivágás renderelése sikertelen."
        )

        st.code(
            repr(e)
        )


    # =====================================================
    # 5. ÖVEZETI FELIRATOK
    # =====================================================

    st.subheader(
        "6. Felismert közeli övezeti feliratok"
    )


    if visual_rect is not None:
        near = nearby_labels(
            page,
            visual_rect,
            legal,
            radius,
        )
    else:
        near = []


    if near:

        df = pd.DataFrame(
            near
        )

        df = df.rename(
            columns={
                "code":
                    "Övezeti jel",

                "distance":
                    "PDF-távolság",

                "hesz_match":
                    "Szerepel a HÉSZ-ben",
            }
        )

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.info(
            "Nincs felismert övezeti jel "
            "a megadott környezetben."
        )


    # =====================================================
    # EREDMÉNY
    # =====================================================

    st.subheader(
        "Vizuális helymeghatározás eredménye"
    )

    st.warning(
        "Ebben a verzióban továbbra sem történik automatikus "
        "övezeti besorolás. Először a hrsz. valódi térképi "
        "helyét ellenőrizzük."
    )

    st.write(
        f"A v2.3 a PDF-szöveget csak a tervoldal kiválasztására használja; a {clean_hrsz} helyét gyors előszűrés után célzott képi OCR-rel keresi. "
        "A döntési pont az, hogy a piros keret valóban közvetlenül "
        "a terven látható helyrajzi számot jelöli-e."
    )


    st.caption(
        f"Oldal: {pno + 1} • "
        f"Hrsz-találatok: {len(candidates)} • "
        f"HÉSZ-ben felismert övezeti kódok: {len(legal)}"
    )


    doc.close()


st.divider()

st.caption(
    "TelekElőírás AI v2.3 – gyors előszűrés + célzott képi hrsz.-helymeghatározási teszt"
)