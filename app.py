# =========================================================
# TELEKELŐÍRÁS AI v5.1
# Tiszta új vezérlési réteg a bevált v4.x feldolgozó függvények fölött
# Cél: "Mit lehet és mit nem lehet ezen a konkrét telken csinálni,
#       és ezt melyik hatályos hivatalos forrás mondja?"
# =========================================================

import io
import re
import urllib.request
import urllib.error
import urllib.parse
import datetime
import json
import os
from html.parser import HTMLParser
from urllib.parse import urljoin

import fitz
import streamlit as st
from PIL import Image, ImageDraw


# =========================================================
# TELEKELŐÍRÁS AI v5.1
# NJT-MELLÉKLET FELDERÍTÉS + NATÍV PDF HELYMEGHATÁROZÁS
# =========================================================

st.set_page_config(page_title="TelekElőírás AI v5.1", page_icon="🏗️", layout="wide")



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
            "User-Agent": "Mozilla/5.0 TelekEloirasAI/5.0",
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
            "User-Agent": "Mozilla/5.0 TelekEloirasAI/5.0",
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




def official_web_zone_search(town: str, hrsz: str):
    """Ingyenes webes fallback: nyilvános keresőindex + hivatalos források.

    Nem használ fizetős AI/API-t. A találatot csak akkor tekinti igazoltnak,
    ha hivatalos oldal/dokumentum szövegében a hrsz és az övezeti kód érdemi
    közelségben együtt szerepel. Egyébként csak jelöltet ad vissza.
    """
    town = normalize_text(town).strip()
    hrsz = normalize_hrsz(hrsz)
    domains = ("njt.jog.gov.hu", "or.njt.hu", "kormanyhivatalok.hu", ".gov.hu", ".hu")
    queries = [
        f'"{town}" "{hrsz}" "Gip"',
        f'"{town}" "{hrsz}" "építési övezet"',
        f'"{town}" "{hrsz}" "övezet"',
        f'"{town}" "{hrsz}" "szabályozási terv"',
    ]
    urls, seen = [], set()
    for q in queries:
        for u in _free_search_urls(q):
            if not u.startswith("http") or u in seen:
                continue
            host=urllib.parse.urlparse(u).netloc.lower()
            if not any(d in host for d in domains):
                continue
            seen.add(u); urls.append((host,u))

    zone_rx = re.compile(r'(?<![A-Za-z0-9ÁÉÍÓÖŐÚÜŰáéíóöőúüű])((?:Gip|Gksz|Gá|Ge|Gipe|Gipez|Lke|Lk|Lf|Ln|Vt|Üü|Üh|Köu|Kök|Köm|Má|Mk|Ev|Eg|Kst|Ksp|Kte|Kap|Ksz|Ke|Kcs|Kre|Kkm|Kmg|Kb)\s*(?:[/_-]\s*[A-Za-z0-9ÁÉÍÓÖŐÚÜŰáéíóöőúüű.-]+)+)', re.I)
    evidence=[]
    for title,u in urls[:20]:
        try:
            if u.lower().split('?')[0].endswith('.pdf'):
                raw=fetch_pdf_bytes(u)
                doc=fitz.open(stream=raw,filetype='pdf')
                chunks=[]
                for pno in range(min(len(doc),250)):
                    txt=extract_page_text(doc[pno])
                    if hrsz.lower() in txt.lower() or hrsz.replace('/',' / ').lower() in txt.lower():
                        chunks.append((pno+1,txt))
                doc.close()
                for page_no,txt in chunks:
                    for sn in snippets_around(txt, hrsz, radius=1600):
                        zones=[canonical_zone(m.group(1).replace(' ','')) for m in zone_rx.finditer(sn)]
                        zones=[z for z in zones if z]
                        if zones:
                            evidence.append({"zone":zones[0],"url":u,"title":title,"detail":f"PDF {page_no}. oldal: hrsz és övezeti kód egy szövegkörnyezetben"})
            else:
                req=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0 TelekEloirasAI/5.0","Accept-Language":"hu-HU,hu;q=0.9"})
                with urllib.request.urlopen(req,timeout=20) as resp:
                    raw=resp.read()
                    ctype=(resp.headers.get('Content-Type') or '').lower()
                if 'pdf' in ctype:
                    doc=fitz.open(stream=raw,filetype='pdf'); txt=' '.join(extract_page_text(doc[i]) for i in range(len(doc))); doc.close()
                else:
                    parser=_HTMLTextExtractor(); parser.feed(raw.decode('utf-8',errors='replace')); txt=parser.text()
                for sn in snippets_around(txt, hrsz, radius=1600):
                    zones=[canonical_zone(m.group(1).replace(' ','')) for m in zone_rx.finditer(sn)]
                    zones=[z for z in zones if z]
                    if zones:
                        evidence.append({"zone":zones[0],"url":u,"title":title,"detail":"hrsz és övezeti kód egy hivatalos szövegkörnyezetben"})
        except Exception:
            continue
    if evidence:
        counts={}
        for e in evidence: counts[e['zone']]=counts.get(e['zone'],0)+1
        best=max(counts,key=counts.get)
        best_ev=[e for e in evidence if e['zone']==best]
        status='verified' if any('njt.jog.gov.hu' in e['url'] or 'or.njt.hu' in e['url'] for e in best_ev) else 'candidate'
        return {"status":status,"zone":best,"confidence":"magas" if status=='verified' else "közepes","reason":"Hivatalos webes forrásban a hrsz és az övezeti kód együtt szerepel.","evidence":best_ev}
    return {"status":"not_found","zone":"","confidence":"nincs","reason":"A nyilvánosan indexelt hivatalos webes forrásokban nem találtam explicit hrsz–övezet kapcsolatot.","evidence":[]}


def full_zone_codes_from_docs(*docs):
    """Összegyűjti a teljes alövezeti kódokat (pl. Gip/3) a natív PDF-szövegből."""
    rx = re.compile(r'(?<![A-Za-z0-9ÁÉÍÓÖŐÚÜŰáéíóöőúüű])((?:Gip|Gksz|Gá|Ge|Gipe|Gipez|Lke|Lk|Lf|Ln|Vt|Üü|Üh|Köu|Kök|Köm|Má|Mk|Ev|Eg|Kst|Ksp|Kte|Kap|Ksz|Ke|Kcs|Kre|Kkm|Kmg|Kb)\s*(?:[/_-]\s*[A-Za-z0-9ÁÉÍÓÖŐÚÜŰáéíóöőúüű.-]+)+)', re.I)
    out=set()
    for doc in docs:
        if doc is None: continue
        for pno in range(len(doc)):
            txt=extract_page_text(doc[pno])
            for m in rx.finditer(txt):
                z=canonical_zone(re.sub(r'\s+','',m.group(1)))
                if z: out.add(z)
    return sorted(out)


def spatial_full_zone_candidates(page, visible_hit, full_codes, radius_factor=160):
    """A hrsz körül konkrét alövezeti kódokat keres search_for()-ral (pl. Gip/3)."""
    cx=(visible_hit.x0+visible_hit.x1)/2; cy=(visible_hit.y0+visible_hit.y1)/2
    base=max(visible_hit.width,visible_hit.height,8); max_dist=base*radius_factor
    found=[]; seen=set()
    for code in full_codes:
        variants={code, code.replace('/',' / '), code.replace('/','/ '), code.replace('/',' /')}
        for v in variants:
            for rect in page.search_for(v):
                vr=visible_rect(page,rect); wx=(vr.x0+vr.x1)/2; wy=(vr.y0+vr.y1)/2
                dist=((wx-cx)**2+(wy-cy)**2)**0.5
                if dist>max_dist: continue
                key=(code,round(vr.x0,1),round(vr.y0,1))
                if key in seen: continue
                seen.add(key); found.append({"Övezeti kód":code,"Felirat":v,"Távolság":round(dist,1),"_rect":vr})
    found.sort(key=lambda r:r['Távolság'])
    return found

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

NJT_BASE = "https://njt.jog.gov.hu"


def _clean_search_redirect(href):
    """DuckDuckGo találati átirányításból kinyeri a valódi URL-t."""
    if not href:
        return ""
    full = urljoin("https://html.duckduckgo.com", href)
    parsed = urllib.parse.urlparse(full)
    qs = urllib.parse.parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return urllib.parse.unquote(qs["uddg"][0])
    return full


def _extract_search_result_urls(html, engine):
    """Keresőtalálatok URL-jeinek kinyerése több ingyenes HTML/RSS forrásból."""
    out=[]
    if engine == "bing_rss":
        # RSS-ben a <link> mezők közvetlen cél URL-ek.
        for u in re.findall(r"<link>(https?://[^<]+)</link>", html, flags=re.I):
            out.append(u.replace("&amp;", "&"))
        return out
    parser=_HTMLLinkExtractor(); parser.feed(html)
    for _, href in parser.links:
        if not href: continue
        u=href
        if engine == "duck":
            u=_clean_search_redirect(href)
        elif engine == "google":
            full=urljoin("https://www.google.com", href)
            pr=urllib.parse.urlparse(full)
            qs=urllib.parse.parse_qs(pr.query)
            if pr.path == "/url" and qs.get("q"):
                u=qs["q"][0]
            else:
                u=full
        elif engine == "bing":
            u=urljoin("https://www.bing.com", href)
        if u.startswith("http"):
            out.append(u)
    return out


def _free_search_urls(query):
    """API-kulcs nélküli, több keresőmotoros discovery.

    Egyetlen szolgáltató blokkolása nem állítja le a HÉSZ-felderítést.
    A kereső csak jelölt URL-t ad; a végső elfogadást az NJT-oldal tartalma dönti el.
    """
    q=urllib.parse.quote_plus(query)
    endpoints=[
        ("bing_rss", f"https://www.bing.com/search?format=rss&q={q}"),
        ("bing", f"https://www.bing.com/search?q={q}"),
        ("google", f"https://www.google.com/search?hl=hu&num=10&q={q}"),
        ("duck", f"https://html.duckduckgo.com/html/?q={q}"),
        ("duck", f"https://lite.duckduckgo.com/lite/?q={q}"),
    ]
    headers={
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept-Language":"hu-HU,hu;q=0.9,en;q=0.7",
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    out=[]; seen=set()
    for engine,url in endpoints:
        try:
            req=urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(req,timeout=15) as resp:
                html=resp.read().decode(resp.headers.get_content_charset() or "utf-8",errors="replace")
            for u in _extract_search_result_urls(html,engine):
                u=u.replace("&amp;","&")
                if u not in seen:
                    seen.add(u); out.append(u)
            # Ha egy motor már adott érdemi találatot, a többiek csak fallbackek.
            if any("njt.jog.gov.hu/jogszabaly/" in x for x in out):
                break
        except Exception:
            continue
    return out


@st.cache_data(show_spinner=False, ttl=21600)
def discover_current_njt_hesz(town):
    """Hatályos helyi HÉSZ/TÉSZ felderítése API-kulcs nélkül.

    Több ingyenes webes indexet használ kizárólag discoveryre, majd MINDEN jelöltet
    az NJT saját jogszabályoldalának tartalmával validál. Nem választ nem hivatalos
    találatot, és nem használ településhez előre beégetett jogszabály-URL-t.
    """
    town=normalize_text(town).strip()
    if not town:
        return {"status":"HIBA","url":"","title":"","candidates":[],"detail":"Hiányzó településnév."}

    queries=[
        f'site:njt.jog.gov.hu/jogszabaly "{town}" "Építési Szabályzat"',
        f'site:njt.jog.gov.hu/jogszabaly "{town}" "helyi építési szabályzat"',
        f'site:njt.jog.gov.hu/jogszabaly "{town}" "Szabályozási Terve"',
        f'site:njt.jog.gov.hu/jogszabaly "{town}" "TÉSZ"',
        f'site:njt.jog.gov.hu/jogszabaly "{town}" önkormányzati rendelet építési',
    ]
    urls=[]; seen=set()
    for q in queries:
        for u in _free_search_urls(q):
            if "njt.jog.gov.hu/jogszabaly/" not in u:
                continue
            u=u.split("#",1)[0].split("?",1)[0]
            # történeti állapot (.2, .3...) helyett a kanonikus jogszabályoldal
            u=re.sub(r"(https://njt\.jog\.gov\.hu/jogszabaly/[^/?#]+?)\.\d+$",r"\1",u)
            if u not in seen:
                seen.add(u); urls.append(u)

    candidates=[]; town_low=town.lower()
    for u in urls[:25]:
        try:
            txt=fetch_njt_text(u)
        except Exception:
            continue
        low=txt.lower(); head=low[:5000]
        score=0
        if town_low in head: score += 8
        elif town_low in low: score += 4
        if "építési szabályzat" in low: score += 7
        if "helyi építési szabályzat" in low: score += 2
        if "szabályozási terv" in low: score += 3
        if "1.1. melléklet" in low or "1. melléklet" in low: score += 2
        if "övezetei" in low or "építési övezetei" in low: score += 2
        if "módosításáról" in head: score -= 6
        if "hatályát veszti" in head and "építési szabályzat" not in low: score -= 4
        title=normalize_text(txt[:450])
        if score >= 12:
            candidates.append({"url":u,"title":title,"score":score})

    # URL szerint deduplikálás, legerősebb pontszám megtartásával
    uniq={}
    for c in candidates:
        if c["url"] not in uniq or c["score"]>uniq[c["url"]]["score"]:
            uniq[c["url"]]=c
    candidates=sorted(uniq.values(),key=lambda x:x["score"],reverse=True)
    if not candidates:
        return {"status":"NINCS","url":"","title":"","candidates":[],"detail":"A nyilvános keresőindexekből nem érkezett validálható NJT HÉSZ/TÉSZ-találat. Ez keresési hozzáférési hiba, nem azt jelenti, hogy nincs hatályos HÉSZ."}

    best=candidates[0]["score"]
    tied=[c for c in candidates if c["score"]==best]
    if len(tied)>1:
        return {"status":"TÖBB JELÖLT","url":"","title":"","candidates":tied,"detail":"Több azonos erősségű, NJT-tartalommal igazolt HÉSZ/TÉSZ-jelölt van; automatikus választás helyett ellenőrzés szükséges."}
    return {"status":"OK","url":candidates[0]["url"],"title":candidates[0]["title"],"candidates":candidates,"detail":"Ingyenes webes discovery után az NJT saját tartalmával visszaellenőrizve."}

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



# =========================================================
# v5.0 – EGYSÉGES FELHASZNÁLÓI FOLYAMAT
# =========================================================

def _v5_open_uploaded_pdf(uploaded):
    if uploaded is None:
        return None
    raw = uploaded.getvalue()
    return fitz.open(stream=raw, filetype="pdf")


def _v5_safe_call(name, *args, **kwargs):
    fn = globals().get(name)
    if not callable(fn):
        return None
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def _v5_zone_from_spatial(plan_doc, hrsz, hesz_doc=None):
    """A telek helyét és a közeli teljes alövezeti kódokat kapcsolja össze."""
    if plan_doc is None:
        return {"status": "missing_plan", "zone": "", "candidates": [], "hit": None}

    hits = find_hrsz(plan_doc, normalize_hrsz(hrsz))
    if not hits:
        return {"status": "parcel_not_found", "zone": "", "candidates": [], "hit": None}

    hit = hits[0]
    page = plan_doc[hit["page_number"]]
    vh = visible_rect(page, hit["pdf_rect"])

    full_codes = full_zone_codes_from_docs(plan_doc, hesz_doc)
    candidates = spatial_full_zone_candidates(page, vh, full_codes, radius_factor=160)

    if not candidates:
        # fallback a dokumentumokból felépített gyökkód-szótárra
        dictionary = build_zone_dictionary(plan_doc, hesz_doc)
        allowed = [r["Övezeti kód"] for r in dictionary
                   if r.get("Bizonyosság") in ("erős", "közepes")]
        candidates = spatial_zone_candidates(page, vh, allowed, radius_factor=90)

    zone = candidates[0]["Övezeti kód"] if candidates else ""
    return {
        "status": "candidate" if zone else "zone_not_found",
        "zone": zone,
        "candidates": candidates,
        "hit": hit,
    }


def _v5_source_link(label, url):
    if url:
        st.markdown(f"[{label}]({url})")


def _v5_extract_decision_rows(njt_text, zone):
    """Forráshű döntéstámogató kivonat. Nem talál ki hiányzó adatot."""
    if not njt_text or not zone:
        return [], {}

    table_params, contexts = parse_zone_table_context(njt_text, zone)
    prose_params = extract_basic_zone_params_from_text(njt_text, zone)
    params = dict(prose_params)
    params.update(table_params)

    rows = []
    for key, value in params.items():
        rows.append({
            "Kérdés": key,
            "Válasz": value,
            "Minősítés": "forrásból kinyert adat",
            "Forrás": "hatályos HÉSZ/TÉSZ – NJT",
        })

    # Rendeltetési / tiltó mondatok csak akkor kerülnek ki, ha ténylegesen szerepelnek
    # a zónakód közeli szövegkörnyezetében.
    snippets = njt_snippets(njt_text, zone, radius=1100, max_items=30)
    seen = set()
    for sn in snippets:
        for sentence in re.split(r"(?<=[.!?;])\s+", sn):
            low = sentence.lower()
            if not any(k in low for k in (
                "elhelyezhető", "nem helyezhető", "megengedett",
                "tilos", "rendeltetés", "kialakítható", "létesíthető"
            )):
                continue
            clean = normalize_text(sentence)
            if len(clean) < 25 or clean in seen:
                continue
            seen.add(clean)
            status = "NEM LEHET / korlátozott" if any(
                k in low for k in ("nem helyezhető", "tilos")
            ) else "LEHET / feltételesen alkalmazható"
            rows.append({
                "Kérdés": "Rendeltetés / használat",
                "Válasz": clean,
                "Minősítés": status,
                "Forrás": "hatályos HÉSZ/TÉSZ – NJT",
            })
            if len(rows) >= 16:
                break
        if len(rows) >= 16:
            break
    return rows, params



# =========================================================
# v5.1 – ROBUSZTUS, INGYENES HÉSZ/TÉSZ WEBES FELDERÍTÉS
# Nem Google HTML-t kapar. Több nyilvános keresési útvonalat használ,
# majd MAGÁT AZ NJT/OR.NJT OLDALT validálja.
# =========================================================

from urllib.parse import quote_plus, urljoin, urlparse, parse_qs
import html as _html


def _v51_clean_search_url(href):
    """Keresőmotor-átirányításból kinyeri a valódi cél-URL-t."""
    if not href:
        return ""
    href = _html.unescape(href)

    # DuckDuckGo redirect: /l/?uddg=https%3A...
    if "uddg=" in href:
        try:
            return requests.utils.unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
        except Exception:
            pass

    if href.startswith("//"):
        href = "https:" + href
    return href


def _v51_search_web(query, timeout=14):
    """
    Ingyenes keresés több nyilvános HTML keresőfelületen.
    Nincs API-kulcs. Sikertelenség esetén üres listát ad.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TelekEloirasAI/5.2; +public-web-search)",
        "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.6",
    }
    endpoints = [
        ("duckduckgo", "https://html.duckduckgo.com/html/?q=" + quote_plus(query)),
        ("bing", "https://www.bing.com/search?q=" + quote_plus(query)),
    ]
    found = []
    seen = set()

    for engine, url in endpoints:
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = _v51_clean_search_url(a.get("href", ""))
                title = normalize_text(a.get_text(" ", strip=True))
                if not href.startswith("http"):
                    continue
                host = urlparse(href).netloc.lower()
                if not (host.endswith("njt.hu") or host.endswith("tiszaujvaros.hu") or
                        "onkormanyzati-rendelet" in href or "jogszabaly" in href):
                    continue
                key = href.split("#")[0]
                if key in seen:
                    continue
                seen.add(key)
                found.append({"engine": engine, "title": title, "url": key})
        except Exception:
            continue
    return found


def _v51_candidate_queries(town):
    town = normalize_text(town)
    return [
        f'"{town}" "Építési Szabályzatáról" NJT',
        f'"{town}" "Helyi Építési Szabályzatáról" NJT',
        f'{town} építési szabályzat NJT',
        f'{town} HÉSZ NJT',
        f'site:or.njt.hu {town} építési szabályzat',
        f'site:njt.jog.gov.hu {town} építési szabályzat',
    ]


def _v51_fetch_page(url, timeout=18):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TelekEloirasAI/5.2)",
        "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.5",
    }
    r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title = normalize_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    body = normalize_text(soup.get_text(" ", strip=True))
    return {"url": r.url, "title": title, "text": body, "html": r.text}


def _v51_score_hesz_page(town, page):
    """Nem a találati címet, hanem a céloldal tartalmát pontozza."""
    town_l = normalize_text(town).lower()
    title_l = page.get("title", "").lower()
    text_l = page.get("text", "").lower()
    url_l = page.get("url", "").lower()

    score = 0
    reasons = []

    if town_l and town_l in (title_l + " " + text_l[:5000]):
        score += 35
        reasons.append("településnév egyezik")

    exact_terms = [
        "építési szabályzatáról",
        "helyi építési szabályzatáról",
        "építési szabályzata",
        "helyi építési szabályzata",
    ]
    if any(t in title_l for t in exact_terms):
        score += 45
        reasons.append("HÉSZ/TÉSZ cím")
    elif any(t in text_l[:7000] for t in exact_terms):
        score += 28
        reasons.append("HÉSZ/TÉSZ tartalom")

    if "módosításáról" in title_l:
        score -= 28
        reasons.append("módosító rendelet")
    if "hatályon kívül" in text_l[:5000]:
        score -= 8

    if "or.njt.hu" in url_l or "njt.jog.gov.hu" in url_l:
        score += 20
        reasons.append("NJT domain")

    # Erős jel: szabályozási terv/mellékletek említése.
    if "szabályozási terv" in text_l:
        score += 12
        reasons.append("szabályozási terv hivatkozás")
    if "1. melléklet" in text_l or "1.1. melléklet" in text_l:
        score += 5

    # Az alaprendeletet preferáljuk a puszta módosító rendelettel szemben.
    if "önkormányzati rendelete" in text_l[:2500]:
        score += 4

    return score, reasons


def discover_current_njt_hesz_v51(town):
    """
    1) több keresőkifejezés;
    2) találatok összegyűjtése;
    3) NJT céloldalak tényleges letöltése;
    4) tartalmi validálás;
    5) módosító rendelet visszasorolása.
    """
    raw = []
    seen = set()
    diagnostics = []

    for q in _v51_candidate_queries(town):
        results = _v51_search_web(q)
        diagnostics.append({"query": q, "hits": len(results)})
        for item in results:
            u = item["url"]
            if u not in seen:
                seen.add(u)
                raw.append(item)

    validated = []
    for item in raw[:40]:
        try:
            page = _v51_fetch_page(item["url"])
        except Exception:
            continue
        score, reasons = _v51_score_hesz_page(town, page)
        if score >= 55:
            validated.append({
                "title": page["title"] or item.get("title", ""),
                "url": page["url"],
                "score": score,
                "reasons": ", ".join(reasons),
                "engine": item.get("engine", ""),
                "text": page["text"],
            })

    # Deduplikálás + legerősebb előre.
    dedup = {}
    for c in validated:
        key = c["url"].split("?")[0].rstrip("/")
        if key not in dedup or c["score"] > dedup[key]["score"]:
            dedup[key] = c
    validated = sorted(dedup.values(), key=lambda x: x["score"], reverse=True)

    if not validated:
        return {
            "status": "NINCS",
            "url": "",
            "title": "",
            "candidates": [],
            "diagnostics": diagnostics,
        }

    best = validated[0]

    # Ha a legjobb találat módosító rendelet, próbáljuk a szövegben szereplő
    # alaprendeletet megtalálni a többi validált jelölt között.
    if "módosításáról" in best["title"].lower():
        base_candidates = [
            c for c in validated
            if "módosításáról" not in c["title"].lower()
            and normalize_text(town).lower() in (c["title"] + " " + c.get("text", "")[:3000]).lower()
        ]
        if base_candidates:
            best = base_candidates[0]

    return {
        "status": "OK",
        "url": best["url"],
        "title": best["title"],
        "score": best["score"],
        "reasons": best["reasons"],
        "candidates": validated[:10],
        "diagnostics": diagnostics,
    }


def discover_current_njt_hesz_v51_with_legacy(town):
    """
    Elsőként az új keresőréteg fut.
    Ha a hosztolt környezet mindkét nyilvános kereső HTML-jét blokkolja,
    utolsó tartalékként meghívja a korábbi felderítőt.
    """
    result = discover_current_njt_hesz_v51(town)
    if result.get("status") == "OK":
        return result

    legacy = globals().get("_legacy_discover_current_njt_hesz")
    if callable(legacy):
        try:
            old = legacy(town)
            if isinstance(old, dict) and old.get("status") in ("OK", "TÖBB JELÖLT"):
                old["finder"] = "legacy fallback"
                return old
        except Exception:
            pass
    return result


# A korábbi függvényt megőrizzük fallbacknek, majd az újra irányítjuk a v5 folyamatot.
if "discover_current_njt_hesz" in globals():
    _legacy_discover_current_njt_hesz = discover_current_njt_hesz
discover_current_njt_hesz = discover_current_njt_hesz_v51_with_legacy


# =========================================================
# v5.2 – NJT SAJÁT ÖNKORMÁNYZATI RENDELETKERESŐJE AZ ELSŐDLEGES FORRÁS
# A program először NEM Google/Bing/DDG találati oldalt kapar.
# Közvetlenül az OR.NJT hivatalos önkormányzati rendeletkeresőjét próbálja
# településre + címre + hatályosságra szűrni, majd a találat céloldalát validálja.
# A v5.1 webes kereső csak tartalék marad.
# =========================================================

from urllib.parse import urlencode

_OR_NJT_SEARCH = "https://or.njt.hu/onkorm"

def _v52_http_get(url, params=None, timeout=22):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TelekEloirasAI/5.2; official-NJT-client)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.5",
        "Cache-Control": "no-cache",
    }
    r = requests.get(url, params=params, headers=headers, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r

def _v52_http_post(url, data=None, timeout=22):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TelekEloirasAI/5.2; official-NJT-client)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.5",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://or.njt.hu",
        "Referer": _OR_NJT_SEARCH,
    }
    r = requests.post(url, data=data, headers=headers, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r

def _v52_is_hesz_title(title):
    t = normalize_text(title).lower()
    if "építési szabályzat" not in t and "helyi építési szabályzat" not in t:
        return False
    # A módosító rendelet önmagában ne legyen elsődleges alaprendelet.
    if "módosításáról" in t or "módosítás" in t:
        return False
    return True

def _v52_extract_official_results(html, town):
    """OR.NJT keresési eredményoldalból hivatalos rendelet-linkek."""
    soup = BeautifulSoup(html or "", "html.parser")
    out, seen = [], set()
    town_l = normalize_text(town).lower()

    for a in soup.find_all("a", href=True):
        href = urljoin("https://or.njt.hu", a.get("href", ""))
        title = normalize_text(a.get_text(" ", strip=True))
        blob = (title + " " + normalize_text(a.parent.get_text(" ", strip=True) if a.parent else "")).lower()
        if "or.njt.hu" not in href:
            continue
        if not ("/onkormanyzati-rendelet/" in href or "/eli/" in href):
            continue
        if town_l and town_l not in blob and "építési szabályzat" not in blob:
            continue
        key = href.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "url": key, "source": "OR.NJT rendeletkereső"})
    return out

def _v52_find_town_option(soup, town):
    """Megkeresi a település opcióját az NJT kereső űrlapjában."""
    town_l = normalize_text(town).lower()
    matches = []
    for sel in soup.find_all("select"):
        name = sel.get("name") or sel.get("id") or ""
        for opt in sel.find_all("option"):
            label = normalize_text(opt.get_text(" ", strip=True))
            if town_l == label.lower() or town_l in label.lower():
                matches.append({
                    "select": sel,
                    "name": name,
                    "value": opt.get("value", ""),
                    "label": label,
                })
    if not matches:
        return None
    # Pontos településnév előnyben.
    matches.sort(key=lambda x: (normalize_text(x["label"]).lower() != town_l, len(x["label"])))
    return matches[0]

def _v52_submit_njt_form(base_html, base_url, town):
    """
    Az NJT saját HTML-űrlapját használja. Nem feltételezi előre a mezőneveket:
    a település-selectet és a cím/szókereső mezőt a DOM-ból azonosítja.
    """
    soup = BeautifulSoup(base_html or "", "html.parser")
    town_opt = _v52_find_town_option(soup, town)
    if not town_opt:
        return None, {"stage": "town-option", "detail": "A település nem volt felismerhető az NJT kereső űrlapjában."}

    form = town_opt["select"].find_parent("form")
    if form is None:
        # Egyes NJT-verziók kliensoldali útvonalat építenek. Ilyenkor
        # legalább a település belső azonosítóját visszaadjuk a következő próbához.
        return None, {
            "stage": "no-form",
            "town_id": town_opt["value"],
            "detail": "Településazonosító megvan, de hagyományos HTML form nem található."
        }

    data = {}
    # Hidden és alapértelmezett mezők megtartása.
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        typ = (inp.get("type") or "text").lower()
        if typ in ("submit", "button", "image", "file"):
            continue
        if typ in ("checkbox", "radio") and not inp.has_attr("checked"):
            continue
        data[name] = inp.get("value", "")

    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        chosen = sel.find("option", selected=True)
        if chosen is not None:
            data[name] = chosen.get("value", "")

    data[town_opt["name"]] = town_opt["value"]

    # Cím/szókereső mező automatikus felismerése.
    for inp in form.find_all("input"):
        name = inp.get("name") or ""
        ident = (name + " " + (inp.get("id") or "") + " " +
                 (inp.get("placeholder") or "") + " " + (inp.get("aria-label") or "")).lower()
        if any(k in ident for k in ("cím", "cim", "title", "szókeres", "szokeres", "search")):
            if (inp.get("type") or "text").lower() in ("text", "search", ""):
                data[name] = "építési szabályzat"
                break

    # "csak hatályos" checkbox – ha felismerhető, kapcsoljuk be.
    for inp in form.find_all("input"):
        if (inp.get("type") or "").lower() != "checkbox":
            continue
        name = inp.get("name")
        ident = ((name or "") + " " + (inp.get("id") or "") + " " +
                 (inp.get("aria-label") or "")).lower()
        parent_txt = normalize_text(inp.parent.get_text(" ", strip=True) if inp.parent else "").lower()
        if "hatályos" in ident or "hatályos" in parent_txt:
            if name:
                data[name] = inp.get("value") or "1"

    action = urljoin(base_url, form.get("action") or base_url)
    method = (form.get("method") or "get").lower()
    try:
        if method == "post":
            r = _v52_http_post(action, data=data)
        else:
            r = _v52_http_get(action, params=data)
        return r, {"stage": "form-submit", "town_id": town_opt["value"], "method": method}
    except Exception as e:
        return None, {"stage": "form-submit-error", "town_id": town_opt["value"], "detail": str(e)[:240]}

def _v52_route_candidates_from_town_id(town_id):
    """
    OR.NJT jelenlegi keresője útvonal-paraméteres találati oldalt is használ.
    A DOM-ból kinyert településazonosítóval néhány dokumentáltan megfigyelhető
    útvonalalakot próbálunk; a találatot utána mindig tartalmilag validáljuk.
    """
    if not town_id:
        return []
    tid = str(town_id).strip()
    return [
        f"https://or.njt.hu/onkorm/-:5:{tid}:-:-:1:-:1:-/1/100",
        f"https://or.njt.hu/onkorm/-:-:{tid}:-:-:1:-:1:-/1/100",
        f"https://or.njt.hu/onkorm/-:5:{tid}:-:-:-:-:1:-/1/100",
    ]

def discover_current_njt_hesz_v52(town):
    diagnostics = []
    candidates = []

    # 1. KÖZVETLENÜL az NJT saját rendeletkeresője.
    try:
        landing = _v52_http_get(_OR_NJT_SEARCH)
        diagnostics.append({"stage": "NJT kereső megnyitása", "status": landing.status_code, "url": landing.url})

        # Ha a kezdőoldal már tartalmaz találatot (ritka), azt is feldolgozzuk.
        candidates.extend(_v52_extract_official_results(landing.text, town))

        submitted, meta = _v52_submit_njt_form(landing.text, landing.url, town)
        diagnostics.append(meta)
        if submitted is not None:
            candidates.extend(_v52_extract_official_results(submitted.text, town))

        # Ha az NJT felület JS/útvonal alapú, a DOM-ból kiolvasott település-ID-vel
        # közvetlenül a hivatalos találati útvonalakat próbáljuk.
        town_id = meta.get("town_id") if isinstance(meta, dict) else None
        if town_id:
            for route in _v52_route_candidates_from_town_id(town_id):
                try:
                    rr = _v52_http_get(route)
                    hits = _v52_extract_official_results(rr.text, town)
                    diagnostics.append({"stage": "NJT route", "url": rr.url, "hits": len(hits)})
                    candidates.extend(hits)
                    if hits:
                        break
                except Exception as e:
                    diagnostics.append({"stage": "NJT route hiba", "url": route, "detail": str(e)[:160]})
    except Exception as e:
        diagnostics.append({"stage": "NJT közvetlen kereső hiba", "detail": str(e)[:240]})

    # Deduplikálás.
    dedup = {}
    for c in candidates:
        key = c["url"].split("?")[0].rstrip("/")
        if key not in dedup:
            dedup[key] = c
    candidates = list(dedup.values())

    # 2. A hivatalos találatok CÉLOLDALÁNAK validálása.
    validated = []
    for item in candidates[:50]:
        try:
            page = _v51_fetch_page(item["url"])
            score, reasons = _v51_score_hesz_page(town, page)
            title = page.get("title") or item.get("title", "")
            # alaprendelet előnyben; módosító csak diagnosztikai jelölt
            if _v52_is_hesz_title(title):
                score += 35
            elif "módosítás" in normalize_text(title).lower():
                score -= 35
            if score >= 55:
                validated.append({
                    "title": title,
                    "url": page["url"],
                    "score": score,
                    "reasons": ", ".join(reasons),
                    "source": item.get("source", "OR.NJT"),
                    "text": page.get("text", ""),
                })
        except Exception as e:
            diagnostics.append({"stage": "NJT céloldal validálási hiba", "url": item["url"], "detail": str(e)[:160]})

    validated.sort(key=lambda x: x["score"], reverse=True)
    base = [c for c in validated if _v52_is_hesz_title(c["title"])]
    if base:
        best = base[0]
        return {
            "status": "OK",
            "url": best["url"],
            "title": best["title"],
            "score": best["score"],
            "reasons": best["reasons"],
            "candidates": validated[:10],
            "diagnostics": diagnostics,
            "finder": "OR.NJT hivatalos rendeletkereső",
        }

    # 3. TARTALÉK: a v5.1 többmotoros nyilvános webes keresés.
    # Ez már nem elsődleges logika.
    fallback = globals().get("_v52_v51_fallback")
    if callable(fallback):
        try:
            old = fallback(town)
            if isinstance(old, dict) and old.get("status") in ("OK", "TÖBB JELÖLT"):
                old["finder"] = "webes tartalékkeresés"
                old["diagnostics"] = diagnostics + old.get("diagnostics", [])
                return old
        except Exception as e:
            diagnostics.append({"stage": "webes fallback hiba", "detail": str(e)[:200]})

    return {
        "status": "NINCS",
        "url": "",
        "title": "",
        "candidates": validated[:10],
        "diagnostics": diagnostics,
        "detail": (
            "Az NJT hivatalos önkormányzati rendeletkeresőjéből sem sikerült "
            "automatikusan validált HÉSZ/TÉSZ-alaprendeletet kinyerni. "
            "Ez technikai hozzáférési/feldolgozási hiba, nem a HÉSZ hiányának állítása."
        ),
    }

# v5.1 keresőt csak fallbackként őrizzük meg.
_v52_v51_fallback = discover_current_njt_hesz
discover_current_njt_hesz = discover_current_njt_hesz_v52


def run_v5():
    st.title("TelekElőírás AI")
    st.caption(
        "v5.2 • telek → hatályos HÉSZ/TÉSZ → szabályozási terv → övezet → "
        "telekspecifikus előírások → forrásolt döntéstámogató adatlap"
    )

    with st.sidebar:
        st.header("Telek")
        town = st.text_input("Település", value="Tiszaújváros")
        hrsz = st.text_input("Helyrajzi szám", value="2200/8")

        st.header("Térképi forrás")
        plan_upload = st.file_uploader(
            "Szabályozási terv PDF (ha az NJT melléklet nem tölthető le automatikusan)",
            type=["pdf"],
            key="v5_plan",
        )
        st.caption(
            "A kézi PDF csak tartalék. A program elsőként automatikusan keresi "
            "a hatályos hivatalos forrást."
        )

        manual_zone = st.text_input(
            "Kézzel igazolt övezeti kód (csak ellenőrzéshez, opcionális)",
            value="",
            placeholder="pl. Gip/3",
        )
        start = st.button("Telekvizsgálat indítása", type="primary", use_container_width=True)

        st.divider()
        st.markdown("**Hivatalos térképi ellenőrzés**")
        st.link_button("E-közmű térkép megnyitása", EKOZMU_MAP, use_container_width=True)

    if not start:
        st.info(
            "Add meg a települést és a helyrajzi számot, majd indítsd el a vizsgálatot. "
            "A program nem tekint övezeti besorolásnak pusztán egy közeli feliratot."
        )
        return

    if not town.strip() or not normalize_hrsz(hrsz):
        st.error("A település és a helyrajzi szám megadása kötelező.")
        return

    # 1. HÉSZ/TÉSZ
    st.header("1. Hatályos hivatalos forrás felderítése")
    with st.spinner("Hatályos HÉSZ/TÉSZ keresése és NJT-validálása…"):
        hesz = discover_current_njt_hesz(town)

    njt_url = hesz.get("url", "") if isinstance(hesz, dict) else ""
    if hesz.get("status") == "OK":
        st.success("Hatályos HÉSZ/TÉSZ-jelölt megtalálva és az NJT tartalmával visszaellenőrizve.")
        _v5_source_link("NJT – hivatalos HÉSZ/TÉSZ megnyitása", njt_url)
    elif hesz.get("status") == "TÖBB JELÖLT":
        st.warning("Több hivatalos HÉSZ/TÉSZ-jelölt maradt. Automatikusan nem választok közülük.")
        for c in hesz.get("candidates", [])[:5]:
            _v5_source_link(c.get("title", "NJT találat"), c.get("url", ""))
    else:
        st.error(
            "A hatályos HÉSZ/TÉSZ automatikus felderítése most nem adott "
            "kellően igazolt NJT-találatot. Ez keresési hozzáférési hiba is lehet; "
            "nem jelenti azt, hogy nincs hatályos szabályzat."
        )

    njt_text = ""
    if njt_url:
        try:
            njt_text = fetch_njt_text(njt_url)
        except Exception as e:
            st.warning(f"Az NJT szövegét nem sikerült betölteni: {e}")

    # 2. Mellékletek
    st.header("2. Szabályozási terv és mellékletek")
    attachments = []
    if njt_url:
        try:
            attachments = discover_njt_attachments(njt_url)
        except Exception:
            attachments = []

    if attachments:
        st.success(f"{len(attachments)} hivatalos melléklet-hivatkozást találtam.")
        st.dataframe(attachments, width="stretch", hide_index=True)
    else:
        st.warning(
            "Az NJT HTML-ből nem sikerült stabil közvetlen melléklet-hivatkozást kinyerni. "
            "Ezért a feltöltött szabályozási terv használható térbeli ellenőrzésre."
        )

    plan_doc = _v5_open_uploaded_pdf(plan_upload)
    hesz_doc = None

    # 3. Telek
    st.header("3. Telekazonosítás")
    st.write(f"**{town} {normalize_hrsz(hrsz)} hrsz.**")
    if plan_doc is None:
        st.info(
            "A telek térbeli helyét szabályozási terv nélkül nem állítom be találgatással. "
            "Ha az NJT melléklet közvetlenül nem tölthető le, töltsd fel a hivatalos "
            "szabályozási terv PDF-jét a bal oldalon."
        )
        spatial = {"status": "missing_plan", "zone": "", "candidates": [], "hit": None}
    else:
        spatial = _v5_zone_from_spatial(plan_doc, hrsz, hesz_doc)
        if spatial["hit"]:
            pno = spatial["hit"]["page_number"] + 1
            st.success(f"A helyrajzi szám megtalálva a szabályozási terv {pno}. PDF-oldalán.")
            page = plan_doc[spatial["hit"]["page_number"]]
            vh = visible_rect(page, spatial["hit"]["pdf_rect"])
            st.image(parcel_crop(page, vh, scale=0.10, zoom=0.75),
                     caption="A helyrajzi szám környezete a szabályozási terven",
                     width="stretch")
        else:
            st.error("A helyrajzi számot nem találtam meg a feltöltött szabályozási terv natív szövegrétegében.")

    # 4. Övezet
    st.header("4. A konkrét telek övezete")
    auto_zone = spatial.get("zone", "")
    zone = manual_zone.strip() or auto_zone

    if manual_zone.strip():
        st.warning(
            f"Kézzel megadott ellenőrzési övezeti kód: **{manual_zone.strip()}**. "
            "Ezt a program nem tekinti automatikusan térben igazolt besorolásnak."
        )
    elif auto_zone:
        st.success(f"Legközelebbi dokumentumalapú övezeti jelölt: **{auto_zone}**.")
        st.caption(
            "A közeli felirat önmagában nem azonos a telekpolygon és az övezetpolygon "
            "geometriai metszésével; ezért ezt addig jelöltként kezeljük, amíg a térbeli "
            "besorolás egyértelműen nem igazolható."
        )
    else:
        st.warning("A konkrét telek övezete jelenleg nincs kellően igazolva.")

    if spatial.get("candidates"):
        st.dataframe(
            [{k: v for k, v in r.items() if k != "_rect"}
             for r in spatial["candidates"][:12]],
            width="stretch",
            hide_index=True,
        )

    # 5. Övezeti előírások
    st.header("5. Mit mond a hatályos szabályzat?")
    decision_rows, params = _v5_extract_decision_rows(njt_text, zone)

    if not zone:
        st.warning("Övezeti kód nélkül nem kapcsolok övezetspecifikus előírást a telekhez.")
    elif not njt_text:
        st.warning("A hatályos NJT-szöveg nem áll rendelkezésre, ezért jogi paramétert nem állítok.")
    elif decision_rows:
        st.dataframe(decision_rows, width="stretch", hide_index=True)
    else:
        st.warning(
            f"A **{zone}** kódhoz nem sikerült kellően strukturált előírást kinyerni. "
            "A program nem egészíti ki feltételezéssel."
        )

    # 6. Korlátozások
    st.header("6. Telekspecifikus korlátozások")
    st.info(
        "Védősáv, védőterület, hidrogeológiai védőterület, veszélyességi övezet, "
        "szabályozási/építési vonal és más térbeli korlátozás csak akkor minősül "
        "telekspecifikusnak, ha a telekkel való térbeli érintettség igazolható. "
        "A szabályzatban való puszta előfordulás nem elegendő."
    )

    # 7. Közmű
    st.header("7. E-közmű – közműérintettségek")
    st.warning(
        "A program nem használ dokumentálatlan E-közmű belső API-végpontot. "
        "A közműérintettség addig kézi/hivatalos térképi ellenőrzést igényel, "
        "amíg nyilvánosan dokumentált gépi szolgáltatás nem áll rendelkezésre."
    )
    st.link_button("E-közmű térkép megnyitása", EKOZMU_MAP)

    # 8. Döntéstámogató válasz
    st.header("8. Mit lehet és mit nem lehet ezen a konkrét telken?")
    spatially_verified = bool(auto_zone) and not bool(manual_zone.strip())
    source_verified = bool(njt_url and njt_text)

    if spatially_verified and source_verified and decision_rows:
        st.success(
            "A program talált dokumentumalapú övezeti kapcsolatot és hozzá hivatalos "
            "NJT-forrást. Az alábbi adatok forrásolt döntéstámogató eredmények; "
            "a térbeli korlátozásokat ettől még külön kell ellenőrizni."
        )
        st.dataframe(decision_rows, width="stretch", hide_index=True)
    else:
        missing = []
        if not spatially_verified:
            missing.append("egyértelműen igazolt telek–övezet kapcsolat")
        if not source_verified:
            missing.append("automatikusan igazolt hatályos NJT-forrás")
        if not decision_rows:
            missing.append("strukturált övezeti előírás")
        st.warning(
            "A konkrét telekre vonatkozó végleges „lehet / nem lehet” válasz még nem "
            "adható ki megbízhatóan. Hiányzik: **" + "; ".join(missing) + "**."
        )

    # 9. Forráslap
    st.header("9. Forrásolt telek-adatlap")
    summary = [
        {"Adat": "Telek", "Eredmény": f"{town} {normalize_hrsz(hrsz)}",
         "Elsődleges forrás": "E-közmű / ingatlan-nyilvántartás",
         "Bizonyosság": "térképen ellenőrzendő"},
        {"Adat": "Hatályos HÉSZ/TÉSZ",
         "Eredmény": hesz.get("title", "") if hesz.get("status") == "OK" else "nincs automatikusan igazolva",
         "Elsődleges forrás": "NJT", "Bizonyosság": hesz.get("status", "NINCS")},
        {"Adat": "Övezet", "Eredmény": zone or "nincs igazolva",
         "Elsődleges forrás": "szabályozási terv",
         "Bizonyosság": "dokumentumalapú jelölt" if auto_zone else "nincs"},
        {"Adat": "Övezeti paraméterek",
         "Eredmény": f"{len(params)} strukturált adat" if params else "nincs kellően kinyert adat",
         "Elsődleges forrás": "NJT HÉSZ/TÉSZ",
         "Bizonyosság": "forrásolt" if params and source_verified else "ellenőrzendő"},
        {"Adat": "Közműérintettség", "Eredmény": "kézi térképi ellenőrzés",
         "Elsődleges forrás": "E-közmű", "Bizonyosság": "még nincs automatizálva"},
    ]
    st.dataframe(summary, width="stretch", hide_index=True)

    st.caption(
        "A TelekElőírás AI döntéstámogató eszköz. Nem helyettesíti a hatósági, "
        "tervezői vagy jogi ellenőrzést. A program csak olyan telekspecifikus állítást "
        "tesz, amelyhez az alkalmazott forrás és az igazolás módja megadható."
    )


if __name__ == "__main__":
    run_v5()
