import re
import math

import fitz
import pandas as pd
import streamlit as st


# =========================================================
# TELEKELŐÍRÁS AI v1.5
# VIZUÁLIS HRSZ-KERESÉSI TESZT
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
    page_title="TelekElőírás AI v1.5",
    page_icon="🏗️",
    layout="wide",
)


st.title(
    "TelekElőírás AI"
)

st.caption(
    "v1.5 • vizuális hrsz-keresés • "
    "látható szövegréteg ellenőrzése a tervlapon"
)


with st.sidebar:

    st.header(
        "Tesztforrások"
    )

    st.info(
        "A program először megkeresi, melyik tervoldalon szerepel a helyrajzi szám. "
        "Ezután külön, a látható oldal szövegstruktúrájában keresi meg annak "
        "tényleges vizuális helyét. A v1.4 hibás koordinátáját nem használja telekazonosításra."
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
    "v1.5 vizuális keresés indítása",
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

        for pno in range(
            len(doc)
        ):

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

    # v1.5: a search_for találat csak az oldal azonosítására szolgál.
    # A tényleges vizuális helyet külön keressük a látható text-spanek között.
    visual_candidates = visual_hrsz_candidates(page, clean_hrsz)

    if visual_candidates:
        visual_rect = visual_candidates[0]["rect"]
        was_corrected = False
        coordinate_method = visual_candidates[0]["source"]
    else:
        visual_rect = None
        was_corrected = False
        coordinate_method = "nincs látható text-span találat"


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

    st.write("**Vizuális hrsz.-keresés eredménye:**")

    if visual_rect is None:
        st.error(
            "A helyrajzi szám ugyan megtalálható a PDF kereshető szövegében, "
            "de a látható oldal szövegstruktúrájában nem sikerült megbízható "
            "helyet találni hozzá. Ez fontos eredmény: a program nem használja "
            "telekpozícióként a v1.4 hibás koordinátáját."
        )
        vcx = vcy = None
    else:
        vcx = (visual_rect.x0 + visual_rect.x1) / 2
        vcy = (visual_rect.y0 + visual_rect.y1) / 2

        st.success(
            f"Látható hrsz.-jelölt található a tervlapon. "
            f"Vizuális jelöltek száma: {len(visual_candidates)}."
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
            f"forrás = {coordinate_method}"
        )

        st.subheader("2. Vizuális találat nagyítása")
        st.caption(
            "A piros keretnek közvetlenül a keresett helyrajzi szám feliratát kell körülvennie. "
            "Ez a v1.5 legfontosabb ellenőrzése."
        )
        try:
            candidate_img, candidate_clip = render_visual_candidate(
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
        f"A v1.5 különválasztja az oldal megtalálását és a helyrajzi szám vizuális "
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
    "TelekElőírás AI v1.5 – vizuális hrsz-helymeghatározási teszt"
)
