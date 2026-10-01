import io
import re

import fitz
import streamlit as st
from PIL import Image, ImageDraw


# =========================================================
# TELEKELŐÍRÁS AI v3.2
# NATÍV PDF HELYMEGHATÁROZÁS – ELLENŐRZÖTT ROTÁCIÓS LEKÉPEZÉS
# =========================================================

st.set_page_config(
    page_title="TelekElőírás AI v3.2",
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
    candidates = spatial_zone_candidates(page, vh, allowed)

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

st.title("TelekElőírás AI")
st.caption(
    "v3.2 • natív PDF-keresés • telekhely + övezeti jelölés összekapcsolása • OCR nélkül"
)

with st.sidebar:
    st.header("Tesztforrások")
    st.info(
        "A v3.2 megtartja az ellenőrzött telekhely-meghatározást, és a teljes PDF kereshető "
        "szövegrétegéből automatikusan felderíti a jelkulcs-oldalakat és az övezeti kódokat. "
        "Fix oldalszámokat nem használ, és kiszűri a tipikus közmű- és szelvényjelöléseket. "
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
    "v3.2 telek + övezet vizsgálat indítása",
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

        # v3.2: a dokumentumokból felismert övezeti szótárat összekapcsoljuk
        # a már ellenőrzött hrsz-találat térbeli helyével.
        if hits:
            zone_dictionary_v32 = build_zone_dictionary(doc, hesz_doc)
            show_parcel_zone_link(doc, hits[0], zone_dictionary_v32, crop_scale)
    finally:
        if hesz_doc is not None:
            hesz_doc.close()

    doc.close()
