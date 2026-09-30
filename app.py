import re
import math

import fitz
import pandas as pd
import streamlit as st


ZONE_RE = re.compile(
    r"\b(?:Lk|Lke|Ln|Vt|Vi|Gksz|Gip|Ge|Gá|K|Kb|KÖu|KÖk|"
    r"Zkp|Zkk|Z|Ev|Ek|Má|Mk|V|Ve|Lf|Üü)"
    r"(?:[-/][A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9]+){0,5}\b"
)


def ncode(s):
    s = (s or "").strip()
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s*([/-])\s*", r"\1", s)


def hits(page, hrsz):
    out = []

    variants = {
        hrsz,
        f"({hrsz})",
        hrsz.replace("/", " / "),
        hrsz.replace("/", "/ "),
    }

    for v in variants:
        out += list(page.search_for(v))

    return out


def legal_codes(data):
    doc = fitz.open(stream=data, filetype="pdf")
    codes = set()

    for page in doc:
        text = page.get_text()

        for m in ZONE_RE.finditer(text):
            codes.add(ncode(m.group(0)))

    return sorted(codes)


def labels(page):
    out = []

    for w in page.get_text("words"):
        raw = w[4].strip("()[]{}.,;:")
        code = ncode(raw)

        if ZONE_RE.fullmatch(code):
            out.append(
                {
                    "code": code,
                    "x": (w[0] + w[2]) / 2,
                    "y": (w[1] + w[3]) / 2,
                }
            )

    return out


def crop(page, r, margin=700):
    cx = (r.x0 + r.x1) / 2
    cy = (r.y0 + r.y1) / 2

    clip = fitz.Rect(
        max(0, cx - margin),
        max(0, cy - margin),
        min(page.rect.width, cx + margin),
        min(page.rect.height, cy + margin),
    )

    pix = page.get_pixmap(
        matrix=fitz.Matrix(1.8, 1.8),
        clip=clip,
        alpha=False,
    )

    return pix.tobytes("png")


def nearby(page, r, codes, radius):
    cx = (r.x0 + r.x1) / 2
    cy = (r.y0 + r.y1) / 2

    legal = set(codes)
    rows = []

    for item in labels(page):
        distance = math.hypot(
            item["x"] - cx,
            item["y"] - cy,
        )

        if distance <= radius:
            rows.append(
                {
                    "code": item["code"],
                    "distance": round(distance, 1),
                    "hesz_match": item["code"] in legal,
                }
            )

    rows.sort(
        key=lambda x: (
            not x["hesz_match"],
            x["distance"],
        )
    )

    seen = set()
    result = []

    for row in rows:
        if row["code"] not in seen:
            seen.add(row["code"])
            result.append(row)

    return result[:12]


st.set_page_config(
    page_title="TelekElőírás AI v1.1",
    page_icon="🏗️",
    layout="wide",
)


st.title("TelekElőírás AI")

st.caption(
    "v1.1 • térképi bizonyíték + "
    "HÉSZ-ellenőrzött közeli övezeti jelölt"
)


with st.sidebar:

    st.header("Tesztforrások")

    st.info(
        "A program megkeresi a helyrajzi számot a szabályozási "
        "tervben, megjeleníti annak térképi környezetét, majd "
        "megkeresi a közelben található övezeti jelöléseket."
    )

    plan = st.file_uploader(
        "Szabályozási terv (PDF)",
        type=["pdf"],
    )

    hesz = st.file_uploader(
        "HÉSZ/TÉSZ vagy övezeti melléklet (PDF)",
        type=["pdf"],
    )

    radius = st.slider(
        "Fallback keresési sugár",
        200,
        1800,
        900,
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
    "Vizsgálat indítása",
    type="primary",
    use_container_width=True,
):

    if not plan or not hesz:
        st.error(
            "Töltsd fel mindkét PDF-forrást."
        )
        st.stop()

    with st.spinner(
        "A dokumentum és az övezeti jelölések vizsgálata…"
    ):

        codes = legal_codes(
            hesz.getvalue()
        )

        doc = fitz.open(
            stream=plan.getvalue(),
            filetype="pdf",
        )

        clean_hrsz = re.sub(
            r"\s+",
            "",
            hrsz,
        )

        target = None

        for pno, page in enumerate(doc):

            found = hits(
                page,
                clean_hrsz,
            )

            if found:
                target = (
                    pno,
                    page,
                    found[0],
                )
                break


    if not target:

        st.error(
            "A helyrajzi szám nem található "
            "a PDF gépi szövegrétegében."
        )

        st.warning(
            "VISION AI SZÜKSÉGES – "
            "a terv valószínűleg raszteres vagy "
            "a helyrajzi szám nem kereshető szövegként."
        )

        st.stop()


    pno, page, rect = target


    st.success(
        f"A {clean_hrsz} helyrajzi szám megtalálva: "
        f"{pno + 1}. oldal."
    )


    st.subheader(
        "Térképi bizonyíték"
    )

    st.image(
        crop(
            page,
            rect,
        ),
        use_container_width=True,
    )

    st.caption(
        f"A fenti kivágás a {pno + 1}. oldal "
        f"{clean_hrsz} helyrajzi számának környezetét mutatja."
    )


    near = nearby(
        page,
        rect,
        codes,
        radius,
    )

    exact = [
        x for x in near
        if x["hesz_match"]
    ]


    st.subheader(
        "Övezeti előszűrés"
    )


    if exact:

        top = exact[0]

        a, b, c = st.columns(3)

        a.metric(
            "Övezet-jelölt",
            top["code"],
        )

        b.metric(
            "Státusz",
            "ELLENŐRIZENDŐ",
        )

        c.metric(
            "Felirat távolsága",
            f'{top["distance"]:.0f}',
        )

        st.warning(
            "A program a helyrajzi szám közelében olyan "
            "övezeti feliratot talált, amely a HÉSZ-ben is "
            "szerepel. Ez jelenleg közelségi jelölt, nem "
            "geometriailag bizonyított övezeti besorolás."
        )


    elif near:

        top = near[0]

        a, b = st.columns(2)

        a.metric(
            "Közeli övezeti felirat",
            top["code"],
        )

        b.metric(
            "Státusz",
            "BIZONYTALAN",
        )

        st.warning(
            "A tervoldalon találtunk közeli övezeti feliratot, "
            "de ahhoz nem találtunk pontos HÉSZ-kódegyezést."
        )


    else:

        st.error(
            "VISION AI SZÜKSÉGES"
        )

        st.write(
            "A helyrajzi szám helye ismert, de a PDF "
            "szövegrétegéből nem nyerhető megbízható "
            "övezeti jelölt."
        )


    if near:

        st.subheader(
            "Közeli övezeti jelölések"
        )

        df = pd.DataFrame(near)

        df = df.rename(
            columns={
                "code": "Övezeti jel",
                "distance": "Távolság",
                "hesz_match": "Szerepel a HÉSZ-ben",
            }
        )

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
        )


    st.checkbox(
        "Szakmailag visszaellenőriztem az eredményt"
    )


    st.caption(
        f"Felhasznált tervoldal: {pno + 1}. oldal • "
        f"HÉSZ-ben felismert egyedi övezeti kódok: "
        f"{len(codes)}"
    )


st.divider()

st.caption(
    "Tesztverzió. Az automatikusan kiválasztott övezeti jelölt "
    "nem helyettesíti a hatályos szabályozási terv és a HÉSZ/TÉSZ "
    "szakmai ellenőrzését."
)
