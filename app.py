import re
import math

import fitz
import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw
import io

try:
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    pytesseract = None
    OCR_AVAILABLE = False


# =========================================================
# TELEKELŐÍRÁS AI v1.8
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


def raster_ocr_hrsz(
    page,
    hrsz,
    hint_rect=None,
    progress_cb=None,
    local_zoom=3.0,
    fallback_zoom=2.0,
    tile_px=1800,
    overlap_px=180,
):
    """
    v1.8:
    1) a PDF szövegtalálat koordinátáját csak KERESÉSI TÁMPONTKÉNT használja;
    2) először a támpont környezetét vizsgálja nagy felbontásban;
    3) ha ott nincs biztos találat, a teljes oldalt csempénként járja be;
    4) pontos OCR-egyezésnél azonnal leáll.
    A PDF-szövegkoordináta önmagában továbbra sem bizonyít telekhelyet.
    """
    if not OCR_AVAILABLE:
        return [], "A pytesseract Python-csomag nem érhető el."

    target = _ocr_norm(hrsz)
    first, last = _target_parts(target)
    if not target:
        return [], "Üres helyrajzi szám."

    pr = page.rect
    jobs = []
    seen_jobs = set()

    def add_job(clip, zoom, phase):
        clip = fitz.Rect(clip) & pr
        if clip.is_empty:
            return
        key = (
            round(clip.x0, 1), round(clip.y0, 1),
            round(clip.x1, 1), round(clip.y1, 1),
            round(zoom, 2),
        )
        if key not in seen_jobs:
            seen_jobs.add(key)
            jobs.append((clip, zoom, phase))

    # --- 1. fázis: célzott keresés a PDF-szövegkoordináta környezetében ---
    if hint_rect is not None:
        hr = fitz.Rect(hint_rect)
        cx = min(max((hr.x0 + hr.x1) / 2, pr.x0), pr.x1)
        cy = min(max((hr.y0 + hr.y1) / 2, pr.y0), pr.y1)

        # Több, fokozatosan táguló ablak a támpont körül.
        for radius in (350, 700, 1200):
            add_job(
                fitz.Rect(cx - radius, cy - radius, cx + radius, cy + radius),
                local_zoom,
                "célzott",
            )

    # --- 2. fázis: teljes oldal, de kisebb felbontású átfedő csempékkel ---
    step_px = max(500, tile_px - overlap_px)
    page_w_px = max(1, int(pr.width * fallback_zoom))
    page_h_px = max(1, int(pr.height * fallback_zoom))

    full_jobs = []
    for top in range(0, page_h_px, step_px):
        for left in range(0, page_w_px, step_px):
            right = min(page_w_px, left + tile_px)
            bottom = min(page_h_px, top + tile_px)
            clip = fitz.Rect(
                pr.x0 + left / fallback_zoom,
                pr.y0 + top / fallback_zoom,
                pr.x0 + right / fallback_zoom,
                pr.y0 + bottom / fallback_zoom,
            ) & pr
            if not clip.is_empty:
                full_jobs.append(clip)

    # Ha van támpont, a teljes oldalas csempéket is ahhoz legközelebbi sorrendben járjuk.
    if hint_rect is not None:
        hr = fitz.Rect(hint_rect)
        hx = min(max((hr.x0 + hr.x1) / 2, pr.x0), pr.x1)
        hy = min(max((hr.y0 + hr.y1) / 2, pr.y0), pr.y1)

        def dist2(r):
            cx = (r.x0 + r.x1) / 2
            cy = (r.y0 + r.y1) / 2
            return (cx - hx) ** 2 + (cy - hy) ** 2

        full_jobs.sort(key=dist2)

    for clip in full_jobs:
        add_job(clip, fallback_zoom, "teljes oldal")

    total = max(1, len(jobs))

    for idx, (clip, zoom, phase) in enumerate(jobs, start=1):
        if progress_cb:
            progress_cb(idx - 1, total, phase)

        try:
            pix = page.get_pixmap(
                matrix=fitz.Matrix(zoom, zoom),
                clip=clip,
                alpha=False,
            )
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

            # Kontrasztos szürke kép általában jobb a halvány CAD-feliratokhoz.
            gray = img.convert("L")
            data = pytesseract.image_to_data(
                gray,
                config="--psm 11",
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            continue

        words = []
        n = len(data.get("text", []))
        for i in range(n):
            raw = (data["text"][i] or "").strip()
            if not raw:
                continue
            try:
                conf = float(data["conf"][i])
            except Exception:
                conf = -1
            if conf < 0:
                continue

            x = int(data["left"][i])
            y = int(data["top"][i])
            w = int(data["width"][i])
            h = int(data["height"][i])

            words.append({
                "raw": raw,
                "norm": _ocr_norm(raw),
                "conf": conf,
                "box": (x, y, x + w, y + h),
                "line": (
                    data.get("block_num", [0] * n)[i],
                    data.get("par_num", [0] * n)[i],
                    data.get("line_num", [0] * n)[i],
                ),
            })

        local_hits = []

        # Egyetlen OCR-tokenben szereplő pontos hrsz.
        for w in words:
            if w["norm"] == target:
                local_hits.append(
                    (w["box"], w["conf"], w["raw"], f"{phase} OCR – egy token")
                )

        # Több tokenből összerakott hrsz. ugyanazon OCR-sorban.
        by_line = {}
        for w in words:
            by_line.setdefault(w["line"], []).append(w)

        for line_words in by_line.values():
            line_words.sort(key=lambda q: q["box"][0])
            for i in range(len(line_words)):
                combined = ""
                boxes, confs, raws = [], [], []

                for j in range(i, min(i + 6, len(line_words))):
                    q = line_words[j]
                    combined += q["norm"]
                    boxes.append(q["box"])
                    confs.append(q["conf"])
                    raws.append(q["raw"])

                    exact = combined == target
                    split_match = (
                        first and last
                        and first in combined
                        and combined.endswith(last)
                        and "/" in combined
                    )

                    if exact or split_match:
                        x0 = min(b[0] for b in boxes)
                        y0 = min(b[1] for b in boxes)
                        x1 = max(b[2] for b in boxes)
                        y1 = max(b[3] for b in boxes)
                        local_hits.append((
                            (x0, y0, x1, y1),
                            sum(confs) / len(confs),
                            " ".join(raws),
                            f"{phase} OCR – több token",
                        ))
                        break

                    if len(combined) > len(target) + 3:
                        break

        if local_hits:
            # A legerősebb találatot visszaadjuk, és AZONNAL leállunk.
            local_hits.sort(key=lambda q: q[1], reverse=True)
            box, conf, raw, method = local_hits[0]
            x0, y0, x1, y1 = box

            r = fitz.Rect(
                clip.x0 + x0 / zoom,
                clip.y0 + y0 / zoom,
                clip.x0 + x1 / zoom,
                clip.y0 + y1 / zoom,
            ) & pr

            if not r.is_empty:
                if progress_cb:
                    progress_cb(idx, total, "találat")
                return [{
                    "rect": r,
                    "text": raw,
                    "source": method,
                    "confidence": round(conf, 1),
                    "tiles_checked": idx,
                    "tiles_total": total,
                }], None

    if progress_cb:
        progress_cb(total, total, "kész")

    return [], (
        f"Az irányított tile-OCR {total} képrészletet vizsgált meg, "
        "de nem talált biztos vizuális egyezést."
    )


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
    page_title="TelekElőírás AI v1.8",
    page_icon="🏗️",
    layout="wide",
)


st.title(
    "TelekElőírás AI"
)

st.caption(
    "v1.8 • irányított progresszív tile-OCR • "
    "a PDF-oldal képén végzett OCR-helymeghatározás"
)


with st.sidebar:

    st.header(
        "Tesztforrások"
    )

    st.info(
        "A program először a PDF kereshető szövegével azonosítja a tervoldalt. "
        "Ezután az oldalt képpé rendereli, és OCR-rel a ténylegesen látható "
        "helyrajzi számot keresi meg. A hibás PDF-szövegkoordinátát nem használja telekazonosításra."
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
    "v1.8 irányított OCR keresés indítása",
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

    # v1.8: a PDF-szövegkoordináta csak KERESÉSI TÁMPONT.
    # A tényleges helyet továbbra is kizárólag képi OCR-találat igazolja.
    st.markdown("### Képi helymeghatározás")
    ocr_status = st.empty()
    ocr_progress = st.progress(0)

    def update_ocr_progress(done, total, phase):
        pct = int(100 * done / max(1, total))
        ocr_progress.progress(min(100, max(0, pct)))
        if phase == "találat":
            ocr_status.success(
                f"Vizuális helyrajzi szám megtalálva • {done}/{total} képrészlet vizsgálva."
            )
        elif phase == "kész":
            ocr_status.info(
                f"OCR-vizsgálat befejezve • {done}/{total} képrészlet."
            )
        else:
            ocr_status.info(
                f"OCR keresés: {phase} • {done + 1}/{total} képrészlet"
            )

    visual_candidates, ocr_error = raster_ocr_hrsz(
        page,
        clean_hrsz,
        hint_rect=rect,
        progress_cb=update_ocr_progress,
        local_zoom=3.0,
        fallback_zoom=2.0,
        tile_px=1800,
        overlap_px=180,
    )
    ocr_debug, ocr_debug_error = ocr_debug_tokens(page, clean_hrsz, zoom=1.5)

    if visual_candidates:
        best_candidate = visual_candidates[0]
        visual_rect = best_candidate["rect"]
        was_corrected = False
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
    # 1. DIAGNOSZTIKAI ADATOK
    # =====================================================

    st.subheader(
        "1. Koordináta-ellenőrzés"
    )


    page_width = page.rect.width
    page_height = page.rect.height

    cx = (
        rect.x0 + rect.x1
    ) / 2

    cy = (
        rect.y0 + rect.y1
    ) / 2


    d1, d2, d3 = st.columns(3)

    d1.metric(
        "PDF-oldal szélessége",
        f"{page_width:.1f}"
    )

    d2.metric(
        "PDF-oldal magassága",
        f"{page_height:.1f}"
    )

    d3.metric(
        "Oldalszám",
        pno + 1,
    )


    st.write(
        "**A megtalált hrsz. koordinátái:**"
    )

    st.code(
        f"x0 = {rect.x0:.2f}\n"
        f"y0 = {rect.y0:.2f}\n"
        f"x1 = {rect.x1:.2f}\n"
        f"y1 = {rect.y1:.2f}\n"
        f"középpont X = {cx:.2f}\n"
        f"középpont Y = {cy:.2f}"
    )


    st.write(
        "**Relatív hely az oldalon:**"
    )

    st.code(
        f"X = {(cx / page_width) * 100:.2f}%\n"
        f"Y = {(cy / page_height) * 100:.2f}%"
    )

    st.write("**OCR diagnosztika – mit lát a Tesseract?**")
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
            "A Tesseract ezen a renderelt oldalon még a 2200/8 részleteire "
            "hasonlító OCR-tokeneket sem talált."
        )

    st.write("**Képi/OCR hrsz.-keresés eredménye:**")

    if visual_rect is None:
        st.error(
            "A helyrajzi szám megtalálható a PDF kereshető szövegében, "
            "de a renderelt tervlapon az OCR nem talált hozzá megbízható vizuális helyet. "
            "A program ezért nem készít telek-központú kivágást és nem ad övezeti következtetést."
        )
        if ocr_error:
            st.code(ocr_error)
        vcx = vcy = None
    else:
        vcx = (visual_rect.x0 + visual_rect.x1) / 2
        vcy = (visual_rect.y0 + visual_rect.y1) / 2

        st.success(
            f"Képi hrsz.-jelölt található a renderelt tervlapon. "
            f"OCR-jelöltek száma: {len(visual_candidates)}."
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

        st.subheader("2. OCR-találat nagyítása")
        st.caption(
            "A piros keretnek közvetlenül a keresett helyrajzi szám feliratát kell körülvennie. "
            "Ez a v1.5 legfontosabb ellenőrzése."
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
        "A teljes tervlapon piros keret jelöli a látható szövegstruktúrából megtalált hrsz.-jelöltet. "
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
            "A képi helymeghatározás nem bizonyított, ezért a program itt megáll. "
            "Nagy/szűk kivágás és közeli övezeti keresés csak bizonyított OCR-találat után készül."
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
        f"A v1.6 különválasztja az oldal megtalálását és a helyrajzi szám vizuális "
        f"lokalizálását. A döntési pont az, hogy a {clean_hrsz} piros kerete "
        "valóban közvetlenül a terven látható helyrajzi számot jelöli-e."
    )


    st.caption(
        f"Oldal: {pno + 1} • "
        f"Hrsz-találatok: {len(candidates)} • "
        f"HÉSZ-ben felismert övezeti kódok: {len(legal)}"
    )


    doc.close()


st.divider()

st.caption(
    "TelekElőírás AI v1.8 – raszteres/OCR hrsz-helymeghatározási teszt"
)