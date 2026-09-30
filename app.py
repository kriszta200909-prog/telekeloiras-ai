import re
import math

import fitz
import pandas as pd
import streamlit as st


# =========================================================
# TELEKELŐÍRÁS AI v1.2.1
# DIAGNOSZTIKAI VERZIÓ
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
    page_title="TelekElőírás AI v1.2.1",
    page_icon="🏗️",
    layout="wide",
)


st.title(
    "TelekElőírás AI"
)

st.caption(
    "v1.2.1 • diagnosztika • "
    "PDF-oldal és hrsz-koordináta ellenőrzése"
)


with st.sidebar:

    st.header(
        "Tesztforrások"
    )

    st.info(
        "Ez a diagnosztikai verzió megmutatja a teljes "
        "megtalált tervoldalt és a helyrajzi szám "
        "környezetének többféle kivágását."
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
    "Diagnosztikai vizsgálat indítása",
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


    st.success(
        f"{clean_hrsz} megtalálva • "
        f"{pno + 1}. oldal • "
        f"találatok száma: {len(candidates)}"
    )


    # =====================================================
    # 1. DIAGNOSZTIKAI ADATOK
    # =====================================================

    st.subheader(
        "1. PDF-diagnosztika"
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


    # =====================================================
    # 2. TELJES OLDAL
    # =====================================================

    st.subheader(
        "2. A teljes 205. tervoldal"
    )

    st.caption(
        "Ezzel ellenőrizzük, hogy a PyMuPDF ugyanazt "
        "a tervlapot rendereli-e, amelyet a PDF-ben látunk."
    )


    try:

        full_image = render_full_page(
            page
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
        "3. Nagy környezeti kivágás"
    )


    try:

        img_large, clip_large = render_crop(
            page,
            rect,
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
        "4. Szűk hrsz-környezet"
    )


    try:

        img_small, clip_small = render_crop(
            page,
            rect,
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
        "5. Felismert közeli övezeti feliratok"
    )


    near = nearby_labels(
        page,
        rect,
        legal,
        radius,
    )


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
        "Diagnosztikai eredmény"
    )

    st.warning(
        "Ebben a verzióban szándékosan nem történik "
        "automatikus övezeti besorolás."
    )

    st.write(
        "A három térképi nézetből azt vizsgáljuk, hogy "
        "a PDF szövegrétegének koordinátái és a látható "
        "szabályozási terv geometriája azonos "
        "koordinátarendszerben helyezkednek-e el."
    )


    st.caption(
        f"Oldal: {pno + 1} • "
        f"Hrsz-találatok: {len(candidates)} • "
        f"HÉSZ-ben felismert övezeti kódok: {len(legal)}"
    )


    doc.close()


st.divider()

st.caption(
    "TelekElőírás AI v1.2.1 – diagnosztikai tesztverzió"
)
