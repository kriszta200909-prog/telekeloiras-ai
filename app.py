# TelekElőírás AI v15.4
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
from html.parser import HTMLParser
from urllib.parse import urljoin

import fitz
import streamlit as st
from PIL import Image, ImageDraw


st.set_page_config(
    page_title="TelekElőírás AI v15.4",
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
            "User-Agent": "Mozilla/5.0 TelekEloirasAI/15.4",
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
# v15-ben a böngészőben ellenőrzött XII. kerületi tesztkódot használjuk.
# További településkódokat csak ellenőrzött forrásból veszünk fel.
KSH_CODES = {
    "budapest xii. kerulet": "24697",
    "budapest 12. kerulet": "24697",
}

def _json_get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 TelekEloirasAI/15.4",
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
    record = _find_parcel_record(data, h) or data
    parcel_id = _extract_id(record)
    if not parcel_id:
        raise RuntimeError("A HRSZ-kereső válaszából nem sikerült ingatlan-azonosítót kinyerni.")
    bbox_url = f"{HRSZ_API_BASE}/bounding-box?" + urllib.parse.urlencode({"id": parcel_id})
    geom = _json_get(bbox_url)
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
    headers = {"User-Agent": "Mozilla/5.0 TelekEloirasAI/15.4", "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.5"}
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

def minerva_xii_status_for_parcel(parcel_api):
    result = {"supported": True, "parcel_geometry": False, "bootstrap": False, "runtime_map": False, "zone": "", "detail": ""}
    if not parcel_api:
        result["detail"] = "Az OÉNY telekgeometria nem áll rendelkezésre."; return result
    bbox, gtype = geometry_summary(parcel_api.get("geometry", {}))
    result["parcel_geometry"] = bool(bbox) and bool(gtype)
    try:
        boot = minerva_xii_bootstrap()
        result["bootstrap"] = bool(boot.get("session_created")); result["runtime_map"] = bool(boot.get("mapdefinition_seen"))
        result["detail"] = f"internet.php HTTP {boot.get('entry_status')}; ajaxviewer HTTP {boot.get('viewer_status')}; saját session: {'igen' if result['bootstrap'] else 'nem'}; runtime map nyom: {'igen' if result['runtime_map'] else 'nem'}; TLS ellenőrzés: {'igen' if boot.get('tls_verified') else 'MINERVA-only fallback'}."
    except Exception as exc:
        result["detail"] = f"MINERVA kapcsolat nem sikerült: {exc}"
    return result


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
    """Szabályozási terv jelölt kiválasztása az NJT mellékletekből."""
    if not attachments:
        return None

    scored = []
    for row in attachments:
        label = key_text(row.get("Megnevezés", ""))
        url = (row.get("URL") or "").casefold()
        score = 0

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
        if any(x in label for x in ("akadalymentes", "nyilatkozat")):
            score -= 20

        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)

    # A v11 5 pontos küszöbe valós NJT tervmellékletet is kizárhatott.
    # Itt továbbra is kell tervi jelleg, de nem követeljük meg a teljes
    # "szabályozási terv" kifejezést.
    return scored[0][1] if scored and scored[0][0] >= 4 else None


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
# 1.2 melléklet – övezeti paramétertábla
# ---------------------------------------------------------------------

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
            return doc, final_url, native, "Az 1.2 PDF natív szövegrétege túl kevés adatot tartalmaz."
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
# Felület
# ---------------------------------------------------------------------

def main():
    st.title("TelekElőírás AI")
    st.caption(
        "v15.2 • nyilvános HRSZ API + telekgeometria • "
        "NJT szabályozási terv + övezeti paramétertábla • OCR nélkül"
    )

    with st.sidebar:
        st.header("Telek")
        town = st.text_input("Település", value="Tiszaújváros")
        hrsz = st.text_input("Helyrajzi szám", value="2200/8")
        budapest_district = ""
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
    ksh = KSH_CODES.get(key_text(parcel_place), "")
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
        st.info("Ehhez a településhez/kerülethez még nincs ellenőrzött HRSZ-kereső kód a v15-ben.")

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
        if minerva_diag.get("parcel_geometry"):
            st.write("**OÉNY → MINERVA térbeli lekérdezés bemenete:** telek MultiPolygon/bounding box rendelkezésre áll.")
        st.info("A következő automatizálási pont a hatályos KÉSZ övezeti objektumának térbeli lekérdezése. A program bizonyíték nélkül nem ír ki övezeti kódot.")

    # 1. NJT
    st.header("1. Hatályos hivatalos forrás")

    meta = source_for_town(town, manual_njt_url)
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
            plan_doc, plan_source, auto_plan_error = try_auto_plan(attachments)

        if plan_doc:
            st.success("A szabályozási terv PDF automatikusan betöltődött.")
        elif auto_plan_error:
            st.caption(f"Automatikus PDF-letöltés nem sikerült: {auto_plan_error}")

    zone_table_doc = None
    zone_table_source = ""
    zone_table_text = ""
    zone_table_error = ""

    if attachments:
        with st.spinner("1.2 övezeti paramétertábla betöltése…"):
            zone_table_doc, zone_table_source, zone_table_text, zone_table_error = load_zone_table(attachments)

        if zone_table_doc and zone_table_text:
            st.success("Az 1.2 melléklet övezeti paramétertáblája automatikusan betöltődött.")
        elif zone_table_error:
            st.caption(f"1.2 melléklet: {zone_table_error}")

    # 3. Telek
    st.header("3. Telekazonosítás")
    st.write(f"**{town} {normalize_hrsz(hrsz)} hrsz.**")

    spatial = locate_parcel(plan_doc, hrsz)

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
    zone = clean_text(manual_zone) or auto_zone

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
    table_rows, table_params, table_context = zone_table_rows(zone_table_text, zone)

    if not zone:
        st.warning("Övezeti kód nélkül nem kapcsolok övezetspecifikus előírást a telekhez.")
    else:
        if table_rows:
            st.subheader("1.2 melléklet – övezeti paraméterek")
            st.dataframe(table_rows, hide_index=True, use_container_width=True)
            if zone_table_source:
                st.caption(f"Forrás: {zone_table_source}")
            with st.expander("1.2 melléklet – nyers forráskörnyezet"):
                st.write(table_context)
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
        else "közeli dokumentumalapú jelölt"
        if auto_zone
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
            "Forrás": "felhasználói adat + hivatalos térképi ellenőrzés" if map_verified else "felhasználói adat / hivatalos térképen ellenőrzendő",
            "Bizonyosság": "térképen ellenőrzött" if map_verified else "ellenőrzendő",
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
            "Bizonyosság": "NJT / 1.2 mellékletből kinyert" if combined_params else "nincs adat",
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
