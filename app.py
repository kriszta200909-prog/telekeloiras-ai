# TelekElőírás AI v7.0
# Tiszta, újraírt Streamlit alkalmazás.
# Cél: telek -> hivatalos NJT-forrás -> szabályozási terv -> övezeti jelölt
#      -> forrásolt övezeti előírások.
#
# Fontos: döntéstámogató eszköz, nem hatósági vagy jogi állásfoglalás.

import io
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin

import fitz
import streamlit as st
from PIL import Image, ImageDraw


st.set_page_config(
    page_title="TelekElőírás AI v7.0",
    page_icon="🏗️",
    layout="wide",
)

EKOZMU_MAP = "https://ekozmu.e-epites.hu/lakossag/#/lakossag/kozmuterkep"

# Validált hivatalos forrásindex.
# Új település később egyetlen új rekorddal felvehető.
HESZ_INDEX = {
    "tiszaujvaros": {
        "municipality": "Tiszaújváros",
        "title": "Tiszaújváros Építési Szabályzatáról",
        "regulation": "11/2018. (VI.12.) önkormányzati rendelet",
        "url": "https://njt.jog.gov.hu/jogszabaly/2018-11-SP-5Y1228",
    },
}


# ---------------------------------------------------------------------
# Általános segédfüggvények
# ---------------------------------------------------------------------

def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def key_text(value):
    s = unicodedata.normalize("NFKD", clean_text(value))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold()


def normalize_hrsz(value):
    return re.sub(r"\s+", "", str(value or "").strip())


def hrsz_variants(hrsz):
    h = normalize_hrsz(hrsz)
    return list(dict.fromkeys([
        h,
        f"({h})",
        h.replace("/", " / "),
        h.replace("/", "/ "),
        h.replace("/", " /"),
    ]))


def http_get(url, timeout=25, accept="text/html,*/*;q=0.8"):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 TelekEloirasAI/7.0",
            "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.5",
            "Accept": accept,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return (
            response.read(),
            response.geturl(),
            getattr(response, "status", 200),
            response.headers.get_content_charset() or "utf-8",
            response.headers.get("Content-Type", ""),
        )


# ---------------------------------------------------------------------
# HTML feldolgozás
# ---------------------------------------------------------------------

class HTMLCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.links = []
        self._skip = 0
        self._href = None
        self._link_text = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "noscript"):
            self._skip += 1
            return

        if tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []

        if not self._skip and tag in (
            "p", "div", "br", "li", "tr", "td", "th",
            "h1", "h2", "h3", "h4", "h5",
        ):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in ("script", "style", "noscript"):
            if self._skip:
                self._skip -= 1
            return

        if tag == "a" and self._href is not None:
            self.links.append((clean_text(" ".join(self._link_text)), self._href))
            self._href = None
            self._link_text = []

        if not self._skip and tag in ("p", "div", "li", "tr", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._link_text.append(data)

    def text(self):
        return clean_text(" ".join(self.parts))


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_njt_page(url):
    try:
        raw, final_url, status, charset, content_type = http_get(url)
        html = raw.decode(charset, errors="replace")
        parser = HTMLCollector()
        parser.feed(html)
        text = parser.text()

        ok = (
            status == 200
            and "njt.jog.gov.hu" in final_url
            and len(text) > 300
        )
        return {
            "ok": ok,
            "url": final_url,
            "status_code": status,
            "content_type": content_type,
            "html": html,
            "text": text,
            "links": parser.links,
            "error": "" if ok else "A válasz nem tartalmazott elegendő feldolgozható NJT-szöveget.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "status_code": getattr(getattr(exc, "code", None), "status", None),
            "content_type": "",
            "html": "",
            "text": "",
            "links": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def discover_attachments(page):
    out = []
    seen = set()

    for label, href in page.get("links", []):
        if not href:
            continue

        full = urljoin(page.get("url", ""), href)
        hay = key_text(f"{label} {full}")

        if not any(x in hay for x in (
            "melleklet", ".pdf", "/document/", "/download/"
        )):
            continue

        if full in seen:
            continue

        seen.add(full)
        out.append({
            "Megnevezés": label or "NJT melléklet",
            "URL": full,
        })

    return out


def named_annexes(text):
    patterns = [
        r"\b\d+(?:\.\d+)?\.\s*melléklet[^.;]{0,180}",
        r"\b\d+\.\s*melléklet[^.;]{0,180}",
    ]
    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, text or "", flags=re.I):
            item = clean_text(match.group(0))
            if item and item not in found:
                found.append(item)
    return found[:20]


# ---------------------------------------------------------------------
# HÉSZ/TÉSZ forrás
# ---------------------------------------------------------------------

def source_for_town(town, manual_url=""):
    if manual_url.strip():
        return {
            "municipality": town,
            "title": "Kézzel megadott NJT-forrás",
            "regulation": "",
            "url": manual_url.strip(),
            "source": "kézi NJT URL",
        }

    meta = HESZ_INDEX.get(key_text(town))
    if meta:
        return {**meta, "source": "validált forrásindex"}

    return None


def validate_njt_source(town, meta, page):
    if not meta or not page.get("ok"):
        return False, {}

    body = key_text(page.get("text"))
    town_key = key_text(town)

    checks = {
        "NJT-domain": "njt.jog.gov.hu" in page.get("url", ""),
        "település": town_key in body,
        "építési szabályzat": (
            "epitesi szabalyzat" in body
            or "helyi epitesi szabalyzat" in body
        ),
    }
    return all(checks.values()), checks


# ---------------------------------------------------------------------
# PDF kezelés
# ---------------------------------------------------------------------

@st.cache_data(show_spinner=False, ttl=3600)
def download_pdf(url):
    raw, final_url, status, _, content_type = http_get(
        url,
        timeout=90,
        accept="application/pdf,*/*;q=0.8",
    )
    if status != 200:
        raise RuntimeError(f"HTTP {status}")

    # Sok szerver hibás Content-Type-pal ad PDF-et, ezért a fejlécet is ellenőrizzük.
    if not raw.startswith(b"%PDF"):
        raise RuntimeError(
            f"A letöltött tartalom nem PDF ({content_type or 'ismeretlen tartalomtípus'})."
        )
    return raw, final_url


def open_uploaded_pdf(uploaded):
    if uploaded is None:
        return None
    return fitz.open(stream=uploaded.getvalue(), filetype="pdf")


def open_pdf_bytes(raw):
    return fitz.open(stream=raw, filetype="pdf")


def find_hrsz(doc, hrsz):
    hits = []
    seen = set()

    for page_no in range(len(doc)):
        page = doc[page_no]
        for variant in hrsz_variants(hrsz):
            for rect in page.search_for(variant):
                signature = (
                    page_no,
                    round(rect.x0, 1),
                    round(rect.y0, 1),
                    round(rect.x1, 1),
                    round(rect.y1, 1),
                )
                if signature not in seen:
                    seen.add(signature)
                    hits.append({
                        "page_number": page_no,
                        "pdf_rect": fitz.Rect(rect),
                    })
    return hits


def visible_rect(page, pdf_rect):
    return fitz.Rect(pdf_rect) * page.rotation_matrix


def render_page(page, zoom=0.75):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def rect_to_pixels(page, image, rect):
    return (
        rect.x0 * image.width / page.rect.width,
        rect.y0 * image.height / page.rect.height,
        rect.x1 * image.width / page.rect.width,
        rect.y1 * image.height / page.rect.height,
    )


def parcel_crop(page, hit_rect, scale=0.11, zoom=0.8):
    image = render_page(page, zoom=zoom)
    rect = visible_rect(page, hit_rect)
    x0, y0, x1, y1 = rect_to_pixels(page, image, rect)

    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    half_w = image.width * scale
    half_h = image.height * scale

    crop_box = (
        max(0, int(cx - half_w)),
        max(0, int(cy - half_h)),
        min(image.width, int(cx + half_w)),
        min(image.height, int(cy + half_h)),
    )
    crop = image.crop(crop_box)
    draw = ImageDraw.Draw(crop)

    rx0 = x0 - crop_box[0]
    ry0 = y0 - crop_box[1]
    rx1 = x1 - crop_box[0]
    ry1 = y1 - crop_box[1]
    pad = 8

    draw.rectangle(
        (rx0 - pad, ry0 - pad, rx1 + pad, ry1 + pad),
        outline="red",
        width=4,
    )
    return crop


# ---------------------------------------------------------------------
# Övezeti jelölt keresése a hrsz. környezetében
# ---------------------------------------------------------------------

ZONE_PATTERN = re.compile(
    r"\b(?:"
    r"Lk|Lke|Lk|Vt|Vi|Gip|Gksz|K|KÖu|KÖk|Ev|Eg|Má|Mk|Kb|Üh|Üü|Lf|Lke"
    r")[A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9._-]*(?:\s*/\s*[A-Za-z0-9._-]+)+\b",
    flags=re.I,
)


def words_near_hit(page, pdf_rect, radius=180):
    words = page.get_text("words") or []
    cx = (pdf_rect.x0 + pdf_rect.x1) / 2
    cy = (pdf_rect.y0 + pdf_rect.y1) / 2
    nearby = []

    for word in words:
        x0, y0, x1, y1, text = word[:5]
        wx = (x0 + x1) / 2
        wy = (y0 + y1) / 2
        distance = ((wx - cx) ** 2 + (wy - cy) ** 2) ** 0.5

        if distance <= radius:
            nearby.append((distance, clean_text(text)))

    nearby.sort(key=lambda x: x[0])
    return nearby


def zone_candidates(page, pdf_rect):
    nearby = words_near_hit(page, pdf_rect)
    candidates = []
    seen = set()

    # Egyes CAD-PDF-ekben a kód egyetlen szó, másokban szóközökkel tördelődik.
    strings = [text for _, text in nearby]
    joined = " ".join(strings)

    for distance, text in nearby:
        for match in ZONE_PATTERN.finditer(text):
            code = re.sub(r"\s*/\s*", "/", match.group(0))
            if code.casefold() not in seen:
                seen.add(code.casefold())
                candidates.append((distance, code))

    for match in ZONE_PATTERN.finditer(joined):
        code = re.sub(r"\s*/\s*", "/", match.group(0))
        if code.casefold() not in seen:
            seen.add(code.casefold())
            candidates.append((999, code))

    candidates.sort(key=lambda x: x[0])
    return [
        {"Övezeti kód": code, "Távolsági sorrend": i + 1}
        for i, (_, code) in enumerate(candidates[:12])
    ]


def locate_parcel(doc, hrsz):
    if doc is None:
        return {
            "status": "missing_plan",
            "hit": None,
            "candidates": [],
            "zone": "",
        }

    hits = find_hrsz(doc, hrsz)
    if not hits:
        return {
            "status": "parcel_not_found",
            "hit": None,
            "candidates": [],
            "zone": "",
        }

    hit = hits[0]
    page = doc[hit["page_number"]]
    candidates = zone_candidates(page, hit["pdf_rect"])

    return {
        "status": "candidate" if candidates else "zone_not_found",
        "hit": hit,
        "candidates": candidates,
        "zone": candidates[0]["Övezeti kód"] if candidates else "",
    }


# ---------------------------------------------------------------------
# NJT szövegből övezeti adatok
# ---------------------------------------------------------------------

def snippets_around(text, needle, radius=1000, max_items=20):
    if not text or not needle:
        return []

    low = text.casefold()
    variants = {
        needle.casefold(),
        needle.replace("/", " / ").casefold(),
        needle.replace("/", "/ ").casefold(),
    }

    positions = []
    for variant in variants:
        start = 0
        while True:
            pos = low.find(variant, start)
            if pos < 0:
                break
            positions.append(pos)
            start = pos + max(1, len(variant))

    snippets = []
    for pos in sorted(set(positions))[:max_items]:
        snippets.append(clean_text(text[max(0, pos-radius): pos+radius]))
    return snippets


def extract_zone_parameters(njt_text, zone):
    contexts = snippets_around(njt_text, zone, radius=1200, max_items=30)
    blob = " ".join(contexts)
    if not blob:
        return {}, contexts

    patterns = {
        "Legnagyobb beépítettség": (
            r"(?:legnagyobb|max(?:imális)?)[^%]{0,100}"
            r"beépít(?:ettség|ési)[^0-9]{0,40}(\d{1,3}(?:[.,]\d+)?)\s*%"
        ),
        "Legkisebb zöldfelület": (
            r"(?:legkisebb|min(?:imális)?)[^%]{0,100}"
            r"zöldfelület[^0-9]{0,40}(\d{1,3}(?:[.,]\d+)?)\s*%"
        ),
        "Épület-/építménymagasság": (
            r"(?:épületmagasság|építménymagasság)[^0-9]{0,60}"
            r"(\d{1,3}(?:[.,]\d+)?)\s*m"
        ),
        "Legkisebb telekterület": (
            r"(?:legkisebb|min(?:imális)?)[^0-9]{0,100}"
            r"telek(?:terület|méret)[^0-9]{0,40}"
            r"(\d[\d\s]*(?:[.,]\d+)?)\s*m[²2]"
        ),
    }

    params = {}
    for label, pattern in patterns.items():
        match = re.search(pattern, blob, flags=re.I)
        if match:
            params[label] = clean_text(match.group(1))

    return params, contexts


def extract_rules(njt_text, zone):
    params, contexts = extract_zone_parameters(njt_text, zone)
    rows = []

    units = {
        "Legnagyobb beépítettség": "%",
        "Legkisebb zöldfelület": "%",
        "Épület-/építménymagasság": "m",
        "Legkisebb telekterület": "m²",
    }

    for label, value in params.items():
        rows.append({
            "Előírás": label,
            "Érték": f"{value} {units.get(label, '')}".strip(),
            "Minősítés": "forrásszövegből kinyert adat",
            "Forrás": "NJT – hatályos HÉSZ/TÉSZ",
        })

    seen = set()
    for context in contexts:
        for sentence in re.split(r"(?<=[.!?;])\s+", context):
            low = sentence.casefold()
            if not any(word in low for word in (
                "elhelyezhető",
                "nem helyezhető",
                "megengedett",
                "tilos",
                "rendeltetés",
                "kialakítható",
                "létesíthető",
            )):
                continue

            sentence = clean_text(sentence)
            if len(sentence) < 25 or sentence in seen:
                continue

            seen.add(sentence)
            rows.append({
                "Előírás": "Rendeltetés / használat",
                "Érték": sentence,
                "Minősítés": (
                    "korlátozó előírás"
                    if any(x in low for x in ("nem helyezhető", "tilos"))
                    else "forrásból kinyert előírás"
                ),
                "Forrás": "NJT – hatályos HÉSZ/TÉSZ",
            })

            if len(rows) >= 16:
                break
        if len(rows) >= 16:
            break

    return rows, params, contexts


# ---------------------------------------------------------------------
# Automatikus szabályozásiterv-PDF keresése az NJT mellékletek között
# ---------------------------------------------------------------------

def choose_plan_attachment(attachments):
    if not attachments:
        return None

    scored = []
    for row in attachments:
        label = key_text(row.get("Megnevezés", ""))
        url = row.get("URL", "")
        score = 0

        if "szabalyozasi terv" in label:
            score += 10
        if "szabalyozasi" in label:
            score += 5
        if "terv" in label:
            score += 2
        if ".pdf" in url.casefold():
            score += 1

        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


def try_auto_plan(attachments):
    candidate = choose_plan_attachment(attachments)
    if not candidate:
        return None, "", ""

    try:
        raw, final_url = download_pdf(candidate["URL"])
        return open_pdf_bytes(raw), final_url, ""
    except Exception as exc:
        return None, candidate.get("URL", ""), f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------
# Felület
# ---------------------------------------------------------------------

def main():
    st.title("TelekElőírás AI")
    st.caption(
        "v7.0 • tiszta újraírás • hivatalos forrás → szabályozási terv → "
        "telek → övezeti jelölt → forrásolt előírások"
    )

    with st.sidebar:
        st.header("Telek")
        town = st.text_input("Település", value="Tiszaújváros")
        hrsz = st.text_input("Helyrajzi szám", value="2200/8")

        st.header("Források")
        uploaded_plan = st.file_uploader(
            "Szabályozási terv PDF – csak ha az automatikus letöltés nem sikerül",
            type=["pdf"],
        )

        with st.expander("Haladó / ellenőrzési beállítások"):
            manual_njt_url = st.text_input(
                "Hivatalos NJT jogszabályoldal URL-je",
                value="",
                placeholder="https://njt.jog.gov.hu/jogszabaly/...",
            )
            manual_zone = st.text_input(
                "Kézzel ellenőrzött övezeti kód",
                value="",
                placeholder="pl. Gip/3",
            )

        start = st.button(
            "Telekvizsgálat indítása",
            type="primary",
            use_container_width=True,
        )

        st.divider()
        st.link_button(
            "E-közmű térkép megnyitása",
            EKOZMU_MAP,
            use_container_width=True,
        )

    if not start:
        st.info(
            "Add meg a települést és a helyrajzi számot, majd indítsd el a vizsgálatot."
        )
        return

    if not town.strip() or not normalize_hrsz(hrsz):
        st.error("A település és a helyrajzi szám megadása kötelező.")
        return

    # 1. NJT
    st.header("1. Hatályos hivatalos forrás")

    meta = source_for_town(town, manual_njt_url)
    page = {}
    source_valid = False
    checks = {}
    attachments = []

    if meta:
        with st.spinner("NJT-forrás ellenőrzése…"):
            page = fetch_njt_page(meta["url"])
            source_valid, checks = validate_njt_source(town, meta, page)

        if source_valid:
            st.success("A hivatalos NJT-forrás elérhető és tartalmilag ellenőrizhető.")
        else:
            st.warning(
                "A hivatalos forrás címe rendelkezésre áll, de az aktuális "
                "online tartalmi ellenőrzés nem sikerült teljesen."
            )

        st.write(f"**{meta.get('title', '')}**")
        if meta.get("regulation"):
            st.write(f"**Alaprendelet:** {meta['regulation']}")
        st.link_button("NJT – hivatalos jogszabályoldal", meta["url"])

        if page.get("error"):
            with st.expander("Kapcsolati diagnosztika"):
                st.code(
                    "\n".join([
                        f"url: {page.get('url', '')}",
                        f"http_status: {page.get('status_code', '')}",
                        f"content_type: {page.get('content_type', '')}",
                        f"detail: {page.get('error', '')}",
                    ]),
                    language="text",
                )

        if page.get("ok"):
            attachments = discover_attachments(page)
    else:
        st.warning(
            "Ehhez a településhez még nincs validált forrás az alkalmazás "
            "forrásindexében. A Haladó beállításoknál megadható a hivatalos NJT URL."
        )

    # 2. Mellékletek
    st.header("2. Szabályozási terv és mellékletek")

    if attachments:
        st.success(f"{len(attachments)} melléklet-/dokumentumhivatkozás található.")
        st.dataframe(attachments, hide_index=True, use_container_width=True)
    elif page.get("ok"):
        annexes = named_annexes(page.get("text", ""))
        if annexes:
            st.info(
                "A rendelet szövege mellékleteket nevez meg, de közvetlen "
                "letölthető PDF-hivatkozást nem sikerült kinyerni."
            )
            for item in annexes:
                st.write(f"• {item}")
        else:
            st.info("Közvetlen melléklet-hivatkozást nem sikerült azonosítani.")
    else:
        st.info("A mellékletek online vizsgálatához elérhető NJT-tartalom szükséges.")

    # Elsőbbség: feltöltött terv; második: automatikusan letöltött melléklet.
    plan_doc = open_uploaded_pdf(uploaded_plan)
    plan_source = "feltöltött hivatalos PDF" if plan_doc else ""
    auto_plan_error = ""

    if plan_doc is None and attachments:
        with st.spinner("Szabályozási terv automatikus letöltésének kísérlete…"):
            plan_doc, plan_source, auto_plan_error = try_auto_plan(attachments)

        if plan_doc:
            st.success("A szabályozási terv PDF automatikusan betöltődött.")
        elif auto_plan_error:
            st.caption(f"Automatikus PDF-letöltés nem sikerült: {auto_plan_error}")

    # 3. Telek
    st.header("3. Telekazonosítás")
    st.write(f"**{town} {normalize_hrsz(hrsz)} hrsz.**")

    spatial = locate_parcel(plan_doc, hrsz)

    if spatial["status"] == "missing_plan":
        st.info(
            "A telek térbeli vizsgálatához szabályozási terv szükséges. "
            "Ha az NJT-ből nem tölthető le automatikusan, töltsd fel a hivatalos PDF-et."
        )
    elif spatial["status"] == "parcel_not_found":
        st.error(
            "A helyrajzi számot nem találtam meg a szabályozási terv natív "
            "szövegrétegében. A v7.0 nem használ OCR-t."
        )
    else:
        hit = spatial["hit"]
        st.success(
            f"A helyrajzi szám megtalálva a szabályozási terv "
            f"{hit['page_number'] + 1}. PDF-oldalán."
        )
        page_obj = plan_doc[hit["page_number"]]
        st.image(
            parcel_crop(page_obj, hit["pdf_rect"]),
            caption="A helyrajzi szám környezete",
            use_container_width=True,
        )
        if plan_source:
            st.caption(f"Térképi forrás: {plan_source}")

    # 4. Övezet
    st.header("4. A konkrét telek övezete")

    auto_zone = spatial.get("zone", "")
    zone = clean_text(manual_zone) or auto_zone

    if manual_zone.strip():
        st.warning(
            f"Kézzel megadott ellenőrzési kód: **{manual_zone.strip()}**. "
            "Ezt a program nem minősíti automatikusan térben igazolt besorolásnak."
        )
    elif auto_zone:
        st.warning(
            f"A hrsz. közelében talált első övezeti jelölt: **{auto_zone}**. "
            "Ez közeli felirat alapján képzett jelölt, nem telekpolygon–övezetpolygon "
            "geometriai metszés."
        )
    else:
        st.warning("A konkrét telek övezeti kódja nem állapítható meg biztonságosan.")

    if spatial.get("candidates"):
        st.dataframe(
            spatial["candidates"],
            hide_index=True,
            use_container_width=True,
        )

    # 5. Előírások
    st.header("5. Mit mond a hatályos szabályzat?")

    njt_text = page.get("text", "")
    rows, params, contexts = extract_rules(njt_text, zone)

    if not zone:
        st.warning("Övezeti kód nélkül nem kapcsolok övezetspecifikus előírást a telekhez.")
    elif not njt_text:
        st.warning("Nincs feldolgozható NJT-szöveg, ezért övezeti előírást nem állítok.")
    elif rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
        with st.expander("NJT forráskörnyezet – ellenőrzéshez"):
            for i, context in enumerate(contexts[:5], 1):
                st.markdown(f"**{i}. találat**")
                st.write(context)
    else:
        st.warning(
            f"A **{zone}** kódhoz nem sikerült kellően strukturált előírást "
            "kinyerni. A program nem egészíti ki feltételezéssel."
        )

    # 6. Korlátozások
    st.header("6. Telekspecifikus korlátozások")
    st.info(
        "Védősáv, védőterület, hidrogeológiai védőterület, veszélyességi övezet, "
        "szabályozási/építési vonal vagy más térbeli korlátozás csak akkor tekinthető "
        "telekspecifikusnak, ha a telekkel való térbeli érintettség igazolható."
    )

    # 7. Összegzés
    st.header("7. Forrásolt telek-adatlap")

    spatial_status = (
        "közeli dokumentumalapú jelölt"
        if auto_zone and not manual_zone.strip()
        else "kézi ellenőrzési adat"
        if manual_zone.strip()
        else "nincs igazolva"
    )

    summary = [
        {
            "Adat": "Telek",
            "Eredmény": f"{town} {normalize_hrsz(hrsz)}",
            "Forrás": "felhasználói adat / hivatalos térképen ellenőrzendő",
            "Bizonyosság": "ellenőrzendő",
        },
        {
            "Adat": "HÉSZ/TÉSZ",
            "Eredmény": meta.get("title", "") if meta else "nincs azonosítva",
            "Forrás": "NJT",
            "Bizonyosság": "online validált" if source_valid else "nem teljesen validált",
        },
        {
            "Adat": "Övezet",
            "Eredmény": zone or "nincs igazolva",
            "Forrás": "szabályozási terv",
            "Bizonyosság": spatial_status,
        },
        {
            "Adat": "Övezeti paraméterek",
            "Eredmény": f"{len(params)} strukturált adat" if params else "nincs",
            "Forrás": "NJT HÉSZ/TÉSZ",
            "Bizonyosság": "forrásszövegből kinyert" if params else "nincs adat",
        },
        {
            "Adat": "Közműérintettség",
            "Eredmény": "kézi térképi ellenőrzés",
            "Forrás": "E-közmű",
            "Bizonyosság": "nincs automatizálva",
        },
    ]
    st.dataframe(summary, hide_index=True, use_container_width=True)

    st.caption(
        "A TelekElőírás AI döntéstámogató eszköz. Nem helyettesíti a hatósági, "
        "tervezői vagy jogi ellenőrzést. A program bizonytalan adatból nem készít "
        "biztos telekspecifikus állítást."
    )


if __name__ == "__main__":
    main()
