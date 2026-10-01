import io
import re
import urllib.request
import urllib.error
import urllib.parse
import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import fitz
import streamlit as st
from PIL import Image, ImageDraw


# =========================================================
# TELEKELŐÍRÁS AI v4.3
# NJT-MELLÉKLET FELDERÍTÉS + NATÍV PDF HELYMEGHATÁROZÁS
# =========================================================

st.set_page_config(
    page_title="TelekElőírás AI v4.3",
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
# v3.2 – ÖVEZETI JELKULCS / SZÓTÁR + TELEKHELY KAPCSOLÁS
# NINCS FIX OLDALSZÁM, NINCS OCR
# =========================================================

def normalize_text(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def extract_page_text(page):
    try:
        return page.get_text("text") or ""
    except Exception:
        return ""

# Tudatosan nem engedünk tetszőleges műszaki feliratokat.
# A regex a településrendezési övezeti jelölések tipikus alapalakjait keresi.
ZONE_TOKEN_RX = re.compile(
    r"(?<![A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9])"
    r"(?:Gip|Gksz|Gá|Ge|Gipe|Gipez|"
    r"Lke|Lk|Lf|Ln|Vt|"
    r"Üü|Üh|"
    r"Köu|Kök|Köm|"
    r"Má|Mk|"
    r"Ev|Eg|"
    r"Kst|Ksp|Kte|Kap|Ksz|Ke|Kcs|Kre|Kkm|Kmg|Kb)"
    r"(?:[/_-][A-Za-z0-9ÁÉÍÓÖŐÚÜŰáéíóöőúüű.-]+)*"
    r"(?![A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9])",
    re.IGNORECASE,
)

# Tipikus nem-övezeti műszaki/szelvény jelölések.
REJECT_PATTERNS = [
    re.compile(r"^(?:V|A)-\d+-\d+$", re.I),             # pl. V-0-0, V-1-0
    re.compile(r"^(?:DK|D)\s*\d+", re.I),               # csőátmérő jelölések
    re.compile(r"\b(?:KPE|PVC|ACÉL|VEZETÉK)\b", re.I),  # közmű
]

LEGEND_WORDS = (
    "jelmagyarázat", "területfelhasználás", "övezeti jel",
    "építési övezet", "övezet jele", "területfelhasználás jele",
)
LEGAL_WORDS = (
    "§", "elhelyezhető", "nem helyezhető", "rendeltetés",
    "beépítettség", "zöldfelület", "épületmagasság",
    "építménymagasság", "legkisebb telek", "szintterületi",
    "előírás", "építési övezet",
)

def canonical_zone(token):
    t = normalize_text(token).strip(".,;:()[]{}")
    if not t:
        return ""
    # A gyököt egységesítjük, az alövezeti rész megmarad.
    parts = re.split(r"([/_-])", t)
    root = parts[0].lower()
    roots = {
        "gip":"Gip", "gksz":"Gksz", "gá":"Gá", "ge":"Ge",
        "gipe":"Gipe", "gipez":"Gipez",
        "lke":"Lke", "lk":"Lk", "lf":"Lf", "ln":"Ln", "vt":"Vt",
        "üü":"Üü", "üh":"Üh", "köu":"Köu", "kök":"Kök", "köm":"Köm",
        "má":"Má", "mk":"Mk", "ev":"Ev", "eg":"Eg",
        "kst":"Kst", "ksp":"Ksp", "kte":"Kte", "kap":"Kap",
        "ksz":"Ksz", "ke":"Ke", "kcs":"Kcs", "kre":"Kre",
        "kkm":"Kkm", "kmg":"Kmg", "kb":"Kb",
    }
    if root not in roots:
        return ""
    return roots[root] + "".join(parts[1:])

def is_rejected_label(token):
    return any(rx.search(token or "") for rx in REJECT_PATTERNS)

def zone_tokens(text):
    out = []
    for m in ZONE_TOKEN_RX.finditer(text or ""):
        tok = canonical_zone(m.group(0))
        if tok and not is_rejected_label(tok):
            out.append(tok)
    return out

def page_role(text, tokens):
    low = (text or "").lower()
    legend_score = sum(3 for w in LEGEND_WORDS if w in low)
    legal_score = sum(2 for w in LEGAL_WORDS if w.lower() in low)
    unique = len(set(tokens))
    # Sok különböző övezeti jel egy oldalon erős jelkulcs/jelmagyarázat-jel.
    if unique >= 8:
        legend_score += 5
    elif unique >= 4:
        legend_score += 2
    if legend_score >= max(legal_score, 3):
        return "jelkulcs / övezeti jelölések", legend_score
    if legal_score >= 3:
        return "övezeti előírás / szabályozási szöveg", legal_score
    return "egyéb övezeti előfordulás", max(legend_score, legal_score)

def build_zone_dictionary(plan_doc, hesz_doc=None):
    evidence = {}

    def add(doc, source_name):
        for pno in range(len(doc)):
            txt = extract_page_text(doc[pno])
            toks = zone_tokens(txt)
            if not toks:
                continue
            role, role_score = page_role(txt, toks)
            for tok in toks:
                root = re.split(r"[/_-]", tok, maxsplit=1)[0]
                e = evidence.setdefault(root, {
                    "Kód": root,
                    "Összes előfordulás": 0,
                    "Szabályozási terv": 0,
                    "HÉSZ/TÉSZ": 0,
                    "Jelkulcs-oldalak": set(),
                    "Előírás-oldalak": set(),
                    "_score": 0,
                })
                e["Összes előfordulás"] += 1
                e[source_name] += 1
                if role == "jelkulcs / övezeti jelölések":
                    e["Jelkulcs-oldalak"].add(pno + 1)
                    e["_score"] += 4 + role_score
                elif role == "övezeti előírás / szabályozási szöveg":
                    e["Előírás-oldalak"].add(pno + 1)
                    e["_score"] += 3 + role_score
                else:
                    e["_score"] += 1

    add(plan_doc, "Szabályozási terv")
    if hesz_doc is not None:
        add(hesz_doc, "HÉSZ/TÉSZ")

    rows = []
    for e in evidence.values():
        # Ha ugyanaz a gyök mindkét dokumentumban szerepel, az különösen erős bizonyíték.
        cross = e["Szabályozási terv"] > 0 and e["HÉSZ/TÉSZ"] > 0
        confidence = e["_score"] + (10 if cross else 0)
        if cross and confidence >= 16:
            level = "erős"
        elif confidence >= 10:
            level = "közepes"
        else:
            level = "gyenge"
        rows.append({
            "Övezeti kód": e["Kód"],
            "Bizonyosság": level,
            "Szabályozási terv előfordulás": e["Szabályozási terv"],
            "HÉSZ/TÉSZ előfordulás": e["HÉSZ/TÉSZ"],
            "Jelkulcs-oldalak": ", ".join(map(str, sorted(e["Jelkulcs-oldalak"]))),
            "Előírás-oldalak": ", ".join(map(str, sorted(e["Előírás-oldalak"]))),
            "_score": confidence,
        })
    rows.sort(key=lambda r: (-r["_score"], r["Övezeti kód"].lower()))
    return rows

def find_legend_pages(doc):
    rows = []
    for pno in range(len(doc)):
        txt = extract_page_text(doc[pno])
        toks = zone_tokens(txt)
        if not toks:
            continue
        role, score = page_role(txt, toks)
        if role == "jelkulcs / övezeti jelölések":
            rows.append({
                "Oldal": pno + 1,
                "Pontszám": score,
                "Különböző övezeti kódok": len(set(toks)),
                "Felismert kódok": ", ".join(sorted(set(toks))[:40]),
            })
    rows.sort(key=lambda r: (-r["Pontszám"], -r["Különböző övezeti kódok"], r["Oldal"]))
    return rows

def snippets_around(text, needle, radius=260):
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

def classify_context(text):
    low = (text or "").lower()
    scores = {
        "övezeti jelölés / jelmagyarázat": 0,
        "általános övezeti előírás": 0,
        "beépítési mutató / táblázat": 0,
    }
    for w in LEGEND_WORDS:
        if w in low:
            scores["övezeti jelölés / jelmagyarázat"] += 3
    for w in ("előírás", "ipari gazdasági", "gazdasági terület",
              "rendeltetés", "elhelyezhető", "nem helyezhető", "§"):
        if w in low:
            scores["általános övezeti előírás"] += 2
    for w in ("beépítési", "beépítettség", "építménymagasság", "épületmagasság",
              "legkisebb telek", "zöldfelület", "szintterületi", "oldalkert",
              "előkert", "hátsókert", "%"):
        if w in low:
            scores["beépítési mutató / táblázat"] += 2
    label, score = max(scores.items(), key=lambda kv: kv[1])
    return label if score > 0 else "egyéb találat", score

def search_term_in_document(doc, term):
    rows = []
    for pno in range(len(doc)):
        page = doc[pno]
        txt = extract_page_text(page)
        rects = page.search_for(term)
        if not rects and term.lower() not in txt.lower():
            continue
        snippets = snippets_around(txt, term) or [normalize_text(txt[:700])]
        for snip in snippets[:4]:
            cls, score = classify_context(snip)
            rows.append({
                "Oldal": pno + 1,
                "Típus": cls,
                "Pontszám": score,
                "Szövegkörnyezet": snip,
            })
    rows.sort(key=lambda r: (-r["Pontszám"], r["Oldal"]))
    return rows

def show_document_discovery(plan_doc, hesz_doc=None):
    st.markdown("## 4. Övezeti jelkulcs automatikus felderítése")
    st.info(
        "A v3.2 nem ismer előre oldalszámot. A teljes natív PDF-szövegrétegből keresi meg "
        "azokat az oldalakat, amelyek jelkulcsnak vagy övezeti jelölés-listának látszanak. "
        "A közmű- és szelvényfeliratokat (például DK 150 KPE, V-0-0, V-1-0) nem tekinti övezeti kódnak."
    )

    legend_rows = find_legend_pages(plan_doc)
    if legend_rows:
        st.success(f"{len(legend_rows)} lehetséges jelkulcs/övezeti jelölés oldalt találtam.")
        st.dataframe(legend_rows[:30], use_container_width=True, hide_index=True)
    else:
        st.warning("Nem találtam kellően erős jelkulcs-oldal jelöltet.")

    st.markdown("## 5. Felismert övezeti kódok – ellenőrzött szótár")
    dictionary = build_zone_dictionary(plan_doc, hesz_doc)
    visible = [{k:v for k,v in r.items() if k != "_score"} for r in dictionary]
    if visible:
        st.dataframe(visible, use_container_width=True, hide_index=True)
    else:
        st.warning("Nem sikerült övezeti kódszótárat felépíteni.")

    gip = next((r for r in dictionary if r["Övezeti kód"].lower() == "gip"), None)
    if gip:
        st.success(
            "A Gip övezeti jelölést a program önállóan felismerte a dokumentumokból. "
            f"Bizonyosság: {gip['Bizonyosság']}."
        )

    st.markdown("## 6. Kiválasztott övezeti kód tartalmi vizsgálata")
    st.caption(
        "Ez még nem jelenti azt, hogy a keresett telek övezete ez a kód. "
        "Itt csak azt ellenőrizzük, hogy a felismert kódhoz hol találhatók jelölések, "
        "általános előírások és beépítési mutatók."
    )
    default_term = "Gip" if gip else (dictionary[0]["Övezeti kód"] if dictionary else "")
    zone_term = st.text_input(
        "Vizsgálandó övezeti kifejezés",
        value=default_term,
        key="zone_term_v32",
    ).strip()

    docs = [("Szabályozási terv", plan_doc)]
    if hesz_doc is not None:
        docs.append(("HÉSZ/TÉSZ", hesz_doc))

    if zone_term:
        all_rows = []
        for name, doc in docs:
            for r in search_term_in_document(doc, zone_term):
                all_rows.append({"Forrás": name, **r})

        if all_rows:
            st.success(f"{len(all_rows)} szövegkörnyezetet találtam a(z) {zone_term} kifejezéshez.")
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
        else:
            st.warning(f"A(z) {zone_term} kifejezést nem találtam.")


def spatial_zone_candidates(page, visible_hit, allowed_roots, radius_factor=18):
    """
    v3.2: A már ellenőrzött hrsz-találat környezetében keres övezeti kódokat.
    Nem oldalszámot kódolunk, hanem a hrsz ugyanazon tervlapján, natív PDF-szavakból
    keressük a felismert övezeti szótár elemeit.
    """
    words = page.get_text("words") or []
    cx = (visible_hit.x0 + visible_hit.x1) / 2
    cy = (visible_hit.y0 + visible_hit.y1) / 2
    base = max(visible_hit.width, visible_hit.height, 8)
    max_dist = base * radius_factor

    allowed = {str(x).lower(): str(x) for x in allowed_roots}
    found = []
    seen = set()

    for w in words:
        if len(w) < 5:
            continue
        raw = str(w[4]).strip()
        tok = canonical_zone(raw)
        if not tok or is_rejected_label(tok):
            continue
        root = re.split(r"[/_-]", tok, maxsplit=1)[0]
        if root.lower() not in allowed:
            continue

        pdf_rect = fitz.Rect(w[0], w[1], w[2], w[3])
        vr = visible_rect(page, pdf_rect)
        wx = (vr.x0 + vr.x1) / 2
        wy = (vr.y0 + vr.y1) / 2
        dist = ((wx - cx) ** 2 + (wy - cy) ** 2) ** 0.5
        if dist > max_dist:
            continue

        key = (root.lower(), round(vr.x0, 1), round(vr.y0, 1))
        if key in seen:
            continue
        seen.add(key)
        found.append({
            "Övezeti kód": root,
            "Felirat": raw,
            "Távolság": round(dist, 1),
            "_rect": vr,
        })

    found.sort(key=lambda r: r["Távolság"])
    return found


def marked_zone_crop(page, visible_hit, candidates, scale=0.13, zoom=0.85):
    image = render_page(page, zoom=zoom)
    hx0, hy0, hx1, hy1 = rect_to_pixels(page, image, visible_hit)
    cx = (hx0 + hx1) / 2
    cy = (hy0 + hy1) / 2
    half_w = image.width * scale
    half_h = image.height * scale
    left = max(0, int(cx - half_w))
    top = max(0, int(cy - half_h))
    right = min(image.width, int(cx + half_w))
    bottom = min(image.height, int(cy + half_h))
    crop = image.crop((left, top, right, bottom))
    draw = ImageDraw.Draw(crop)

    # telek/hrsz helye: fekete célkereszt
    hx = cx - left
    hy = cy - top
    rr = 15
    draw.ellipse((hx-rr, hy-rr, hx+rr, hy+rr), outline="black", width=4)
    draw.line((hx-rr-8, hy, hx+rr+8, hy), fill="black", width=3)
    draw.line((hx, hy-rr-8, hx, hy+rr+8), fill="black", width=3)

    # övezeti jelöltek: piros keret
    for i, c in enumerate(candidates[:12], start=1):
        x0, y0, x1, y1 = rect_to_pixels(page, image, c["_rect"])
        x0 -= left; x1 -= left; y0 -= top; y1 -= top
        pad = 7
        draw.rectangle((x0-pad, y0-pad, x1+pad, y1+pad), outline="red", width=4)
        draw.text((x1+10, y0-5), f"{i}: {c['Övezeti kód']}", fill="red")
    return crop


def show_parcel_zone_link(plan_doc, hit, dictionary, crop_scale_pct=10):
    st.markdown("## 7. Telek és övezeti jelölés térbeli összekapcsolása")
    st.info(
        "Itt már nem a dokumentumban általában előforduló kódot keressük. "
        "A korábban natív PDF-kereséssel azonosított helyrajzi szám ugyanazon tervlapján, "
        "annak közvetlen környezetében vizsgáljuk a dokumentumokból felismert övezeti kódokat. "
        "Fix oldalszám és OCR nincs."
    )

    page = plan_doc[hit["page_number"]]
    vh = visible_rect(page, hit["pdf_rect"])
    allowed = [r["Övezeti kód"] for r in dictionary if r.get("Bizonyosság") in ("erős", "közepes")]
    candidates = spatial_zone_candidates(page, vh, allowed, radius_factor=90)

    if not candidates:
        st.warning(
            "A telek közvetlen környezetében nem találtam kellően megbízható, "
            "a felismert övezeti szótárhoz tartozó natív PDF-feliratot. "
            "Ez önmagában nem jelenti azt, hogy nincs övezeti besorolás."
        )
        return

    st.success(f"{len(candidates)} térbeli övezeti jelöltet találtam a telek környezetében.")
    st.dataframe(
        [{k:v for k,v in r.items() if k != "_rect"} for r in candidates[:20]],
        use_container_width=True,
        hide_index=True,
    )
    img = marked_zone_crop(
        page, vh, candidates,
        scale=max(0.08, min(0.22, crop_scale_pct / 100.0 + 0.03))
    )
    st.image(img, caption="Fekete célkereszt: hrsz helye • piros keretek: közeli, szótárból igazolt övezeti kód-jelöltek",
             use_container_width=True)

    nearest = candidates[0]
    st.markdown("### Legközelebbi térbeli jelölt")
    st.write(
        f"**{nearest['Övezeti kód']}** — natív PDF-felirat: **{nearest['Felirat']}**, "
        f"relatív távolság: **{nearest['Távolság']}**."
    )
    st.caption(
        "A legközelebbi felirat még nem automatikus jogi besorolás. "
        "A következő fejlesztési lépés a telek geometriai területének és az övezethatárnak az összevetése."
    )


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        elif tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self):
        return "\n".join(
            line.strip() for line in "".join(self.parts).splitlines() if line.strip()
        )


class _HTMLLinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.links.append((normalize_text(" ".join(self._text)), self._href))
            self._href = None
            self._text = []


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_njt_html(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 TelekEloirasAI/4.3",
            "Accept-Language": "hu-HU,hu;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def discover_njt_attachments(url):
    """A jogszabály NJT-oldalán megjelenő melléklet-hivatkozásokat tárja fel.
    Nem feltételez előre fájlnevet vagy oldalszámot.
    """
    html = fetch_njt_html(url)
    parser = _HTMLLinkExtractor()
    parser.feed(html)
    out, seen = [], set()
    for text, href in parser.links:
        if not href:
            continue
        label = normalize_text(text)
        hay = (label + " " + href).lower()
        if not any(k in hay for k in ("melléklet", "melleklet", "attachment", ".pdf")):
            continue
        full = urljoin(url, href)
        key = (label, full)
        if key in seen:
            continue
        seen.add(key)
        out.append({"Megnevezés": label or "melléklet", "URL": full})
    return out



def attachment_by_label(rows, wanted):
    wanted = wanted.lower().strip()
    for r in rows or []:
        label = normalize_text(r.get("Megnevezés", "")).lower()
        if label.startswith(wanted):
            return r.get("URL")
    return None


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_pdf_bytes(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 TelekEloirasAI/4.3",
            "Accept": "application/pdf,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read()


def open_pdf_from_url(url):
    raw = fetch_pdf_bytes(url)
    return fitz.open(stream=raw, filetype="pdf"), len(raw)

def expected_attachment_labels(njt_text):
    """A rendeletszöveg záró részéből felismeri a név szerint felsorolt mellékleteket."""
    patterns = [
        r"1\.1\.\s*melléklet[^\n]{0,180}",
        r"1\.2\.\s*melléklet[^\n]{0,180}",
        r"2\.1\.\s*melléklet[^\n]{0,180}",
        r"2\.2\.\s*melléklet[^\n]{0,180}",
        r"2\.3\.\s*melléklet[^\n]{0,180}",
        r"2\.4\.\s*melléklet[^\n]{0,180}",
    ]
    rows=[]
    for pat in patterns:
        m=re.search(pat, njt_text or "", re.I)
        if m:
            rows.append(normalize_text(m.group(0)))
    return rows


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_njt_text(url):
    """NJT oldal letöltése és olvasható szöveggé alakítása."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 TelekEloirasAI/3.3",
            "Accept-Language": "hu-HU,hu;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    html = raw.decode(charset, errors="replace")
    parser = _HTMLTextExtractor()
    parser.feed(html)
    txt = parser.text()
    return normalize_text(txt)


def njt_snippets(text, term, radius=650, max_items=20):
    if not text or not term:
        return []
    low = text.lower()
    needle = term.lower()
    out = []
    pos = 0
    while len(out) < max_items:
        i = low.find(needle, pos)
        if i < 0:
            break
        a = max(0, i - radius)
        b = min(len(text), i + len(term) + radius)
        snippet = normalize_text(text[a:b])
        if snippet and snippet not in out:
            out.append(snippet)
        pos = i + max(1, len(needle))
    return out


def score_njt_snippet(snippet):
    low = snippet.lower()
    score = 0
    for word, weight in (
        ("§", 5),
        ("építési övezet", 5),
        ("övezet", 3),
        ("beépítettség", 5),
        ("zöldfelület", 5),
        ("épületmagasság", 5),
        ("építménymagasság", 5),
        ("legkisebb telek", 4),
        ("szintterületi", 4),
        ("elhelyezhető", 4),
        ("nem helyezhető", 4),
        ("rendeltetés", 3),
        ("beépítési mód", 4),
    ):
        if word in low:
            score += weight
    return score


def show_njt_source(njt_url, zone_term):
    st.markdown("## 8. Hatályos jogszabályi forrás – NJT")
    st.info(
        "A v3.3-ban a szabályozási terv marad a térbeli forrás, "
        "a szöveges övezeti előírásokat pedig elsődlegesen a Nemzeti Jogszabálytárból vizsgáljuk. "
        "A program nem PDF-oldalszámot keres az NJT-ben, hanem a rendelet teljes kereshető szövegét."
    )

    if not njt_url:
        st.warning("Nincs megadva NJT-forrás.")
        return

    try:
        with st.spinner("NJT jogszabályi szöveg betöltése…"):
            njt_text = fetch_njt_text(njt_url)
    except Exception as e:
        st.error(f"Az NJT-forrást nem sikerült betölteni: {e}")
        return

    if len(njt_text) < 500:
        st.warning("Az NJT oldalról túl kevés szöveget sikerült kinyerni.")
        return

    st.success(f"NJT-forrás betöltve • {len(njt_text):,} karakter kereshető jogszabályszöveg.")

    # Alapvető forrásellenőrzés
    checks = []
    for phrase in (
        "építési övezet vagy övezet határa",
        "építési övezet vagy övezet besorolása",
        "telkenként betartandó beépítési mutatók",
    ):
        checks.append({
            "Forrásellenőrzés": phrase,
            "Megtalálva": "igen" if phrase.lower() in njt_text.lower() else "nem",
        })
    st.dataframe(checks, use_container_width=True, hide_index=True)

    if not zone_term:
        st.warning("Nincs kiválasztott övezeti kód, ezért az NJT-ben még nem végzek övezetspecifikus keresést.")
        return

    snippets = njt_snippets(njt_text, zone_term)
    ranked = sorted(
        [{"Pontszám": score_njt_snippet(s), "Szövegkörnyezet": s} for s in snippets],
        key=lambda r: -r["Pontszám"],
    )

    st.markdown(f"### `{zone_term}` találatok az NJT-ben")
    if not ranked:
        st.warning(f"A(z) {zone_term} kifejezést nem találtam az NJT rendeletszövegében.")
        return

    st.success(f"{len(ranked)} releváns szövegkörnyezetet találtam.")
    for i, row in enumerate(ranked[:10], start=1):
        with st.expander(
            f"{i}. NJT-találat • relevancia: {row['Pontszám']}",
            expanded=(i <= 3),
        ):
            st.write(row["Szövegkörnyezet"])

    st.caption(
        "A v3.3 még forrás- és tartalomfelderítő verzió: az NJT-találatokból még nem állít elő "
        "automatikusan jogi következtetést. A következő lépésben ezekből strukturált mezőket "
        "készíthetünk (beépítettség, zöldfelület, magasság, telekméret, rendeltetés stb.)."
    )


# =========================================================
# v4.1 – FORRÁSHIERARCHIA ÉS TELEK-ADATLAP
# Elsődleges elv:
#   E-közmű / állami ingatlan-nyilvántartás = telekazonosítás
#   NJT szabályozási terv = övezeti térbeli besorolás
#   NJT HÉSZ/TÉSZ + mellékletek = jogi paraméterek
#   E-közmű = közműérintettségek
# =========================================================

DEFAULT_NJT = "https://njt.jog.gov.hu/jogszabaly/2018-11-SP-5Y1228"
EKOZMU_MAP = "https://ekozmu.e-epites.hu/lakossag/#/lakossag/kozmuterkep"

def source_status(label, status, detail=""):
    icon = {"OK":"✅", "RÉSZBEN":"🟡", "NINCS":"⚪", "HIBA":"🔴"}.get(status, "•")
    st.write(f"{icon} **{label}** — {status}" + (f" · {detail}" if detail else ""))

def extract_basic_zone_params_from_text(text_blob, zone_code):
    """
    Konzervatív parser: csak olyan értéket mutat, amelyet a szövegkörnyezetben
    egyértelmű kulcsszóval együtt talál. Nem talál ki hiányzó adatot.
    """
    if not text_blob or not zone_code:
        return {}
    snippets = njt_snippets(text_blob, zone_code, radius=1200, max_items=30)
    blob = " ".join(snippets)
    result = {}
    patterns = {
        "Legnagyobb beépítettség": r"(?:legnagyobb|max(?:imális)?)[^%]{0,80}beépít(?:ettség|ési)[^0-9]{0,30}(\d{1,3}(?:[.,]\d+)?)\s*%",
        "Legkisebb zöldfelület": r"(?:legkisebb|min(?:imális)?)[^%]{0,80}zöldfelület[^0-9]{0,30}(\d{1,3}(?:[.,]\d+)?)\s*%",
        "Épület-/építménymagasság": r"(?:épületmagasság|építménymagasság)[^0-9]{0,50}(\d{1,3}(?:[.,]\d+)?)\s*m",
        "Legkisebb telekterület": r"(?:legkisebb|min(?:imális)?)[^0-9]{0,80}telek(?:terület|méret)[^0-9]{0,30}(\d[\d\s]*(?:[.,]\d+)?)\s*m[²2]",
    }
    low = blob.lower()
    for key, pat in patterns.items():
        m = re.search(pat, low, flags=re.I)
        if m:
            result[key] = m.group(1).strip()
    return result

def parse_zone_table_context(text_blob, zone_code):
    """Kísérleti v4.1 parser az NJT szövegében megjelenő övezeti táblázatsorokra.
    Csak a kód közvetlen környezetéből dolgozik, és a nyers kontextust is visszaadja.
    """
    if not text_blob or not zone_code:
        return {}, []
    contexts = njt_snippets(text_blob, zone_code, radius=450, max_items=20)
    params = {}
    # Tipikus sorrend a Tiszaújváros 1.2 mellékletben: kód, beépítési mód, beépítettség,
    # legkisebb telekterület, zöldfelület, magasság. Nem tekintjük univerzális sémának.
    code_rx = re.escape(zone_code).replace(r'\\/', r'\\s*/\\s*')
    for c in contexts:
        compact = normalize_text(c)
        m = re.search(code_rx + r"\\s+(SZ|O|K|Z)\\s+(\\d{1,3}(?:[.,]\\d+)?)\\s+(\\d[\\d .]{2,})\\s+(\\d{1,3}(?:[.,]\\d+)?)\\s+(\\d{1,3}(?:[.,]\\d+)?)", compact, re.I)
        if m:
            params = {
                "Beépítési mód": m.group(1).upper(),
                "Legnagyobb beépítettség": m.group(2) + " %",
                "Legkisebb telekterület": re.sub(r"\\s+", " ", m.group(3)).strip() + " m²",
                "Legkisebb zöldfelület": m.group(4) + " %",
                "Legnagyobb épület-/építménymagasság": m.group(5) + " m",
            }
            break
    return params, contexts


def show_zone_parameter_card(njt_text, zone):
    if not zone:
        return
    st.markdown("### Övezeti paraméter-adatlap")
    table_params, contexts = parse_zone_table_context(njt_text, zone)
    prose_params = extract_basic_zone_params_from_text(njt_text, zone)
    merged = dict(table_params)
    for k, v in prose_params.items():
        merged.setdefault(k, v)
    if merged:
        st.dataframe([
            {"Paraméter": k, "Érték": v, "Forrás": "NJT – hatályos rendelet / melléklet", "Bizonyosság": "forrásszövegből kinyert"}
            for k, v in merged.items()
        ], use_container_width=True, hide_index=True)
    else:
        st.info("Az övezeti kódhoz nem sikerült biztonságosan strukturált számszerű paramétereket kinyerni az NJT letöltött szövegéből.")
    if contexts:
        with st.expander("NJT nyers forráskörnyezet – ellenőrzéshez", expanded=False):
            for i, c in enumerate(contexts[:5], 1):
                st.markdown(f"**{i}. találat**")
                st.write(c)

def render_reference_card(town, hrsz, zone=None, zone_verified=False):
    st.markdown("## Telek-adatlap")
    c1, c2, c3 = st.columns(3)
    c1.metric("Település", town or "—")
    c2.metric("Helyrajzi szám", hrsz or "—")
    c3.metric("Építési övezet", zone if zone else "még nem igazolt")
    if zone:
        if zone_verified:
            st.success(f"Övezeti besorolás: **{zone}** — térképi ellenőrzéssel igazolt.")
        else:
            st.warning(
                f"Övezeti jelölt: **{zone}**. A program ezt addig nem kezeli jogilag igazolt "
                "besorolásként, amíg a telek és az övezethatár térbeli kapcsolata nincs bizonyítva."
            )

st.title("TelekElőírás AI")
st.caption("v4.3 • NJT mellékletek automatikus betöltése + hrsz. keresés + övezeti jelölt + forrásolt telek-adatlap")

with st.sidebar:
    st.header("Telek")
    town = st.text_input("Település", value="Tiszaújváros")
    hrsz = st.text_input("Helyrajzi szám", value="2200/8")

    st.header("Hivatalos források")
    njt_url = st.text_input("NJT – hatályos helyi építési szabályzat", value=DEFAULT_NJT)
    st.link_button("E-közmű térkép megnyitása", EKOZMU_MAP)
    st.caption(
        "Az E-közmű/ingatlan-nyilvántartási térkép a telekazonosítás elsődleges forrása. "
        "A v4.0 nem használ dokumentálatlan belső API-végpontot."
    )

    st.header("Térképi forrás")
    plan = st.file_uploader(
        "NJT szabályozási terv / ellenőrzött terv-PDF (opcionális)",
        type=["pdf"],
        key="plan_v4",
        help="Amíg az NJT nagy térképi mellékletének automatikus letöltése nincs stabilizálva, ezzel ellenőrizhető a térbeli övezeti kapcsolat.",
    )

    st.header("Referencia-ellenőrzés")
    verified_zone = st.text_input(
        "Kézzel igazolt övezeti kód (opcionális)",
        value="",
        placeholder="pl. Gip/3",
        help="Csak teszteléshez. Nem helyettesíti az automatikus térbeli meghatározást.",
    ).strip()

    run = st.button("v4.2 telekvizsgálat indítása", type="primary", use_container_width=True)

if not run:
    st.markdown(
        """
### Forráshierarchia
**1. E-közmű / állami ingatlan-nyilvántartás** → telek, hrsz., aktuális telekhatár  
**2. NJT szabályozási terv** → övezet és tervi korlátozások  
**3. NJT HÉSZ/TÉSZ + mellékletek** → beépítési paraméterek és szöveges előírások  
**4. E-közmű** → közműérintettségek  
**5. Egyéb hatósági források** → telekspecifikus korlátozások
        """
    )
    st.stop()

town = town.strip()
hrsz = hrsz.strip()
clean_hrsz = re.sub(r"\s+", "", hrsz)

if not town or not clean_hrsz:
    st.error("A település és a helyrajzi szám kötelező.")
    st.stop()

st.markdown("## 1. Forrásellenőrzés")
source_status(
    "E-közmű / ingatlan-nyilvántartási telekazonosítás",
    "RÉSZBEN",
    "hivatalos térképi forrás; nyilvánosan dokumentált telekpolygon-API-t a program nem feltételez",
)

njt_text = ""
njt_attachments = []
expected = []
try:
    with st.spinner("NJT rendeletszöveg betöltése…"):
        njt_text = fetch_njt_text(njt_url)
    source_status("NJT rendeletszöveg", "OK", f"{len(njt_text):,} karakter")
    expected = expected_attachment_labels(njt_text)
    if expected:
        source_status("NJT mellékletjegyzék", "OK", f"{len(expected)} név szerint felismert melléklet")
    try:
        njt_attachments = discover_njt_attachments(njt_url)
        source_status(
            "NJT melléklet-hivatkozások",
            "OK" if njt_attachments else "RÉSZBEN",
            f"{len(njt_attachments)} közvetlen hivatkozás felismerve" if njt_attachments else "a mellékletnevek a rendeletszövegből felismerhetők, közvetlen letöltési link nem volt kinyerhető",
        )
    except Exception as e:
        njt_attachments = []
        source_status("NJT melléklet-hivatkozások", "HIBA", str(e))
except Exception as e:
    source_status("NJT rendeletszöveg", "HIBA", str(e))

plan_doc = None
zone_doc = None
hits = []
plan_source = ""

# v4.3: az NJT-ben ténylegesen megtalált 1.1 és 1.2 mellékletet automatikusan használjuk.
plan_url = attachment_by_label(njt_attachments, "1.1. melléklet")
zone_url = attachment_by_label(njt_attachments, "1.2. melléklet")

if plan_url:
    try:
        with st.spinner("NJT 1.1. szabályozási terv automatikus betöltése… (nagy PDF)"):
            plan_doc, plan_size = open_pdf_from_url(plan_url)
        plan_source = "NJT 1.1. melléklet – automatikusan betöltve"
        source_status("NJT 1.1. szabályozási terv", "OK", f"{len(plan_doc)} oldal · {plan_size/1024/1024:.1f} MB")
    except Exception as e:
        source_status("NJT 1.1. szabályozási terv", "HIBA", str(e))

if zone_url:
    try:
        with st.spinner("NJT 1.2. övezeti melléklet automatikus betöltése…"):
            zone_doc, zone_size = open_pdf_from_url(zone_url)
        source_status("NJT 1.2. övezeti melléklet", "OK", f"{len(zone_doc)} oldal · {zone_size/1024/1024:.1f} MB")
    except Exception as e:
        source_status("NJT 1.2. övezeti melléklet", "HIBA", str(e))

# A kézzel feltöltött terv csak tartalék/diagnosztikai felülbírálás.
if plan is not None:
    try:
        if plan_doc is not None:
            plan_doc.close()
        plan_doc = fitz.open(stream=plan.getvalue(), filetype="pdf")
        plan_source = "kézzel feltöltött ellenőrző terv-PDF"
        source_status("Szabályozási terv – kézi felülbírálás", "OK", f"{len(plan_doc)} oldal")
    except Exception as e:
        source_status("Szabályozási terv – kézi PDF", "HIBA", str(e))

if plan_doc is not None:
    try:
        hits = find_hrsz(plan_doc, clean_hrsz)
        if hits:
            source_status(
                "Szabályozási terv – hrsz. térbeli találat",
                "OK",
                f"{hits[0]['page_number'] + 1}. oldal · {len(hits)} natív találat · {plan_source}",
            )
        else:
            source_status("Szabályozási terv – hrsz. térbeli találat", "NINCS", "a PDF natív szövegrétegében nem található")
    except Exception as e:
        source_status("Szabályozási terv – hrsz. keresés", "HIBA", str(e))

st.markdown("## 2. NJT mellékletek automatikus felderítése")
if expected:
    st.write("A rendelet szövegében név szerint hivatkozott mellékletek:")
    for item in expected:
        st.write(f"• {item}")
else:
    st.info("A rendeletszövegből nem sikerült név szerint mellékletjegyzéket felismerni.")

if njt_attachments:
    st.dataframe(njt_attachments, use_container_width=True, hide_index=True)
    st.caption("A program ezeket közvetlenül az NJT oldal HTML-jéből tárta fel; nincs előre beégetett melléklet-fájlnév.")
else:
    st.warning(
        "Az NJT rendeletoldal szövege igazolja a mellékletek létét, de a jelenlegi HTML-ből nem sikerült "
        "stabil közvetlen fájlhivatkozást kinyerni. Emiatt a program nem talál ki letöltési URL-t. "
        "A kézi PDF-feltöltés továbbra is ellenőrzési tartalékút."
    )

st.markdown("## 3. Telekazonosítás")
st.write(f"**{town} {clean_hrsz} hrsz.**")
st.write(
    "Elsődleges telekforrás: **E-közmű / állami ingatlan-nyilvántartási térképi adat**. "
    "A program jelenleg nem állít elő telekpolygont nem dokumentált szolgáltatásból."
)
st.link_button("Telek ellenőrzése az E-közműben", EKOZMU_MAP)

if plan_doc is not None and hits:
    hit = hits[0]
    page = plan_doc[hit["page_number"]]
    vr = visible_rect(page, hit["pdf_rect"])
    st.image(
        parcel_crop(page, vr, scale=0.10, zoom=0.75),
        caption=f"{town} {clean_hrsz} – szabályozási tervi környezet",
        use_container_width=True,
    )

st.markdown("## 4. Övezeti besorolás")
zone = verified_zone or ""
zone_verified = bool(verified_zone)

if zone_verified:
    st.success(
        f"Referencia-ellenőrzésként megadott, térképen igazolt övezet: **{zone}**. "
        "Ez a mező tesztadat; a végleges cél az automatikus térbeli meghatározás."
    )
elif plan_doc is not None and hits:
    # A régi közelségi keresést csak diagnosztikaként használjuk.
    dictionary = build_zone_dictionary(plan_doc, zone_doc)
    page = plan_doc[hits[0]["page_number"]]
    vh = visible_rect(page, hits[0]["pdf_rect"])
    allowed = [r["Övezeti kód"] for r in dictionary if r.get("Bizonyosság") in ("erős", "közepes")]
    candidates = spatial_zone_candidates(page, vh, allowed, radius_factor=90)
    if candidates:
        auto_zone = candidates[0]['Övezeti kód']
        zone = auto_zone
        st.warning(
            f"Automatikusan felismert legközelebbi övezeti jelölt: **{auto_zone}** "
            f"(PDF-felirat: {candidates[0]['Felirat']}, relatív távolság: {candidates[0]['Távolság']}). "
            "Ez még jelölt, nem jogilag igazolt besorolás: a következő lépés a tényleges övezethatár-geometria vizsgálata."
        )
        st.image(
            marked_zone_crop(page, vh, candidates, scale=0.20, zoom=0.85),
            caption="Fekete célkereszt: keresett hrsz. • piros keretek: közeli övezeti feliratok",
            use_container_width=True,
        )
    else:
        st.info("A hrsz. közvetlen közelében nincs megbízható övezeti bélyeg. Ez nagy ipari telkeknél normális lehet.")
else:
    st.info("Az övezet automatikus térbeli meghatározásához szabályozási tervi geometria szükséges.")

render_reference_card(town, clean_hrsz, zone or None, zone_verified)

st.markdown("## 5. NJT – vonatkozó jogi előírások")
if not njt_text:
    st.warning("Az NJT rendeletszöveg nem áll rendelkezésre.")
else:
    # Forrásstruktúra – nem csak övezeti szókeresés.
    structural_terms = [
        "építési övezet vagy övezet határa",
        "építési övezet vagy övezet besorolása",
        "telkenként betartandó beépítési mutatók",
        "védősáv",
        "hidrogeológiai",
        "veszélyességi övezet",
        "beültetési kötelezettség",
    ]
    checks = [{"Vizsgált szabályozási elem": t, "NJT-ben": "igen" if t.lower() in njt_text.lower() else "nem"}
              for t in structural_terms]
    st.dataframe(checks, use_container_width=True, hide_index=True)

    if zone:
        root_zone = re.split(r"[/_-]", zone, maxsplit=1)[0]
        specific = njt_snippets(njt_text, zone, radius=850, max_items=10)
        general = njt_snippets(njt_text, root_zone, radius=850, max_items=20)
        combined = []
        seen = set()
        for label, arr in (("alövezet", specific), ("övezetcsoport", general)):
            for s in arr:
                if s in seen:
                    continue
                seen.add(s)
                combined.append((label, score_njt_snippet(s), s))
        combined.sort(key=lambda x: -x[1])

        st.markdown(f"### {zone} – jogszabályi találatok")
        if combined:
            for i, (kind, score, s) in enumerate(combined[:8], start=1):
                with st.expander(f"{i}. {kind} · relevancia {score}", expanded=(i <= 3)):
                    st.write(s)
        else:
            st.warning(f"A {zone} / {root_zone} kódhoz nem találtam szöveges NJT-környezetet.")

        params = extract_basic_zone_params_from_text(njt_text, zone)
        st.markdown("### Strukturált paraméterek")
        if params:
            st.dataframe(
                [{"Paraméter": k, "Érték": v, "Forrás": "NJT rendeletszöveg"} for k, v in params.items()],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info(
                "A rendeletszövegből nem nyerhető ki biztonságosan minden számszerű paraméter. "
                "Ezek elsődleges forrása az NJT övezeti melléklete."
            )

        show_zone_parameter_card(njt_text, zone)
    else:
        st.info("Övezetspecifikus előírásokat csak igazolt övezeti besorolás után alkalmazunk a telekre.")

st.markdown("## 6. Tervi korlátozások")
restriction_terms = [
    "szabályozási vonal",
    "építési vonal",
    "beültetési kötelezettség",
    "védőterület",
    "védősáv",
    "hidrogeológiai védőterület",
    "veszélyességi övezet",
    "rekultivációra kötelezett terület",
    "kötött funkciójú",
]
if njt_text:
    st.dataframe(
        [{"Korlátozás típusa": t, "A TÉSZ szabályozási rendszerében szerepel":
          "igen" if t.lower() in njt_text.lower() else "nem"} for t in restriction_terms],
        use_container_width=True,
        hide_index=True,
    )
st.caption(
    "Az, hogy egy korlátozástípus szerepel a TÉSZ-ben, még nem jelenti azt, hogy a konkrét telket érinti. "
    "A telekspecifikus érintettséget térbeli metszéssel kell igazolni."
)

st.markdown("## 7. E-közmű – közműérintettségek")
st.warning(
    "A v4.0 nem hív dokumentálatlan E-közmű belső végpontot. "
    "A modul elő van készítve arra, hogy hivatalos, programozottan hozzáférhető WMS/WFS/egyéb szolgáltatás "
    "esetén a telekpolygonnal térbeli metszést végezzen."
)
st.link_button("Közműtérkép megnyitása", EKOZMU_MAP)

st.markdown("## 8. Forrásolt összegzés")
summary_rows = [
    ["Telek", f"{town} {clean_hrsz}", "E-közmű / ingatlan-nyilvántartás", "ellenőrzendő a térképen"],
    ["Övezet", zone if zone else "nincs igazolva", "NJT szabályozási terv", "igazolt" if zone_verified else "további térbeli ellenőrzés"],
    ["Jogi előírások", "NJT rendeletszöveg betöltve" if njt_text else "nem elérhető", "NJT", "forrásolt"],
    ["Közműérintettség", "még nincs automatikusan lekérdezve", "E-közmű", "következő integráció"],
]
st.dataframe(
    [{"Adat": a, "Eredmény": b, "Elsődleges forrás": c, "Bizonyosság": d}
     for a,b,c,d in summary_rows],
    use_container_width=True,
    hide_index=True,
)

st.caption(
    "A TelekElőírás AI nem helyettesíti a hatósági vagy tervezői jogi ellenőrzést. "
    "A cél minden állítást visszakövethető hivatalos forráshoz és bizonyossági szinthez kötni."
)

if plan_doc is not None:
    plan_doc.close()
if zone_doc is not None:
    zone_doc.close()
