import re
import math

import fitz
import pandas as pd
import streamlit as st


# ---------------------------------------------------------
# ÖVEZETI KÓD FELISMERÉS
# ---------------------------------------------------------

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
        results.extend(page.search_for(variant))

    # duplikációk kiszűrése
    unique = []

    for r in results:

        key = (
            round(r.x0, 1),
            round(r.y0, 1),
            round(r.x1, 1),
            round(r.y1, 1),
        )

        if key not in [
            (
                round(x.x0, 1),
                round(x.y0, 1),
                round(x.x1, 1),
                round(x.y1, 1),
            )
            for x in unique
        ]:
            unique.append(r)

    return unique


# ---------------------------------------------------------
# HÉSZ-BEN SZEREPLŐ ÖVEZETI KÓDOK
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
# TERVOLDALON TALÁLHATÓ ÖVEZETI FELIRATOK
# ---------------------------------------------------------

def zone_labels(page):

    words = page.get_text("words")

    labels = []

    # 1. Egyetlen PDF-szóként szereplő kódok
    for w in words:

        raw = w[4].strip(
            "()[]{}.,;:"
        )

        code = ncode(raw)

        if ZONE_RE.fullmatch(code):

            labels.append(
                {
                    "code": code,
                    "x": (w[0] + w[2]) / 2,
                    "y": (w[1] + w[3]) / 2,
                }
            )

    return labels


# ---------------------------------------------------------
# TÉRKÉPI KIVÁGÁS
# ---------------------------------------------------------

def make_map_crop(page, rect):

    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2

    # A kivágás méretét az oldal méretéhez igazítjuk.
    half_width = min(
        page.rect.width * 0.20,
        850,
    )

    half_height = min(
        page.rect.height * 0.20,
        650,
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

    # A PDF-részletet PNG-vé rendereljük.
    matrix = fitz.Matrix(
        2.0,
        2.0,
    )

    pix = page.get_pixmap(
        matrix=matrix,
        clip=clip,
        alpha=False,
    )

    png_bytes = pix.tobytes("png")

    return png_bytes, clip


# ---------------------------------------------------------
# KÖZELI FELIRATOK – CSAK BIZONYÍTÉKKÉNT
# ---------------------------------------------------------

def nearby_labels(
    page,
    rect,
    legal,
    radius,
):

    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2

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
                    "code": item["code"],
                    "distance": round(
                        distance,
                        1,
                    ),
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
# STREAMLIT
# ---------------------------------------------------------

st.set_page_config(
    page_title="TelekElőírás AI v1.2",
    page_icon="🏗️",
    layout="wide",
)


st.title("TelekElőírás AI")

st.caption(
    "v1.2 • helyrajzi szám lokalizálása • "
    "térképi bizonyíték • biztonságos övezeti előszűrés"
)


with st.sidebar:

    st.header("Tesztforrások")

    st.info(
        "A program először megkeresi a helyrajzi számot "
        "a szabályozási tervben. Ezután megjeleníti a "
        "tényleges térképi környezetét. Az övezeti feliratokat "
        "egyelőre bizonyítékként mutatja, és nem tekinti "
        "automatikusan a telek övezeti besorolásának."
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


run = st.button(
    "Vizsgálat indítása",
    type="primary",
    use_container_width=True,
)


if run:

    if not plan:

        st.error(
            "Töltsd fel a szabályozási terv PDF-et."
        )

        st.stop()

    if not hesz:

        st.error(
            "Töltsd fel a HÉSZ/TÉSZ vagy övezeti PDF-et."
        )

        st.stop()


    clean_hrsz = re.sub(
        r"\s+",
        "",
        hrsz,
    )


    with st.spinner(
        "A helyrajzi szám keresése és a térképi környezet vizsgálata…"
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
                        "page_number": pno,
                        "rect": rect,
                    }
                )


    if not candidates:

        st.error(
            f"A {clean_hrsz} helyrajzi szám nem található "
            "a PDF kereshető szövegrétegében."
        )

        st.warning(
            "A következő fejlesztési lépésben az ilyen "
            "esetekhez képi / Vision alapú felismerést kell "
            "beépíteni."
        )

        doc.close()

        st.stop()


    # Első találat – jelenleg tesztüzem.
    target = candidates[0]

    pno = target[
        "page_number"
    ]

    rect = target[
        "rect"
    ]

    page = doc[pno]


    st.success(
        f"A {clean_hrsz} helyrajzi szám megtalálva: "
        f"{pno + 1}. oldal."
    )


    if len(candidates) > 1:

        st.info(
            f"A dokumentumban összesen "
            f"{len(candidates)} lehetséges találat van. "
            "A v1.2 jelenleg az első találat térképi "
            "környezetét mutatja."
        )


    # -----------------------------------------------------
    # TÉRKÉPI BIZONYÍTÉK
    # -----------------------------------------------------

    st.subheader(
        "Térképi bizonyíték"
    )


    try:

        image_bytes, clip = make_map_crop(
            page,
            rect,
        )

        if image_bytes:

            st.image(
                image_bytes,
                caption=(
                    f"{town} – {clean_hrsz} hrsz. "
                    f"– szabályozási terv "
                    f"{pno + 1}. oldal"
                ),
                use_container_width=True,
            )

        else:

            st.error(
                "A térképi kivágás létrejött, "
                "de nem tartalmaz képadatot."
            )


    except Exception as e:

        st.error(
            "A térképi kivágás megjelenítése "
            "technikai hibába ütközött."
        )

        st.code(
            str(e)
        )


    # -----------------------------------------------------
    # ÖVEZETI FELIRATOK
    # -----------------------------------------------------

    near = nearby_labels(
        page,
        rect,
        legal,
        radius,
    )


    st.subheader(
        "Övezeti vizsgálat"
    )


    a, b, c = st.columns(3)

    a.metric(
        "Övezet",
        "—",
    )

    b.metric(
        "Státusz",
        "NEM BIZONYÍTOTT",
    )

    c.metric(
        "Közeli övezeti feliratok",
        len(near),
    )


    st.warning(
        "A v1.2 nem azonosítja automatikusan a telek "
        "övezetét pusztán a legközelebbi felirat alapján. "
        "Az alábbi feliratok a helyrajzi szám térképi "
        "környezetében található bizonyítékok."
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
                    "Távolság a hrsz. feliratától",
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
            "A megadott vizsgálati sugáron belül "
            "nem találtunk géppel felismerhető "
            "övezeti feliratot."
        )


    # -----------------------------------------------------
    # KÖVETKEZTETÉS
    # -----------------------------------------------------

    st.subheader(
        "Automatikus következtetés"
    )

    st.error(
        "Övezet automatikusan még nem bizonyítható."
    )

    st.write(
        "A következő fejlesztési lépés feladata a telek "
        "geometriájának és az övezethatároknak a vizsgálata. "
        "Csak ezután engedjük meg a programnak, hogy konkrét "
        "övezeti besorolást adjon."
    )


    st.checkbox(
        "Szakmailag visszaellenőriztem az eredményt"
    )


    st.caption(
        f"Felhasznált tervoldal: {pno + 1}. oldal • "
        f"HÉSZ-ben felismert egyedi övezeti kódok: "
        f"{len(legal)} • "
        f"Helyrajziszám-találatok: {len(candidates)}"
    )


    doc.close()


st.divider()

st.caption(
    "Tesztverzió. A program által megjelenített adatok "
    "nem helyettesítik a hatályos szabályozási terv, "
    "HÉSZ/TÉSZ és egyéb településrendezési dokumentumok "
    "szakmai ellenőrzését."
)
