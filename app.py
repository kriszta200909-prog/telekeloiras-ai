import io
import re

import fitz
import streamlit as st
from PIL import Image, ImageDraw


# =========================================================
# TELEKELŐÍRÁS AI v2.5
# NATÍV PDF HELYMEGHATÁROZÁS – ELLENŐRZÖTT ROTÁCIÓS LEKÉPEZÉS
# =========================================================

st.set_page_config(
    page_title="TelekElőírás AI v2.5",
    page_icon="🏗️",
    layout="wide",
)


def normalize_hrsz(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip())


def hrsz_variants(hrsz: str):
    return [
        hrsz,
        f"({hrsz})",
        hrsz.replace("/", " / "),
        hrsz.replace("/", "/ "),
        hrsz.replace("/", " /"),
    ]


def find_hrsz(doc, hrsz: str):
    """Natív PDF-szövegkeresés. OCR-t nem használ."""
    hits = []
    seen = set()

    for pno in range(len(doc)):
        page = doc[pno]

        for variant in hrsz_variants(hrsz):
            for rect in page.search_for(variant):
                key = (
                    pno,
                    round(rect.x0, 2),
                    round(rect.y0, 2),
                    round(rect.x1, 2),
                    round(rect.y1, 2),
                )
                if key not in seen:
                    seen.add(key)
                    hits.append(
                        {
                            "page_number": pno,
                            "pdf_rect": fitz.Rect(rect),
                        }
                    )
    return hits


def visible_rect(page, pdf_rect):
    """
    FONTOS v2.5:
    A search_for() találatára NEM alkalmazunk page.transformation_matrix-ot.

    A vizsgált Tiszaújváros CAD-PDF-ben a keresési találat koordinátája
    az oldal elforgatás előtti koordinátarendszerében van. A látható,
    renderelt oldal helyes koordinátáját közvetlenül a rotation_matrix adja.
    """
    return fitz.Rect(pdf_rect) * page.rotation_matrix


def render_page(page, zoom=0.45):
    pix = page.get_pixmap(
        matrix=fitz.Matrix(zoom, zoom),
        alpha=False,
    )
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def rect_to_pixels(page, image, rect):
    """A látható oldal koordinátáját a renderelt kép pixelkoordinátájára viszi."""
    sx = image.width / page.rect.width
    sy = image.height / page.rect.height

    return (
        rect.x0 * sx,
        rect.y0 * sy,
        rect.x1 * sx,
        rect.y1 * sy,
    )


def marked_full_page(page, visible_hit):
    image = render_page(page, zoom=0.22)
    draw = ImageDraw.Draw(image)

    x0, y0, x1, y1 = rect_to_pixels(page, image, visible_hit)

    # A hrsz. felirat nagyon kicsi, ezért a teljes oldalon jól látható keretet rajzolunk.
    pad = max(7, int(min(image.size) * 0.008))
    draw.rectangle(
        (x0 - pad, y0 - pad, x1 + pad, y1 + pad),
        outline="red",
        width=max(3, pad // 3),
    )
    return image


def parcel_crop(page, visible_hit, scale=0.10, zoom=0.75):
    """
    A HELYES, már elforgatott koordináta környezetét a teljes renderelt képből
    vágja ki. Így a PDF clip-koordináták újabb félreértelmezése nem tudja
    eltolni a kivágást.
    """
    image = render_page(page, zoom=zoom)
    x0, y0, x1, y1 = rect_to_pixels(page, image, visible_hit)

    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2

    half_w = image.width * scale
    half_h = image.height * scale

    left = max(0, int(cx - half_w))
    top = max(0, int(cy - half_h))
    right = min(image.width, int(cx + half_w))
    bottom = min(image.height, int(cy + half_h))

    crop = image.crop((left, top, right, bottom))

    draw = ImageDraw.Draw(crop)
    rx0 = x0 - left
    ry0 = y0 - top
    rx1 = x1 - left
    ry1 = y1 - top

    pad = max(10, int(min(crop.size) * 0.025))
    draw.rectangle(
        (rx0 - pad, ry0 - pad, rx1 + pad, ry1 + pad),
        outline="red",
        width=max(4, pad // 4),
    )

    return crop


st.title("TelekElőírás AI")
st.caption(
    "v2.5 • natív PDF-szövegkeresés • helyes rotation_matrix leképezés • "
    "OCR nélkül • telekhely-ellenőrzési verzió"
)

with st.sidebar:
    st.header("Tesztforrások")
    st.info(
        "A v2.5 célja kizárólag a telek helyének biztos meghatározása. "
        "A helyrajzi számot a PDF kereshető szövegrétegében keresi, majd a "
        "találatot közvetlenül page.rotation_matrix-szal vetíti a látható tervlapra. "
        "page.transformation_matrix nincs használva."
    )

    plan = st.file_uploader(
        "Szabályozási terv (PDF)",
        type=["pdf"],
        key="plan",
    )

    hesz = st.file_uploader(
        "HÉSZ/TÉSZ vagy övezeti melléklet (PDF) – ebben a verzióban még nem elemzi",
        type=["pdf"],
        key="hesz",
    )

    crop_scale = st.slider(
        "Telek környezetének mérete",
        min_value=5,
        max_value=20,
        value=10,
        step=1,
        help="A renderelt teljes oldal szélességének/magasságának százaléka a találat körül.",
    )

c1, c2 = st.columns(2)
town = c1.text_input("Település", "Tiszaújváros")
hrsz = c2.text_input("Helyrajzi szám", "2200/8")

if st.button(
    "v2.5 ellenőrzött telekhely keresés indítása",
    type="primary",
    use_container_width=True,
):
    if not plan:
        st.error("Töltsd fel a szabályozási terv PDF-et.")
        st.stop()

    clean_hrsz = normalize_hrsz(hrsz)

    if not clean_hrsz:
        st.error("Adj meg helyrajzi számot.")
        st.stop()

    plan_bytes = plan.getvalue()

    with st.spinner("Natív PDF-szövegkeresés…"):
        doc = fitz.open(stream=plan_bytes, filetype="pdf")
        hits = find_hrsz(doc, clean_hrsz)

    if not hits:
        st.error(
            f"A(z) {clean_hrsz} helyrajzi számot a PDF kereshető szövegrétegében nem találtam."
        )
        doc.close()
        st.stop()

    target = hits[0]
    pno = target["page_number"]
    page = doc[pno]
    pdf_rect = target["pdf_rect"]

    # A most már bizonyított helyes leképezés.
    vrect = visible_rect(page, pdf_rect)

    st.success(
        f"{clean_hrsz} megtalálva • {pno + 1}. oldal • "
        f"natív PDF-találatok száma: {len(hits)} • OCR nem futott"
    )

    st.markdown("## 1. Helymeghatározási diagnosztika")

    d1, d2, d3 = st.columns(3)
    d1.metric("Azonosított tervoldal", pno + 1)
    d2.metric("Oldal elforgatása", f"{page.rotation}°")
    d3.metric("Találatok száma", len(hits))

    st.code(
        "\n".join(
            [
                f"PDF search_for bbox = ({pdf_rect.x0:.2f}, {pdf_rect.y0:.2f}, "
                f"{pdf_rect.x1:.2f}, {pdf_rect.y1:.2f})",
                f"rotation_matrix bbox = ({vrect.x0:.2f}, {vrect.y0:.2f}, "
                f"{vrect.x1:.2f}, {vrect.y1:.2f})",
                f"látható oldal = {page.rect.width:.2f} × {page.rect.height:.2f}",
                f"MediaBox = {page.mediabox}",
                f"CropBox = {page.cropbox}",
                "page.transformation_matrix = NINCS HASZNÁLVA",
            ]
        )
    )

    st.markdown("## 2. Hrsz.-találat nagyítása")
    st.caption(
        "A piros keretnek közvetlenül a keresett helyrajzi szám feliratát kell körülvennie."
    )

    crop = parcel_crop(
        page,
        vrect,
        scale=crop_scale / 100.0,
        zoom=0.75,
    )
    st.image(crop, use_container_width=True)

    st.markdown("## 3. Teljes tervoldal")
    st.caption(
        "A piros jelölés ugyanannak a natív PDF-találatnak a helyét mutatja a teljes tervlapon."
    )

    full = marked_full_page(page, vrect)
    st.image(full, use_container_width=True)

    st.markdown("## 4. Következő vizsgálat")
    st.info(
        "Ebben a verzióban szándékosan nincs automatikus övezeti besorolás. "
        "Először azt ellenőrizzük, hogy a piros jelölés és a kivágás valóban a "
        f"{clean_hrsz} telket mutatja. Ha igen, a következő verzió erre a már "
        "ellenőrzött koordinátára építi az övezeti jel és a HÉSZ-előírások keresését."
    )

    doc.close()
