# TelekElőírás AI v15.21
# Tiszta, újraírt Streamlit alkalmazás.
# Cél: telek -> hivatalos NJT-forrás -> szabályozási terv -> övezeti jelölt
#      -> forrásolt övezeti előírások.
#
# Fontos: döntéstámogató eszköz, nem hatósági vagy jogi állásfoglalás.

import io
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
import ssl
import math
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin

import fitz
import streamlit as st
from PIL import Image, ImageDraw


st.set_page_config(
    page_title="TelekElőírás AI v15.21",
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
    # Budapest XII. kerület – Dél-Hegyvidék KÉSZ. A hivatkozás az NJT
    # egységes szerkezetben megjelenített, hatályos szövegére mutat.
    "budapest xii. kerulet": {
        "municipality": "Budapest XII. kerület",
        "title": "Dél-Hegyvidék Kerületi Építési Szabályzat",
        "regulation": "36/2021. (XII. 14.) önkormányzati rendelet",
        "url": "https://njt.jog.gov.hu/jogszabaly/2021-36-SP-5Y261",
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
            "User-Agent": "Mozilla/5.0 TelekEloirasAI/15.16",
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
        "NJT-domain": is_official_njt_url(page.get("url", "")),
        "település": town_key in body or (key_text(town) == "budapest" and "budapest" in body),
        "építési szabályzat": (
            "epitesi szabalyzat" in body
            or "helyi epitesi szabalyzat" in body
            or "keruleti epitesi szabalyzat" in body
        ),
    }
    return all(checks.values()), checks



# ---------------------------------------------------------------------
# Dinamikus hivatalos forrásfelderítés (v10)
# ---------------------------------------------------------------------

def _search_web(query, timeout=20):
    """Nyilvános keresőtalálatok jelöltkereséshez.

    v14: DuckDuckGo után Bing HTML fallback. A keresőtalálat önmagában soha
    nem jogi bizonyíték: a céloldalt mindig közvetlenül letöltjük és ellenőrizzük.
    """
    out = []

    def add(url):
        if not url:
            return
        url = url.replace("&amp;", "&")
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("http") and url not in out:
            out.append(url)

    # 1) DuckDuckGo
    try:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        raw, _, status, charset, _ = http_get(url, timeout=timeout)
        if status < 400:
            html = raw.decode(charset or "utf-8", errors="replace")
            for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I):
                href = href.replace("&amp;", "&")
                if "uddg=" in href:
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href = qs.get("uddg", [href])[0]
                add(urllib.parse.unquote(href))
    except Exception:
        pass

    # 2) Bing fallback / kiegészítés
    if len(out) < 12:
        try:
            url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
            raw, _, status, charset, _ = http_get(url, timeout=timeout)
            if status < 400:
                html = raw.decode(charset or "utf-8", errors="replace")
                # Elsősorban a normál keresési találatok.
                for href in re.findall(
                    r'<li[^>]+class=["\'][^"\']*b_algo[^"\']*["\'][\s\S]*?<a[^>]+href=["\']([^"\']+)["\']',
                    html, flags=re.I
                ):
                    add(urllib.parse.unquote(href))
                # Biztonsági tartalék: minden külső http(s) link.
                if len(out) < 8:
                    for href in re.findall(r'href=["\'](https?://[^"\']+)["\']', html, flags=re.I):
                        host = (urllib.parse.urlparse(href).hostname or "").casefold()
                        if "bing.com" not in host and "microsoft.com" not in host:
                            add(urllib.parse.unquote(href))
        except Exception:
            pass

    return out[:40]


def is_official_njt_url(url):
    host = (urllib.parse.urlparse(url).hostname or "").casefold()
    return host in {"njt.jog.gov.hu", "or.njt.hu", "njt.hu"} or host.endswith(".njt.hu")


def discover_budapest_district(hrsz):
    """Budapesti hrsz. -> kerület, bizonyíték-alapon.

    Sorrend:
      1. OÉNY / e-építés HRSZ szerinti nyilvános ingatlanoldal
      2. Budapest Főváros Kormányhivatala hivatalos dokumentumai
      3. egyéb hivatalos budapesti/e-építés forrás

    OCR nincs. Csak olyan eredmény tér vissza, ahol a pontos alaphRSZ és a
    kerület ugyanabban a letöltött hivatalos tartalomban igazolható.
    """
    h = normalize_hrsz(hrsz)
    if not h:
        return "", ""

    compact_h = re.sub(r"[\s/.\-]+", "", h).casefold()
    roman = r"(?:XXIII|XXII|XXI|XX|XIX|XVIII|XVII|XVI|XV|XIV|XIII|XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)"
    district_patterns = [
        re.compile(rf"\bBudapest\s+({roman})\.?\s*ker(?:ület|\.)?\b", re.I),
        re.compile(rf"\bBudapest\s+0?(\d{{1,2}})\.?\s*ker(?:ület|\.)?\b", re.I),
    ]
    hrsz_rx = re.compile(
        rf"(?<![\d/]){re.escape(h).replace('/', r'\s*/\s*')}(?!\s*/\s*[A-Za-z0-9])",
        re.I
    )

    roman_map = {
        1:"I",2:"II",3:"III",4:"IV",5:"V",6:"VI",7:"VII",8:"VIII",9:"IX",
        10:"X",11:"XI",12:"XII",13:"XIII",14:"XIV",15:"XV",16:"XVI",
        17:"XVII",18:"XVIII",19:"XIX",20:"XX",21:"XXI",22:"XXII",23:"XXIII"
    }

    def normalize_district(value):
        v = clean_text(value).upper().replace(".", "")
        if v.isdigit():
            n = int(v)
            return roman_map.get(n, "")
        return v if v in roman_map.values() else ""

    def inspect_text(plain, source_url, strict_distance=1200):
        if not plain:
            return None
        compact_plain = re.sub(r"[\s/.\-]+", "", plain).casefold()
        if compact_h not in compact_plain:
            return None

        hits = list(hrsz_rx.finditer(plain))
        if not hits:
            return None

        for hm in hits:
            a = max(0, hm.start() - strict_distance)
            b = min(len(plain), hm.end() + strict_distance)
            window = plain[a:b]
            local_hrsz = hm.start() - a
            candidates = []
            for rx in district_patterns:
                for dm in rx.finditer(window):
                    d = normalize_district(dm.group(1))
                    if d:
                        candidates.append((abs(local_hrsz - dm.start()), d))
            if candidates:
                candidates.sort(key=lambda x: x[0])
                distance, d = candidates[0]
                if distance <= strict_distance:
                    return d + ". kerület", source_url
        return None

    # OÉNY / e-építés: a nyilvános részletes oldal fejlécében tipikusan együtt
    # szerepel a "Település: Budapest XII. kerület" és a "Helyrajzi szám: ...".
    queries = [
        f'"{h}" site:e-epites.hu/altlek8x76y/ingatlanReszletek',
        f'"{h}" site:e-epites.hu "Nyilvántartások együttes lekérdezése"',
        f'"{h}" site:e-epites.hu "Település: Budapest"',
        f'"{h}" site:kormanyhivatalok.hu/system/files/dokumentum/budapest',
        f'"{h}" site:kormanyhivatalok.hu Budapest',
        f'"{h}" "Budapest" "BELTERULET"',
        f'"{h}" "Budapest" "kerület"',
        f'"{h}" site:budapest.hu',
    ]

    allowed_hosts = (
        "e-epites.hu",
        "www.e-epites.hu",
        "kormanyhivatalok.hu",
        "www.kormanyhivatalok.hu",
        "budapest.hu",
        "www.budapest.hu",
    )

    seen = set()
    for q in queries:
        for u in _search_web(q)[:30]:
            if u in seen:
                continue
            seen.add(u)
            host = (urllib.parse.urlparse(u).hostname or "").casefold()
            if host not in allowed_hosts and not host.endswith(".e-epites.hu") and not host.endswith(".kormanyhivatalok.hu"):
                continue
            try:
                raw, final, status, charset, content_type = http_get(u, timeout=15)
                if status >= 400:
                    continue
                final_host = (urllib.parse.urlparse(final).hostname or "").casefold()
                if not (
                    final_host.endswith("e-epites.hu")
                    or final_host.endswith("kormanyhivatalok.hu")
                    or final_host.endswith("budapest.hu")
                ):
                    continue

                is_pdf = raw.startswith(b"%PDF") or "application/pdf" in (content_type or "").casefold()
                if is_pdf:
                    try:
                        doc = fitz.open(stream=raw, filetype="pdf")
                        plain = "\n".join((page.get_text("text") or "") for page in doc)
                    except Exception:
                        continue
                else:
                    html = raw.decode(charset or "utf-8", errors="replace")[:1500000]
                    parser = HTMLCollector()
                    parser.feed(html)
                    plain = parser.text()

                found = inspect_text(plain, final)
                if found:
                    return found
            except Exception:
                pass

    return "", ""


def discover_njt_source(town, hrsz):
    """Településhez automatikusan keres HÉSZ/KÉSZ NJT-jelöltet.
    A kereső csak felderít; a találatot fetch + tartalmi validáció követi.
    """
    place = clean_text(town)
    district = ""
    district_evidence = ""
    if key_text(place) == "budapest":
        district, district_evidence = discover_budapest_district(hrsz)
        if district:
            place = f"Budapest {district}"

    queries = [
        f'site:or.njt.hu "{place}" "kerületi építési szabályzat"',
        f'site:or.njt.hu "{place}" "helyi építési szabályzat"',
        f'site:njt.jog.gov.hu "{place}" "kerületi építési szabályzat"',
        f'site:njt.jog.gov.hu "{place}" "építési szabályzat"',
        f'site:or.njt.hu "{place}" KÉSZ',
        f'site:or.njt.hu "{place}" HÉSZ',
        f'site:or.njt.hu "{place}" "szabályozási terv"',
    ]
    candidates = []
    for q in queries:
        for u in _search_web(q):
            if is_official_njt_url(u) and u not in candidates:
                candidates.append(u)

    # Legfeljebb néhány hivatalos találatot kérünk le, hogy ne legyen lassú.
    town_terms = [key_text(town)]
    if district:
        town_terms += [key_text(district), key_text(place)]
    for u in candidates[:8]:
        page = fetch_njt_page(u)
        if not page.get("ok"):
            continue
        body = key_text(page.get("text", ""))
        is_building_rule = any(x in body for x in ("epitesi szabalyzat", "helyi epitesi szabalyzat", "keruleti epitesi szabalyzat"))
        place_ok = any(term and term in body for term in town_terms)
        if is_building_rule and place_ok:
            title = clean_text(page.get("text", "").split("\n")[0])[:180] or "Automatikusan felderített NJT-forrás"
            return {
                "municipality": town,
                "title": title,
                "regulation": "",
                "url": page.get("url") or u,
                "source": "automatikus NJT-forrásfelderítés",
                "district": district,
                "district_evidence": district_evidence,
            }, page
    return None, {}


# ---------------------------------------------------------------------
# Nyilvános HRSZ-kereső / telekgeometria (v15)
# ---------------------------------------------------------------------

HRSZ_API_BASE = "https://www.oeny.hu/hk-api/parcels"

# A nyilvános HRSZ-kereső település/kerület kódja.
# Budapest kerületneveihez megőrzött aliasok; más település kódját
# az országos hivatalos településkeresőből, pontos névegyezéssel kérjük le.
KSH_CODES = {
    "budapest xii. kerulet": "24697",
    "budapest 12. kerulet": "24697",
}


@st.cache_data(show_spinner=False, ttl=3600)
def resolve_settlement_code(place):
    """Resolve the exact municipality through the same public service as the
    official HRSZ search UI. A prefix result is never accepted as an exact name.
    """
    name=clean_text(place)
    known=KSH_CODES.get(key_text(name))
    if known:return known
    base=HRSZ_API_BASE.rsplit('/parcels',1)[0]
    url=base+'/settlements/search?'+urllib.parse.urlencode({'searchString':name})
    rows=_json_get(url)
    if not isinstance(rows,list):raise RuntimeError('A településkereső válaszának szerkezete nem támogatott.')
    exact={str(row.get('kshCode','')) for row in rows if isinstance(row,dict)
           and key_text(row.get('name',''))==key_text(name)
           and re.fullmatch(r'\d{5}',str(row.get('kshCode','')))}
    if len(exact)!=1:
        raise RuntimeError('Nincs egyetlen, pontosan egyező település a hivatalos HRSZ-keresőben.')
    return exact.pop()

def _json_get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 TelekEloirasAI/15.16",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.oeny.hu/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _find_parcel_record(data, hrsz):
    target = normalize_hrsz(hrsz).casefold()
    if isinstance(data, dict):
        # Először azt a rekordot keressük, amelyben a HRSZ ténylegesen egyezik.
        vals = {str(v).strip().casefold() for v in data.values() if isinstance(v, (str,int,float))}
        if target in vals or any(k.casefold() in {"lotnumber","hrsz","landregister"} and normalize_hrsz(v).casefold()==target for k,v in data.items() if isinstance(v,(str,int,float))):
            return data
        for v in data.values():
            hit = _find_parcel_record(v, hrsz)
            if hit:
                return hit
    elif isinstance(data, list):
        for v in data:
            hit = _find_parcel_record(v, hrsz)
            if hit:
                return hit
    return None

def _extract_id(obj):
    if isinstance(obj, dict):
        for k in ("id", "parcelId", "parcel_id", "objectId", "objectID"):
            if k in obj and obj[k] not in (None, ""):
                return str(obj[k])
        for v in obj.values():
            x = _extract_id(v)
            if x:
                return x
    elif isinstance(obj, list):
        for v in obj:
            x = _extract_id(v)
            if x:
                return x
    return ""

@st.cache_data(show_spinner=False, ttl=3600)
def public_parcel_geometry(ksh_code, hrsz):
    h = normalize_hrsz(hrsz)
    q = urllib.parse.urlencode({"kshCode": ksh_code, "lotNumber": h})
    search_url = f"{HRSZ_API_BASE}/search?{q}"
    data = _json_get(search_url)
    record = _find_parcel_record(data, h)
    if record is None:
        raise RuntimeError("A kereső nem adott pontosan egyező HRSZ-rekordot.")
    parcel_id = _extract_id(record)
    if not parcel_id:
        raise RuntimeError("A HRSZ-kereső válaszából nem sikerült ingatlan-azonosítót kinyerni.")
    bbox_url = f"{HRSZ_API_BASE}/bounding-box?" + urllib.parse.urlencode({"id": parcel_id})
    geom = _json_get(bbox_url)
    if (geom.get('lotNumber') is not None
            and normalize_hrsz(geom['lotNumber'])!=h):
        raise RuntimeError('A visszakapott geometria másik helyrajzi számhoz tartozik.')
    settlement=geom.get('settlement') or {}
    if settlement.get('kshCode') is not None and str(settlement['kshCode'])!=str(ksh_code):
        raise RuntimeError('A visszakapott geometria másik településhez tartozik.')
    return {"id": parcel_id, "search_url": search_url, "geometry_url": bbox_url, "search": data, "geometry": geom}

def geometry_summary(geom):
    if not isinstance(geom, dict):
        return {}, ""
    bbox = geom.get("boundingBox") or geom.get("bbox") or {}
    outline = geom.get("outline") or {}
    gtype = outline.get("type", "") if isinstance(outline, dict) else ""
    return bbox, gtype


# ---------------------------------------------------------------------
# XII. kerületi MINERVA / MapGuide kapcsolat (v15.4)
# ---------------------------------------------------------------------
MINERVA_XII_ENTRY = "https://minerva.bp12ker.hu/minerva/bp12ker/internet.php"
MINERVA_XII_BASE = "https://minerva.bp12ker.hu"
MINERVA_MAPAGENT = "https://minerva.bp12ker.hu/minerva/mapagent/mapagent.fcgi"
MINERVA_MAPNAME = "internet"
MINERVA_MAPDEFINITION = "Library://XII/map/internet.MapDefinition"

def _decode_http_response(resp, raw):
    charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")

def _extract_minerva_bootstrap(html):
    m = re.search(r"src=[\"']([^\"']*ajaxviewer\.php\?[^\"']+)[\"']", html, flags=re.I)
    if not m:
        raise RuntimeError("Az internet.php válaszában nem található ajaxviewer.php hivatkozás.")
    viewer = m.group(1).replace("&amp;", "&")
    viewer_url = urllib.parse.urljoin(MINERVA_XII_ENTRY, viewer)
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(viewer_url).query)
    session = (qs.get("SESSION") or qs.get("session") or [""])[0]
    if not session:
        raise RuntimeError("A MINERVA válaszából nem sikerült SESSION azonosítót kinyerni.")
    return session, viewer_url

def _minerva_opener(jar, verify_tls=True):
    # A MINERVA publikus, csak olvasott térképi forrás. Egyes hosztokon a
    # tanúsítvány-lánc nem épül fel a Render CA-tárából. Először mindig
    # szabályos TLS-ellenőrzéssel próbálunk; csak CERTIFICATE_VERIFY_FAILED
    # esetén engedünk célzott, MINERVA-only visszaesést.
    handlers = [urllib.request.HTTPCookieProcessor(jar)]
    if not verify_tls:
        handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    return urllib.request.build_opener(*handlers)

def _minerva_bootstrap_attempt(verify_tls=True):
    jar = http.cookiejar.CookieJar()
    opener = _minerva_opener(jar, verify_tls=verify_tls)
    headers = {"User-Agent": "Mozilla/5.0 TelekEloirasAI/15.16", "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.5"}
    req = urllib.request.Request(MINERVA_XII_ENTRY, headers=headers)
    with opener.open(req, timeout=30) as resp:
        raw = resp.read(); entry_status = getattr(resp, "status", 200); html = _decode_http_response(resp, raw)
    session, viewer_url = _extract_minerva_bootstrap(html)
    req2 = urllib.request.Request(viewer_url, headers={**headers, "Referer": MINERVA_XII_ENTRY})
    with opener.open(req2, timeout=30) as resp2:
        viewer_raw = resp2.read(); viewer_status = getattr(resp2, "status", 200); viewer_html = _decode_http_response(resp2, viewer_raw)
    mapdef_seen = ("XII/map/internet.MapDefinition" in viewer_html or "XII%2Fmap%2Finternet.MapDefinition" in viewer_html or "MAPDEFINITION" in viewer_html.upper())
    return {
        "ok": entry_status == 200 and viewer_status == 200,
        "entry_status": entry_status,
        "viewer_status": viewer_status,
        "session_created": bool(session),
        "mapdefinition_seen": mapdef_seen,
        "tls_verified": verify_tls,
        "session": session,
    }

@st.cache_data(show_spinner=False, ttl=900)
def minerva_xii_bootstrap():
    try:
        return _minerva_bootstrap_attempt(verify_tls=True)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        msg = str(exc)
        is_cert_error = isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in msg
        if not is_cert_error:
            raise
        # Csak a nyilvános MINERVA hostra, olvasási célból. Nem továbbítunk
        # felhasználói tokent, jelszót vagy böngésző-cookie-t.
        return _minerva_bootstrap_attempt(verify_tls=False)

def _xml_text(node, name):
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1] == name:
            return (child.text or "").strip()
    return ""


def _mapagent_xml(session, operation, **params):
    data = urllib.parse.urlencode({
        "OPERATION": operation, "VERSION": "1.0.0", "SESSION": session,
        **params,
    }).encode("utf-8")
    def request(verify):
        opener = _minerva_opener(http.cookiejar.CookieJar(), verify)
        req = urllib.request.Request(MINERVA_MAPAGENT, data=data, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "TelekEloirasAI/15.16",
        })
        try:
            with opener.open(req, timeout=20) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            body = exc.read(65536)
            try:
                error_root = ET.fromstring(body)
                message = _xml_text(error_root, "Message") or _xml_text(error_root, "Details")
            except ET.ParseError:
                parser = HTMLCollector()
                parser.feed(body.decode("utf-8", errors="replace"))
                message = parser.text()
            # A szerver egyes hibaüzenetei tartalmazhatják a sessiont.
            message = clean_text(message).replace(session, "[session]")[:1500]
            raise RuntimeError(f"{operation}: HTTP {exc.code}: {message or exc.reason}") from None
        if len(raw) > 8 * 1024 * 1024:
            raise RuntimeError("A térképi válasz meghaladja a feldolgozási korlátot.")
        root = ET.fromstring(raw)
        if root.tag.rsplit("}", 1)[-1] == "Exception":
            raise RuntimeError(_xml_text(root, "Message") or "MapGuide lekérdezési hiba")
        return root
    try:
        return request(True)
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            return request(False)
        raise


def parcel_polygon_wkt(geometry):
    """Valódi telekgyűrűk; a bounding box nem helyettesíti a telket."""
    outline = geometry.get("outline", {})
    kind = outline.get("type")
    polygons = outline.get("coordinates", [])
    if kind == "Polygon":
        polygons = [polygons]
    elif kind != "MultiPolygon":
        raise ValueError("Polygon/MultiPolygon telekgeometria szükséges.")
    if not polygons:
        raise ValueError("A telek koordinátalistája üres.")
    rendered = []
    for polygon in polygons:
        rings = []
        if not polygon:
            raise ValueError("Üres telekpolygon.")
        for ring in polygon:
            if len(ring) < 4 or ring[0][:2] != ring[-1][:2]:
                raise ValueError("Hibás vagy nem zárt telekgyűrű.")
            points = []
            for point in ring:
                x, y = float(point[0]), float(point[1])
                if not math.isfinite(x) or not math.isfinite(y):
                    raise ValueError("Nem véges telekkoordináta.")
                # Az OÉNY bemeneti geometriájának EOV tartománya.
                if not (400000 <= x <= 950000 and 0 <= y <= 400000):
                    raise ValueError("A telek koordinátái nem az elvárt EOV tartományban vannak.")
                points.append(f"{x:.8f} {y:.8f}")
            rings.append("(" + ",".join(points) + ")")
        rendered.append("(" + ",".join(rings) + ")")
    return "MULTIPOLYGON (" + ",".join(rendered) + ")"


def _feature_properties(root):
    records = []
    for feature in root.iter():
        if feature.tag.rsplit("}", 1)[-1] != "Feature":
            continue
        props = {}
        for prop in feature:
            if prop.tag.rsplit("}", 1)[-1] == "Property":
                props[_xml_text(prop, "Name")] = _xml_text(prop, "Value")
        if props:
            records.append(props)
    return records


def select_minerva_zone_layers(layers):
    """Csak övezeti rétegek: a rendeletszám-réteg nem övezeti adat."""
    selected = []
    for layer in layers:
        name = key_text(_xml_text(layer, "Name"))
        resource = key_text(_xml_text(layer, "ResourceId"))
        if "epitesiovezet" not in name.replace(" ", "") or any(term in name + resource for term in (
            "hatalyon_kivul", "hatalyon kivul", "kitakart", "rendeletszam",
        )):
            continue
        dates = re.findall(r"(?<!\d)(20\d{6})(?!\d)", name + " " + resource)
        date = max(dates, default="00000000")
        # A határoló geometria előbb kerül feldolgozásra, mint a felirat.
        boundary = "hatar" in name
        selected.append((date, boundary, layer))
    selected.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in selected]


def available_feature_class(session, resource, declared, query):
    schema = declared.split(":", 1)[0] if ":" in declared else ""
    root = query(session, "GETCLASSES", RESOURCEID=resource, SCHEMANAME=schema)
    classes = [clean_text(n.text) for n in root.iter()
               if n.tag.rsplit("}", 1)[-1] in {"Item", "String"} and clean_text(n.text)]
    if declared in classes:
        return declared
    matches = [c for c in classes if c.rsplit(":", 1)[-1] == declared.rsplit(":", 1)[-1]]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"A réteg adatosztálya nem igazolható: {declared}. Elérhető osztályok: {', '.join(classes[:20]) or 'nincs'}")


def minerva_circular_arc(text, tolerance=0.001):
    """Egy AWKT körív szakaszonként, legfeljebb 1 mm húrhibával."""
    from shapely.geometry import LineString
    pattern=r"CURVESTRING\s+(XY|XYZ)\s*\(([^()]+)\(CIRCULARARCSEGMENT\s*\(([^()]+)\)\)\)"
    match=re.fullmatch(pattern,text.strip(),flags=re.I)
    if not match:
        raise ValueError("Nem támogatott összetett AWKT görbe.")
    dim=3 if match[1].upper()=="XYZ" else 2
    def point(value):
        values=[float(n) for n in value.strip().split()]
        if len(values)!=dim or not all(math.isfinite(n) for n in values):
            raise ValueError("Hibás körívkoordináta.")
        return values[:2]
    start=point(match[2]);parts=match[3].split(",")
    if len(parts)!=2:raise ValueError("A körívhez három pont szükséges.")
    mid,end=map(point,parts)
    ax,ay=mid[0]-start[0],mid[1]-start[1]
    bx,by=end[0]-start[0],end[1]-start[1]
    determinant=2*(ax*by-ay*bx)
    if abs(determinant)<1e-12:raise ValueError("Nem meghatározható körív.")
    ux=((ax*ax+ay*ay)*by-(bx*bx+by*by)*ay)/determinant
    uy=(ax*(bx*bx+by*by)-bx*(ax*ax+ay*ay))/determinant
    cx,cy=start[0]+ux,start[1]+uy
    radius=math.hypot(ux,uy)
    first=math.atan2(start[1]-cy,start[0]-cx)
    middle=(math.atan2(mid[1]-cy,mid[0]-cx)-first)%(2*math.pi)
    final=(math.atan2(end[1]-cy,end[0]-cx)-first)%(2*math.pi)
    sweep=final if middle<=final else final-2*math.pi
    step=2*math.acos(max(-1.0,min(1.0,1-tolerance/radius)))
    if step<=0:raise ValueError("Nem felbontható körív.")
    count=max(1,math.ceil(abs(sweep)/step))
    if count>100000:raise ValueError("Túl sok körívszakasz.")
    points=[(cx+radius*math.cos(first+sweep*i/count),cy+radius*math.sin(first+sweep*i/count)) for i in range(count+1)]
    points[0]=start;points[-1]=end
    return LineString(points)


def minerva_wkt_geometry(value):
    from shapely import wkt
    text = clean_text(value)
    if text.upper().startswith("CURVESTRING"):
        return minerva_circular_arc(text)
    # MapGuide AWKT dimension tokens are explicit; curved geometries are not
    # approximated, and unknown binary encodings are not guessed.
    text = re.sub(r"\bXYZM\b", "ZM", text)
    text = re.sub(r"\bXYZ\b", "Z", text)
    text = re.sub(r"\bXYM\b", "M", text)
    text = re.sub(r"^(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|GEOMETRYCOLLECTION)\s+XY\s*", r"\1 ", text, flags=re.I)
    if not re.match(r"^(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|GEOMETRYCOLLECTION)\s*[\s(]", text, flags=re.I):
        raise ValueError("A MINERVA geometriája nem támogatott WKT/AWKT szöveg.")
    from shapely import force_2d
    geom = force_2d(wkt.loads(text))
    if geom.is_empty or not geom.is_valid:
        raise ValueError("Üres vagy érvénytelen MINERVA geometria.")
    return geom


def enclosing_zone(parcel_wkt, boundary_records, label_records, window_wkt, geometry_field="Geom", diagnostics=None):
    """Teljes telek + zárt határ + területen belüli egyező felirat szükséges."""
    from shapely.ops import polygonize, unary_union
    parcel = minerva_wkt_geometry(parcel_wkt)
    window = minerva_wkt_geometry(window_wkt)
    lines = []
    for row in boundary_records:
        geom = minerva_wkt_geometry(row.get(geometry_field, ""))
        if geom.geom_type in {"Polygon", "MultiPolygon"}:
            lines.append(geom.boundary)
        elif geom.geom_type in {"LineString", "MultiLineString"}:
            lines.append(geom)
        else:
            raise ValueError("Az övezethatár nem vonal vagy polygon.")
    if not lines:
        return "", "Nincs övezethatár a vizsgált környezetben."
    polygons = list(polygonize(unary_union(lines)))
    if diagnostics is not None:
        diagnostics["Zárt területek"] = len(polygons)
        diagnostics["Telekkel metsző területek"] = sum(p.intersects(parcel) for p in polygons)
        diagnostics["Teljes telket tartalmazó területek"] = sum(p.covers(parcel) for p in polygons)
    regions = [p for p in polygons
               if p.covers(parcel) and window.contains(p) and not p.boundary.intersects(window.boundary)]
    if len(regions) != 1:
        return "", "A teljes telekhez nem tartozik egyetlen igazolt, zárt övezetterület."
    codes = set()
    for row in label_records:
        text = next((v for k,v in row.items() if key_text(k)=="karakterlanc"), "")
        if not ZONE_PATTERN.fullmatch(text):
            continue
        geom = minerva_wkt_geometry(row.get(geometry_field, ""))
        if geom.geom_type not in {"Point", "MultiPoint"}:
            raise ValueError("Az övezeti felirat helye nem pontgeometria.")
        if regions[0].contains(geom):
            codes.add(text)
    if len(codes) != 1:
        return "", "A zárt övezetterületnek nincs egyetlen egyértelmű övezeti felirata."
    return next(iter(codes)), "A teljes telek zárt övezetterületben van, annak felirata egyértelmű."


def minerva_enclosing_zone(session, resource, classes, geometry_field, parcel_wkt, query):
    from shapely.geometry import box
    from shapely import wkt
    names = [clean_text(n.text) for n in classes.iter()
             if n.tag.rsplit("}",1)[-1] in {"Item", "String"} and clean_text(n.text)]
    boundaries = [c for c in names if "hatar" in key_text(c)
        and ("epitesiovezet" in key_text(c).replace(" ", "") or "beepitesrenemszant" in key_text(c).replace(" ", ""))]
    # A hivatalos térkép szabályozási vonalai további geometriai lezárást
    # adhatnak. Az eredmény továbbra is ellenőrzendő térképi jelölt.
    regulation_lines = [c for c in names if re.match(r"k-0[12]-", key_text(c.rsplit(":",1)[-1]))
                        and "szabaly" in key_text(c) and c.upper().endswith("_L")]
    boundaries += regulation_lines
    labels = minerva_label_classes(classes)
    if not boundaries or len(labels) != 1:
        return "", {"Adatforrás":resource, "Eredmény":"Az övezethatár- és feliratosztály nem egyértelmű."}
    minx,miny,maxx,maxy = wkt.loads(parcel_wkt).bounds
    detail={"Adatforrás":resource}
    snapshots=[]
    for radius in (500, 1500):
        window = box(minx-radius,miny-radius,maxx+radius,maxy+radius).wkt
        records=[]
        class_counts={}
        try:
            for cls in (*boundaries,labels[0]):
                root=query(session,"SELECTFEATURES",RESOURCEID=resource,CLASSNAME=cls,
                           FILTER=f"{geometry_field} INTERSECTS GeomFromText('{window}')")
                rows=_feature_properties(root)
                class_counts[cls]=len(rows)
                records.append(rows)
            records = [[row for part in records[:-1] for row in part], records[-1]]
        except Exception as exc:
            detail["Eredmény"] = f"A {radius} m-es környezet lekérése nem teljes: {exc}"
            break
        snapshots.append({"resource":resource,"parcel_wkt":parcel_wkt,"window_wkt":window,
            "geometry_field":geometry_field,"boundary_classes":boundaries,"label_class":labels[0],
            "available_classes":names,"class_counts":class_counts,
            "curve_chord_tolerance_m":0.001,
            "boundary_records":records[0],"label_records":records[1]})
        detail.update({"Sugár (m)":radius,"Határszakaszok":len(records[0]),"Feliratok":len(records[1]),
                       "Szabályozási vonalak":sum(class_counts.get(c,0) for c in regulation_lines)})
        try:
            code,reason=enclosing_zone(parcel_wkt,records[0],records[1],window,geometry_field,detail)
            detail["Eredmény"]=reason
            if code:
                detail["snapshot"]=snapshots
                return code,detail
        except Exception as exc:
            detail["Eredmény"]=str(exc)
            detail["Geometria formátuma"] = str([
                {"eleje":r.get(geometry_field,"")[:60],"hossz":len(r.get(geometry_field,""))}
                for r in records[0][:1]+records[1][:1]])
            break
    detail["snapshot"]=snapshots
    return "",detail


def minerva_label_classes(root):
    """Csak a forrás által ténylegesen felsorolt építésiövezet-feliratosztályok."""
    classes = {clean_text(n.text) for n in root.iter()
               if n.tag.rsplit("}", 1)[-1] in {"Item", "String"} and clean_text(n.text)}
    return sorted(c for c in classes if "epitesiovezet" in key_text(c).replace(" ", "")
                  and "jel" in key_text(c) and c.upper().endswith("_T"))


def minerva_zone_candidates(session, geometry, query=_mapagent_xml, diagnostics=None):
    original_query = query
    cached = {}
    def query(session, operation, **params):
        cache_key = (operation, tuple(sorted(params.items())))
        if operation not in {"GETCLASSES", "GETSPATIALCONTEXTS"}:
            return original_query(session, operation, **params)
        if cache_key not in cached:
            cached[cache_key] = original_query(session, operation, **params)
        return cached[cache_key]
    discovered_sources = {}
    intersecting_sources = set()
    queried_classes = set()
    wkt = parcel_polygon_wkt(geometry)
    mapdef = query(session, "GETRESOURCECONTENT", RESOURCEID=MINERVA_MAPDEFINITION)
    candidates, errors = [], []
    layers = [n for n in mapdef.iter() if n.tag.rsplit("}", 1)[-1] == "MapLayer"]
    if diagnostics is not None:
        diagnostics["layers"] = [
            {"Réteg": _xml_text(n, "Name"), "Definíció": _xml_text(n, "ResourceId")}
            for n in layers
        ]
    # Csak az övezeti rétegekhez fordulunk; a térképi megjelenítés önmagában
    # nem bizonyítja, hogy az adott réteg a hatályos NJT melléklete.
    selected = select_minerva_zone_layers(layers)
    if not selected:
        raise RuntimeError("A térképdefinícióban nem található azonosítható övezeti réteg.")
    if diagnostics is not None:
        diagnostics["definitions"] = []
    started = time.monotonic()
    for layer in selected:
        if time.monotonic() - started > 90:
            errors.append("Az övezeti rétegek vizsgálata elérte a 90 másodperces időkeretet; a lista feldolgozása nem teljes.")
            break
        name = _xml_text(layer, "Name")
        stage = "GETRESOURCECONTENT"
        try:
            definition = query(session, "GETRESOURCECONTENT", RESOURCEID=_xml_text(layer, "ResourceId"))
            kind = next((n.tag.rsplit("}", 1)[-1] for n in definition.iter()
                         if n.tag.rsplit("}", 1)[-1] in {
                             "VectorLayerDefinition", "DrawingLayerDefinition", "GridLayerDefinition"
                         }), "ismeretlen")
            vector = next((n for n in definition.iter() if n.tag.rsplit("}", 1)[-1] == "VectorLayerDefinition"), None)
            diagnostic_row = None
            if diagnostics is not None:
                diagnostic_row = {
                    "Réteg": name, "Típus": kind,
                    "Adatforrás": _xml_text(definition, "ResourceId"),
                    "Adatosztály": _xml_text(definition, "FeatureName"),
                    "Geometria": _xml_text(definition, "Geometry"),
                    "Találatok": "nem lekérdezett", "Mezők": "",
                }
                diagnostics["definitions"].append(diagnostic_row)
            if vector is None:
                errors.append(f"{name}: {kind}; nem SELECTFEATURES-szel lekérdezhető vektorréteg.")
                continue
            resource = _xml_text(vector, "ResourceId")
            feature_class = _xml_text(vector, "FeatureName")
            geom_name = _xml_text(vector, "Geometry")
            if not resource or not feature_class or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", geom_name):
                raise ValueError("A réteg forrása vagy geometriai mezője nem értelmezhető.")
            stage = "GETCLASSES"
            feature_class = available_feature_class(session, resource, feature_class, query)
            stage = "GETSPATIALCONTEXTS"
            contexts = query(session, "GETSPATIALCONTEXTS", RESOURCEID=resource, ACTIVEONLY="1")
            crs = " ".join(contexts.itertext()).upper()
            if not any(token in crs for token in ("23700", "HD72", "HUNGARIAN_UNIFIED", "EOV")):
                raise ValueError("A réteg EOV koordinátarendszere nem igazolható.")
            discovered_sources[resource] = (feature_class.split(":", 1)[0], geom_name)
            queried_classes.add((resource, feature_class))
            stage = "SELECTFEATURES"
            result = query(session, "SELECTFEATURES", RESOURCEID=resource,
                           CLASSNAME=feature_class,
                           FILTER=f"{geom_name} INTERSECTS GeomFromText('{wkt}')")
            records = _feature_properties(result)
            if records:
                intersecting_sources.add(resource)
            text_expressions = [_xml_text(n, "Text") for n in definition.iter()
                                if n.tag.rsplit("}", 1)[-1] == "TextSymbol"]
            label_fields = {expr.strip()[1:-1] for expr in text_expressions
                            if re.fullmatch(r"\[[^\[\]]+\]", expr.strip())}
            if diagnostic_row is not None:
                diagnostic_row["Találatok"] = len(records)
                diagnostic_row["Mezők"] = ", ".join(sorted({f for props in records for f in props}))
                diagnostic_row["Feliratmező"] = ", ".join(sorted(label_fields))
                diagnostic_row["Minta"] = str([{k: v[:120] for k, v in props.items()
                                                 if k in label_fields or any(t in key_text(k) for t in ("text", "karakterlanc", "ovezet", "zone", "kod", "jel"))}
                                                for props in records[:3]])
            for props in records:
                for field, value in props.items():
                    if (field in label_fields or key_text(field) == "karakterlanc" or any(t in key_text(field) for t in ("ovezet", "zone", "kod", "jel"))) and ZONE_PATTERN.fullmatch(value):
                        candidates.append({"Övezeti kód": value, "Réteg": name,
                                           "Forrás": resource, "Mező": field})
        except Exception as exc:
            errors.append(f"{name} / {stage}: {exc}")
    # A térképi réteglista az ÚJ feliratokat is tartalmazhatja, a teljes
    # feliratosztály pedig csak az adatforrás osztálylistájában szerepelhet.
    # Nem képezünk osztálynevet: csak GETCLASSES által visszaadott nevet használunk.
    for resource, (schema, geom_name) in discovered_sources.items():
        if "20250806" not in resource:
            continue
        try:
            classes = query(session, "GETCLASSES", RESOURCEID=resource, SCHEMANAME=schema)
            labels = minerva_label_classes(classes)
            if diagnostics is not None:
                diagnostics.setdefault("source_classes", []).append({
                    "Adatforrás": resource, "Feliratosztályok": ", ".join(labels) or "nincs",
                    "Összes adatosztály": ", ".join(clean_text(n.text) for n in classes.iter()
                        if n.tag.rsplit("}",1)[-1] in {"Item", "String"} and clean_text(n.text))})
            code, topology_detail = (minerva_enclosing_zone(session, resource, classes, geom_name, wkt, query)
                if resource in intersecting_sources else ("", {"Adatforrás":resource,"Eredmény":"A forrás övezeti rétegei nem metszik a telket."}))
            snapshots = topology_detail.pop("snapshot", [])
            if diagnostics is not None and snapshots:
                diagnostics.setdefault("geometry_snapshots", []).extend(snapshots)
            if diagnostics is not None:
                diagnostics.setdefault("topology", []).append(topology_detail)
            if code:
                candidates.append({"Övezeti kód": code, "Réteg": "Zárt övezethatár és területen belüli felirat",
                                   "Forrás": resource, "Mező": "KARAKTERLÁNC"})
            for feature_class in labels:
                if (resource, feature_class) in queried_classes:
                    continue
                if time.monotonic() - started > 90:
                    errors.append("Az adatforrás további feliratosztályainak vizsgálata nem fért az időkeretbe.")
                    break
                records = _feature_properties(query(session, "SELECTFEATURES", RESOURCEID=resource,
                    CLASSNAME=feature_class, FILTER=f"{geom_name} INTERSECTS GeomFromText('{wkt}')"))
                if diagnostics is not None:
                    diagnostics["definitions"].append({"Réteg": "Adatforrásban felfedezett feliratosztály",
                        "Típus": "GETCLASSES", "Adatforrás": resource,
                        "Adatosztály": feature_class, "Geometria": geom_name,
                        "Találatok": len(records), "Minta": str([
                            {k:v for k,v in row.items() if key_text(k)=="karakterlanc"}
                            for row in records[:5]])})
                for props in records:
                    for field, value in props.items():
                        if key_text(field) == "karakterlanc" and ZONE_PATTERN.fullmatch(value):
                            candidates.append({"Övezeti kód": value,
                                "Réteg": feature_class, "Forrás": resource, "Mező": field})
        except Exception as exc:
            errors.append(f"{resource} / további feliratosztályok: {exc}")
    unique = {tuple(row.items()): row for row in candidates}
    return list(unique.values()), errors


def minerva_xii_status_for_parcel(parcel_api):
    result = {"supported": True, "parcel_geometry": False, "bootstrap": False,
              "runtime_map": False, "zone": "", "candidates": [], "detail": ""}
    if not parcel_api:
        result["detail"] = "Az OÉNY telekgeometria nem áll rendelkezésre."
        return result
    try:
        geometry = parcel_api.get("geometry", {})
        parcel_polygon_wkt(geometry)
        result["parcel_geometry"] = True
        boot = minerva_xii_bootstrap()
        result["bootstrap"] = bool(boot.get("session_created"))
        result["runtime_map"] = bool(boot.get("mapdefinition_seen"))
        diagnostics = {}
        candidates, errors = minerva_zone_candidates(boot["session"], geometry, diagnostics=diagnostics)
        result["layers"] = diagnostics.get("layers", [])
        result["definitions"] = diagnostics.get("definitions", [])
        result["source_classes"] = diagnostics.get("source_classes", [])
        result["topology"] = diagnostics.get("topology", [])
        result["geometry_snapshots"] = diagnostics.get("geometry_snapshots", [])
        result["candidates"] = candidates
        result["detail"] = f"Telekpolygon térbeli lekérdezése: {len(candidates)} övezeti találat. " + " | ".join(errors)
    except Exception as exc:
        result["detail"] = f"MINERVA térbeli lekérdezés: {type(exc).__name__}: {exc}"
    return result


# ---------------------------------------------------------------------
# PDF kezelés
# ---------------------------------------------------------------------

@st.cache_data(show_spinner=False, ttl=3600, max_entries=8)
def download_pdf_location(url):
    # Cache the location, not another serialized copy of a 56 MB plan in RAM.
    import hashlib,tempfile
    from pathlib import Path
    raw,final_url,status,_,content_type=http_get(url,timeout=90,accept="application/pdf,*/*;q=0.8")
    if status!=200:raise RuntimeError(f"HTTP {status}")
    if not raw.startswith(b"%PDF"):
        raise RuntimeError(f"A letöltött tartalom nem PDF ({content_type or 'ismeretlen tartalomtípus'}).")
    folder=Path(tempfile.gettempdir())/'telekeloiras_pdf_cache'
    folder.mkdir(mode=0o700,exist_ok=True)
    path=folder/(hashlib.sha256(url.encode()).hexdigest()+'.pdf')
    with tempfile.NamedTemporaryFile(dir=folder,delete=False) as handle:
        temporary=Path(handle.name)
        try:handle.write(raw)
        except Exception:
            temporary.unlink(missing_ok=True);raise
    try:temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
    return str(path),final_url


def download_pdf(url):
    from pathlib import Path
    path,final_url=download_pdf_location(url)
    try:return Path(path).read_bytes(),final_url
    except FileNotFoundError:
        download_pdf_location.clear(url)
        path,final_url=download_pdf_location(url)
        return Path(path).read_bytes(),final_url


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
        textpage = page.get_textpage()
        for variant in hrsz_variants(hrsz):
            for rect in page.search_for(variant, textpage=textpage):
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
        del textpage
        fitz.TOOLS.store_shrink(100)
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
    r"L[1-9]|Lk|Lke|Lk|Vt|Vi|Gip|Gksz|K|KÖu|KÖk|Ev|Eg|Má|Mk|Kb|Üh|Üü|Lf|Lke"
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

def choose_plan_attachment(attachments, legal_text=""):
    """Szabályozási terv jelölt kiválasztása az NJT mellékletekből."""
    if not attachments:
        return None

    scored = []
    for row in attachments:
        label = key_text(row.get("Megnevezés", ""))
        url = (row.get("URL") or "").casefold()
        score = 0

        annex=re.match(r"^(\d+(?:\.\d+)*)\.\s*melleklet\b",label)
        if annex and re.search(r"\b"+re.escape(annex.group(1))+r"\.\s*melleklet\b[^.;]{0,100}szabalyozasi terv",key_text(legal_text)):
            score += 12

        if "szabalyozasi terv" in label:
            score += 12
        elif "szabalyozasi" in label:
            score += 7

        if "tervlap" in label:
            score += 5
        elif "terv" in label:
            score += 3

        if any(x in label for x in ("terkep", "szelveny")):
            score += 3

        if ".pdf" in url:
            score += 1

        # Biztosan nem szabályozási terv.
        if re.match(r"^1\.\s*melleklet\b", label):
            score += 4

        if any(x in label for x in ("akadalymentes", "nyilatkozat")):
            score -= 20

        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)

    # A v11 5 pontos küszöbe valós NJT tervmellékletet is kizárhatott.
    # Itt továbbra is kell tervi jelleg, de nem követeljük meg a teljes
    # "szabályozási terv" kifejezést.
    return scored[0][1] if scored and scored[0][0] >= 4 else None


def try_auto_plan(attachments, legal_text=""):
    candidate = choose_plan_attachment(attachments, legal_text)
    if not candidate:
        return None, "", ""

    try:
        raw, final_url = download_pdf(candidate["URL"])
        doc = open_pdf_bytes(raw)
        opening = key_text(doc[0].get_text("text")) if doc else ""
        if opening.strip() and 'szabalyozasi terv' not in opening:
            for index in range(1,min(6,len(doc))):
                opening+=' '+key_text(doc[index].get_text('text'))
                fitz.TOOLS.store_shrink(100)
                if 'szabalyozasi terv' in opening:break
        if not opening.strip() and doc and legal_text:
            recognized=[]
            for scale in (2,3):
                pix=doc[0].get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
                pix.set_dpi(72*scale,72*scale)
                with fitz.open(stream=pix.pdfocr_tobytes(language='eng',tessdata=plan_ocr_data()),filetype='pdf') as cover:
                    recognized.append(key_text(cover[0].get_text()))
            if all('szabalyozasi terv' in title for title in recognized):opening=recognized[0]
            fitz.TOOLS.store_shrink(100)
        if not any(term in opening for term in ("szabalyozasi terv", "szabalyozasi tervlap")):
            doc.close()
            return None, final_url, "A melléklet PDF megnyitható, de szabályozási tervként nem igazolható a szövegéből."
        return doc, final_url, ""
    except Exception as exc:
        return None, candidate.get("URL", ""), f"{type(exc).__name__}: {exc}"



# ---------------------------------------------------------------------
# 1.2 melléklet – övezeti paramétertábla
# ---------------------------------------------------------------------

class NJTZoneTableCollector(HTMLParser):
    """Keep source cell boundaries and spans; never infer columns from nearby text."""
    def __init__(self):
        super().__init__()
        self.tables=[]
        self.table=None
        self.row=None
        self.cell=None
        self.annex=""
        self.annex_parts=None
        self.annex_depth=0

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=="div":
            if "mellekletCimke" in attrs.get("class", "").split():
                self.annex_parts=[]
                self.annex_depth=1
            elif self.annex_parts is not None:
                self.annex_depth+=1
        if tag=="table":
            self.table={"annex":self.annex,"rows":[]}
        elif tag=="tr" and self.table is not None:
            self.row=[]
        elif tag in {"td","th"} and self.row is not None:
            self.cell={"parts":[],"colspan":attrs.get("colspan","1"),"rowspan":attrs.get("rowspan","1")}
        elif tag in {"br","p","sup","sub"} and self.cell is not None:
            self.cell["parts"].append(" ")

    def handle_data(self, data):
        if self.annex_parts is not None:
            self.annex_parts.append(data)
        if self.cell is not None:
            self.cell["parts"].append(data)

    def handle_endtag(self, tag):
        if tag=="div" and self.annex_parts is not None:
            self.annex_depth-=1
            if not self.annex_depth:
                self.annex=clean_text("".join(self.annex_parts))
                self.annex_parts=None
        if tag in {"td","th"} and self.cell is not None:
            self.cell["text"]=clean_text("".join(self.cell.pop("parts")))
            self.row.append(self.cell)
            self.cell=None
        elif tag=="tr" and self.row is not None:
            self.table["rows"].append(self.row)
            self.row=None
        elif tag=="table" and self.table is not None:
            self.tables.append(self.table)
            self.table=None
        elif tag in {"p","sup","sub"} and self.cell is not None:
            self.cell["parts"].append(" ")


def inline_zone_tables(html):
    parser=NJTZoneTableCollector()
    parser.feed(html or "")
    result=[]
    for table in parser.tables:
        header=None
        for row in table["rows"]:
            columns=[key_text(c["text"]) for c in row]
            if "epitesi ovezet jele" in columns or "ovezet jele" in columns:
                header=row
                zone_column=columns.index("epitesi ovezet jele") if "epitesi ovezet jele" in columns else columns.index("ovezet jele")
                continue
            if header is None or len(row)!=len(header):
                continue
            if any(c["rowspan"]!="1" for c in row+header):
                continue
            if [c["colspan"] for c in row]!=[c["colspan"] for c in header]:
                continue
            zone=row[zone_column]["text"]
            if not re.fullmatch(r"[^\W\d_][\w./-]*", zone):
                continue
            values=[(h["text"],c["text"]) for i,(h,c) in enumerate(zip(header,row))
                    if i!=zone_column and h["text"] and not re.fullmatch(r"\d+\.?",h["text"])]
            if not values or len({h for h,v in values})!=len(values):
                continue
            result.append({"zone":zone,"annex":table["annex"],"values":values})
    return result


def inline_zone_rows(tables, zone):
    matches=[r for r in tables if r["zone"]==clean_text(zone)] if zone else []
    if len(matches)!=1:
        return [],{},""
    match=matches[0]
    source="NJT – "+(match["annex"] or "beágyazott övezeti táblázat")
    params=dict(match["values"])
    rows=[{"Előírás":h,"Érték":v,"Forrás":source,
           "Bizonyosság":"pontos övezeti sor és forrásoszlop; a jelölések és feltételek a rendelet szerint"}
          for h,v in match["values"]]
    return rows,params,"; ".join(f"{h}: {v}" for h,v in match["values"])


def choose_zone_table_attachment(attachments):
    """Az NJT mellékletek közül az 1.2 övezeti táblázat legjobb jelöltje."""
    if not attachments:
        return None

    scored = []
    for row in attachments:
        label = key_text(row.get("Megnevezés", ""))
        url = row.get("URL", "")
        score = 0
        if "1.2" in label or "1_2" in label or "1-2" in label:
            score += 12
        if "epitesi ovezetei" in label or "ovezetei" in label:
            score += 8
        if "melleklet" in label:
            score += 2
        if ".pdf" in url.casefold():
            score += 1
        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] >= 8 else None


def pdf_native_text(doc):
    if doc is None:
        return ""
    return "\n".join(page.get_text("text") or "" for page in doc)


def load_zone_table(attachments):
    candidate = choose_zone_table_attachment(attachments)
    if not candidate:
        return None, "", "", "Az 1.2 mellékletet nem sikerült egyértelműen azonosítani."

    try:
        raw, final_url = download_pdf(candidate["URL"])
        doc = open_pdf_bytes(raw)
        native = pdf_native_text(doc)
        if len(clean_text(native)) < 100:
            return doc, final_url, "", "Az 1.2 PDF beszkennelt; a natív szövegrétege nem tartalmaz olvasható övezeti adatot."
        return doc, final_url, native, ""
    except Exception as exc:
        return None, candidate.get("URL", ""), "", f"{type(exc).__name__}: {exc}"


def zone_code_variants(zone):
    z = clean_text(zone)
    if not z:
        return []
    return list(dict.fromkeys([
        z,
        z.replace("/", " / "),
        z.replace("/", "/ "),
        z.replace("/", " /"),
    ]))


def zone_table_context(zone_table_text, zone, radius=700):
    if not zone_table_text or not zone:
        return ""
    low = zone_table_text.casefold()
    for variant in zone_code_variants(zone):
        pos = low.find(variant.casefold())
        if pos >= 0:
            return clean_text(zone_table_text[max(0, pos-radius):pos+radius])
    return ""


def parse_zone_table_parameters(zone_table_text, zone):
    """
    Óvatos kinyerés az 1.2 mellékletből.
    Csak címkézett mintát fogadunk el automatikus adatként.
    """
    context = zone_table_context(zone_table_text, zone)
    if not context:
        return {}, ""

    result = {}
    patterns = {
        "Legnagyobb beépítettség": [
            r"(?:legnagyobb|max(?:imális)?)\s+beépítettség[^0-9]{0,80}(\d{1,3}(?:[.,]\d+)?)\s*%?",
            r"beépítettség[^0-9]{0,80}(\d{1,3}(?:[.,]\d+)?)\s*%?",
        ],
        "Legkisebb zöldfelület": [
            r"(?:legkisebb|min(?:imális)?)\s+zöldfelület[^0-9]{0,80}(\d{1,3}(?:[.,]\d+)?)\s*%?",
            r"zöldfelület[^0-9]{0,80}(\d{1,3}(?:[.,]\d+)?)\s*%?",
        ],
        "Legkisebb telekterület": [
            r"(?:legkisebb|min(?:imális)?)\s+telek(?:terület|méret)[^0-9]{0,80}(\d[\d\s]*(?:[.,]\d+)?)\s*m?[²2]?",
            r"telek(?:terület|méret)[^0-9]{0,80}(\d[\d\s]*(?:[.,]\d+)?)\s*m?[²2]?",
        ],
        "Legnagyobb épületmagasság": [
            r"(?:legnagyobb|max(?:imális)?)\s+(?:épület|építmény)magasság[^0-9]{0,80}(\d{1,3}(?:[.,]\d+)?)\s*m?",
            r"(?:épület|építmény)magasság[^0-9]{0,80}(\d{1,3}(?:[.,]\d+)?)\s*m?",
        ],
    }

    for label, alternatives in patterns.items():
        for pattern in alternatives:
            match = re.search(pattern, context, flags=re.I)
            if match:
                result[label] = clean_text(match.group(1))
                break

    for mode in ("szabadon álló", "oldalhatáron álló", "zártsorú", "ikres", "kialakult"):
        if mode.casefold() in context.casefold():
            result["Beépítési mód"] = mode
            break

    return result, context


def zone_table_rows(zone_table_text, zone):
    params, context = parse_zone_table_parameters(zone_table_text, zone)
    units = {
        "Legnagyobb beépítettség": "%",
        "Legkisebb zöldfelület": "%",
        "Legkisebb telekterület": "m²",
        "Legnagyobb épületmagasság": "m",
        "Beépítési mód": "",
    }
    rows = []
    for label, value in params.items():
        rows.append({
            "Előírás": label,
            "Érték": f"{value} {units.get(label, '')}".strip(),
            "Forrás": "NJT – 1.2 melléklet",
            "Bizonyosság": "a keresett övezeti kód PDF-szövegkörnyezetéből",
        })
    return rows, params, context



# ---------------------------------------------------------------------
# ---------------------------------------------------------------------
# XII. kerületi tervlap: igazolt koordinátaillesztés, alaptérkép és HRSZ
# ---------------------------------------------------------------------

def plan_boundary_records(snapshot):
    rows=[]; offset=0
    for name in snapshot.get('boundary_classes',[]):
        count=snapshot.get('class_counts',{}).get(name,0)
        if 'epitesi ovezet' in key_text(name) and 'hatar' in key_text(name):
            rows.extend(snapshot['boundary_records'][offset:offset+count])
        offset+=count
    return rows


def plan_drawings(page):
    """Keep only source paths used by geometry, without allocating Point objects
    for thousands of outlined text glyphs or retaining their drawing lists.
    """
    selected=[]
    for drawing in page.get_cdrawings():
        color=drawing.get('fill')
        road=(color is not None and color[0]>=.99
              and .49<color[2]<.51 and .7<color[1]<=1)
        base=(drawing.get('color')==(0.,0.,0.)
              and .22<(drawing.get('width') or 0)<.26)
        if not road and not base:continue
        items=[]
        for item in drawing['items']:
            if item[0]=='re':item=('re',fitz.Rect(item[1]),*item[2:])
            elif item[0]=='qu':item=('qu',fitz.Quad(item[1]),*item[2:])
            items.append(item)
        drawing['items']=items
        selected.append(drawing)
    fitz.TOOLS.store_shrink(100)
    return selected


def register_plan_page(drawings, world_segments, origin, min_matches=20, min_votes=12):
    """Scale and translation from independent vectors; no manual control points."""
    import numpy as np
    from collections import Counter,defaultdict
    starts=np.array([a for a,b in world_segments],dtype=float)
    vectors=np.array([b-a for a,b in world_segments],dtype=float)
    pdf=[]
    for d in drawings:
        if d.get('color')!=(0.,0.,0.) or not .22<(d.get('width') or 0)<.26:
            continue
        for item in d['items']:
            if item[0]=='l':
                a=np.array(item[1]);b=np.array(item[2])
                if np.linalg.norm(b-a)>5:pdf.append((a,b-a))
    if len(pdf)<min_matches or len(vectors)<min_matches:return None
    angles=np.arctan2(vectors[:,1],vectors[:,0]);order=np.argsort(angles)
    angles=angles[order];ordered=vectors[order];hist=Counter()
    for a,v in pdf:
        length=np.linalg.norm(v)
        if length<10:continue
        angle=np.arctan2(v[1],v[0])
        lo=np.searchsorted(angles,angle-.005);hi=np.searchsorted(angles,angle+.005)
        lengths=np.linalg.norm(ordered[lo:hi],axis=1)
        hist.update(set(round(float(length/l),2) for l in lengths if .3<length/l<4))
    for initial_scale,votes in hist.most_common(3):
        if votes<min_votes:continue
        scale=initial_scale;bucket=defaultdict(list)
        for i,v in enumerate(vectors*scale):bucket[tuple(np.rint(v/.25).astype(int))].append(i)
        pairs=[]
        for a,v in pdf:
            key=np.rint(v/.25).astype(int)
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    for i in bucket.get((key[0]+dx,key[1]+dy),[]):
                        if np.linalg.norm(v-vectors[i]*scale)<.22:
                            pairs.append((i,a))
        if len(pairs)<min_matches or len(pairs)>100000:continue
        x=np.array([starts[i]-origin for i,a in pairs]);y=np.array([a for i,a in pairs])
        offsets=y-x*scale;grid=defaultdict(list)
        for i,p in enumerate(offsets):grid[tuple(np.floor(p/.25).astype(int))].append(i)
        groups=[]
        for key in sorted(grid,key=lambda k:len(grid[k]),reverse=True)[:12]:
            indexes=[i for dx in (-1,0,1) for dy in (-1,0,1) for i in grid.get((key[0]+dx,key[1]+dy),[])]
            center=np.median(offsets[indexes],axis=0)
            groups.append(np.where(np.linalg.norm(offsets-center,axis=1)<.25)[0])
        best=max(groups,key=len)
        if len(best)<min_matches:continue
        target=np.median(offsets[best],axis=0)
        for _ in range(8):
            mask=np.linalg.norm(y-(x*scale+target),axis=1)<.22
            if mask.sum()<min_matches:break
            xm=x[mask];ym=y[mask];xb=xm.mean(axis=0);yb=ym.mean(axis=0)
            denominator=np.sum((xm-xb)**2)
            if denominator<=0:break
            scale=float(np.sum((xm-xb)*(ym-yb))/denominator)
            target=yb-scale*xb
        residual=np.linalg.norm(y-(x*scale+target),axis=1);mask=residual<.22
        count=len(set(pairs[i][0] for i in np.where(mask)[0]))
        if count<min_matches or min(np.ptp(x[mask],axis=0))<100:continue
        if scale<=0 or abs(scale-initial_scale)>.015:continue
        return {'scale':scale,'target':target,'origin':origin,
                'inliers':count,'error_m':float(residual[mask].max()/scale),
                'span_m':[float(v) for v in np.ptp(x[mask],axis=0)]}
    return None


def plan_to_eov(point, registration):
    s=registration['scale'];t=registration['target'];o=registration['origin']
    return (float(o[0]+(point[0]-t[0])/s),float(-o[1]-(point[1]-t[1])/s))


def eov_to_plan(point, registration):
    s=registration['scale'];t=registration['target'];o=registration['origin']
    return (float(t[0]+s*(point[0]-o[0])),float(t[1]+s*(-point[1]-o[1])))


def plan_road_polygons(drawings, registration):
    """Dél-Hegyvidék legend: yellow/orange street hatches, closed source tiles."""
    from shapely.geometry import Polygon
    result=[]
    for d in drawings:
        color=d.get('fill')
        if color is None or color[0]<.99 or not .49<color[2]<.51 or not .7<color[1]<=1:
            continue
        if any(item[0] not in {'l','re','qu'} for item in d['items']):continue
        chains=[];current=[]
        for item in d['items']:
            if item[0]=='l':points=[plan_to_eov(item[1],registration),plan_to_eov(item[2],registration)]
            elif item[0]=='re':
                r=item[1];points=[plan_to_eov(p,registration) for p in (r.tl,r.tr,r.br,r.bl,r.tl)]
            else:
                q=item[1];points=[plan_to_eov(p,registration) for p in (q.ul,q.ur,q.lr,q.ll,q.ul)]
            if current and current[-1]!=points[0]:chains.append(current);current=[]
            current.extend(points if not current else points[1:])
        if current:chains.append(current)
        for points in chains:
            if len(points)<3:continue
            polygon=Polygon(points)
            if polygon.is_valid and polygon.area>0:result.append(polygon)
    return result


def plan_parcel_candidates(drawings, registration, outline):
    from shapely.geometry import Polygon,LineString
    from shapely.ops import polygonize,unary_union
    lines=[]
    for d in drawings:
        if d.get('color')!=(0.,0.,0.) or not .22<(d.get('width') or 0)<.26:continue
        for item in d['items']:
            if item[0]=='l':lines.append(LineString([plan_to_eov(item[1],registration),plan_to_eov(item[2],registration)]))
    candidates=[]
    for face in polygonize(unary_union(lines)):
        parcel=Polygon(face.exterior)  # Buildings are holes of the planar face, not holes of a land parcel.
        if (.5*outline.area<parcel.area<1.2*outline.area and parcel.covers(outline.centroid)
            and outline.buffer(registration['error_m']).covers(parcel)
            and parcel.hausdorff_distance(outline)<3):candidates.append(parcel)
    return candidates


def plan_ocr_data():
    import os,tempfile,hashlib
    from pathlib import Path
    for folder in (os.environ.get('TESSDATA_PREFIX',''),'/usr/share/tesseract-ocr/5/tessdata'):
        if folder and (Path(folder)/'eng.traineddata').is_file():return folder
    folder=Path(tempfile.gettempdir())/'telekeloiras_tessdata';folder.mkdir(exist_ok=True)
    path=folder/'eng.traineddata'
    expected='7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2'
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        url='https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata'
        with urllib.request.urlopen(url,timeout=25) as response:raw=response.read(5*1024*1024)
        if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Az OCR nyelvi állomány ellenőrzése sikertelen.')
        temporary=path.with_suffix('.tmp');temporary.write_bytes(raw);temporary.replace(path)
    return str(folder)


def confirm_plan_hrsz(page, registration, parcel, hrsz, tessdata=None):
    """Exact HRSZ plus its position inside the matched parcel. No nearby-text rule."""
    from shapely.geometry import Point
    def matches(words,offset=(0,0)):
        found=[]
        for w in words:
            if clean_text(w[4])!=normalize_hrsz(hrsz):continue
            center=((w[0]+w[2])/2+offset[0],(w[1]+w[3])/2+offset[1])
            if parcel.contains(Point(plan_to_eov(center,registration))):found.append(center)
        return found
    native=matches(page.get_text('words'))
    if native:return {'method':'natív PDF-szöveg','positions':native}
    corners=[eov_to_plan(p,registration) for p in parcel.exterior.coords]
    rect=fitz.Rect(min(p[0] for p in corners)-6,min(p[1] for p in corners)-6,
                   max(p[0] for p in corners)+6,max(p[1] for p in corners)+6)&page.rect
    tessdata=tessdata or plan_ocr_data();found=[]
    for scale in (4,5):
        pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False)
        pix.set_dpi(72*scale,72*scale)
        raw=pix.pdfocr_tobytes(language='eng',tessdata=tessdata)
        with fitz.open(stream=raw,filetype='pdf') as document:
            found.append(matches(document[0].get_text('words'),(rect.x0,rect.y0)))
    stable=[a for a in found[0] if any(math.dist(a,b)<2 for b in found[1])]
    return {'method':'rajzi HRSZ célzott felismerése két felbontásban','positions':stable} if stable else None


def classify_plan_parcel(parcel, roads, zone_lines, labels, window, coverage, uncertainty):
    from shapely.geometry import Point,LineString
    from shapely.ops import nearest_points,unary_union,polygonize
    boundary=roads.boundary;lines=[];adjustments=[]
    for line in zone_lines:
        coords=list(line.coords)
        for index in (0,-1):
            point=Point(coords[index]);distance=point.distance(boundary)
            if distance<=uncertainty:
                nearest=nearest_points(point,boundary)[1];coords[index]=(nearest.x,nearest.y);adjustments.append(distance)
        lines.append(LineString(coords))
    core=parcel.buffer(-uncertainty)
    if core.is_empty or core.area<.95*parcel.area:return '',{'reason':'A rajzi bizonytalanság a telek méretéhez képest túl nagy.'}
    regions=list(polygonize(unary_union([boundary]+lines)))
    candidates=[]
    for region in regions:
        if not region.covers(core):continue
        if (not window.contains(region) or region.boundary.intersects(window.boundary)
                or not coverage.buffer(-uncertainty).contains(region)):continue
        codes={code for code,point in labels if region.contains(point)}
        if len(codes)==1:candidates.append((next(iter(codes)),region))
    if len(candidates)!=1:return '',{'reason':'Nincs egyetlen, teljes telekbelsőt lefedő, egyértelműen feliratozott terület.'}
    code,region=candidates[0]
    return code,{'zone_area_m2':region.area,'parcel_area_m2':parcel.area,
        'parcel_inside_zone_m2':region.intersection(parcel).area,'uncertainty_m':uncertainty,
        'max_endpoint_adjustment_m':max(adjustments,default=0),'zone_wkt':region.wkt}


def resolve_plan_zone(doc, snapshot, hrsz, zone_lines, selected, solids, parcels, footprints, result, proof_cache):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from shapely import set_precision
    field=snapshot.get('geometry_field','Geom')
    # 0.12 PDF point source quantization, plus measured registration residual.
    uncertainty=max(r['error_m']+math.sqrt(2)*.12/r['scale'] for n,r in selected)+.001
    if uncertainty>.3:
        result['detail']='Az illesztési bizonytalanság meghaladja a 30 cm-t.';return result
    roads=unary_union([set_precision(p,.09) for p in solids]);coverage=unary_union(footprints)
    window=minerva_wkt_geometry(snapshot['window_wkt']);labels=[]
    for row in snapshot['label_records']:
        code=next((v for k,v in row.items() if key_text(k)=='karakterlanc'),'')
        if code and inline_zone_code_valid(code):labels.append((code,minerva_wkt_geometry(row[field])))
    confirmed=[]
    for number,registration,parcel in sorted(parcels,key=lambda p:-p[1]['inliers']):
        if confirmed and parcel.hausdorff_distance(confirmed[0][2])<=2*uncertainty:
            continue  # Duplicate parcel on the overlapping sheet; coordinates already agree.
        key=(number,parcel.wkt)
        if key not in proof_cache:
            proof_cache[key]=confirm_plan_hrsz(doc[number],registration,parcel,hrsz)
            fitz.TOOLS.store_shrink(100)
        proof=proof_cache[key]
        if proof:confirmed.append((number,registration,parcel,proof))
    if not confirmed:
        result['detail']='A tervlapi telek belsejében nem sikerült a pontos HRSZ-et megerősíteni.';return result
    parcel=confirmed[0][2]
    if any(parcel.hausdorff_distance(p[2])>2*uncertainty for p in confirmed[1:]):
        result['detail']='Az azonos HRSZ-hez tartozó tervlapi körvonalak eltérnek.';return result
    code,diagnostics=classify_plan_parcel(parcel,roads,zone_lines,labels,window,coverage,uncertainty)
    result.update(diagnostics)
    if not code:result['detail']=diagnostics['reason'];return result
    number,registration,parcel,proof=confirmed[0]
    result.update(zone=code,parcel_wkt=parcel.wkt,pdf_page=number+1,hrsz_method=proof['method'],
        detail='A pontos HRSZ, a tervlapi telek körvonala, a koordinátaillesztés és a zárt övezeti terület együtt ellenőrizve.')
    corners=[eov_to_plan(p,registration) for p in parcel.exterior.coords]
    doc[number].draw_polyline([fitz.Point(*p) for p in corners],color=(0,.3,1),width=.9,closePath=True,overlay=True)
    rect=fitz.Rect(min(p[0] for p in corners)-40,min(p[1] for p in corners)-60,
                   max(p[0] for p in corners)+40,max(p[1] for p in corners)+35)&doc[number].rect
    result['preview']=doc[number].get_pixmap(matrix=fitz.Matrix(3,3),clip=rect).tobytes('png')
    return result


@st.cache_data(show_spinner=False,ttl=900,max_entries=3)
def georeferenced_plan_zone(pdf_bytes, snapshot, hrsz):
    import numpy as np
    from shapely.geometry import Polygon
    result={'zone':'','detail':'','registrations':[]}
    resource=snapshot.get('resource','')
    # This adapter's source styles and version must be the same as the official attached plan.
    if '20250806_DEL_HEGYVIDEK_KESZ' not in resource:
        result['detail']='Ehhez az adatforráshoz még nincs ellenőrzött tervlap-adapter.';return result
    with fitz.open(stream=pdf_bytes,filetype='pdf') as doc:
        cover=key_text(doc[0].get_text())
        if 'del-hegyvidek' not in cover or not re.search(r'2025\s*\.\s*08\s*\.\s*06',cover):
            result['detail']='A térképi adatforrás és a PDF kiadása nem egyezik.';return result
        field=snapshot.get('geometry_field','Geom')
        zone_lines=[minerva_wkt_geometry(r[field]) for r in plan_boundary_records(snapshot)]
        if any(l.geom_type!='LineString' for l in zone_lines):
            result['detail']='Nem támogatott övezethatár-geometria.';return result
        outline=minerva_wkt_geometry(snapshot['parcel_wkt']);centroid=outline.centroid
        origin=np.array([centroid.x,-centroid.y]);segments=[]
        for line in zone_lines:
            points=np.array(line.coords);points[:,1]*=-1
            for a,b in zip(points,points[1:]):
                if np.linalg.norm(b-a)>5:segments.extend(((a,b),(b,a)))
        selected=[];solids=[];parcels=[];footprints=[];proof_cache={}
        for number,page in enumerate(doc):
            if number<4:continue
            drawings=plan_drawings(page)
            registration=register_plan_page(drawings,segments,origin)
            if not registration:
                del drawings
                continue
            target=registration['target']
            if not page.rect.contains(fitz.Point(float(target[0]),float(target[1]))):
                del drawings
                continue
            entry={'PDF-oldal':number+1,'Egyező szakaszok':registration['inliers'],
                'Legnagyobb illesztési eltérés (m)':registration['error_m'],'Méretarány (pont/m)':registration['scale']}
            result['registrations'].append(entry)
            selected.append((number,registration))
            solids.extend(plan_road_polygons(drawings,registration))
            for candidate in plan_parcel_candidates(drawings,registration,outline):parcels.append((number,registration,candidate))
            footprints.append(Polygon([plan_to_eov(p,registration) for p in ((20,20),(1170,20),(1170,822),(20,822))]))
            del drawings
            # A fully contained, closed and uniquely labelled region already proves
            # the result. Other sheets cannot be used to invent a missing boundary.
            if parcels and solids:
                attempt=resolve_plan_zone(doc,snapshot,hrsz,zone_lines,selected,solids,parcels,footprints,dict(result),proof_cache)
                if attempt.get('zone'):return attempt
        if not selected or not solids or not parcels:
            result['detail']='Nem igazolható együtt a tervlap illesztése, a közterületi határ és a telek körvonala.';return result
        return resolve_plan_zone(doc,snapshot,hrsz,zone_lines,selected,solids,parcels,footprints,result,proof_cache)



def inline_zone_code_valid(code):
    return bool(re.fullmatch(r'[^\W\d_][\w./-]*',clean_text(code)))



# Felület
# ---------------------------------------------------------------------


def ink_components(mask):
    """Connected-component boxes using scan-line runs, without an extra dependency."""
    import numpy as np
    parents=[];boxes=[];previous=[]
    def find(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=parents[i]
        return i
    for y,row in enumerate(mask):
        edges=np.flatnonzero(np.diff(np.pad(row.astype(np.int8),(1,1))))
        current=[];left=0
        for x0,x1 in zip(edges[::2],edges[1::2]):
            i=len(parents);parents.append(i);boxes.append([int(x0),y,int(x1),y+1]);current.append((x0,x1,i))
            while left<len(previous) and previous[left][1]<=x0:left+=1
            j=left
            while j<len(previous) and previous[j][0]<x1:
                a=find(i);b=find(previous[j][2])
                if a!=b:parents[b]=a
                j+=1
        previous=current
    groups={}
    for i,b in enumerate(boxes):
        root=find(i)
        if root not in groups:groups[root]=b.copy()
        else:
            g=groups[root];g[0]=min(g[0],b[0]);g[1]=min(g[1],b[1]);g[2]=max(g[2],b[2]);g[3]=max(g[3],b[3])
    return list(groups.values())


def outlined_text_crops(page, registration, parcel):
    import numpy as np
    from PIL import ImageFilter
    from shapely.geometry import Point
    points=[eov_to_plan(p,registration) for p in parcel.exterior.coords]
    clip=fitz.Rect(min(x for x,y in points),min(y for x,y in points),
                   max(x for x,y in points),max(y for x,y in points))&page.rect
    if clip.is_empty:return []
    scale=3
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
    origin=(pix.x/scale,pix.y/scale)
    a=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
    ink=(a.max(axis=2)<110)&((a.max(axis=2)-a.min(axis=2))<15)
    connected=np.asarray(Image.fromarray(ink.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(7)))>0
    result=[]
    for x0,y0,x1,y1 in ink_components(connected):
        w=(x1-x0)/scale;h=(y1-y0)/scale
        if not (3<w<22 and 3<h<22):continue
        center=(origin[0]+(x0+x1)/2/scale,origin[1]+(y0+y1)/2/scale)
        if not parcel.contains(Point(plan_to_eov(center,registration))):continue
        result.append(fitz.Rect(origin[0]+x0/scale-3,origin[1]+y0/scale-3,
                                origin[0]+x1/scale+3,origin[1]+y1/scale+3)&page.rect)
    return result


def ocr_rotated_crop(page, clip, scale, angle=0):
    """Return text and word centres in display-page coordinates, including rotation."""
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
    origin=(pix.x/scale,pix.y/scale)
    source=Image.open(io.BytesIO(pix.tobytes('png')))
    rotated=source.rotate(angle,expand=True,fillcolor='white')
    data=io.BytesIO();rotated.save(data,format='PNG')
    pix=fitz.Pixmap(data.getvalue());pix.set_dpi(72*scale,72*scale)
    theta=math.radians(angle);c=math.cos(theta);s=math.sin(theta)
    with fitz.open(stream=pix.pdfocr_tobytes(language='eng',tessdata=plan_ocr_data()),filetype='pdf') as doc:
        words=[]
        for w in doc[0].get_text('words'):
            dx=(w[0]+w[2])/2*scale-rotated.width/2
            dy=(w[1]+w[3])/2*scale-rotated.height/2
            center=(origin[0]+(c*dx-s*dy+source.width/2)/scale,
                    origin[1]+(s*dx+c*dy+source.height/2)/scale)
            words.append((clean_text(w[4]),center))
        return clean_text(doc[0].get_text()),words


def confirm_outlined_hrsz(page, registration, parcel, hrsz, crops=None):
    from shapely.geometry import Point
    target=normalize_hrsz(hrsz)
    # Search order only: exact text and interior/two-resolution proof still
    # decide acceptance. Proximity never assigns a parcel or an urban zone.
    center=eov_to_plan((parcel.centroid.x,parcel.centroid.y),registration)
    candidates=crops if crops is not None else outlined_text_crops(page,registration,parcel)
    candidates=sorted(candidates,key=lambda r:math.dist(((r.x0+r.x1)/2,(r.y0+r.y1)/2),center))
    for crop in candidates:
        for angle in (45,-45,0):
            matches=[]
            for scale in (12,16):
                text,words=ocr_rotated_crop(page,crop,scale,angle)
                matches.append([p for word,p in words if word==target
                    and parcel.contains(Point(plan_to_eov(p,registration)))])
                if not matches[-1]:break
            if len(matches)==2:
                stable=[a for a in matches[0] if any(math.dist(a,b)<2 for b in matches[1])]
                if stable:return {'method':'ferde rajzi HRSZ, két felbontás és telekbelső ellenőrizve',
                                  'positions':stable,'angle':angle}
    return None


def read_tisza_zone_labels(page, registration, parcel, crops):
    from shapely.geometry import Point
    labels=[]
    for rect in crops:
        if not .8<rect.width/rect.height<1.25 or rect.width<20:continue
        # The source legend defines a two-tier circular symbol. Read each field,
        # rather than guessing from a neighbouring whole-page OCR fragment.
        top=fitz.Rect(rect.x0+.29*rect.width,rect.y0+.25*rect.height,
                      rect.x1-.25*rect.width,rect.y0+.50*rect.height)
        bottom=fitz.Rect(rect.x0+.33*rect.width,rect.y0+.54*rect.height,
                         rect.x1-.33*rect.width,rect.y0+.76*rect.height)
        top=fitz.Rect(*(round(v) for v in top))
        bottom=fitz.Rect(round(bottom.x0),round(bottom.y0),round(bottom.x1),round(rect.y0+.735*rect.height))
        readings=[]
        for scale in (12,16):
            upper,_=ocr_rotated_crop(page,top,scale)
            lower,_=ocr_rotated_crop(page,bottom,scale)
            upper=upper.strip(' |“”\"');lower=lower.strip(' |“”\"')
            if not re.fullmatch(r'[A-Za-z]{1,5}',upper) or not re.fullmatch(r'\d{1,2}',lower):break
            readings.append(upper+'/'+lower)
        if len(readings)==2 and readings[0]==readings[1]:
            center=((rect.x0+rect.x1)/2,(rect.y0+rect.y1)/2)
            point=Point(plan_to_eov(center,registration))
            if parcel.contains(point):labels.append((readings[0],point))
    return labels


def tisza_display_outline(geometry):
    """Undo the rounded display buffer only when its circular arcs prove the radius."""
    import numpy as np
    from shapely.geometry import shape
    multi=shape(geometry['outline'])
    parts=list(multi.geoms) if hasattr(multi,'geoms') else [multi]
    if len(parts)!=1 or not parts[0].is_valid:raise ValueError('Nem egyetlen érvényes telekkörvonal.')
    outline=parts[0];coords=list(outline.exterior.coords);radii=[]
    for a,b,d in zip(coords,coords[1:],coords[2:]):
        a,b,d=map(np.array,(a,b,d));u=b-a;v=d-a
        if np.linalg.norm(u)>1 or np.linalg.norm(d-b)>1 or abs(u[0]*v[1]-u[1]*v[0])<1e-8:continue
        center=np.linalg.solve(2*np.array([u,v]),np.array([u@u,v@v]));radius=np.linalg.norm(center)
        if .5<radius<5:radii.append(radius)
    if len(radii)<20:raise ValueError('A megjelenítési körvonal kiterjesztése nem ellenőrizhető.')
    radius=float(np.median(radii));cluster=[r for r in radii if abs(r-radius)<.003]
    if len(cluster)<20:raise ValueError('Nincs elegendő egyező körív.')
    parcel=outline.buffer(-radius,quad_segs=32).simplify(.02,preserve_topology=True)
    if parcel.is_empty or not parcel.is_valid or parcel.geom_type!='Polygon':raise ValueError('Nem állítható helyre egyetlen telek.')
    return parcel,radius


def tisza_cad_drawings(page):
    # Stream source paths through the native callback. Materialising every
    # outlined glyph on this CAD sheet would exceed Render's memory budget.
    result=[];rotation=page.rotation_matrix
    def collect(drawing):
        color=drawing.get('color');width=drawing.get('width') or 0
        if color and max(color)-min(color)<.001 and .44<color[0]<.48 and .10<width<.14:
            items=[('l',tuple(fitz.Point(*i[1])*rotation),tuple(fitz.Point(*i[2])*rotation))
                   for i in drawing['items'] if i[0]=='l']
            result.append(dict(color=(0.,0.,0.),width=.24,items=items))
    try:page.get_cdrawings(callback=collect)
    except TypeError as exc:
        raise RuntimeError('A memóriahatékony CAD-feldolgozáshoz a PyMuPDF csomag frissítése szükséges.') from exc
    fitz.TOOLS.store_shrink(100)
    return result


def tisza_red_zone_dots(page, registration, parcel, scale=6):
    """The frozen source legend uses filled round red dots for zone boundaries.
    Thin dashed utilities are rejected by thickness, roundness and fill ratio.
    Return control dots as well: a blank/unreadable raster cannot prove absence.
    """
    import numpy as np
    from shapely.geometry import Point
    points=[eov_to_plan(p,registration) for p in parcel.exterior.coords]
    clip=fitz.Rect(min(x for x,y in points)-25,min(y for x,y in points)-25,
                   max(x for x,y in points)+25,max(y for x,y in points)+25)&page.rect
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
    origin=(pix.x/scale,pix.y/scale)
    a=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
    red=(a[:,:,0]>140)&(a[:,:,1]<130)&(a[:,:,2]<130)
    dots=[]
    for x0,y0,x1,y1 in ink_components(red):
        w=x1-x0;h=y1-y0
        if (.66*scale<=min(w,h) and max(w,h)<=1.67*scale
                and min(w,h)/max(w,h)>.65 and red[y0:y1,x0:x1].mean()>.65):
            center=(origin[0]+(x0+x1)/2/scale,origin[1]+(y0+y1)/2/scale)
            dots.append(Point(plan_to_eov(center,registration)))
    return dots


TISZA_PLAN_SHA256='dd81c298d2b12e85d21ea258f3dabae97525d72ead32134d1b8c363aa452d9ef'
TISZA_TABLE_SHA256='646f63c2c6da99ea9862dc6da3abfc3ed54883be718180f48e446496eac62a42'


@st.cache_data(show_spinner=False,ttl=900,max_entries=2)
def tiszaujvaros_plan_zone(pdf_bytes, geometry, hrsz):
    import hashlib,numpy as np
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    result={'zone':'','detail':'','registrations':[]}
    if hashlib.sha256(pdf_bytes).hexdigest()!=TISZA_PLAN_SHA256:
        result['detail']='A tiszaújvárosi terv kiadása megváltozott; új forrásellenőrzés szükséges.';return result
    if (str((geometry.get('settlement') or {}).get('kshCode'))!='28352'
            or normalize_hrsz(geometry.get('lotNumber',''))!=normalize_hrsz(hrsz)):
        result['detail']='A geometria települése vagy pontos helyrajzi száma nem egyezik.';return result
    parcel,radius=tisza_display_outline(geometry)
    origin=np.array([parcel.centroid.x,-parcel.centroid.y]);segments=[]
    for a,b in zip(parcel.exterior.coords,list(parcel.exterior.coords)[1:]):
        a=np.array([a[0],-a[1]]);b=np.array([b[0],-b[1]])
        if np.linalg.norm(b-a)>5:segments.extend(((a,b),(b,a)))
    with fitz.open(stream=pdf_bytes,filetype='pdf') as doc:
        if len(doc)!=56:result['detail']='Eltérő tervlapszerkezet.';return result
        # Source sheets 26 and 32 share a printed 1500 x 1000 m EOV grid.
        # The northern native vectors establish coordinates; sheet 32 is the
        # current raster replacement directly south of it. No parcel/zone is stored.
        registration=register_plan_page(tisza_cad_drawings(doc[28]),segments,origin,min_matches=4,min_votes=3)
        if (not registration or registration['inliers']<8 or registration['error_m']>.12
                or abs(registration['scale']-72/25.4/4)>.0002):
            result['detail']='A telek körvonala nem illeszthető az ellenőrzött tiszaújvárosi tervlapokhoz.';return result
        grid=plan_to_eov((75.001,52.46),registration)
        if math.dist(grid,(801000,289000))>.2:
            result['detail']='A vektorillesztés nem egyezik a forrás tervlapi koordinátahálójával.';return result
        second=dict(registration,target=np.array(registration['target'])-np.array([0,1000*registration['scale']]))
        selected=[(28,registration),(34,second)];footprints=[];labels=[];proof=None;chosen=None
        core=parcel.buffer(-1)
        if core.is_empty or core.area<.98*parcel.area:
            result['detail']='A telek túl keskeny a beszkennelt terv bizonytalanságához.';return result
        for number,reg in selected:
            corners=[plan_to_eov(p,reg) for p in ((75.001,52.46),(1138.021,52.46),(1138.021,761.12),(75.001,761.12))]
            footprints.append(Polygon(corners))
            result['registrations'].append({'PDF-oldal':number+1,'Egyező vektorok':registration['inliers'],
                'Legnagyobb eltérés (m)':round(registration['error_m'],3),
                'Illesztés':'natív telekhatár' if number==28 else 'szomszédos forrás-szelvényháló'})
            crops=outlined_text_crops(doc[number],reg,parcel)
            labels.extend(read_tisza_zone_labels(doc[number],reg,parcel,crops))
            if proof is None and footprints[-1].contains(parcel.centroid):
                proof=confirm_outlined_hrsz(doc[number],reg,parcel,hrsz,crops)
                if proof:chosen=(number,reg)
            # Independent resolutions and visible neighbouring source boundaries.
            for scale in (6,8):
                dots=tisza_red_zone_dots(doc[number],reg,parcel,scale)
                if len(dots)<20:
                    result['detail']='Az övezethatár-jelölés nem olvasható ellenőrizhetően.';return result
                if any(core.contains(p) for p in dots):
                    result['detail']='Övezethatár érinti a telek belsejét; nincs egyetlen automatikus övezet.';return result
            fitz.TOOLS.store_shrink(100)
        if not unary_union(footprints).buffer(.2).covers(core):
            result['detail']='A telek nem fér el teljesen az ellenőrzött tervlapokon.';return result
        codes={code for code,point in labels}
        if proof is None or len(codes)!=1:
            result['detail']='A pontos helyrajzi szám vagy az egyértelmű övezeti felirat nem igazolható.';return result
        number,reg=chosen
        result.update(zone=next(iter(codes)),parcel_wkt=parcel.wkt,parcel_area_m2=parcel.area,
            uncertainty_m=1.,display_buffer_m=radius,hrsz_method=proof['method'],pdf_page=number+1,
            detail='Pontos HRSZ és övezeti körjel két felbontásban; vektorillesztés, teljes szelvényfedés és belső övezethatár-vizsgálat ellenőrizve.')
        # Stitch only the inner source grids, so the complete parcel remains
        # visible across the two source sheets and margins do not obscure it.
        xmin,ymin,xmax,ymax=parcel.bounds
        xmin-=25;ymin-=25;xmax+=25;ymax+=25
        pixels_per_m=2*registration['scale']
        canvas=Image.new('RGB',(math.ceil((xmax-xmin)*pixels_per_m),
                                math.ceil((ymax-ymin)*pixels_per_m)),'white')
        for index,reg in selected:
            left,top=eov_to_plan((xmin,ymax),reg)
            right,bottom=eov_to_plan((xmax,ymin),reg)
            clip=fitz.Rect(left,top,right,bottom)&fitz.Rect(75.001,52.46,1138.021,761.12)
            if clip.is_empty:continue
            pix=doc[index].get_pixmap(matrix=fitz.Matrix(2,2),clip=clip,alpha=False)
            source=Image.open(io.BytesIO(pix.tobytes('png')))
            world_left,world_top=plan_to_eov((pix.x/2,pix.y/2),reg)
            canvas.paste(source,(round((world_left-xmin)*pixels_per_m),round((ymax-world_top)*pixels_per_m)))
        ImageDraw.Draw(canvas).line([((x-xmin)*pixels_per_m,(ymax-y)*pixels_per_m)
                                    for x,y in parcel.exterior.coords],fill=(0,77,255),width=2)
        output=io.BytesIO();canvas.save(output,format='PNG');result['preview']=output.getvalue()
    return result


def verified_tisza_table_rows(doc, zone):
    """Reference transcription of the three Gip rows, checked against the official
    scanned 1.2 annex. Exact PDF fingerprint guards against silently using old data.
    This is reference data, never a parcel-to-zone lookup.
    """
    import hashlib
    if doc is None or zone not in {'Gip/1','Gip/2','Gip/3'}:return [],{},''
    if hashlib.sha256(doc.stream or b'').hexdigest()!=TISZA_TABLE_SHA256:return [],{},''
    params={'Beépítési mód':'SZ','Legnagyobb beépítettség':'30 %',
            'Legkisebb telekterület':'10000 m²','Legkisebb zöldfelület':'25 %',
            'Legnagyobb épületmagasság':'12,50 m; 2. lábjegyzet'}
    source='NJT – 1.2. melléklet, 2. PDF-oldal'
    rows=[{'Előírás':k,'Érték':v,'Forrás':source,
           'Bizonyosság':'ellenőrzött forrássor; azonos PDF-kiadás'} for k,v in params.items()]
    note='2. lábjegyzet: Technológiai indokoltság esetére alkalmazható eltérés. (3. PDF-oldal)'
    return rows,params,note

def main():
    st.title("TelekElőírás AI")
    st.caption(
        "v15.21 • nyilvános HRSZ API + telekgeometria • "
        "NJT szabályozási terv + övezeti paramétertábla • geometriai ellenőrzés + szükség esetén célzott HRSZ-felismerés"
    )

    with st.sidebar:
        st.header("Telek")
        town = st.text_input("Település", value="Tiszaújváros")
        hrsz = st.text_input("Helyrajzi szám", value="2200/8")
        budapest_district = ""
        if key_text(town) in {"budapest xii. kerulet", "budapest 12. kerulet"}:
            town = "Budapest"
        if key_text(town) == "budapest":
            budapest_district = st.selectbox("Budapest kerület", ["XII. kerület"], help="A nyilvános HRSZ-kereső Budapesten kerületet kér. A v15 első tesztje a XII. kerületet támogatja.")

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
                "E-közműben / hivatalos térképen ellenőrzött övezeti kód",
                value="",
                placeholder="pl. Gip/3",
                help=(
                    "Csak akkor add meg, ha a telek helyét és az övezeti jelet "
                    "hivatalos térképen ténylegesen ellenőrizted."
                ),
            )
            map_verified = st.checkbox(
                "A helyrajzi számot és a telek helyét hivatalos térképen ellenőriztem",
                value=False,
            )

        start = st.button(
            "Telekvizsgálat indítása",
            type="primary",
            use_container_width=True,
        )

        st.divider()
        st.link_button(
            "E-közmű térkép megnyitása – telek ellenőrzése",
            EKOZMU_MAP,
            use_container_width=True,
        )
        st.caption(
            "Az E-közmű itt hivatalos térképi ellenőrzési forrás. "
            "A program nem állítja, hogy publikus API-ból automatikusan kiolvasta az övezetet."
        )

    if not start:
        st.info(
            "Add meg a települést és a helyrajzi számot, majd indítsd el a vizsgálatot."
        )
        return

    if not town.strip() or not normalize_hrsz(hrsz):
        st.error("A település és a helyrajzi szám megadása kötelező.")
        return

    # 0. HRSZ -> hivatalos telekgeometria
    st.header("0. Hivatalos telekazonosítás")
    parcel_api = None
    parcel_place = town.strip()
    if key_text(town) == "budapest" and budapest_district:
        parcel_place = f"Budapest {budapest_district}"
    ksh = ""
    try:
        with st.spinner("Település azonosítása a hivatalos HRSZ-keresőben…"):
            ksh=resolve_settlement_code(parcel_place)
    except Exception as exc:
        st.warning(f"A település HRSZ-kereső azonosítása nem sikerült: {exc}")
    if ksh:
        try:
            with st.spinner("Helyrajzi szám és telekgeometria lekérése…"):
                parcel_api = public_parcel_geometry(ksh, hrsz)
            bbox, gtype = geometry_summary(parcel_api.get("geometry", {}))
            st.success(f"A telek azonosítva. Ingatlan ID: **{parcel_api['id']}**")
            c1, c2 = st.columns(2)
            c1.write(f"**Forrás:** nyilvános HRSZ-kereső (`kshCode={ksh}`)")
            c2.write(f"**Geometria:** {gtype or 'elérhető'}")
            if bbox:
                st.json({"boundingBox": bbox})
        except Exception as exc:
            st.error(f"A nyilvános HRSZ-lekérdezés nem sikerült: {exc}")
    else:
        st.info("Pontos településazonosítás nélkül nem kérek le és nem kapcsolok más településről telekgeometriát.")

    minerva_diag = {}
    # 0/B. XII. kerületi publikus MINERVA kapcsolat
    if key_text(town) == "budapest" and key_text(budapest_district) in {"xii. kerulet", "12. kerulet"}:
        st.subheader("0/B. MINERVA térinformatikai kapcsolat")
        with st.spinner("Publikus MINERVA kapcsolat ellenőrzése…"):
            minerva_diag = minerva_xii_status_for_parcel(parcel_api)
        if minerva_diag.get("bootstrap"):
            st.success("A program saját anonim MINERVA-sessiont hozott létre; nem használ böngészőből másolt sessiont vagy tokent.")
        else:
            st.warning("A MINERVA publikus session automatikus létrehozása ezen a futtatási környezeten még nem sikerült.")
        st.caption(minerva_diag.get("detail", ""))
        if minerva_diag.get("layers"):
            with st.expander("MINERVA réteglista – övezeti adatforrás ellenőrzése"):
                st.dataframe(minerva_diag["layers"], hide_index=True, use_container_width=True)
        if minerva_diag.get("definitions"):
            with st.expander("Övezeti rétegek adatforrása és típusa"):
                st.dataframe(minerva_diag["definitions"], hide_index=True, use_container_width=True)
        if minerva_diag.get("source_classes"):
            with st.expander("Jelenlegi adatforrás övezeti feliratosztályai"):
                st.dataframe(minerva_diag["source_classes"], hide_index=True, use_container_width=True)
        if minerva_diag.get("topology"):
            with st.expander("Telek és zárt övezethatár összekapcsolása"):
                st.dataframe(minerva_diag["topology"], hide_index=True, use_container_width=True)
        if minerva_diag.get("geometry_snapshots"):
            st.download_button("Övezeti geometria ellenőrzési adatainak letöltése",
                data=json.dumps(minerva_diag["geometry_snapshots"], ensure_ascii=False).encode("utf-8"),
                file_name="minerva_geometry_snapshot.json", mime="application/json",
                on_click="ignore")
        if minerva_diag.get("parcel_geometry"):
            st.write("**OÉNY → MINERVA térbeli lekérdezés bemenete:** telek MultiPolygon/bounding box rendelkezésre áll.")
        if minerva_diag.get("candidates"):
            st.dataframe(minerva_diag["candidates"], hide_index=True)
            st.warning("A telekpolygonnal metsző réteg övezeti találatai rendelkezésre állnak. A réteg hatályos NJT-melléklethez tartozása még ellenőrzendő; ezért ezekből nem képezek automatikusan hatályos előírást.")
        elif minerva_diag.get("bootstrap"):
            st.caption("A térképi kapcsolat létrejött. Az övezet ellenőrzése a hatályos tervlap feldolgozásával folytatódik.")

    # 1. NJT
    st.header("1. Hatályos hivatalos forrás")

    # Budapesten a HÉSZ/KÉSZ kerületi jogszabály, ezért a már OÉNY-nel
    # ellenőrzött kerületet is bevonjuk a forráskulcsba.
    source_place = town
    if key_text(town) == "budapest" and budapest_district:
        source_place = f"Budapest {budapest_district}"
    meta = source_for_town(source_place, manual_njt_url)
    page = {}
    source_valid = False
    checks = {}
    attachments = []

    detected_district = ""
    detected_district_source = ""
    if key_text(town) == "budapest" and budapest_district:
        detected_district = budapest_district
        detected_district_source = "felhasználó által kiválasztott kerület + nyilvános HRSZ-kereső"
        if parcel_api:
            st.success(f"Budapesti kerület és HRSZ együtt ellenőrizve: **{detected_district}**.")
        else:
            st.info(f"Budapesti kerület: **{detected_district}**.")

    if meta is None:
        with st.spinner("Hivatalos NJT-forrás automatikus felderítése…"):
            if detected_district:
                # Ne keressük újra a kerületet: közvetlenül a már igazolt kerületre
                # szűkített NJT-felderítést használjuk.
                place_for_search = f"Budapest {detected_district}"
                queries = [
                    f'site:or.njt.hu "{place_for_search}" "kerületi építési szabályzat"',
                    f'site:njt.jog.gov.hu "{place_for_search}" "kerületi építési szabályzat"',
                    f'site:or.njt.hu "{place_for_search}" KÉSZ',
                    f'site:or.njt.hu "{place_for_search}" "szabályozási terv"',
                ]
                candidates = []
                for q in queries:
                    for u in _search_web(q):
                        if is_official_njt_url(u) and u not in candidates:
                            candidates.append(u)
                page = {}
                meta = None
                terms = [key_text("Budapest"), key_text(detected_district), key_text(place_for_search)]
                for u in candidates[:10]:
                    p = fetch_njt_page(u)
                    if not p.get("ok"):
                        continue
                    body = key_text(p.get("text", ""))
                    is_rule = any(x in body for x in (
                        "epitesi szabalyzat", "helyi epitesi szabalyzat",
                        "keruleti epitesi szabalyzat", "kesz"
                    ))
                    place_ok = any(t and t in body for t in terms[1:])
                    if is_rule and place_ok:
                        meta = {
                            "municipality": town,
                            "title": clean_text(p.get("text", "").split("\n")[0])[:180] or "Automatikusan felderített NJT-forrás",
                            "regulation": "",
                            "url": p.get("url") or u,
                            "source": "kerületre szűkített automatikus NJT-forrásfelderítés",
                            "district": detected_district,
                            "district_evidence": detected_district_source,
                        }
                        page = p
                        break
            else:
                meta, page = discover_njt_source(town, hrsz)

    if meta:
        with st.spinner("NJT-forrás ellenőrzése…"):
            if not page:
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
            "Nem sikerült automatikusan olyan hivatalos NJT HÉSZ/KÉSZ-forrást találni, "
            "amelyet tartalmilag is ellenőrizni tudtam. A Haladó beállításoknál továbbra is "
            "megadható a hivatalos NJT URL."
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
            plan_doc, plan_source, auto_plan_error = try_auto_plan(attachments, page.get("text", ""))

        if plan_doc:
            st.success("A szabályozási terv PDF automatikusan betöltődött.")
        elif auto_plan_error:
            st.caption(f"Automatikus PDF-letöltés nem sikerült: {auto_plan_error}")

    zone_table_doc = None
    zone_table_source = ""
    zone_table_text = ""
    zone_table_error = ""

    inline_tables = inline_zone_tables(page.get("html", ""))
    if inline_tables:
        st.success(f"Az NJT-oldal övezeti táblázatai betöltődtek: {len(inline_tables)} övezeti sor.")
    elif attachments:
        with st.spinner("Övezeti paramétertábla betöltése…"):
            zone_table_doc, zone_table_source, zone_table_text, zone_table_error = load_zone_table(attachments)

        if zone_table_doc and verified_tisza_table_rows(zone_table_doc,"Gip/1")[0]:
            st.success("A beszkennelt 1.2 melléklet forráskiadása és ipari övezeti sorai ellenőrizve.")
        elif zone_table_doc and clean_text(zone_table_text):
            st.success("Az 1.2 melléklet övezeti paramétertáblája automatikusan betöltődött.")
        elif zone_table_error:
            st.caption(f"1.2 melléklet: {zone_table_error}")

    # 3. Telek
    st.header("3. Telekazonosítás")
    st.write(f"**{town} {normalize_hrsz(hrsz)} hrsz.**")

    plan_zone = {"zone": ""}
    snapshots = minerva_diag.get("geometry_snapshots", [])
    if (plan_doc is not None and source_valid and snapshots
            and plan_source.startswith("https://njt.jog.gov.hu/document/")):
        with st.spinner("Tervlap koordinátaillesztése, telekhatár és övezet ellenőrzése…"):
            try:
                snapshot = next((item for item in snapshots
                    if "20250806_DEL_HEGYVIDEK_KESZ" in item.get("resource", "")), None)
                if snapshot:
                    raw_plan=plan_doc.tobytes()
                    plan_doc.close()
                    plan_doc=None
                    fitz.TOOLS.store_shrink(100)
                    try:
                        plan_zone = georeferenced_plan_zone(raw_plan, snapshot, hrsz)
                        if plan_zone.get("zone") and not inline_zone_rows(inline_tables, plan_zone["zone"])[0]:
                            plan_zone["zone"] = ""
                            plan_zone["detail"] = "A geometriai eredményhez nincs pontosan egyező hatályos NJT-táblázatsor."
                    finally:
                        if not plan_zone.get('zone'):
                            plan_doc=fitz.open(stream=raw_plan,filetype='pdf')
                        del raw_plan
                        fitz.TOOLS.store_shrink(100)
            except Exception as exc:
                plan_zone = {"zone": "", "detail": f"A tervlapi ellenőrzés nem teljes: {exc}"}
        if plan_zone.get("registrations"):
            with st.expander("Tervlap koordinátaillesztése – ellenőrzési eredmény"):
                st.dataframe(plan_zone["registrations"], hide_index=True, use_container_width=True)
        if not plan_zone.get("zone") and plan_zone.get("detail"):
            st.warning(plan_zone["detail"])

    if (plan_doc is not None and source_valid and parcel_api and ksh=="28352"
            and plan_source.startswith("https://njt.jog.gov.hu/document/")):
        with st.spinner("Tiszaújváros: tervlapszelvények, pontos HRSZ és övezethatárok ellenőrzése…"):
            raw_plan=plan_doc.stream or plan_doc.tobytes()
            plan_doc.close();plan_doc=None
            fitz.TOOLS.store_shrink(100)
            try:
                plan_zone=tiszaujvaros_plan_zone(raw_plan,parcel_api['geometry'],hrsz)
                if plan_zone.get('zone') and not verified_tisza_table_rows(zone_table_doc,plan_zone['zone'])[0]:
                    plan_zone['zone']=''
                    plan_zone['detail']='Nincs azonos kiadású, ellenőrzött NJT-paramétersor az övezethez.'
            except Exception as exc:
                plan_zone={'zone':'','detail':f'A tiszaújvárosi ellenőrzés nem teljes: {exc}'}
            finally:
                if not plan_zone.get('zone'):plan_doc=fitz.open(stream=raw_plan,filetype='pdf')
                del raw_plan
                fitz.TOOLS.store_shrink(100)
        if plan_zone.get('registrations'):
            with st.expander("Tiszaújvárosi tervlapok – ellenőrzési eredmény"):
                st.dataframe(plan_zone['registrations'],hide_index=True,use_container_width=True)
        if not plan_zone.get('zone') and plan_zone.get('detail'):st.warning(plan_zone['detail'])

    # Native text is a fallback. Do not scan every large CAD sheet before the
    # geometric method or keep a second open document during that computation.
    spatial = ({'status':'verified','hit':None,'candidates':[],'zone':''}
               if plan_zone.get('zone') else locate_parcel(plan_doc,hrsz))

    if parcel_api:
        bbox, gtype = geometry_summary(parcel_api.get("geometry", {}))
        st.success(
            f"A helyrajzi számot a nyilvános ingatlan-nyilvántartási HRSZ-szolgáltatás azonosította; "
            f"telekgeometria: **{gtype or 'elérhető'}**."
        )
        if bbox:
            st.caption(f"Telek bounding box: {bbox}")

    if spatial["status"] == "missing_plan":
        st.info(
            "A telek térbeli vizsgálatához szabályozási terv szükséges. "
            "Ha az NJT-ből nem tölthető le automatikusan, töltsd fel a hivatalos PDF-et."
        )
    elif plan_zone.get("zone"):
        st.success(f"A telek a szabályozási terv {plan_zone['pdf_page']}. PDF-oldalán, a koordináták és a pontos HRSZ alapján azonosítva.")
        st.image(plan_zone["preview"], caption="Az azonosított telek a hatályos szabályozási terven", use_container_width=True)
    elif spatial["status"] == "parcel_not_found":
        if parcel_api:
            st.warning(
                "A telek hivatalos geometriája már rendelkezésre áll, de a szabályozási terv "
                "még nincs georeferáltan összekapcsolva vele. A v15 ezért nem próbálja a HRSZ-et "
                "a PDF szövegében a telek helyettesítőjeként használni."
            )
        else:
            st.error(
                "A helyrajzi számot nem találtam meg a szabályozási terv natív szövegrétegében, "
                "és automatikus telekgeometria sem áll rendelkezésre."
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
    zone = clean_text(manual_zone) or plan_zone.get("zone", "")

    if manual_zone.strip():
        if map_verified:
            st.success(
                f"Hivatalos térképen ellenőrzött övezeti kód: **{manual_zone.strip()}**. "
                "A kód felhasználói térképi ellenőrzésből származik; a program ezt nem "
                "automatikus geometriai metszésként állítja."
            )
        else:
            st.warning(
                f"Kézzel megadott övezeti kód: **{manual_zone.strip()}**. "
                "Jelöld az ellenőrzőnégyzetet csak akkor, ha a telek helyét hivatalos "
                "térképen ténylegesen ellenőrizted."
            )
    elif plan_zone.get("zone"):
        st.success(f"Automatikusan ellenőrzött övezet: **{zone}**.")
        st.caption(f"{plan_zone['detail']} A rajzi ellenőrzés bizonytalansága: ±{plan_zone['uncertainty_m']:.2f} m; ez nem földmérési pontosság.")
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
    if inline_tables:
        # Flattened HTML includes adjacent zone rows; it cannot establish numeric values.
        params = {}
        rows = []  # Adjacent HTML rows and prose snippets cannot establish parcel-specific rules.
        table_rows, table_params, table_context = inline_zone_rows(inline_tables, zone)
        zone_table_source = page.get("url", "")
    elif verified_tisza_table_rows(zone_table_doc,zone)[0]:
        rows=[];params={}
        table_rows, table_params, table_context = verified_tisza_table_rows(zone_table_doc,zone)
    else:
        table_rows, table_params, table_context = zone_table_rows(zone_table_text, zone)

    if not zone:
        st.warning("Övezeti kód nélkül nem kapcsolok övezetspecifikus előírást a telekhez.")
    else:
        if table_rows:
            st.subheader("Övezeti paraméterek – NJT-táblázat")
            st.dataframe(table_rows, hide_index=True, use_container_width=True)
            if zone_table_source:
                st.caption(f"Forrás: {zone_table_source}")
            with st.expander("Övezeti táblázat – nyers forráskörnyezet"):
                st.write(table_context)
                if verified_tisza_table_rows(zone_table_doc,zone)[0]:
                    st.image(zone_table_doc[1].get_pixmap(matrix=fitz.Matrix(3,3),
                             clip=fitz.Rect(62,730,576,783),alpha=False).tobytes('png'),
                             caption="Az ipari övezetek eredeti forrássorai – 1.2. melléklet, 2. PDF-oldal",
                             use_container_width=True)
        elif inline_tables:
            st.warning(f"A {zone} kódhoz nincs egyetlen, pontosan egyező és feldolgozható NJT-táblázatsor.")
        elif zone_table_text:
            st.warning(
                f"Az 1.2 melléklet betöltődött, de a **{zone}** kódhoz nem tudtam "
                "biztonságosan címkézett paramétereket kinyerni. Nem találgatok."
            )

        if not njt_text:
            st.warning("Nincs feldolgozható NJT-szöveg a további övezeti szabályokhoz.")
        elif rows:
            st.subheader("HÉSZ szöveges előírásai")
            st.dataframe(rows, hide_index=True, use_container_width=True)
            with st.expander("NJT forráskörnyezet – ellenőrzéshez"):
                for i, context in enumerate(contexts[:5], 1):
                    st.markdown(f"**{i}. találat**")
                    st.write(context)
        elif not table_rows:
            st.warning(
                f"A **{zone}** kódhoz nem sikerült kellően strukturált előírást "
                "kinyerni. A program nem egészíti ki feltételezéssel."
            )

    combined_params = dict(params)
    combined_params.update(table_params)

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
        "hivatalos térképen felhasználó által ellenőrzött"
        if manual_zone.strip() and map_verified
        else "kézzel megadott, még nem igazolt"
        if manual_zone.strip()
        else "georeferált tervlap, pontos HRSZ és övezethatárok alapján ellenőrzött"
        if plan_zone.get("zone")
        else "nincs igazolva"
    )

    summary = []
    if meta and meta.get("district"):
        summary.append({
            "Adat": "Budapesti kerületi jelölt",
            "Eredmény": meta.get("district", ""),
            "Forrás": meta.get("district_evidence", "") or "nyilvános forrásfelderítés",
            "Bizonyosság": "keresési segédadat – hivatalos forrásból ellenőrzendő",
        })

    summary += [
        {
            "Adat": "Telek",
            "Eredmény": f"{town} {normalize_hrsz(hrsz)}",
            "Forrás": ("hivatalos HRSZ-szolgáltatás + hatályos szabályozási terv" if plan_zone.get("zone")
                       else "felhasználói adat + hivatalos térképi ellenőrzés" if map_verified
                       else "felhasználói adat / hivatalos térképen ellenőrzendő"),
            "Bizonyosság": ("pontos HRSZ és geometria alapján ellenőrzött" if plan_zone.get("zone")
                            else "térképen ellenőrzött" if map_verified else "ellenőrzendő"),
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
            "Eredmény": f"{len(combined_params)} strukturált adat" if combined_params else "nincs",
            "Forrás": "NJT HÉSZ/TÉSZ",
            "Bizonyosság": "NJT-forrásból kinyert" if combined_params else "nincs adat",
        },
        {
            "Adat": "Közműérintettség",
            "Eredmény": "térképi ellenőrzés elvégezve" if map_verified else "kézi térképi ellenőrzés szükséges",
            "Forrás": "E-közmű / hivatalos térkép",
            "Bizonyosság": "felhasználó által ellenőrzött" if map_verified else "nincs automatizálva",
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
