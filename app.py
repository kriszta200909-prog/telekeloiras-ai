import io
import re

import fitz
import streamlit as st
from PIL import Image, ImageDraw


# =========================================================
# TELEKELŐÍRÁS AI v3.0
# NATÍV PDF HELYMEGHATÁROZÁS – ELLENŐRZÖTT ROTÁCIÓS LEKÉPEZÉS
# =========================================================

st.set_page_config(
    page_title="TelekElőírás AI v3.0",
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
    FONTOS v3.0:
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



# =========================================================
# v3.0 – NATÍV PDF DOKUMENTUMFELDERÍTÉS
# NINCS FIX OLDALSZÁM, NINCS OCR
# =========================================================

ZONE_ROOTS = (
    "GIP", "GKSZ", "KG", "K", "VT", "V", "LKE", "LF", "LK", "L", "ÜÜ",
    "ÜH", "KÖU", "KÖK", "KÖM", "EV", "EG", "MÁ", "MK", "VÍZ", "V"
)

def normalize_text(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def extract_page_text(page):
    try:
        return page.get_text("text") or ""
    except Exception:
        return ""

def page_has_any(text, terms):
    low = text.lower()
    return any(t.lower() in low for t in terms)

def classify_context(text):
    """Tartalom alapján osztályoz, nem oldalszám alapján."""
    low = text.lower()
    scores = {
        "övezeti jelölés / jelmagyarázat": 0,
        "általános övezeti előírás": 0,
        "beépítési mutató / táblázat": 0,
    }

    for w in ("jelmagyarázat", "övezet", "övezeti jel", "területfelhasználás"):
        if w in low:
            scores["övezeti jelölés / jelmagyarázat"] += 2

    for w in ("előírás", "általános", "ipari gazdasági", "gazdasági terület",
              "rendeltetés", "elhelyezhető", "nem helyezhető"):
        if w in low:
            scores["általános övezeti előírás"] += 2

    for w in ("beépítési", "beépítettség", "építménymagasság", "épületmagasság",
              "legkisebb telek", "zöldfelület", "szintterületi", "oldalkert",
              "előkert", "hátsókert", "%"):
        if w in low:
            scores["beépítési mutató / táblázat"] += 2

    label, score = max(scores.items(), key=lambda kv: kv[1])
    return label if score > 0 else "egyéb találat", score

def snippets_around(text, needle, radius=260):
    """Keresési találatok rövid szövegkörnyezete."""
    out = []
    if not text or not needle:
        return out
    low = text.lower()
    nlow = needle.lower()
    start = 0
    while True:
        i = low.find(nlow, start)
        if i < 0:
            break
        a = max(0, i - radius)
        b = min(len(text), i + len(needle) + radius)
        out.append(normalize_text(text[a:b]))
        start = i + max(1, len(needle))
        if len(out) >= 12:
            break
    return out

def discover_zone_terms(doc):
    """
    A teljes PDF-ben előforduló, tipikus övezeti gyököket számlálja.
    Ez NEM dönti el a telek övezetét; csak a dokumentum szókészletét deríti fel.
    """
    counts = {}
    pages = {}
    rx = re.compile(r"\b(?:Gip|Gksz|Köu|Kök|Köm|Lke|Lf|Lk|Vt|Má|Mk|Ev|Eg|K|V)(?:[-/][A-Za-z0-9ÁÉÍÓÖŐÚÜŰáéíóöőúüű]+)*\b",
                    re.IGNORECASE)
    for pno in range(len(doc)):
        txt = extract_page_text(doc[pno])
        for m in rx.finditer(txt):
            raw = normalize_text(m.group(0))
            key = raw.upper()
            counts[key] = counts.get(key, 0) + 1
            pages.setdefault(key, set()).add(pno + 1)
    result = []
    for key, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        result.append({
            "Kifejezés": key,
            "Előfordulás": cnt,
            "Oldalak": ", ".join(map(str, sorted(pages[key])[:20])),
        })
    return result

def search_term_in_document(doc, term):
    """Natív PDF-szövegkeresés a teljes dokumentumban, tartalmi osztályozással."""
    rows = []
    for pno in range(len(doc)):
        page = doc[pno]
        rects = page.search_for(term)
        if not rects:
            # Egyes CAD/PDF-ekben a search_for és a kinyert text eltérően viselkedhet.
            txt = extract_page_text(page)
            if term.lower() not in txt.lower():
                continue
        txt = extract_page_text(page)
        snippets = snippets_around(txt, term)
        if not snippets:
            snippets = [normalize_text(txt[:700])]
        for snip in snippets[:4]:
            cls, score = classify_context(snip)
            rows.append({
                "Oldal": pno + 1,
                "Típus": cls,
                "Pontszám": score,
                "Szövegkörnyezet": snip,
            })
    # fontosabb tartalmi találatok előre, de az oldalszámot csak megjelenítjük
    rows.sort(key=lambda r: (-r["Pontszám"], r["Oldal"]))
    return rows

def show_document_discovery(plan_doc, hesz_doc=None):
    st.markdown("## 5. Dokumentumszerkezet felderítése")
    st.info(
        "Ez a verzió nem használ fix oldalszámokat. A teljes kereshető PDF-szövegréteget "
        "vizsgálja, és azt deríti fel, hol találhatók övezeti jelölések, általános "
        "előírások és beépítési mutatók. A 14., 56. és 123. oldal nincs belekódolva."
    )

    docs = [("Szabályozási terv", plan_doc)]
    if hesz_doc is not None:
        docs.append(("HÉSZ/TÉSZ", hesz_doc))

    for name, doc in docs:
        with st.expander(f"{name} – felismert övezeti kifejezések", expanded=True):
            terms = discover_zone_terms(doc)
            if terms:
                st.dataframe(terms[:80], use_container_width=True, hide_index=True)
            else:
                st.warning("Nem találtam tipikus övezeti kifejezést a natív szövegrétegben.")

    st.markdown("## 6. Övezeti kifejezés vizsgálata")
    st.caption(
        "A tesztmezőben most ellenőrizhetjük a dokumentumban szereplő övezeti kifejezést. "
        "A program a teljes PDF-ben keresi, és tartalom alapján próbálja elkülöníteni "
        "a jelölést, az általános előírást és a beépítési mutatókat."
    )
    zone_term = st.text_input(
        "Vizsgálandó övezeti kifejezés",
        value="GIP",
        key="zone_term_v30",
    ).strip()

    if zone_term:
        all_rows = []
        for name, doc in docs:
            rows = search_term_in_document(doc, zone_term)
            for r in rows:
                r = {"Forrás": name, **r}
                all_rows.append(r)

        if not all_rows:
            st.warning(f"A(z) {zone_term} kifejezést egyik feltöltött PDF natív szövegrétegében sem találtam.")
        else:
            st.success(f"{len(all_rows)} releváns szövegkörnyezetet találtam a(z) {zone_term} kifejezéshez.")
            st.dataframe(all_rows, use_container_width=True, hide_index=True)

            st.markdown("### Tartalomtípusonként")
            groups = {}
            for r in all_rows:
                groups.setdefault(r["Típus"], []).append(r)
            for typ in (
                "övezeti jelölés / jelmagyarázat",
                "általános övezeti előírás",
                "beépítési mutató / táblázat",
                "egyéb találat",
            ):
                if typ not in groups:
                    continue
                with st.expander(f"{typ} – {len(groups[typ])} találat", expanded=(typ != "egyéb találat")):
                    for r in groups[typ][:12]:
                        st.markdown(f"**{r['Forrás']} • {r['Oldal']}. oldal**")
                        st.write(r["Szövegkörnyezet"])


st.title("TelekElőírás AI")
st.caption(
    "v3.0 • natív PDF-keresés • stabil telekhely • dokumentumszerkezet-felderítés • OCR nélkül"
)

with st.sidebar:
    st.header("Tesztforrások")
    st.info(
        "A v3.0 megtartja az ellenőrzött telekhely-meghatározást, és a teljes PDF kereshető "
        "szövegrétegében felderíti az övezeti jelölések, általános előírások és beépítési "
        "mutatók lehetséges helyeit. Fix oldalszámokat nem használ. "
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
        "HÉSZ/TÉSZ vagy övezeti melléklet (PDF)",
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
    "v3.0 dokumentumfelderítés indítása",
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
    
    hesz_doc = None
    try:
        if hesz:
            hesz_doc = fitz.open(stream=hesz.getvalue(), filetype="pdf")

        st.divider()
        show_document_discovery(doc, hesz_doc)
    finally:
        if hesz_doc is not None:
            hesz_doc.close()

    doc.close()
