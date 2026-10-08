# TelekElőírás AI v15.49
# Tiszta, újraírt Streamlit alkalmazás.
# Cél: telek -> hivatalos NJT-forrás -> szabályozási terv -> övezeti jelölt
#      -> forrásolt övezeti előírások.
#
# Fontos: döntéstámogató eszköz, nem hatósági vagy jogi állásfoglalás.

import os
# Avoid oversubscribing CPU-limited hosting during Tesseract OCR.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

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
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin

import fitz
import streamlit as st
from PIL import Image, ImageDraw
from rule_inventory import Clause, build_inventory, source_digest
from plan_labels import native_hrsz_hits, outlined_label_index, verify_outlined_hrsz


st.set_page_config(
    page_title="TelekElőírás AI v15.49",
    page_icon="🏗️",
    layout="wide",
)

EKOZMU_MAP = "https://ekozmu.e-epites.hu/lakossag/#/lakossag/kozmuterkep"


@st.cache_resource(show_spinner=False)
def pdf_processing_lock():
    """One process-wide gate, shared by all Streamlit sessions and reruns.

    MuPDF/Leptonica must not be entered from concurrent session threads.
    Lock the entire investigation because rendering and text extraction also
    use MuPDF; protecting only the OCR call would leave those races open.
    """
    import threading
    return threading.RLock()

# Validált hivatalos forrásindex.
# Új település később egyetlen új rekorddal felvehető.
HESZ_INDEX = {
    "komadi": {
        "municipality": "Komádi",
        "title": "Komádi város Szabályozási Tervének és Helyi Építési Szabályzatának elfogadásáról",
        "regulation": "1/2007. (I. 26.) önkormányzati rendelet",
        "url": "https://njt.jog.gov.hu/jogszabaly/2007-1-SP-5Y1608",
        "plan_url": "https://njt.jog.gov.hu/document/29/29e9LL_EJR_109881483-tervlap_T_3_BELTERULETI_SZAB_2025_egyben.pdf",
        "plan_sha256": "15422b7898f58ab3763cbf2289e9b81b1aa4c548ecb5c8721df1e51800c9a041",
        "plan_scope": "belterület",
    },
    "gersekarat": {
        "municipality": "Gersekarát",
        "title": "Gersekarát község helyi építési szabályzatáról és a község szabályozási tervéről",
        "regulation": "2/2007. (II. 15.) önkormányzati rendelet",
        "url": "https://njt.jog.gov.hu/jogszabaly/2007-2-SP-5Y3101",
        "plan_url": "https://njt.jog.gov.hu/document/ff/ffecLL_EJR_55522770-2._mell_klet_H_SZ.pdf",
        "plan_sha256": "a459bb1daa7b442ddd58ffe5bb7bd65ace62eed2412c744897bf705a98566cca",
        "plan_scope": "igazgatási terület",
        "scope_note": "A 2/2007. rendelet területi hatálya a déli községrészt kizárja; arra külön rendelet vonatkozik. A megfelelő helyi rendelet telekre való alkalmazhatóságához a telek helyét is igazolni kell.",
    },
    "miskolc": {
        "municipality": "Miskolc",
        "title": "Miskolc Megyei Jogú Város Építési Szabályzata",
        "regulation": "38/2022. (XII. 16.) önkormányzati rendelet",
        "url": "https://njt.jog.gov.hu/jogszabaly/2022-38-SP-5Y1070",
        "plan_scope": "belterület",
        "plan_url": "https://njt.jog.gov.hu/document/b4/b45dLL_EJR_127797597-Belteruleti_szabalyozasi_tervlapok_modositasa.pdf",
        "plan_reference": "4755/11: PDF 31/92. A felhasználó által bemutatott hivatalos tervlap alapján a felirat Gipe-60.63.5; a programnak ezt önállóan ellenőriznie kell, nem használhatja automatikus övezet-megállapításként.",
        "legend_url": "https://njt.jog.gov.hu/document/03/0366LL_EJR_83921015-Jelmagyarazat_modositasa.pdf",
        "parameter_legend_url": "https://njt.jog.gov.hu/document/f3/f327LL_EJR_124216758-Param_terek_magyar_zata.pdf",
    },
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


def parallel_njt_pdf(url, headers, timeout=90):
    """Assemble ranges only when every response proves the same strong ETag.
    Bounded streaming limits memory; any inconsistency discards the whole result.
    """
    from concurrent.futures import ThreadPoolExecutor
    probe_headers=dict(headers,Range='bytes=0-1023',**{'Accept-Encoding':'identity'})
    with urllib.request.urlopen(urllib.request.Request(url,headers=probe_headers),timeout=min(timeout,15)) as response:
        status=getattr(response,'status',200)
        match=re.fullmatch(r'bytes 0-1023/(\d+)',response.headers.get('Content-Range',''))
        tag=response.headers.get('ETag','')
        encoding=response.headers.get('Content-Encoding','identity')
        final_url=response.geturl()
        if (status!=206 or not match or not re.fullmatch(r'"[^"\r\n]+"',tag)
                or encoding.lower()!='identity'):
            raise ValueError('Nem igazolható az azonos kiadású részletletöltés.')
        size=int(match.group(1))
        if not 16*1024*1024<=size<=128*1024*1024:
            raise ValueError('A részletletöltéshez nem megfelelő PDF-méret.')
        first=response.read(1025)
        if len(first)!=1024 or not first.startswith(b'%PDF'):
            raise ValueError('Hibás PDF-kezdőrészlet.')
        content_type=response.headers.get('Content-Type','')
    buffer=bytearray(size)
    buffer[:1024]=first
    count=4;remaining=size-1024
    ranges=[(1024+remaining*i//count,1024+remaining*(i+1)//count-1) for i in range(count)]
    def load(bounds):
        left,right=bounds
        part_headers=dict(headers,Range=f'bytes={left}-{right}',**{'If-Range':tag,'Accept-Encoding':'identity'})
        with urllib.request.urlopen(urllib.request.Request(url,headers=part_headers),timeout=timeout) as response:
            if (getattr(response,'status',200)!=206 or response.geturl()!=final_url
                    or response.headers.get('ETag')!=tag
                    or response.headers.get('Content-Range')!=f'bytes {left}-{right}/{size}'
                    or response.headers.get('Content-Encoding','identity').lower()!='identity'):
                raise ValueError('A PDF-részlet kiadása vagy bájttartománya eltér.')
            position=left
            while position<=right:
                chunk=response.read(min(1024*1024,right-position+1))
                if not chunk:raise ValueError('Hiányos PDF-részlet.')
                if len(chunk)>right-position+1:raise ValueError('Túl hosszú PDF-részlet.')
                buffer[position:position+len(chunk)]=chunk;position+=len(chunk)
            if response.read(1):raise ValueError('Többletbájt a PDF-részletben.')
    with ThreadPoolExecutor(max_workers=count) as pool:
        # Consume every future so no partial buffer can escape after an error.
        list(pool.map(load,ranges))
    return bytes(buffer),final_url,200,'utf-8',content_type


def http_get(url, timeout=25, accept="text/html,*/*;q=0.8"):
    headers={"User-Agent":"Mozilla/5.0 TelekEloirasAI/15.32",
             "Accept-Language":"hu-HU,hu;q=0.9,en;q=0.5","Accept":accept}
    # Only the known large official attachment uses parallel ranges. Other
    # sources retain their ordinary request and redirect behaviour.
    if (url=='https://njt.jog.gov.hu/document/d9/d95fLL_EJR_81697536-rendelet_mell_klet-1.pdf'
            and 'application/pdf' in accept):
        try:return parallel_njt_pdf(url,headers,timeout)
        except Exception:
            # An unsupported range or a changed server version must never become
            # an incomplete cached document. A single full response remains valid.
            pass
    req=urllib.request.Request(url,headers=headers)
    with urllib.request.urlopen(req,timeout=timeout) as response:
        return (response.read(),response.geturl(),getattr(response,"status",200),
                response.headers.get_content_charset() or "utf-8",
                response.headers.get("Content-Type",""))


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
            and urllib.parse.urlsplit(final_url).hostname in {'njt.jog.gov.hu', 'njt.hu', 'or.njt.hu'}
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
        known = HESZ_INDEX.get(key_text(town), {})
        if manual_url.strip() == known.get("url"):
            return {**known, "source": "kézi NJT URL – ellenőrzött forrásrekord"}
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
    escaped_hrsz = re.escape(h).replace('/', r'\s*/\s*')
    hrsz_rx = re.compile(
        rf"(?<![\d/]){escaped_hrsz}(?!\s*/\s*[A-Za-z0-9])",
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
    return [{**hit,'pdf_rect':fitz.Rect(hit['pdf_rect'])} for hit in native_hrsz_hits(doc,hrsz)]


class IncompletePlanLabelIndex(Exception):
    def __init__(self,result):
        super().__init__('A rajzi feliratkeresés feldolgozási kerete lejárt.')
        self.result=result


@st.cache_data(show_spinner=False,ttl=3600,max_entries=8)
def cached_outlined_plan_index(pdf_sha,algorithm_version,_doc,_on_progress=None):
    checkpoint=outlined_plan_checkpoint(pdf_sha,algorithm_version)
    saved=checkpoint.get('index')
    if saved and saved.get('complete'):return saved
    index=outlined_label_index(_doc,plan_ocr_data(),max_seconds=600,on_progress=_on_progress,
                              resume=saved,on_checkpoint=lambda state:checkpoint.update(index=state))
    if not index['complete']:raise IncompletePlanLabelIndex(index)
    return index


@st.cache_resource(show_spinner=False,ttl=3600,max_entries=8)
def outlined_plan_checkpoint(pdf_sha,algorithm_version):
    # Contains plain label data only; never retain a live MuPDF document.
    return {}


def load_outlined_plan_labels(doc,hrsz,on_progress=None):
    # The key is always computed from this actual document, not source metadata.
    raw=doc.tobytes(no_new_id=True) if doc.is_dirty else (doc.stream or doc.tobytes(no_new_id=True))
    digest=source_digest(raw)
    try:index=cached_outlined_plan_index(digest,'outlined-label-index-v3',_doc=doc,_on_progress=on_progress)
    except IncompletePlanLabelIndex as exc:index=exc.result
    result=verify_outlined_hrsz(doc,index,normalize_hrsz(hrsz),plan_ocr_data())
    result['source_sha256']=digest
    return result


def label_crop(page,hit):
    """Render a bounded source region; the red box marks text, not a parcel."""
    rect=fitz.Rect(hit['pdf_rect'])
    if hit.get('coordinate_space')!='display':rect=visible_rect(page,rect)
    cx,cy=(rect.x0+rect.x1)/2,(rect.y0+rect.y1)/2
    clip=fitz.Rect(cx-150,cy-100,cx+150,cy+100)&page.rect
    scale=4
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
    image=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    draw=ImageDraw.Draw(image)
    draw.rectangle((rect.x0*scale-pix.x-4,rect.y0*scale-pix.y-4,
                    rect.x1*scale-pix.x+4,rect.y1*scale-pix.y+4),outline='red',width=3)
    return image


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
    r"L[1-9]|Lk|Lke|Vt|Vi|Gipe|Gip|Gksz|KÖu|KÖk|Ev|Eg|Má|Mk|Kb|Üh|Üü|Lf|K"
    r")[A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9._-]*"
    r"(?:\s*/\s*[A-Za-z0-9._-]+|-[0-9]+(?:\.[0-9]+){1,})\b",
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
    # A távolság szerint sorba rendezett külön szavakat nem szabad
    # összefűzni: a rajzon egymástól független feliratokból hamis kód keletkezhet.
    strings = [text for _, text in nearby]

    for distance, text in nearby:
        for match in ZONE_PATTERN.finditer(text):
            code = re.sub(r"\s*/\s*", "/", match.group(0))
            if code.casefold() not in seen:
                seen.add(code.casefold())
                candidates.append((distance, code))

    candidates.sort(key=lambda x: x[0])
    return [
        {"Övezeti kód": code, "Távolsági sorrend": i + 1}
        for i, (_, code) in enumerate(candidates[:12])
    ]


def locate_parcel(doc, hrsz, outlined=False, on_progress=None):
    if doc is None:
        return {
            "status": "missing_plan",
            "hit": None,
            "candidates": [],
            "zone": "",
        }

    hits = find_hrsz(doc, hrsz)
    scan={}
    if not hits and outlined:
        scan=load_outlined_plan_labels(doc,hrsz,on_progress=on_progress)
        hits=[{**hit,'pdf_rect':fitz.Rect(hit['pdf_rect']),'coordinate_space':'display'}
              for hit in scan['hits']]
    if not hits:
        return {
            "status": "parcel_not_found",
            "hit": None,
            "candidates": [],
            "zone": "",
            "scan":scan,
        }

    hit = hits[0]
    page = doc[hit["page_number"]]
    candidates = zone_candidates(page, hit["pdf_rect"]) if len(hits)==1 and not scan else []
    # A felirat közelsége nem bizonyítja, hogy a kód ugyanazon telek
    # övezetéhez tartozik. Csak ellenőrizendő jelöltet közlünk.
    return {
        "status": "candidate_unverified" if candidates else "zone_not_found",
        "hit": hit,
        "candidates": candidates,
        "zone": "",
        "hits":hits,
        "scan":scan,
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


def try_auto_plan(attachments, legal_text="", source_meta=None, hrsz=""):
    # A checked source record may identify a raster plan, but only while that
    # exact PDF is still linked by the current official regulation page.
    source_meta = source_meta or {}
    preferred = source_meta.get("plan_url", "")
    eligible = not (source_meta.get("plan_scope") == "belterület"
                    and normalize_hrsz(hrsz).startswith("0"))
    pinned = next((row for row in attachments
                   if eligible and preferred and row.get("URL") == preferred), None)
    if eligible and preferred and not pinned:
        return None, "", "Az ellenőrzött tervmelléklet már nem szerepel az aktuális NJT-rendelet hivatkozásai között."
    if pinned:
        doc = None
        try:
            raw, final_url = download_pdf(preferred)
            if source_meta.get('plan_sha256') and __import__('hashlib').sha256(raw).hexdigest() != source_meta.get('plan_sha256'):
                return None, final_url, "Az ellenőrzött tervmelléklet tartalma megváltozott; új forrásellenőrzés szükséges."
            doc = open_pdf_bytes(raw)
            if not doc or not len(doc):
                if doc is not None:
                    doc.close()
                return None, final_url, "Az ellenőrzött tervmelléklet nem tartalmaz tervlapot."
            return doc, final_url, ""
        except Exception as exc:
            if doc is not None:
                doc.close()
            return None, preferred, f"Ellenőrzött NJT-tervmelléklet: {type(exc).__name__}: {exc}"
    candidate = choose_plan_attachment(attachments, legal_text)
    if not candidate:
        return None, "", ""

    doc = None
    phase = "PDF-letöltés"
    try:
        raw, final_url = download_pdf(candidate["URL"])
        phase = "PDF megnyitása és tervtartalom ellenőrzése"
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
        if doc is not None:
            doc.close()
        return None, candidate.get("URL", ""), f"{phase}: {type(exc).__name__}: {exc}"



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
        # Every session/process owns its temporary file. Atomic replacement
        # publishes only verified bytes, even when downloads finish together.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, prefix='eng-', suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                output.write(raw)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
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


def plan_crop_pixmap(page, scale, clip):
    """Reuse decoded page commands only inside an immutable source inspection.
    No rendered full-page raster is retained, and mutable documents use direct
    rendering so edits cannot accidentally read a stale display list.
    """
    cache=getattr(page.parent,'_telek_display_lists',None)
    if cache is None:
        return page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
    if page.number not in cache:
        # Keep a single decoded page: outlined CAD lettering is memory-heavy.
        cache.clear()
        fitz.TOOLS.store_shrink(100)
        cache[page.number]=page.get_displaylist()
    return cache[page.number].get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)


def tisza_native_circle_crops(page, registration, parcel):
    """Find native legend circles even when their control-point bounds are
    taller than the drawn circle or a separator touches adjacent table lines.
    Stroke geometry supplies candidates only; raster shape and OCR still verify.
    """
    import numpy as np
    from shapely.geometry import Point
    cache=getattr(page.parent,'_telek_circle_crops',None)
    boxes=cache.get(page.number) if cache is not None else None
    if boxes is None:
        boxes=[];rotation=page.rotation_matrix
        def collect(drawing):
            if drawing.get('color')!=(0.,0.,0.) or not .20<(drawing.get('width') or 0)<.28:return
            r=fitz.Rect(drawing['rect'])*rotation
            if not (14<min(r.width,r.height)<24 and max(r.width,r.height)<26):return
            curves=[i for i in drawing['items'] if i[0]=='c']
            if len(curves)<2:return
            points=[]
            for item in curves:
                control=np.array([tuple(fitz.Point(*p)*rotation) for p in item[1:]])
                for t in np.linspace(0,1,41):
                    points.append((1-t)**3*control[0]+3*(1-t)**2*t*control[1]+3*(1-t)*t*t*control[2]+t**3*control[3])
            a=np.array(points);lo=a.min(axis=0);hi=a.max(axis=0)
            if not (14<min(hi-lo)<20 and .9<(hi[0]-lo[0])/(hi[1]-lo[1])<1.1):return
            boxes.append(fitz.Rect(lo[0]-4,lo[1]-4,hi[0]+4,hi[1]+4))
        page.get_cdrawings(callback=collect)
        if cache is not None:cache[page.number]=boxes
        fitz.TOOLS.store_shrink(100)
    return [r for r in boxes if parcel.contains(Point(plan_to_eov(((r.x0+r.x1)/2,(r.y0+r.y1)/2),registration)))]


def outlined_text_crops(page, registration, parcel):
    import numpy as np
    from PIL import ImageFilter
    from shapely.geometry import Point
    points=[eov_to_plan(p,registration) for p in parcel.exterior.coords]
    clip=fitz.Rect(min(x for x,y in points),min(y for x,y in points),
                   max(x for x,y in points),max(y for x,y in points))&page.rect
    if clip.is_empty:return []
    scale=3
    pix=plan_crop_pixmap(page,scale,clip)
    origin=(pix.x/scale,pix.y/scale)
    a=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
    threshold=150 if page.number in (27,28) else 110
    ink=(a.max(axis=2)<threshold)&((a.max(axis=2)-a.min(axis=2))<15)
    connected=np.asarray(Image.fromarray(ink.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(7)))>0
    result=[]
    for x0,y0,x1,y1 in ink_components(connected):
        w=(x1-x0)/scale;h=(y1-y0)/scale
        if not (3<w<22 and 3<h<22):continue
        center=(origin[0]+(x0+x1)/2/scale,origin[1]+(y0+y1)/2/scale)
        if not parcel.contains(Point(plan_to_eov(center,registration))):continue
        result.append(fitz.Rect(origin[0]+x0/scale-3,origin[1]+y0/scale-3,
                                origin[0]+x1/scale+3,origin[1]+y1/scale+3)&page.rect)
    if page.number in (27,28):result.extend(tisza_native_circle_crops(page,registration,parcel))
    return result


def ocr_rotated_crop(page, clip, scale, angle=0):
    """Return text and word centres in display-page coordinates, including rotation."""
    pix=plan_crop_pixmap(page,scale,clip)
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
        angles=(0,15,-15,30,-30,45,-45,90,-90) if getattr(page,'number',None) in (27,28) else (45,-45,0)
        for angle in angles:
            matches=[]
            for scale in ((24,32) if getattr(page,'number',None) in (27,28) else (12,16)):
                text,words=ocr_rotated_crop(page,crop,scale,angle)
                matches.append([p for word,p in words if word==target
                    and parcel.contains(Point(plan_to_eov(p,registration)))])
                if not matches[-1]:break
            if len(matches)==2:
                stable=[a for a in matches[0] if any(math.dist(a,b)<2 for b in matches[1])]
                if stable:return {'method':'ferde rajzi HRSZ, két felbontás és telekbelső ellenőrizve',
                                  'positions':stable,'angle':angle}
    return None


def tisza_zone_circle_candidate(page, rect):
    """Reject ordinary outlined text before OCR; retain the source legend circle.
    This is only a candidate filter. Exact two-resolution text and spatial
    boundary checks still determine whether any zoning answer is accepted.
    """
    import numpy as np
    scale=6;pix=plan_crop_pixmap(page,scale,rect)
    a=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
    ink=a.max(axis=2)<230
    cx=(rect.x0+rect.x1)/2*scale-pix.x;cy=(rect.y0+rect.y1)/2*scale-pix.y
    angles=np.linspace(0,2*math.pi,72,endpoint=False)
    candidates=np.array([(cx+dx*scale,cy+dy*scale,r*scale)
        for dx in np.arange(-2,2.1,.5) for dy in np.arange(-2,2.1,.5)
        for r in np.arange(6,10.1,.5)])
    found=np.zeros((len(candidates),72),dtype=bool)
    for dr in (-.75,0,.75):
        xs=np.rint(candidates[:,0,None]+(candidates[:,2,None]+dr*scale)*np.cos(angles)).astype(int)
        ys=np.rint(candidates[:,1,None]+(candidates[:,2,None]+dr*scale)*np.sin(angles)).astype(int)
        valid=(xs>=0)&(ys>=0)&(xs<pix.width)&(ys<pix.height)
        found[valid]|=ink[ys[valid],xs[valid]]
    return bool((found.mean(axis=1)>=.75).any())


def read_tisza_zone_labels(page, registration, parcel, crops):
    from shapely.geometry import Point
    labels=[]
    for rect in crops:
        if not .8<rect.width/rect.height<1.25 or rect.width<20:continue
        if not tisza_zone_circle_candidate(page,rect):continue
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
    frames=getattr(page.parent,'_telek_grid_frames',None);edges=[]
    def collect(drawing):
        color=drawing.get('color');width=drawing.get('width') or 0
        if frames is not None and color==(0.,0.,0.):
            bounds=drawing.get('rect')
            if bounds and max(bounds[2]-bounds[0],bounds[3]-bounds[1])>650:
                for item in drawing['items']:
                    if item[0]=='l':
                        a=fitz.Point(*item[1])*rotation;b=fitz.Point(*item[2])*rotation
                        if abs(a-b)>650:edges.append((a,b))
        if color and max(color)-min(color)<.001 and .44<color[0]<.48 and .10<width<.14:
            items=[('l',tuple(fitz.Point(*i[1])*rotation),tuple(fitz.Point(*i[2])*rotation))
                   for i in drawing['items'] if i[0]=='l']
            result.append(dict(color=(0.,0.,0.),width=.24,items=items))
    try:page.get_cdrawings(callback=collect)
    except TypeError as exc:
        raise RuntimeError('A memóriahatékony CAD-feldolgozáshoz a PyMuPDF csomag frissítése szükséges.') from exc
    if frames is not None:frames[page.number]=edges
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
    pix=plan_crop_pixmap(page,scale,clip)
    origin=(pix.x/scale,pix.y/scale)
    a=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
    red=(a[:,:,0]>140)&(a[:,:,1]<130)&(a[:,:,2]<130)
    dots=[]
    for x0,y0,x1,y1 in ink_components(red):
        w=x1-x0;h=y1-y0
        if (.66*scale<=min(w,h) and max(w,h)<=1.67*scale
                and min(w,h)/max(w,h)>.65 and red[y0:y1,x0:x1].mean()>.65):
            # This edition uses pure red zone dots; orange and burgundy utility
            # symbols have the same shape. Classify the component's core colour,
            # preserving antialiased red fringes in its size and centroid.
            colours=a[y0:y1,x0:x1][red[y0:y1,x0:x1]]
            if np.median(colours[:,1])>=35 or np.median(colours[:,2])>=35:continue
            center=(origin[0]+(x0+x1)/2/scale,origin[1]+(y0+y1)/2/scale)
            dots.append(Point(plan_to_eov(center,registration)))
    return dots


def tisza_checked_zone_dots(page, registration, parcel, scale):
    """Inspect the entire target first; separate bounded source patches supply
    positive readability controls where a large uniform zone has no nearby dots.
    Control patches never replace inspection of the target area.
    """
    from shapely.geometry import box
    dots=tisza_red_zone_dots(page,registration,parcel,scale)
    if len(dots)>=20:return dots
    dots+=tisza_red_zone_dots(page,registration,parcel.buffer(200),scale)
    def unique(points):return list({(round(p.x,1),round(p.y,1)):p for p in points}.values())
    dots=unique(dots)
    if len(dots)>=20:return dots
    left,top=plan_to_eov((75.001,52.46),registration)
    for dx,dy in ((150,850),(150,150),(1350,850),(1350,150)):
        x=left+dx;y=top-dy
        dots+=tisza_red_zone_dots(page,registration,box(x-100,y-100,x+100,y+100),scale)
        dots=unique(dots)
        if len(dots)>=20:break
    return dots


def tisza_source_grid_registration(page):
    """Calibration of this fingerprinted source edition's printed EOV grid.
    The four frame edges must still be present in the native source. Coordinates
    refer to source sheets, never to a parcel or a stored zoning answer.
    """
    rotation=page.rotation_matrix
    frames=getattr(getattr(page,'parent',None),'_telek_grid_frames',{})
    edges=list(frames.get(page.number,[])) if hasattr(page,'number') else []
    def collect(drawing):
        if drawing.get('color')!=(0.,0.,0.):return
        for item in drawing['items']:
            if item[0]=='l':
                a=fitz.Point(*item[1])*rotation;b=fitz.Point(*item[2])*rotation
                if abs(a-b)>650:edges.append((a,b))
    if not edges:page.get_cdrawings(callback=collect)
    corners=[fitz.Point(x,y) for x,y in ((75.001,52.40),(1138.021,52.40),
                                       (1138.021,761.12),(75.001,761.12))]
    for a,b in zip(corners,corners[1:]+corners[:1]):
        if not any((abs(a-c)<.1 and abs(b-d)<.1) or
                   (abs(a-d)<.1 and abs(b-c)<.1) for c,d in edges):return None
    fitz.TOOLS.store_shrink(100)
    return dict(scale=72/25.4/4,origin=[801000.,-289000.],target=[75.001,52.46],
                inliers=0,error_m=.2,span_m=[1500.,1000.],source_grid=True)


def tisza_clear_zone_connection(doc, selected, parcel, point):
    """Prove a broad connected region between a parcel and an external label.
    A 20 m wide safety margin prevents paths threading between printed dots.
    Both resolutions must contain readable boundary controls on every sheet.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    corridor=parcel.union(point.buffer(10)).convex_hull.buffer(10)
    footprints=[Polygon([plan_to_eov(p,reg) for p in
                ((75.001,52.46),(1138.021,52.46),(1138.021,761.12),(75.001,761.12))])
                for _,reg in selected]
    if not unary_union(footprints).buffer(.2).covers(corridor):return False
    for (number,reg),footprint in zip(selected,footprints):
        if not footprint.intersects(corridor):continue
        for scale in (6,8):
            dots=tisza_checked_zone_dots(doc[number],reg,corridor,scale)
            if len(dots)<20 or any(corridor.contains(p) for p in dots):return False
        fitz.TOOLS.store_shrink(100)
    return True


TISZA_PLAN_SHA256='dd81c298d2b12e85d21ea258f3dabae97525d72ead32134d1b8c363aa452d9ef'
TISZA_TABLE_SHA256='646f63c2c6da99ea9862dc6da3abfc3ed54883be718180f48e446496eac62a42'


class UnverifiedTiszaPlanZone(Exception):
    def __init__(self, result):
        super().__init__(result.get('detail', 'Az övezet nem igazolt.'))
        self.result = result


@st.cache_data(show_spinner=False, ttl=3600, max_entries=8)
def cached_tisza_plan_zone(pdf_sha, geometry_json, hrsz, algorithm_version, _pdf_bytes):
    # Only positive, complete evidence is reusable; exceptions are not cached.
    result = compute_tiszaujvaros_plan_zone(_pdf_bytes, json.loads(geometry_json), hrsz)
    if not result.get('zone'):
        raise UnverifiedTiszaPlanZone(result)
    return result


def tiszaujvaros_plan_zone(pdf_bytes, geometry, hrsz):
    import hashlib
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    if pdf_sha != TISZA_PLAN_SHA256:
        return {'zone': '', 'detail': 'A tiszaújvárosi terv kiadása megváltozott; új forrásellenőrzés szükséges.', 'registrations': []}
    normalized_hrsz = normalize_hrsz(hrsz)
    if (str((geometry.get('settlement') or {}).get('kshCode')) != '28352'
            or normalize_hrsz(geometry.get('lotNumber', '')) != normalized_hrsz):
        return {'zone': '', 'detail': 'A geometria települése vagy pontos helyrajzi száma nem egyezik.', 'registrations': []}
    # Keep every coordinate unchanged. Labels, timestamps and display metadata
    # cannot change the geometry used by the verified native/OCR algorithm.
    stable_geometry = {'settlement': {'kshCode': '28352'}, 'lotNumber': normalized_hrsz,
                       'outline': geometry['outline']}
    geometry_json = json.dumps(stable_geometry, sort_keys=True, separators=(',', ':'), allow_nan=False)
    try:
        return cached_tisza_plan_zone(pdf_sha, geometry_json, normalized_hrsz,
                                      'tisza-plan-zone-v15.45', _pdf_bytes=pdf_bytes)
    except UnverifiedTiszaPlanZone as exc:
        return exc.result


def compute_tiszaujvaros_plan_zone(pdf_bytes, geometry, hrsz):
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
        doc._telek_display_lists={}
        doc._telek_grid_frames={}
        doc._telek_circle_crops={}
        # Source sheets 26 and 32 share a printed 1500 x 1000 m EOV grid.
        # The northern native vectors establish coordinates; sheet 32 is the
        # current raster replacement directly south of it. No parcel/zone is stored.
        core=parcel.buffer(-1)
        if core.is_empty or core.area<.90*parcel.area:
            result['detail']='A telek túl keskeny a beszkennelt terv bizonytalanságához.';return result
        registration=register_plan_page(tisza_cad_drawings(doc[28]),segments,origin,min_matches=4,min_votes=3)
        if (not registration or registration['inliers']<8 or registration['error_m']>.12
                or abs(registration['scale']-72/25.4/4)>.0002):
            registration=tisza_source_grid_registration(doc[28])
            if not registration:
                result['detail']='Nem igazolható a telek illesztése vagy a forrás koordinátahálója.';return result
        grid=plan_to_eov((75.001,52.46),registration)
        if math.dist(grid,(801000,289000))>.2:
            result['detail']='A vektorillesztés nem egyezik a forrás tervlapi koordinátahálójával.';return result
        # Frozen edition: sheets 25/26 above 31/32, confirmed by the official
        # overview. The western native sheet uses the same printed frame.
        selected=[]
        for number,dx,dy in ((28,0,0),(34,0,-1000),(27,-1500,0),(33,-1500,-1000)):
            reg=dict(registration,target=np.array(registration['target'])-np.array([dx,-dy])*registration['scale'])
            footprint=Polygon([plan_to_eov(p,reg) for p in
                ((75.001,52.46),(1138.021,52.46),(1138.021,761.12),(75.001,761.12))])
            if not footprint.intersects(core):continue
            if number==27 and not tisza_source_grid_registration(doc[number]):
                result['detail']='A nyugati tervlap nyomtatott koordinátakerete nem ellenőrizhető.';return result
            selected.append((number,reg))
        footprints=[];labels=[];proof=None;chosen=None
        core=parcel.buffer(-1)
        if core.is_empty or core.area<.90*parcel.area:
            result['detail']='A telek túl keskeny a beszkennelt terv bizonytalanságához.';return result
        for number,reg in selected:
            corners=[plan_to_eov(p,reg) for p in ((75.001,52.46),(1138.021,52.46),(1138.021,761.12),(75.001,761.12))]
            footprints.append(Polygon(corners))
            result['registrations'].append({'PDF-oldal':number+1,'Egyező vektorok':registration['inliers'],
                'Legnagyobb eltérés (m)':round(registration['error_m'],3),
                'Illesztés':('ellenőrzött forrás-koordinátaháló' if registration.get('source_grid') else 'natív telekhatár') if number==28 else 'ellenőrzött szomszédos forrás-szelvényháló'})
            crops=outlined_text_crops(doc[number],reg,parcel)
            labels.extend(read_tisza_zone_labels(doc[number],reg,parcel,crops))
            if proof is None and footprints[-1].contains(parcel.centroid):
                proof=confirm_outlined_hrsz(doc[number],reg,parcel,hrsz,crops)
                if proof:chosen=(number,reg)
            # Independent resolutions and visible neighbouring source boundaries.
            for scale in (6,8):
                dots=tisza_checked_zone_dots(doc[number],reg,parcel,scale)
                if len(dots)<20:
                    result['detail']='Az övezethatár-jelölés nem olvasható ellenőrizhetően.';return result
                if any(core.contains(p) for p in dots):
                    result['detail']='Övezethatár érinti a telek belsejét; nincs egyetlen automatikus övezet.';return result
            fitz.TOOLS.store_shrink(100)
        if not unary_union(footprints).buffer(.2).covers(core):
            result['detail']='A telek nem fér el teljesen az ellenőrzött tervlapokon.';return result
        external_label=False
        if proof is not None and not labels:
            search_area=parcel.buffer(250)
            for index,reg in selected:
                crops=outlined_text_crops(doc[index],reg,search_area)
                candidates=read_tisza_zone_labels(doc[index],reg,search_area,crops)
                for code,point in candidates:
                    if tisza_clear_zone_connection(doc,selected,parcel,point):labels.append((code,point))
                fitz.TOOLS.store_shrink(100)
            external_label=bool(labels)
        codes={code for code,point in labels}
        if proof is None or len(codes)!=1:
            result['detail']='A pontos helyrajzi szám vagy az egyértelmű övezeti felirat nem igazolható.';return result
        number,reg=chosen
        result.update(zone=next(iter(codes)),parcel_wkt=parcel.wkt,parcel_area_m2=parcel.area,
            uncertainty_m=1.,display_buffer_m=radius,hrsz_method=proof['method'],pdf_page=number+1,
            detail='Pontos HRSZ és övezeti körjel két felbontásban; vektorillesztés, teljes szelvényfedés és belső övezethatár-vizsgálat ellenőrizve.')
        if registration.get('source_grid'):
            result['detail']=result['detail'].replace('vektorillesztés','ellenőrzött forrás-koordinátaháló')
        result['external_zone_label']=external_label
        if external_label:
            result['detail']+=' A teleken kívüli övezeti körjelhez vezető összefüggő terület határmentessége két felbontásban ellenőrizve.'
        # Stitch only the inner source grids, so the complete parcel remains
        # visible across the two source sheets and margins do not obscure it.
        xmin,ymin,xmax,ymax=parcel.bounds
        if external_label:
            label_point=min((p for c,p in labels),key=lambda p:p.distance(parcel))
            result['zone_label_eov']=[label_point.x,label_point.y]
            xmin=min(xmin,label_point.x);ymin=min(ymin,label_point.y)
            xmax=max(xmax,label_point.x);ymax=max(ymax,label_point.y)
        xmin-=25;ymin-=25;xmax+=25;ymax+=25
        pixels_per_m=2*registration['scale']
        canvas=Image.new('RGB',(math.ceil((xmax-xmin)*pixels_per_m),
                                math.ceil((ymax-ymin)*pixels_per_m)),'white')
        for index,reg in selected:
            left,top=eov_to_plan((xmin,ymax),reg)
            right,bottom=eov_to_plan((xmax,ymin),reg)
            clip=fitz.Rect(left,top,right,bottom)&fitz.Rect(75.001,52.46,1138.021,761.12)
            if clip.is_empty:continue
            pix=plan_crop_pixmap(doc[index],2,clip)
            source=Image.open(io.BytesIO(pix.tobytes('png')))
            world_left,world_top=plan_to_eov((pix.x/2,pix.y/2),reg)
            canvas.paste(source,(round((world_left-xmin)*pixels_per_m),round((ymax-world_top)*pixels_per_m)))
        ImageDraw.Draw(canvas).line([((x-xmin)*pixels_per_m,(ymax-y)*pixels_per_m)
                                    for x,y in parcel.exterior.coords],fill=(0,77,255),width=2)
        output=io.BytesIO();canvas.save(output,format='PNG');result['preview']=output.getvalue()
    return result


def verified_tisza_table_rows(doc, zone):
    """Source transcription, gated by the exact current official PDF edition.
    These are zone-to-parameter rows, never a parcel-to-zone lookup.
    """
    import hashlib
    if not isinstance(zone,str) or doc is None or doc.is_dirty or hashlib.sha256(doc.stream or b'').hexdigest()!=TISZA_TABLE_SHA256:return [],{},''
    commercial={
        '1':('SZ','40','1500','20','7,50'), '2':('SZ','40','5000','30','9,00'),
        '3':('Z','40','1500','20','7,50'), '4':('O','60','900','20','6,00'),
        '5':('SZ','60','2000','20','12,50'), '6':('SZ','40','2500','20','9,50'),
        '7':('SZ','40','5000','20','9,00'), '8':('SZ','60','2000','20','7,50'),
        '9':('SZ','40','1500','20','7,50'), '10':('SZ','60','1500','20','6,00'),
        '11':('SZ','40','900','20','6,00'), '12':('O','40','1500','20','7,50'),
        '13':('SZ','40','10000','20','20,00'), '14':('O','60','700','20','7,50'),
        '15':('O','60','400','20','4,50'), 'g':('Z','60','K','20','3,00')}
    note=''
    if zone in {'Gip/1','Gip/2','Gip/3'}:
        values=('SZ','30','10000','25','12,50')
        note='2. lábjegyzet: Technológiai indokoltság esetére alkalmazható eltérés. (3. PDF-oldal)'
    elif zone.startswith('Gksz/') and zone[5:] in commercial:values=commercial[zone[5:]]
    else:return [],{},''
    mode,built,area,green,height=values
    params={'Beépítési mód':mode,'Legnagyobb beépítettség':built+' %',
            'Legkisebb telekterület':area+(' m²' if area!='K' else ' (a forrás jelölése)'),
            'Legkisebb zöldfelület':green+' %',
            'Legnagyobb épületmagasság':height+' m'+('; 2. lábjegyzet' if note else '')}
    source='NJT – 1.2. melléklet, 2. PDF-oldal'
    rows=[{'Előírás':k,'Érték':v,'Forrás':source,
           'Bizonyosság':'ellenőrzött forrássor; azonos PDF-kiadás'} for k,v in params.items()]
    return rows,params,note



TISZA_RULES_URL='https://njt.jog.gov.hu/jogszabaly/2018-11-SP-5Y1228'
TISZA_RULE_SECTION_HASHES={7: 'cff5c49ee1a00ba6c890b78a4342d843cbd6751acda3fa898a9419dbe7565f03', 8: 'c1ad75b3cf38fd5a2d11113346acd72fc28c58ea9fd7a4e96ddf18dc9424a5d1', 11: '959bfd19bbb1c2918f3bba3d032e6bb61d92dcdca6d8e1eae3763bcafecfb159', 12: '717f50befce9a1281eb3ebfd748c417fb1bd9248fc186e8e41ddadc30771a5e8', 26: '4cc70661aa0dae8617fe2c8e215b5e23979b6135a52fa7066815e6c200a40618', 27: 'ab712e9be04c98cf3c44d3828b6fd44e49dc38f419760a8c9c58e13590137d6b', 28: '97b21cb83e64fc560c0df098a266f5dfefe0184d5a096ff41a14bfa5062b16f3', 29: '0be00a5ea3b33189777d033113a7b5cd6b969d953c82e42ce6b48d96a37fe84b', 30: '027d4b09225b44233e0b56180f4e61560f3c2d0512138ddbbf7d66cfdeb86033'}  # Filled from the reviewed official source paragraphs.


class TiszaLegalParagraphs(HTMLParser):
    """Read complete clauses from NJT structural markers, including closing bans.
    Numbers mentioned in prose, neighbouring headings and footnotes are excluded.
    """
    def __init__(self,document_id='2018-11-SP-5Y1228'):
        super().__init__()
        self.document_prefix='sc'+document_id+'-'
        self.active=None;self.blocks={};self.anchors={};self.depth=0;self.legal_depth=None
        self.paragraph=None;self.skip=0;self.duplicates=set();self.seen=set()
        self.editions=[];self.edition_depth=None;self.edition_parts=[];self.sup_notes=[]

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='span' and 'jhId' in attrs.get('class','').split():
            marker=attrs.get('id','')
            m=(re.fullmatch(r'SZ(\d+(?:/[A-Z])?)\.@BE(?:\((\d+[a-zA-Z]?)\)|0)(?:@.*)?',marker)
               or re.fullmatch(r'SZ(\d+[A-Z]?)@BE(\d+[a-zA-Z]?)(?:@.*)?',marker))
            if m:
                section=int(m[1]) if m[1].isdigit() else re.sub(r'^(\d+)([A-Z])$',r'\1/\2',m[1])
                clause = m[2].lower() if m[2] else None
                self.active=(section, int(clause) if clause and clause.isdigit() and int(clause) else (clause if clause and not clause.isdigit() else None))
                if marker in self.seen:self.duplicates.add(section)
                self.seen.add(marker)
                self.anchors.setdefault(self.active,marker)
            else:self.active=None
        if tag=='div':
            self.depth+=1
            if attrs.get('class','')=='hataly':
                self.edition_depth=self.depth;self.edition_parts=[]
            if (attrs.get('id','').startswith(self.document_prefix)
                    and attrs.get('class','') in {'bekezdesNyito','bekezdesZaro','betusPontNyito',
                    'betusPontZaro','ketbetusAlPont','szamosPontNyito','alpontNyito','alpontZaro','szamozottPontNyito','szamozottPontZaro'}):
                self.legal_depth=self.depth
        if tag=='p' and self.legal_depth is not None and self.active:
            self.paragraph=[]
        if tag=='sup':
            note=('fnSup' in attrs.get('class','').split() or 'data-note-id' in attrs)
            self.sup_notes.append(note)
            if note:self.skip+=1
        elif tag in ('script','style'):self.skip+=1

    def handle_endtag(self,tag):
        if tag=='sup':
            if self.sup_notes and self.sup_notes.pop():self.skip=max(0,self.skip-1)
        elif tag in ('script','style'):
            self.skip=max(0,self.skip-1)
        if tag=='p' and self.paragraph is not None:
            text=clean_text(''.join(self.paragraph))
            if text:self.blocks.setdefault(self.active,[]).append(text)
            self.paragraph=None
        if tag=='div':
            if self.depth==self.edition_depth:
                self.editions.append(clean_text(''.join(self.edition_parts)));self.edition_depth=None
            if self.depth==self.legal_depth:self.legal_depth=None
            self.depth=max(0,self.depth-1)

    def handle_data(self,data):
        if self.edition_depth is not None and not self.skip:self.edition_parts.append(data)
        if self.paragraph is not None and not self.skip:
            if self.sup_notes and not self.sup_notes[-1]:
                data=data.translate(str.maketrans('0123456789+-=()', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾'))
            self.paragraph.append(data)


@st.cache_data(show_spinner=False, ttl=3600)
def local_pdf_inventory_clauses(url, edition):
    """Keep full native PDF sections across page boundaries, with page provenance."""
    raw,final_url,status,*_=http_get(url)
    if status!=200 or not is_official_njt_url(final_url):
        raise ValueError('A jogszabályi PDF hivatalos forrása nem igazolható.')
    doc=fitz.open(stream=raw,filetype='pdf')
    try:
        texts=[p.get_text('text') for p in doc]
    finally: doc.close()
    sections=[];current=[];start_page=None
    for page_number,text in enumerate(texts,1):
        for line in text.splitlines():
            if re.fullmatch(r'\s*\d+(?:/[A-Z])?\.?\s*§\s*\d*\s*',line):
                if current: sections.append((start_page,'\n'.join(current)))
                current=[];start_page=page_number
            if start_page is not None: current.append(line)
    if current: sections.append((start_page,'\n'.join(current)))
    digest=source_digest(raw)
    return [Clause(text,final_url,'page='+str(number),edition,digest)
            for number,text in sections]


def local_source_inventory(page, source_verified, zone='', zone_verified=False, attachments=None):
    """Extract full NJT HTML clauses for any municipality; no applicability claim."""
    document_id=urllib.parse.urlsplit(page.get('url','')).path.rstrip('/').split('/')[-1]
    reader=TiszaLegalParagraphs(document_id=document_id)
    reader.feed(page.get('html',''))
    edition=reader.editions[0] if len(reader.editions)==1 else ''
    digest=source_digest(page.get('html',''))
    clauses=[Clause(clean_text(' '.join(parts)),page.get('url',''),
                    reader.anchors.get(position,''),edition,digest)
             for position,parts in reader.blocks.items()
             if position[0] not in reader.duplicates]
    issues=[]
    if source_verified and edition:
        for attachment in attachments or []:
            url=attachment.get('URL','')
            name=key_text(urllib.parse.unquote(attachment.get('Megnevezés','')+' '+url))
            # Plan maps are a separate spatial stage, not textual rule sources.
            if (is_official_njt_url(url) and '.pdf' in name
                    and re.search(r'helyi[ _]+[e_]?p[i_]?t[e_]?si[ _]+szab[a_]?lyzat',name)):
                try: clauses.extend(local_pdf_inventory_clauses(url,edition))
                except Exception as exc: issues.append('Jogszabályi PDF feldolgozása nem sikerült: '+str(exc))
    result=build_inventory(clauses,zone,zone_verified,source_verified)
    result['errors'].extend(issues)
    if not clauses:
        result['errors'].append('Nem sikerült teljes, hivatkozható HTML-rendelkezéseket kinyerni. A jogszabályi PDF-mellékletek külön feldolgozást igényelnek.')
    if not edition:
        result['errors'].append('A forrás egyértelmű időállapota nem olvasható ki.')
    return result


def tisza_local_rules(html,source_url,zone,zone_verified=False):
    """Edition-checked local clauses. Spatial conditions are never inferred from zoning."""
    import hashlib
    out={'ok':False,'rows':[],'conditional':[],'error':'','checks':[]}
    parsed=urllib.parse.urlparse(source_url or '')
    if (not zone_verified or parsed.scheme!='https' or parsed.netloc!='njt.jog.gov.hu'
            or parsed.path!='/jogszabaly/2018-11-SP-5Y1228'
            or not isinstance(zone,str) or not re.fullmatch(r'Gip/[1-3]|Gksz/(?:[1-9]|1[0-5]|g)',zone)):
        out['error']='Ehhez az övezethez nincs ellenőrzött helyi szöveges szabálykapcsolat.';return out
    reader=TiszaLegalParagraphs();reader.feed(html or '')
    if reader.editions!=['2024.10.10.']:
        out['error']='A rendelet időállapota eltér az ellenőrzött kiadástól; a helyi szöveges szabályok új ellenőrzése szükséges.';return out
    sections=(7,8,11,12,26,27,28,29,30)
    for section in sections:
        content='\n'.join(str(b)+':'+clean_text(' '.join(parts))
            for (n,b),parts in reader.blocks.items() if n==section)
        if (section in reader.duplicates or not content
                or hashlib.sha256(content.encode()).hexdigest()!=TISZA_RULE_SECTION_HASHES.get(section)):
            out['error']='A helyi szabályok szövege vagy szerkezete eltér az ellenőrzött forráskiadástól; új ellenőrzés szükséges.';return out
    def add(section,clause,category,title,summary,conditional=False):
        raw=clean_text(' '.join(reader.blocks[(section,clause)]))
        ref=f'{section}. §'+(f' ({clause})' if clause is not None else '')
        anchor=reader.anchors[(section,clause)]
        item={'Téma':category,'Előírás':summary,'Hatály':title,'Forrás':ref,
              'URL':TISZA_RULES_URL+'#'+urllib.parse.quote(anchor,safe='.@()'),
              'Forrásszöveg':raw}
        out['conditional' if conditional else 'rows'].append(item)
    add(12,None,'Megengedett építmények','Valamennyi építési övezet',
        'A helyi szabályzat felsorolja a valamennyi övezetben elhelyezhető köztárgyakat, műtárgyakat, közműveket és további építményeket. A teljes felsorolás a forrásszövegben olvasható.')
    add(26,None,'Magassági eltérés','Gazdasági területek',
        'A maximális épületmagasság a technológiai indokoltság mértékéig túlléphető. Ez feltételhez kötött eltérés, nem általános többletmagasság.')
    add(7,5,'Építési hely','Általános szabály',
        'Terepszint alatti építmény csak az építési helyen belül engedélyezhető; kivétel a közmű és a közműépítmény.')
    add(7,10,'Vízvédelem','Tiszaújváros teljes területe',
        'A település teljes területe a felszín alatti vizek állapota szempontjából fokozottan érzékeny.')
    add(11,1,'Előkert','Újonnan kiszabályozott telektömb esetén',
        'Az előkert 5 m újonnan kiszabályozott telektömbben. A telektömb állapotát külön ellenőrizni kell.')
    add(11,2,'Oldalkert','Ha a terv vagy a TÉSZ másként nem rendelkezik',
        'Oldalhatáron álló beépítésnél legalább 4 m. Szabadon álló beépítésnél az épületmagasságra hivatkozó szabály és legalább 3 m szerepel a forrásban; a pontos szöveg és az esetleges eltérő tervi előírás együtt vizsgálandó.')
    add(11,3,'Elő- és hátsókert','Kialakult állapot vizsgálatakor',
        'A kialakult értéket felülírhatja a telepítési távolság, a benapozás és az utcakép védelme. A hátsókertre ebből nem vezethető le egyetlen általános méterérték.')
    add(8,2,'Telekalakítás','Közterületi kiszabályozás miatti telekcsökkenés esetén',
        'Az eredeti telekméret szerinti beépítés feltételesen lehetséges, ha a visszamaradó telek eléri az övezeti minimum 75%-át, és az elhelyezési szabályok teljesülnek.')
    if zone.startswith('Gip/'):
        # This category follows the edition-checked 29. § (1), not the
        # abbreviation alone. It does not select an OTÉK/TÉKA time state.
        out['industrial_type']={
            'kind':'significant','zone':zone,
            'label':'Környezetére jelentős hatást gyakorló ipar',
            'Forrás':'TÉSZ 29. § (1)',
            'URL':TISZA_RULES_URL+'#'+urllib.parse.quote(reader.anchors[(29,1)],safe='.@()')}
        add(29,1,'Megengedett rendeltetések','Gip övezetek',
            'Az OTÉK-ban felsoroltakon túl igazgatási épület, irodaépület, parkolóház és üzemanyagtöltő helyezhető el. Ez a helyi kiegészítés, nem a teljes országos rendeltetési lista.')
        add(29,2,'Tiltott melléképítmények','Gip övezetek',
            'Nem helyezhető el háztartási célú kemence, húsfüstölő, jégverem, zöldségverem, állatól és állatkifutó.')
        add(29,3,'Közművesítés','Gip övezetek','A beépítés feltétele a teljes közművesítés megléte.')
        add(29,4,'Beültetés','Az előírásban megnevezett telekhatárok mentén',
            'A közterületi, illetve lakó- és településközponti területtel határos telekhatár mentén legalább 10 m széles beültetési terület szükséges, ha a tervlap más értéket nem határoz meg. Az érintett határokat külön ellenőrizni kell.')
        add(29,5,'Telephely elhelyezése','Kijelölt veszélyességi, levegővédelmi vagy zajhatásterület esetén',
            'Az ilyen kijelöléssel rendelkező telephely kizárólag jelentős mértékű zavaró hatású ipari területen helyezhető el.')
        add(29,6,'Beépítettség számítása','Gip övezetek',
            'A csővezeték és a kábelköteg közmű-becsatlakozási műtárgy; nem számít bele a beépítettségbe.')
        for clause in range(1,7):
            add(30,clause,'TVK/MOL sajátos előírás','Területi érintettség külön ellenőrizendő',
                'A TVK Ipartelep / MOL Finomító egyesített övezetére vonatkozó sajátos feltételek. A Gip kód önmagában nem igazolja az alkalmazhatóságot.',True)
    else:
        add(27,1,'Tiltott melléképítmények','Gksz övezetek',
            'Nem helyezhető el háztartási célú kemence, húsfüstölő, jégverem, zöldségverem és trágyatároló.')
        add(27,2,'Közművesítés','Gksz övezetek','A beépítés feltétele a teljes közművesítés megléte.')
        if zone=='Gksz/1':
            add(28,1,'Sajátos tiltások','Kizárólag Gksz/1 (a szövegben Gksz-1)',
                'Lakófunkció nem helyezhető el. Meglévő épületen belül szakaszos építés nem engedélyezhető.')
        elif zone=='Gksz/g':
            add(28,2,'Sajátos rendeltetések és tiltások','Kizárólag Gksz/g',
                'Elsősorban garázs elhelyezésére szolgál. Lakó-, igazgatási és irodafunkció, üzemanyagtöltő, egyházi, oktatási, egészségügyi, szociális, közösségi szórakoztató és sportfunkció nem helyezhető el.')
    for clause in (11,12):
        add(7,clause,'Hidrogeológiai védelem','Védőidom / védőövezet érintettsége külön ellenőrizendő',
            'A hidrogeológiai védelem külön feltételeket írhat elő. A telek érintettségét az övezeti kód nem bizonyítja; a teljes feltételrendszer a forrásszövegben olvasható.',True)
    first_section='\n'.join(str(b)+':'+clean_text(' '.join(parts))
        for (n,b),parts in reader.blocks.items() if n==1)
    if (1 not in reader.duplicates and hashlib.sha256(first_section.encode()).hexdigest()
            =='290ad09885c35d9942e6e058fa13f36729d626ed90d0ef727d07d3d91bd70e40'):
        out['basis_evidence']={
            'Forrás':'TÉSZ 1. § (2)',
            'URL':TISZA_RULES_URL+'#'+urllib.parse.quote(reader.anchors[(1,2)],safe='.@()'),
            'Forrásszöveg':clean_text(' '.join(reader.blocks[(1,2)])),
            'Eredmény':'Az OTÉK-ra hivatkozás igazolt. Az alkalmazandó OTÉK-időállapotot ez a bekezdés nem határozza meg.'}
    out['zone']=zone
    out['checks']=['Az országos alkalmazási csomag és a rendeltetési részvizsgálat eredménye az alábbi fejezetekben olvasható; valamennyi építési követelmény teljesülése külön ellenőrizendő.',
        'A szabályozási/építési vonal, az építési hely, a beültetési sáv helye és a védőterületi érintettség nincs teljeskörűen automatikusan feldolgozva.',
        'A meglévő beépítés, a közműellátottság és a telekalakítási feltételek teljesülése külön ellenőrizendő.']
    out['ok']=True
    return out


def render_tisza_local_rules(result,zone,params):
    st.subheader('Mit lehet építeni? – helyi szabályok')
    st.write(f'**Az ellenőrzött övezet: {zone}.** Az alábbiak a TÉSZ helyi szabályai; az építési lehetőséget az országos és a tervi feltételekkel együtt kell megállapítani.')
    category=verified_local_industrial_type(result,zone)
    if category:
        st.write('**Helyi ipari típus:** '+category['label']+'.')
        st.link_button(category['Forrás']+' – ipari besorolás',category['URL'])
    if params:
        st.write('**Fő mutatók:** '+ '; '.join(f'{key}: {value}' for key,value in params.items())+'.')
    groups=(('Megengedett építmények és rendeltetések',lambda r:r['Téma'].startswith('Megengedett')),
            ('Tiltások',lambda r:'tilt' in r['Téma'].casefold()),
            ('További feltételek',lambda r:not r['Téma'].startswith('Megengedett') and 'tilt' not in r['Téma'].casefold()))
    for title,select in groups:
        selected=[r for r in result['rows'] if select(r)]
        if selected:
            st.markdown('**'+title+'**')
            st.dataframe([{k:r[k] for k in ('Téma','Előírás','Hatály','Forrás')} for r in selected],hide_index=True,use_container_width=True)
    if zone.startswith('Gksz/'):
        st.info('A teljes főrendeltetési lista nem állapítható meg a helyi 27. §-ból önmagában. Az országos szabályok külön ellenőrzendők; a más alövezetre írt tilalmat a program nem alkalmazza erre az övezetre.')
    with st.expander('Pontos jogszabályhelyek és teljes forrásszöveg'):
        for row in result['rows']:
            st.markdown(f"**{row['Téma']} – {row['Hatály']}**")
            st.link_button(row['Forrás']+' – NJT',row['URL'])
            st.write(row['Forrásszöveg'])
    with st.expander('Területi érintettségtől függő szabályok – még ellenőrizendők'):
        st.write('Ezek alkalmazhatóságát az övezeti kód önmagában nem igazolja. A program nem állítja, hogy a telek érintett.')
        for row in result['conditional']:
            st.markdown(f"**{row['Téma']} – {row['Forrás']}**")
            st.link_button(row['Forrás']+' – NJT',row['URL'])
            st.write(row['Forrásszöveg'])
    st.markdown('**Mi szükséges még a teljes építési válaszhoz?**')
    for check in result['checks']:st.write('• '+check)



NATIONAL_RULE_PROFILES = {'otek2012': {'document': '1997-253-20-22',
              'url': 'https://njt.jog.gov.hu/jogszabaly/1997-253-20-22.31',
              'edition': '2012.06.29.',
              'hashes': {19: 'f434a92119077d78e0afc396813bd0c739304aa59d75c60b70c036a4ff6c4c09',
                         20: '76c1a503bee2965444eb2041ce9bdf1dd7d18707db23ac59b52413ce3ec1caba'},
              'label': 'OTÉK – 2012. augusztus 6-i állapot',
              'basis': 'TÉKA 136. § (1) a): a terv OTÉK 2012. augusztus 6-i követelményei és jelmagyarázata '
                       'alapján készült.'},
 'otek2021': {'document': '1997-253-20-22',
              'url': 'https://njt.jog.gov.hu/jogszabaly/1997-253-20-22.53',
              'edition': '2021.04.22.',
              'hashes': {19: '0a126889cd33cf1faadb43fbcc3c37b5037a1ad11c24301fda67ea16783ec3c2',
                         20: '96a57fcec2198c20034d0d6c1dcdb251826b02163061c81ae90573b6a8cb6aef'},
              'label': 'OTÉK – 2021. július 15-i állapot',
              'basis': 'TÉKA 136. § (1) b): 2012 utáni OTÉK-követelmények és a 314/2012. rendelet '
                       'jelmagyarázata alapján készült terv.'},
 'otek2024': {'document': '1997-253-20-22',
              'url': 'https://njt.jog.gov.hu/jogszabaly/1997-253-20-22',
              'edition': '2024.01.01.',
              'hashes': {19: 'bb596ac03bed200bb1dac2cb2abc6991436cd042026c88ba299956c9f886cbfa',
                         '19/A': 'fa4ec92bb96207841b7b08e4046b66ee9b0a2a4bdab6fe847d4e72d61d8986d1',
                         20: '3b80b7af56f6dc4e8214ef3a7ad229e9b58db7f0ef969b65429d8febd7d47a73'},
              'label': 'OTÉK – 2024. december 31-i állapot',
              'basis': 'TÉKA 136. § (1) c): 2021 utáni OTÉK-követelmények és a településtervezési szabályzat '
                       'jelmagyarázata alapján készült terv; illetve a 137/A. § szerinti korábbi ügyek.'},
 'teka': {'document': '2024-280-20-22',
          'url': 'https://njt.jog.gov.hu/jogszabaly/2024-280-20-22',
          'edition': '2026.02.27.',
          'hashes': {21: '95381ed1fcbb8dd1c0036cc4dfc7a1e76d7c4430a23ac504700fd3b9534c4e83',
                     23: '416a4d00c5198d1e260ab8377ca93095a48369c8b2651eac077360e2105b773d',
                     24: 'ed31d487c5c52e3e8b9f1b490036f975efc4e910b39a739abcc82b7d7907eb33',
                     50: 'da4904d6e8f4c89372a020c7bdc6d895aa2beb6dd3193ff2203893d91286a825',
                     51: 'b6feee39733bd89e31d0bb3da9f08a863cab5dfce730702d705dce6c25bcd4a0',
                     52: '72cb341ecea0bd85818c8b6cd4d729ed80d3c261cfb5ea99f2b9bef46302e904',
                     59: 'b2f2c191c0deb0d300df132b084af15ccf66ce8d51e98b491af20abda350491a',
                     60: 'a58807b28012bc94176fff816fcd7ec7815e5d11796e3b685bd53ced97302b51',
                     136: '279f0b4598a01e654070022052be22417f346701f7900ae2481b967338863622',
                     '137/A': '5115e6a1dbe1838d55def08a7788f1c0cb472977036abc4476c2d386ddff231b',
                     138: '276277578723c8fd9867d149d881eadd2868850e67a250833ad085c04532fcd7',
                     139: '9c20201cb2e3f1d91bf3f0822895b2f11717fb088f5e5d41152024c5b71764d0'},
          'label': 'TÉKA – ellenőrzött 2026. február 27-i kiadás',
          'basis': 'TÉKA 136. § (1) d): a TÉKA alapján készült terv; egyes rendelkezések más tervi alap '
                   'mellett is alkalmazandók a 136. § (2) és 139. § (2) feltételeivel.'}}


def verified_local_industrial_type(local_rules,zone):
    """Carry category evidence only with the exact verified local zone."""
    if not isinstance(local_rules,dict) or not local_rules.get('ok') or local_rules.get('zone')!=zone:
        return None
    category=local_rules.get('industrial_type')
    if (not isinstance(category,dict) or category.get('kind')!='significant'
            or category.get('zone')!=zone or not re.fullmatch(r'Gip/[1-3]',zone)
            or category.get('Forrás')!='TÉSZ 29. § (1)'
            or not category.get('URL','').startswith(TISZA_RULES_URL+'#')):
        return None
    return category


class TekaParkingAnnex(HTMLParser):
    """Read the complete ME4 region; retain native paragraph anchors."""
    def __init__(self):
        super().__init__()
        self.active = False
        self.starts = 0
        self.ends = 0
        self.parts = []
        self.sup_notes = []
        self.skip = 0
        self.anchor = ''
        self.rows = {}
        self.seen_anchors = set()
        self.duplicates = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'span' and 'jhId' in attrs.get('class', '').split():
            marker = attrs.get('id', '')
            if marker == 'ME4':
                self.starts += 1
                self.active = True
            elif marker == 'ME5':
                self.ends += 1
                self.active = False
            if self.active and marker.startswith('RR'):
                if marker in self.seen_anchors: self.duplicates = True
                self.seen_anchors.add(marker)
                self.anchor = marker
        if tag == 'sup':
            note = 'fnSup' in attrs.get('class', '').split() or 'data-note-id' in attrs
            self.sup_notes.append(note)
            if note: self.skip += 1
        elif tag in ('script', 'style'): self.skip += 1

    def handle_endtag(self, tag):
        if tag == 'sup':
            if self.sup_notes and self.sup_notes.pop(): self.skip = max(0, self.skip - 1)
        elif tag in ('script', 'style'): self.skip = max(0, self.skip - 1)
        if self.active and tag in ('p', 'div'): self.parts.append(' ')

    def handle_data(self, data):
        if self.active and not self.skip:
            if self.sup_notes and not self.sup_notes[-1]: data = data.translate(str.maketrans('23', '²³'))
            self.parts.append(data)
            if self.anchor: self.rows.setdefault(self.anchor, []).append(data)


TEKA_PARKING_ANNEX_SHA256 = '3b73b7e55080c75e58cc52edd6845d8ba8f00983c1bc24f123763b71224dc728'
TEKA_PARKING_POINT_SHA256 = {
    'RR5805': '06cdaf5b8e36d05057d993b93ac1a571bc1838b6875a748502d1844860042031',
    'RR5808': '20e39460b95a00cb9f28700463acea0a0074d8ebcccbf6a37ffd24c19e3f2c5b',
}


def verify_teka_parking_annex(page):
    import hashlib
    out = {'ok': False, 'error': 'A parkolási melléklet teljes szövege vagy szerkezete nincs igazolva.', 'sources': []}
    if not page.get('ok') or page.get('url') != NATIONAL_RULE_PROFILES['teka']['url']: return out
    edition = TiszaLegalParagraphs('2024-280-20-22'); edition.feed(page.get('html', ''))
    if edition.editions != [NATIONAL_RULE_PROFILES['teka']['edition']]: return out
    reader = TekaParkingAnnex(); reader.feed(page.get('html', ''))
    text = clean_text(''.join(reader.parts))
    if (reader.starts != 1 or reader.ends != 1 or reader.duplicates
            or hashlib.sha256(text.encode()).hexdigest() != TEKA_PARKING_ANNEX_SHA256): return out
    sources = []
    for anchor, ref in [('RR5805', '4. melléklet I. 11. pont'), ('RR5808', '4. melléklet I. 14. pont')]:
        if anchor not in reader.rows: return out
        source_text = clean_text(''.join(reader.rows[anchor]))
        if hashlib.sha256(source_text.encode()).hexdigest() != TEKA_PARKING_POINT_SHA256[anchor]: return out
        sources.append({'Forrás': ref, 'Forrásszöveg': source_text,
                        'URL': page['url'] + '#' + anchor})
    out.update(ok=True, error='', sources=sources, full_text=text, URL=page['url']+'#ME4')
    return out


def national_rule_profile(key, page, zone, local_verified=False, local_rules=None):
    """Verify a complete official edition; return conditional source rows, never building permission."""
    import hashlib
    result={'ok':False,'rows':[],'transition':[],'comparison':[],'green':[],'parking':[],'error':'','key':key}
    cfg=NATIONAL_RULE_PROFILES.get(key)
    if not cfg or not local_verified or not isinstance(zone,str) or not re.fullmatch(r'Gip/[1-3]|Gksz/(?:[1-9]|1[0-5]|g)',zone):
        result['error']='Nincs ellenőrzött helyi övezeti kapcsolat az országos szabályokhoz.';return result
    if not page.get('ok') or page.get('url')!=cfg['url']:
        result['error']='Az országos jogszabály ellenőrzött NJT-forrása nem érhető el.';return result
    reader=TiszaLegalParagraphs(cfg['document']);reader.feed(page.get('html',''))
    if reader.editions!=[cfg['edition']]:
        result['error']='Az országos jogszabály időállapota megváltozott; új forrásellenőrzés szükséges.';return result
    for section,expected in cfg['hashes'].items():
        text='\n'.join(str(b)+':'+clean_text(' '.join(parts)) for (n,b),parts in reader.blocks.items() if n==section)
        if section in reader.duplicates or hashlib.sha256(text.encode()).hexdigest()!=expected:
            result['error']='Az országos szabály szövege vagy szerkezete eltér az ellenőrzött kiadástól; új ellenőrzés szükséges.';return result
    sections=([21] if zone.startswith('Gksz/') else [23,24]) if key=='teka' else ([19] if zone.startswith('Gksz/') else ([20] if key in ('otek2012','otek2021') else ['19/A',20]))
    category=verified_local_industrial_type(local_rules,zone)
    for (section,clause),parts in reader.blocks.items():
        transition=key=='teka' and section in (136,'137/A',138,139)
        green=key=='teka' and section in (50,51,52)
        parking=key=='teka' and section in (59,60)
        if section not in sections and not transition and not green and not parking:continue
        text=clean_text(' '.join(parts))
        # Repealed clauses have a number but no operative text.
        if re.fullmatch(r'\(?\d+\)?',text):continue
        ref=f'{section}. §'+(f' ({clause})' if clause is not None else '')
        scope='Alkalmazási és átmeneti rendelkezés' if transition else ('Kereskedelmi, szolgáltató gazdasági terület' if zone.startswith('Gksz/') else ('Ipari és egyéb ipari típus: a pontos besorolás külön igazolandó'))
        if parking:scope='Gépjármű-elhelyezés és parkolók kialakítása'
        if green:scope='Zöldfelületi, fásítási és elhelyezési feltételek'
        if not transition and not green and not parking and zone.startswith('Gip/'):
            scope=({'19/A':'Ipari gazdasági terület – jelentős környezeti hatás',20:'Egyéb ipari gazdasági terület'} if key=='otek2024' else {23:'Ipari gazdasági terület',24:'Egyéb ipari gazdasági terület'} if key=='teka' else {20:'Ipari gazdasági terület – altípus is igazolandó'}).get(section,scope)+'; a pontos besorolás külön igazolandó'
        row={'Területtípus':scope,'Forrás':ref,'Forrásszöveg':text,
             'URL':cfg['url']+'#'+urllib.parse.quote(reader.anchors[(section,clause)],safe='.@()')}
        other_type=(category and not transition and
            ((key=='teka' and section==24) or (key=='otek2024' and section==20)
             or (key in ('otek2012','otek2021') and section==20 and clause==4)))
        if other_type:
            row['Kapcsolat']='Más ipari típus: a helyi Gip-besoroláshoz nem kapcsolt összehasonlító forrás.'
        elif category and not transition and not green and not parking:
            row['Területtípus']=category['label']+' – az időállapot alkalmazhatósága még igazolandó'
            if key in ('otek2012','otek2021') and section==20 and clause==2:
                row['Kapcsolat']='Általános típusfelsorolás; a helyi besorolás a jelentős hatású típus.'
            elif key in ('otek2012','otek2021') and section==20 and clause==5:
                row['Kapcsolat']=('A jelentős zavaró hatású területet a felsorolt kivételes megengedésekből kizárja.'
                    if key=='otek2012' else 'Jelentős hatású területen lakás nem helyezhető el; az egyéb ipari típusra írt lakásmegengedés nem kapcsolható ide.')
            else:
                row['Kapcsolat']='A helyi ipari típushoz kapcsolt, feltételes országos szabály.'
        result['parking' if parking else 'green' if green else 'transition' if transition else 'comparison' if other_type else 'rows'].append(row)
    result['industrial_type']=category
    result.update(ok=True,label=cfg['label'],basis=cfg['basis'])
    if key == 'teka': result['parking_annex'] = verify_teka_parking_annex(page)
    return result


def load_national_rules(zone,local_verified,local_rules=None):
    # Independent source requests share existing NJT caching and bounded timeouts.
    from concurrent.futures import ThreadPoolExecutor
    keys=list(NATIONAL_RULE_PROFILES)
    def load(key):
        try:page=fetch_njt_page(NATIONAL_RULE_PROFILES[key]['url'])
        except Exception:page={}
        return national_rule_profile(key,page,zone,local_verified,local_rules)
    if not local_verified:return []
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(load,keys))



TISZA_BASIS_DOCUMENTS = (
    ('https://tiszaujvaros.hu/images/doks/teszkoz/TISZAUJVAROS_2023_TOBB_RESZTER_TERVIRATOK_JOVAHAGYOTT.pdf',
     '299c7ddac1bdcf51a755fe67c889e2df36815de27d6d3140640323d3ab217c02'),
    ('https://www.tiszaujvaros.hu/images/doks/teszkoz/TISZAUJVAROS_2023_TOBB_RESZTER_JOVAHAGYOTT.pdf',
     'bc380a7f3ca8c1ae329decbe9c73eceaf32b6aa416c7a82dd0970a19b36c505d'),
)


def verify_tisza_plan_basis(paths, plan_sha, local_rules, zone):
    import hashlib
    out = {'ok': False, 'key': '', 'error': '', 'sources': []}
    if (plan_sha != TISZA_PLAN_SHA256 or not local_rules.get('ok')
            or local_rules.get('zone') != zone or not local_rules.get('basis_evidence')):
        out['error'] = 'A tervi alaphoz nincs azonos kiadású, ellenőrzött terv és helyi szabálykapcsolat.'
        return out
    if len(paths) != len(TISZA_BASIS_DOCUMENTS):
        out['error'] = 'A tervi alap forráslánca hiányos.'
        return out
    try:
        for path, (url, expected) in zip(paths, TISZA_BASIS_DOCUMENTS):
            digest = hashlib.sha256()
            with open(path, 'rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                out['error'] = 'A jóváhagyott tervirat kiadása megváltozott; új forrásellenőrzés szükséges.'
                return out
        with fitz.open(paths[0]) as records, fitz.open(paths[1]) as approved:
            chief = clean_text(records[10].get_text())
            cover = clean_text(approved[0].get_text())
            annex = clean_text(approved[61].get_text())
            if (len(records) != 194 or len(approved) != 123
                    or '314/2012.' not in chief or '2021. JÚLIUS' not in chief
                    or '12/2024.' not in cover or '12/2024.' not in annex):
                out['error'] = 'A tervi alap dokumentumszerkezete nem egyezik.'
                return out
        # Reviewed link: approved pages 64–118 and current NJT pages 2–56
        # have identical decoded drawing content streams on all 55 pages.
        out.update(ok=True, key='otek2021',
            label='OTÉK 2021. július 15-i II–III. fejezet – TÉKA 136. § (1) b)',
            proof='A 12/2024. (IX. 27.) rendelettel jóváhagyott, a hatályos NJT-tervvel azonos rajzi anyag; 314/2012. szerinti követelmények és jelmagyarázat, 2021. július 15-ig hatályos OTÉK-alap.',
            sources=[{'Forrás': 'Jóváhagyott módosítás – címoldal és 1. melléklet', 'URL': TISZA_BASIS_DOCUMENTS[1][0] + '#page=62'},
                     {'Forrás': 'Főépítészi feljegyzés 1.2–1.3. pont', 'URL': TISZA_BASIS_DOCUMENTS[0][0] + '#page=11'}])
    except Exception as exc:
        out['error'] = 'A tervi alap forrásellenőrzése nem sikerült: ' + str(exc)
    return out


@st.cache_data(show_spinner=False, ttl=3600, max_entries=2)
def download_tisza_basis_paths():
    # Sequential downloads limit peak memory; the cache stores paths only.
    return [download_pdf_location(url)[0] for url, digest in TISZA_BASIS_DOCUMENTS]


def load_tisza_plan_basis(local_rules, zone, plan_sha):
    if (plan_sha != TISZA_PLAN_SHA256 or not local_rules.get('ok')
            or local_rules.get('zone') != zone):
        return verify_tisza_plan_basis([], plan_sha, local_rules, zone)
    try:
        paths = download_tisza_basis_paths()
        return verify_tisza_plan_basis(paths, plan_sha, local_rules, zone)
    except Exception as exc:
        return {'ok': False, 'key': '', 'sources': [],
                'error': 'A tervi alap hivatalos dokumentumai nem tölthetők le: ' + str(exc)}



def verify_previous_teka_overlay(page):
    import hashlib
    url = 'https://njt.jog.gov.hu/jogszabaly/2024-280-20-22.5'
    out = {'ok': False, 'error': '', 'row': None}
    if not page.get('ok') or page.get('url') != url:
        out['error'] = 'A korábbi TÉKA-kiegészítések hivatalos forrása nem érhető el.'
        return out
    reader = TiszaLegalParagraphs('2024-280-20-22')
    reader.feed(page.get('html', ''))
    hashes = {136: '9d19035a033dfe6f10ea82b0544a870c20288947b0fba763e1ec403fb4f460bb',
              138: '276277578723c8fd9867d149d881eadd2868850e67a250833ad085c04532fcd7'}
    if reader.editions != ['2025.12.24.']:
        out['error'] = 'A korábbi TÉKA-forrás időállapota nem egyezik.'
        return out
    for section, expected in hashes.items():
        text = '\n'.join(str(b) + ':' + clean_text(' '.join(parts))
                         for (n, b), parts in reader.blocks.items() if n == section)
        if section in reader.duplicates or hashlib.sha256(text.encode()).hexdigest() != expected:
            out['error'] = 'A korábbi TÉKA-forrás szövege vagy szerkezete eltér.'
            return out
    out.update(ok=True, row={'Forrás': '136. § (2) – 2026. január 14. előtti szöveg',
                              'Forrásszöveg': clean_text(' '.join(reader.blocks[(136, 2)])),
                              'URL': url + '#SZ136@BE2'})
    return out


def load_previous_teka_overlay():
    try:
        return verify_previous_teka_overlay(fetch_njt_page('https://njt.jog.gov.hu/jogszabaly/2024-280-20-22.5'))
    except Exception as exc:
        return {'ok': False, 'error': 'A korábbi TÉKA-forrás betöltése nem sikerült: ' + str(exc), 'row': None}


def select_national_applicability(case, plan_basis, national_rules, today=None, previous_overlay=None):
    """Select a scoped legal package, not an unconditional building permission."""
    today = today or datetime.now(ZoneInfo('Europe/Budapest')).date()
    case = case or {}
    out = {'ok': False, 'base_key': '', 'overlay': False, 'reasons': [],
           'sources': [], 'missing': [], 'assumptions': []}
    profiles = {r.get('key'): r for r in national_rules if r.get('ok')}
    teka = profiles.get('teka')
    if not teka:
        out['missing'].append('Az átmeneti szabályok ellenőrzött TÉKA-forrása.')
        return out
    refs = {r['Forrás']: r for r in teka.get('transition', [])}
    required_refs = ('136. § (1)', '136. § (2)', '136. § (4)', '137/A. §', '138. §', '139. § (2)')
    if any(ref not in refs for ref in required_refs):
        out['missing'].append('A kiválasztáshoz szükséges teljes átmeneti forráslánc.')
        return out
    prospective = case.get('scenario') == 'prospective'
    if prospective:
        started, event, prior = today, 'Hatósági eljárás kezdete', 'Nincs'
        out['assumptions'].append('Új, a vizsgálat napján induló hatósági eljárás; korábbi engedély vagy bejelentés nélkül. Ez vizsgálati forgatókönyv, nem igazolt ügytörténet.')
    else:
        started, event, prior = case.get('started'), case.get('event'), case.get('prior')
        issues = case_data_review(case, today)['issues']
        if issues:
            out['missing'].extend(issues)
            return out
        if case.get('current_status') != 'Folyamatban / jelenleg vizsgált cselekmény':
            out['missing'].append('Az ügy jelenlegi állapota. Lezárt ügy történeti vizsgálatához a korabeli teljes forráskiadás szükséges.')
        if not isinstance(started, date) or started > today:
            out['missing'].append('A tényleges kezdő esemény dátuma.')
        if prior not in ('Van', 'Nincs'):
            out['missing'].append('Van-e korábbi engedély vagy bejelentés?')
        if out['missing']:
            return out
    authority = 'Hatósági eljárás kezdete'
    doc = 'Engedélyhez / bejelentéshez nem kötött kivitelezési dokumentáció átadása'
    activity = 'Kivitelezési dokumentációhoz nem kötött építési tevékenység kezdete'
    council = 'Építészeti tervtanácsi vagy egyéb eljárás kezdete'
    use = 'Területhasználat kezdete'
    if event not in (authority, doc, activity, council, use):
        out['missing'].append('A kezdő esemény pontos típusa és az engedély-/dokumentációkötöttség.')
        return out
    cutoff = date(2025, 7, 1)
    old_permit = False
    binding = False
    if prior == 'Van':
        previous = case.get('prior_date')
        if not isinstance(previous, date):
            out['missing'].append('A korábbi engedély vagy bejelentés dátuma.')
            return out
        old_permit = previous < cutoff
        deviation = case.get('deviation')
        if old_permit:
            if deviation not in ('Nincs eltérés', 'Engedély- vagy bejelentésköteles eltérés', 'Engedélyhez / bejelentéshez nem kötött eltérés'):
                out['missing'].append('A korábbi dokumentációtól való eltérés pontos kötöttsége.')
                return out
            binding = deviation == 'Engedély- vagy bejelentésköteles eltérés'
            if binding and case.get('occupied') not in ('Használatba vett', 'Nem vették használatba'):
                out['missing'].append('A 2025. július 1-jei használatbavételi állapot.')
                return out
    def choose(key, reason, source_ref):
        if key not in profiles:
            out['missing'].append('A kiválasztott országos forráskiadás ellenőrzése: ' + key)
            return False
        out.update(base_key=key)
        out['reasons'].append(reason)
        out['sources'].append(refs[source_ref])
        return True
    special_binding = old_permit and binding and case.get('occupied') == 'Nem vették használatba'
    grandfather = (not special_binding and
        ((started < cutoff and event in (authority, doc, activity))
         or (old_permit and case.get('deviation') == 'Engedélyhez / bejelentéshez nem kötött eltérés')))
    if grandfather:
        if choose('otek2024', 'Korábbi ügy vagy nem köteles eltérés: OTÉK 2024. december 31-i szöveg, a 137/A. § alapján.', '137/A. §'):
            out['sources'].append(refs['138. §'])
            out['ok'] = True
        return out
    if started < date(2025, 1, 1):
        out['missing'].append('A 2025 előtt kezdett, a 137/A. §-ba nem sorolható ügy korabeli szabályozása.')
        return out
    if not plan_basis.get('ok') or plan_basis.get('key') not in ('otek2012', 'otek2021', 'otek2024', 'teka'):
        out['missing'].append(plan_basis.get('error') or 'A terv készítési követelményei és jelmagyarázata.')
        return out
    key = plan_basis['key']
    if not choose(key, 'A terv igazolt készítési alapja alapján: ' + NATIONAL_RULE_PROFILES[key]['label'] + (' teljes rendelet.' if key == 'teka' else ' II–III. fejezet és mellékletek.'), '136. § (1)'):
        return out
    if special_binding:
        out['reasons'].append('A régi engedélytől / bejelentéstől köteles eltérésre TÉKA is alkalmazandó, a 136. § (1)–(3) figyelembevételével; ez nem írja át a teljes eredeti engedély szabályozását.')
        out['sources'].append(refs['136. § (4)'])
    if started >= cutoff:
        out['overlay'] = True
        if started < date(2026, 1, 14):
            if event not in (authority, council):
                out['missing'].append('Eljárás nélküli, 2026. január 14. előtti cselekménynél az átmeneti időbeli hatály külön vizsgálandó.')
                out['sources'].append(refs['139. § (2)'])
                return out
            if not (previous_overlay or {}).get('ok'):
                out['missing'].append((previous_overlay or {}).get('error') or 'A 2026. január 14. előtti TÉKA 136. § (2) ellenőrzött szövege.')
                return out
            out['reasons'].append('A korábban indult eljárásnál a 136. § (2) korábbi szövege szerinti kötelező TÉKA-kiegészítések tartoznak az OTÉK-alaphoz; a módosított felsorolás nem kerül automatikusan erre az ügyre.')
            out['sources'].extend([previous_overlay['row'], refs['138. §'], refs['139. § (2)']])
        else:
            out['reasons'].append('A TÉKA 136. § (2)-ben felsorolt rendelkezéseit a helyi terv korától függetlenül is alkalmazni kell; a csomag OTÉK-alap és kötelező TÉKA-kiegészítések együttese.')
            out['sources'].extend([refs['136. § (2)'], refs['139. § (2)']])
    elif special_binding:
        out['missing'].append('A köteles eltérés kezdete és korabeli TÉKA-szövege külön ellenőrizendő.')
        return out
    out['ok'] = True
    return out


def render_applicability(result, plan_basis):
    st.subheader('Automatikusan kiválasztott országos szabályozás')
    for assumption in result['assumptions']:
        st.caption(assumption)
    if plan_basis.get('ok'):
        st.write('**Tervi alap forrásból igazolva:** ' + plan_basis['label'])
        for source in plan_basis['sources']:
            st.link_button(source['Forrás'], source['URL'])
    if result['ok']:
        label = NATIONAL_RULE_PROFILES[result['base_key']]['label']
        st.success(label + (' + kötelező TÉKA-kiegészítések' if result['overlay'] else ''))
        st.caption('A kiválasztás a vizsgálati forgatókönyvre vagy a megadott ügyadatokra érvényes. A konkrét építési jogosultságot a helyi, országos és telekspecifikus feltételek együtt határozzák meg.')
    else:
        st.warning('Az alkalmazási csomag nem választható ki teljesen: ' + '; '.join(result['missing']))
    for reason in result['reasons']:
        st.write(reason)
    with st.expander('A kiválasztás pontos átmeneti jogszabályhelyei'):
        for row in result['sources']:
            st.link_button(row['Forrás'] + ' – kiválasztási alap', row['URL'])
            st.write(row['Forrásszöveg'])


def render_national_rules(results,zone,local_rules=None,applicability=None):
    st.subheader('Országos rendeltetési szabályok – alkalmazási alap ellenőrzése')
    selected = (applicability or {}).get('ok', False)
    if not selected:
        st.info('A telekre és ügyre alkalmazandó teljes országos szabályozási csomag még nem igazolt. A forrásváltozatok feltételesek.')
    else:
        st.info('A kiválasztott alkalmazási csomag fent olvasható. Az alábbi forrásjegyzék a kiválasztott alapot és a többi átmeneti út ellenőrzött forrásait tartalmazza.')
    if not selected:
        st.write('A kiválasztáshoz szükséges hiányzó adatok a kiválasztási eredménynél szerepelnek. A helyi rendelet évszáma és a Gip/Gksz kód önmagában nem elegendő.')
    category=verified_local_industrial_type(local_rules,zone)
    evidence=(local_rules or {}).get('basis_evidence') if (local_rules or {}).get('ok') else None
    if evidence:
        st.write('**A helyi forrásból már igazolt:** '+evidence['Eredmény'])
        st.link_button(evidence['Forrás']+' – országos hivatkozás',evidence['URL'])
    if category:
        st.write('**Ipari besorolás igazolva:** '+category['label']+'. Az egyéb ipari típus sajátos megengedései külön összehasonlító forrásként szerepelnek.')
    elif zone.startswith('Gip/'):
        st.write('**Ipari besorolás:** a jelentős környezeti hatású és az egyéb ipari terület eltérő szabályokat kap. A két típus rendeltetési listája nem vonható össze.')
    status=[]
    for result in results:
        cfg=NATIONAL_RULE_PROFILES[result['key']]
        status.append({'Forráskiadás':cfg['label'],'Ellenőrzés':'teljes szöveg ellenőrizve' if result['ok'] else result['error'],
                       'Telekre alkalmazható?':('kiválasztott alap' if selected and result['key']==applicability['base_key'] else 'kötelező kiegészítések forrása' if selected and result['key']=='teka' and applicability['overlay'] else 'másik alkalmazási út forrása') if result['ok'] else 'nem állapítható meg'})
    st.dataframe(status,hide_index=True,use_container_width=True)
    for result in results:
        cfg=NATIONAL_RULE_PROFILES[result['key']]
        with st.expander(cfg['label']+' – feltételes rendeltetési szabályok'):
            st.write(cfg['basis'])
            if not result['ok']:
                st.warning(result['error']);continue
            for row in result['rows']:
                scope = row['Területtípus']
                if selected and result['key']==applicability['base_key']:
                    scope=scope.replace('az időállapot alkalmazhatósága még igazolandó', 'a kiválasztott alap rendeltetési szabálya')
                st.markdown('**'+scope+' – '+row['Forrás']+'**')
                if row.get('Kapcsolat'):st.write(row['Kapcsolat'])
                st.link_button(row['Forrás']+' – NJT',row['URL']);st.write(row['Forrásszöveg'])
        if result.get('comparison'):
            with st.expander(cfg['label']+' – más ipari típus, összehasonlítás'):
                st.write('Ezek az előírások az egyéb ipari típushoz tartoznak. A helyi, jelentős hatású Gip-besorolás megengedett rendeltetéseit a program nem egészíti ki velük.')
                for row in result['comparison']:
                    st.link_button(row['Forrás']+' – NJT',row['URL']);st.write(row['Forrásszöveg'])
    teka=next((r for r in results if r['key']=='teka' and r['ok']),None)
    if teka:
        with st.expander('OTÉK/TÉKA átmenet – teljes alkalmazási szabályok'):
            st.write('A 136. § (2) egyes TÉKA-szabályokat a terv korától függetlenül előír, de csak az ott meghatározott ügyekben. A módosított rendelkezés alkalmazásának időbeli hatályát a 139. § (2) is meghatározza. Korábbi ügyeknél a 137/A. § és az eltérésre vonatkozó 136. § (4) is ellenőrizendő.')
            for row in teka['transition']:
                st.link_button(row['Forrás']+' – NJT',row['URL']);st.write(row['Forrásszöveg'])



def buildability_summary(local_rules, national_rules, zone):
    """Keep local conclusions separate from unresolved national and spatial scope."""
    out={'ok':False,'rows':[],'paths':[],'missing':[]}
    if not local_rules.get('ok'):
        return out
    for row in local_rules['rows']:
        topic=row['Téma']
        category=('Helyi megengedés – további feltételekkel' if topic.startswith('Megengedett')
                  else 'Helyi tiltás' if 'tilt' in topic.casefold()
                  else 'Teljesítendő vagy külön vizsgálandó feltétel')
        out['rows'].append({'Besorolás':category,'Téma':topic,'Válasz':row['Előírás'],
                            'Alkalmazási feltétel':row['Hatály'],'Forrás':row['Forrás'],'URL':row['URL']})
    teka=next((r for r in national_rules if r.get('key')=='teka' and r.get('ok')),None)
    profiles={r['key']:r for r in national_rules}
    if teka:
        transition={r['Forrás']:r for r in teka['transition']}
        base=transition.get('136. § (1)')
        if base:
            for key in ('otek2012','otek2021','otek2024','teka'):
                cfg=NATIONAL_RULE_PROFILES[key]
                out['paths'].append({'Vizsgálandó út':cfg['label'],'Szükséges igazolás':cfg['basis'],
                    'Állapot':'feltételes; ügyadat és tervi alap még igazolandó',
                    'Forrásszöveg ellenőrizve':bool(profiles.get(key,{}).get('ok')),'URL':base['URL']})
        for ref,label,need in (
            ('136. § (2)','Tervi alaptól független TÉKA-rendelkezések',
             'Az eljárás, dokumentációátadás, építés vagy területhasználat kezdete; a 139. § (2) időbeli szabályával együtt.'),
            ('137/A. §','Korábbi ügy: OTÉK 2024. december 31-i szöveg',
             'A 2025. július 1. előtti kezdés, dokumentációátadás, engedély vagy bejelentés és az eltérés pontos jellege.'),
            ('136. § (4)','Korábbi engedélytől/bejelentéstől kötött eltérés',
             'A korábbi engedély/bejelentés, a 2025. július 1-jei használatbavételi állapot és az engedély- vagy bejelentésköteles eltérés.'),
            ('139. § (2)','A módosított átmeneti szabály időbeli hatálya',
             'Az eljárás kezdete és a módosítás hatálybalépése; korábbi ügyre nem választható automatikusan a mai szöveg.')):
            row=transition.get(ref)
            if row:
                out['paths'].append({'Vizsgálandó út':label,'Szükséges igazolás':need,
                    'Állapot':'feltételes; ügyadat még igazolandó','Forrásszöveg ellenőrizve':True,'URL':row['URL']})
    out['missing']=['A terv készítési követelményei és jelmagyarázata.',
        'Az ügy típusa és kezdete; korábbi engedély/bejelentés, használatbavétel és eltérés adatai.',
        'A tervezett rendeltetés részletes technológiai leírása, a meglévő beépítés és a közműellátottság.',
        'Az építési hely, szabályozási vonal és védőterületi érintettség.']
    if zone.startswith('Gip/') and not verified_local_industrial_type(local_rules,zone):
        out['missing'].append('A jelentős környezeti hatású vagy egyéb ipari besorolás igazolása.')
    out['ok']=True
    return out


def render_buildability_summary(local_rules,national_rules,zone,applicability=None):
    summary=buildability_summary(local_rules,national_rules,zone)
    if not summary['ok']:return
    st.subheader('Építési lehetőségek – közös összefoglaló')
    if (applicability or {}).get('ok'):
        st.info('A helyi előírások forrásoltak és az országos alkalmazási csomag kiválasztva. A konkrét rendeltetés, beépítés és térbeli korlátozások teljesülése még vizsgálandó.')
        summary['missing'] = [x for x in summary['missing'] if not x.startswith(('A terv készítési', 'Az ügy típusa'))]
    else:
        st.info('A helyi előírások forrásoltak; a teljes országos alkalmazási csomag és a telekspecifikus feltételek még igazolandók.')
    st.dataframe(summary['rows'],hide_index=True,use_container_width=True,
        column_config={'URL':st.column_config.LinkColumn('Jogszabályhely')})
    st.markdown('**Melyik OTÉK/TÉKA alkalmazandó?**')
    if (applicability or {}).get('ok'):
        st.write(NATIONAL_RULE_PROFILES[applicability['base_key']]['label'] + (' + kötelező TÉKA-kiegészítések' if applicability['overlay'] else ''))
        for path in summary['paths']:
            path['Állapot']='másik átmeneti út – nem a kiválasztott csomag'
            if path['Vizsgálandó út']==NATIONAL_RULE_PROFILES[applicability['base_key']]['label']:
                path['Állapot']='kiválasztott alap'
            elif applicability['overlay'] and path['Vizsgálandó út']=='Tervi alaptól független TÉKA-rendelkezések':
                path['Állapot']='kiválasztott kötelező kiegészítések'
    if summary['paths']:
        st.dataframe(summary['paths'],hide_index=True,use_container_width=True,
            column_config={'URL':st.column_config.LinkColumn('Átmeneti szabály')})
    else:
        st.warning('Az átmeneti szabály ellenőrzött forrása hiányzik. Országos alkalmazási út nem állapítható meg.')
    st.markdown('**A végleges válaszhoz hiányzó adatok:**')
    for item in summary['missing']:st.write('• '+item)
    st.caption(f"{len(local_rules.get('conditional',[]))} területi feltételt a részletes helyi szabályok külön jelölnek; az érintettség nincs igazolva.")



def case_data_review(case, today=None):
    """Review user input completeness only; never select a legal edition."""
    today = today or datetime.now(ZoneInfo('Europe/Budapest')).date()
    case = case or {}
    prospective = case.get('scenario') == 'prospective'
    if prospective:
        case = dict(case, event='Hatósági eljárás kezdete', started=today, prior='Nincs')
    labels = {
        'purpose': 'Tervezett rendeltetés',
        'event': 'Ügy / kezdő esemény típusa',
        'started': 'Kezdő esemény dátuma',
        'prior': 'Korábbi engedély vagy bejelentés',
        'prior_date': 'Korábbi engedély / bejelentés dátuma',
        'occupied': 'Használatbavételi állapot 2025. július 1-jén',
        'deviation': 'Eltérés a korábbi engedélytől / bejelentéstől',
    }
    out = {'rows': [], 'missing': [], 'issues': []}
    for key, label in labels.items():
        value = case.get(key)
        required = key not in ('prior_date', 'occupied', 'deviation') or case.get('prior') == 'Van'
        missing = value is None or not str(value).strip() or value == 'Nem ismert'
        if not required:
            state = 'Korábbi ügy esetén szükséges'
        elif missing:
            state = 'Hiányzik / nem ismert'
            out['missing'].append(label)
        else:
            state = ('Vizsgálati forgatókönyv – feltételezés' if prospective and key in ('event','started','prior') else 'Felhasználó által megadott; nincs forrásból igazolva')
        shown = value.isoformat() if isinstance(value, date) else str(value or 'Nem ismert')
        out['rows'].append({'Ügyadat': label, 'Megadott adat': shown, 'Állapot': state})
    for key in ('started', 'prior_date'):
        value = case.get(key)
        if value is not None and not isinstance(value, date):
            out['issues'].append(labels[key] + ': érvénytelen dátum.')
        elif isinstance(value, date) and value > today:
            out['issues'].append(labels[key] + ': jövőbeli dátum; megtörtént eseményként nem használható.')
    if (case.get('prior') == 'Van' and isinstance(case.get('started'), date)
            and isinstance(case.get('prior_date'), date)
            and case['prior_date'] > case['started']):
        out['issues'].append('A korábbi engedély / bejelentés dátuma későbbi az ügy kezdő eseményénél; a két esemény kapcsolatát ellenőrizni kell.')
    if case.get('prior') == 'Nincs' and (case.get('prior_date') is not None
            or case.get('occupied', 'Nem ismert') != 'Nem ismert'
            or case.get('deviation', 'Nem ismert') != 'Nem ismert'):
        out['issues'].append('Korábbi ügy nélkül korábbi engedélyhez kapcsolódó adat szerepel; ellenőrizd a megadott adatokat.')
    return out


def purpose_review(case, local_rules, national_rules, zone, applicability):
    """A bounded source-linked review, not permission inferred from free text."""
    purpose = clean_text(str((case or {}).get('purpose') or ''))
    out = {'purpose': purpose, 'status': 'Nem értékelhető', 'answer': '', 'sources': [], 'checks': []}
    if not purpose:
        out['answer'] = 'Add meg a tervezett rendeltetést a vizsgálati beállítások között.'
        return out
    if not (applicability or {}).get('ok'):
        out['answer'] = 'A rendeltetés értékeléséhez előbb az országos alkalmazási csomagot kell kiválasztani.'
        return out
    if ((case or {}).get('scenario') != 'prospective'
            or applicability.get('base_key') != 'otek2021'
            or not verified_local_industrial_type(local_rules, zone)):
        out['answer'] = 'Ehhez az ügyhöz vagy övezethez még nincs ellenőrzött rendeltetési értékelés. A kiválasztott forrásszövegek alapján külön vizsgálat szükséges.'
        return out
    profile = next((r for r in national_rules if r.get('key') == 'otek2021' and r.get('ok')), {})
    if profile.get('industrial_type') != verified_local_industrial_type(local_rules, zone):
        out['answer'] = 'Az országos forrás és a helyi ipari besorolás kapcsolata nem igazolt.'
        return out
    national = {r['Forrás']: r for r in profile.get('rows', [])}
    local = {r['Forrás']: r for r in local_rules.get('rows', [])}
    required = ('20. § (1)', '20. § (3)', '20. § (5)')
    if any(ref not in national for ref in required) or '29. § (3)' not in local:
        out['answer'] = 'A rendeltetési válasz ellenőrzött forráskapcsolata hiányos.'
        return out
    # Exact complete names only: negations, composite descriptions and unfamiliar
    # functions must not be converted into permission by keyword matching.
    name = purpose.casefold().strip().rstrip('.').strip()
    if name in ('gyártócsarnok', 'üzemcsarnok', 'ipari üzem', 'ipari csarnok'):
        out['status'] = 'Feltételek vizsgálandók'
        out['answer'] = ('A gyártócsarnok megnevezése önmagában nem igazolja az elhelyezhetőséget. '
            'Ebben a jelentős hatású ipari övezetben a technológia veszélyes, bűzös vagy nagy zajjal járó '
            'jellegét és a más beépítésre szánt területen való elhelyezhetőségét is vizsgálni kell.')
        out['sources'] = [dict(national[r], Forrás='OTÉK ' + r) for r in required[:2]]
        out['checks'] = ['A technológia, a veszélyesség, a szag- és zajhatás leírása.',
            'A más beépítésre szánt területen történő elhelyezés vizsgálata.']
    elif name in ('lakás', 'lakóépület', 'szolgálati lakás', 'családi ház'):
        out['status'] = 'Lakásra vonatkozó tiltás'
        out['answer'] = ('A kiválasztott OTÉK 20. § (5) szerint a környezetre jelentős hatást gyakorló '
            'iparterületen lakás nem helyezhető el. Ez a lakó rendeltetésre vonatkozó szabály; '
            'az egyéb ipari terület lakásmegengedése erre a besorolásra nem alkalmazható.')
        out['sources'] = [dict(national['20. § (5)'], Forrás='OTÉK 20. § (5)')]
    else:
        out['answer'] = ('A megadott rendeltetéshez még nincs ellenőrzött automatikus értékelés. '
            'Összetett használatnál az egyes rendeltetéseket és a technológiát külön kell vizsgálni.')
        return out
    out['sources'].append(dict(local['29. § (3)'], Forrás='TÉSZ 29. § (3)'))
    out['checks'].extend(['Teljes közművesítés igazolása.',
        'Az övezeti beépítési mutatók, az építési hely és a telek térbeli korlátozásai.',
        'A kiválasztott kötelező TÉKA-kiegészítések teljesítése; ez a rendeltetési részvizsgálat nem ellenőrzi valamennyi építési követelményt.'])
    return out


def render_purpose_review(result):
    st.subheader('A tervezett rendeltetés vizsgálata')
    if result['purpose']:
        st.write('**Megadott rendeltetés:** ' + result['purpose'])
    if result['status'] == 'Lakásra vonatkozó tiltás':
        st.warning(result['answer'])
    else:
        st.info(result['answer'])
    for check in result['checks']:
        st.write('• ' + check)
    if result['sources']:
        with st.expander('A rendeltetési válasz pontos forrásai'):
            for source in result['sources']:
                st.link_button(source['Forrás'], source['URL'])
                st.write(source['Forrásszöveg'])


def national_green_review(case, national_rules, applicability):
    """Attach current green obligations only to a verified new-case package."""
    out = {'ok': False, 'rows': [], 'sources': [], 'notes': []}
    if not (applicability or {}).get('ok'):
        out['notes'].append('Az országos alkalmazási csomag még nincs kiválasztva; TÉKA-zöldfelületi feltétel nem kapcsolható automatikusan az ügyhöz.')
        return out
    if (case or {}).get('scenario') != 'prospective':
        out['notes'].append('Korábbi ügy zöldfelületi követelményeinek időállapota külön ellenőrzendő; a mai 50–52. § nem kerül automatikusan erre az ügyre.')
        return out
    if applicability.get('base_key') != 'teka' and not applicability.get('overlay'):
        out['notes'].append('A kiválasztott csomaghoz nem igazolt a kötelező TÉKA-zöldfelületi kiegészítés.')
        return out
    teka = next((r for r in national_rules if r.get('key') == 'teka' and r.get('ok')), {})
    sources = {r['Forrás']: r for r in teka.get('green', [])}
    needed = ('50. § (1)', '50. § (2)', '50. § (3)', '50. § (4)', '51. §',
              '52. § (1)', '52. § (2)', '52. § (3)', '52. § (4)')
    if any(ref not in sources for ref in needed):
        out['notes'].append('A TÉKA 50–52. § ellenőrzött forráslánca hiányos; automatikus feltétellista nem készül.')
        return out
    for ref, topic, condition in (
        ('50. § (1)', 'Fásítás', 'A legkisebb teljes értékű zöldfelület, a helyi szigorúbb előírás és a meglévő faállomány alapján kell meghatározni a fásítást. A forrás nagy, közepes és kis lombkoronához eltérő területi arányt rendel; ezek alternatívák, nem összeadandó darabszámok.'),
        ('50. § (2)', 'Nyári árnyékolás', 'A fák elhelyezésével biztosítani kell az épület nyári árnyékolását.'),
        ('50. § (3)', 'Faültetési távolságok', 'A fa méretétől függően 3, 2 vagy 1 m távolság szerepel az épülettől és a telek kerítésétől; a közterületi határvonalon álló kerítés kivétel. A gyökérzet és ágrendszer károsító hatása is vizsgálandó.'),
        ('50. § (4)', 'Parkolófásítás beszámítása', 'A hivatkozott parkolófásítási követelmény teljesítése beleszámít a zöldfelületi ellátottságba; a parkoló és a fásítás kialakítása külön igazolandó.'),
        ('51. §', 'Visszaváltó automata – feltételes beszámítás', 'A korábban zöldfelületként használt terület csak a szakaszban meghatározott visszaváltó automata és az épületen belüli vagy burkolt elhelyezés hiánya esetén számítható be ezen a jogcímen. Ez nem általános zöldfelület-csökkentési kedvezmény.'),
        ('52. § (1)', 'Telek megközelítése és közterületi fák', 'A megközelítés nem eredményezhet közterületi fakivágást; ha csak fakivágással oldható meg, a hivatkozott zöldinfrastruktúra-szabály szerinti pótlás szükséges.'),
        ('52. § (2)', 'Közterületi parkoló átalakítása', 'Fásítás érdekében várakozóhely megszüntethető az előírás feltételeivel; ez lehetőség, nem minden telekre előírt kötelezettség.'),
        ('52. § (3)', 'Szomszédos építmény használata', 'A közterületi fásítás nem korlátozhatja a szomszédos telken álló építmény használatát.'),
        ('52. § (4)', 'Növénytelepítés helye', 'Növényzet a gyalogossáv és a telekhatár közötti területen is telepíthető; a konkrét kialakítás külön vizsgálandó.')):
        out['rows'].append({'Téma': topic, 'Vizsgálandó feltétel': condition,
                            'Állapot': 'forrás és alkalmazási kapcsolat igazolt; teljesülés nincs igazolva',
                            'Forrás': 'TÉKA ' + ref, 'URL': sources[ref]['URL']})
        out['sources'].append(sources[ref])
    out['notes'].append('A helyi zöldfelületi százalék számszerű összevetése mellett ezek a kiválasztott országos feltételek is vizsgálandók. A beszámítható zöldfelület és a teljes értékű zöldfelület nem kezelhető automatikusan azonos adatként.')
    out['ok'] = True
    return out


def render_national_green_review(result):
    st.subheader('Országos zöldfelületi és fásítási feltételek')
    if result['ok']:
        st.dataframe(result['rows'], hide_index=True, use_container_width=True,
                     column_config={'URL': st.column_config.LinkColumn('TÉKA-forrás')})
        with st.expander('TÉKA 50–52. § – teljes, ellenőrzött forrásszöveg'):
            for source in result['sources']:
                st.link_button('TÉKA '+source['Forrás']+' – zöldfelületi feltétel', source['URL'])
                st.write(source['Forrásszöveg'])
    for note in result['notes']: st.write(note)


def national_parking_review(case, national_rules, applicability):
    """Current prospective source checklist and explicit surface-parking arithmetic."""
    out = {'ok': False, 'rows': [], 'sources': [], 'calculation': [], 'accessible': [], 'notes': [], 'issues': []}
    if not (applicability or {}).get('ok') or (case or {}).get('scenario') != 'prospective':
        out['notes'].append('A mai parkolási feltételek csak kiválasztott országos csomaghoz és új ügy forgatókönyvéhez kapcsolhatók; korábbi ügy időállapota külön ellenőrizendő.')
        return out
    if applicability.get('base_key') != 'teka' and not applicability.get('overlay'):
        out['notes'].append('A TÉKA parkolási kiegészítésének alkalmazási kapcsolata nincs igazolva.')
        return out
    profile = next((r for r in national_rules if r.get('key') == 'teka' and r.get('ok')), {})
    sources = {r['Forrás']: r for r in profile.get('parking', [])}
    needed = tuple(f'{section}. § ({clause})' for section, count in ((59, 11), (60, 13)) for clause in range(1, count + 1)) + ('59. § (4a)',)
    if any(ref not in sources for ref in needed):
        out['notes'].append('A TÉKA 59–60. § teljes ellenőrzött forráslánca hiányos; részszámítás nem készül.')
        return out
    out['sources'] = [sources[ref] for ref in needed]
    for ref, topic, condition in (
        ('59. § (1)', 'Szükséges parkolóhelyszám', 'A 4. melléklet rendeltetésfüggő alapértéke és az igazolt helyi vagy egyedi eltérések együtt vizsgálandók; a megadott parkolóhelyszám nem igazolja a szükséges szám teljesítését.'),
        ('59. § (2)', 'Meglévő építmény módosítása', 'Bővítésnél és rendeltetésváltozásnál a többletigény és a meglévő helyek megtartása vizsgálandó, a forrás kivételeivel.'),
        ('59. § (4)', 'Elhelyezés', 'Elsődlegesen telken belüli elhelyezés. Az (4a) bekezdés felszíni elhelyezési elsőbbsége is vizsgálandó. Más telken, legfeljebb 500 m-en belüli elhelyezés csak a forrás feltételeivel és helyi megengedéssel lehetséges.'),
        ('59. § (4a)', 'Felszíni elhelyezési elsőbbség', 'A TÉKA 6. § (3) bekezdés 7–10. pontja szerinti építési övezetekben elsődlegesen felszíni várakozóhelyek alakítandók ki. A hivatkozott területtípus kapcsolatát külön kell ellenőrizni.'),
        ('59. § (5)', 'Helyi eltérés', 'A helyi parkolóhelyszám-eltérés jogalapját, megalapozását és a főépítészi véleményt külön kell igazolni.'),
        ('59. § (6)', 'Egyedi eltérés', 'Zöldfelület, műemlék, domborzat vagy közösségi közlekedés alapján eltérés csak a 4. § szerinti eljárással adható; nem automatikus kedvezmény.'),
        ('59. § (8)', 'Akadálymentes helyek', 'Közhasználatú építményhez kapcsolódó parkolóban a kapacitásfüggő akadálymentes arány és a kialakítás ellenőrizendő.'),
        ('60. § (1)', 'Felszíni parkoló fásítása', '10 gépjárműnél nagyobb felszíni várakozóhelynél fásítás szükséges; a fasor, a lehetőség szerinti legalább 1,50 m zöldsáv és a közterületi kivétel feltételei együtt vizsgálandók.'),
        ('60. § (2)', 'Árnyékoló fák aránya', 'Minden megkezdett 6 hely után 1 nagy, vagy minden megkezdett 4 hely után 1 közepes lombkoronájú fa. A két változat alternatíva; a fa minősége is előírt.'),
        ('60. § (8)', 'Árurakodás', 'Rendszeres áruszállításnál – helyi eltérő rendelkezés hiányában – rakodóhely szükséges, a járműigény és a forgalom akadályozásának elkerülése alapján.'),
        ('60. § (12)', 'Kerékpár és motorkerékpár', 'Az 5. melléklet rendeltetésfüggő elhelyezési követelménye külön számítandó.')):
        out['rows'].append({'Téma': topic, 'Vizsgálandó parkolási feltétel': condition,
                            'Forrás': 'TÉKA ' + ref, 'URL': sources[ref]['URL']})
    out['ok'] = True
    parking = (case or {}).get('parking') or {}
    count = parking.get('spaces')
    if count is not None:
        from decimal import Decimal, InvalidOperation
        try:
            value = Decimal(str(count))
            if isinstance(count, bool) or not value.is_finite() or value != value.to_integral_value() or not 0 <= value <= 1000000:
                raise ValueError()
            count = int(value)
        except (InvalidOperation, ValueError, TypeError):
            out['issues'].append('A parkolóhelyek száma 0 és 1 000 000 közötti egész szám lehet.')
            count = None
    if count is not None and parking.get('surface') == 'Felszíni parkoló' and count > 10:
        out['calculation'] = [
            {'Változat': 'Nagy lombkoronájú fa', 'Megadott felszíni hely': count, 'Országos arány szerinti fa': (count + 5) // 6, 'Forrás': 'TÉKA 60. § (1)–(2)'},
            {'Változat': 'Közepes lombkoronájú fa', 'Megadott felszíni hely': count, 'Országos arány szerinti fa': (count + 3) // 4, 'Forrás': 'TÉKA 60. § (1)–(2)'}]
        out['notes'].append('A két faállomány-változat nem összeadandó. Ez a megadott felszíni parkolókapacitás részszámítása; a helyi szigorúbb előírás, a kivételek, a tényleges elhelyezés és a faállomány minősége külön igazolandó.')
    else:
        out['notes'].append('Fásítási részszámításhoz 10-nél több helyből álló felszíni parkolót kell megadni. Üres vagy más kialakítású adat, illetve legfeljebb 10 hely nem eredményez automatikus nulla fa követelményt.')
    if parking.get('public_building') == 'Igen' and count is not None and 0 < count <= 200:
        out['accessible'] = [{'Megadott parkolókapacitás (db)': count,
                              'Országos arány szerinti akadálymentes hely (db)': (count + 19) // 20,
                              'Forrás': 'TÉKA 59. § (8) a)', 'URL': sources['59. § (8)']['URL']}]
        out['notes'].append('A részszámítás a közhasználatú építményhez kapcsolódó, legfeljebb 200 férőhelyes parkoló megadott teljes kapacitására vonatkozik, kialakítástól függetlenül. A helyek mérete, megközelítése és tényleges akadálymentessége külön vizsgálandó.')
    elif parking.get('public_building') == 'Igen' and count is not None and count > 200:
        out['notes'].append('200 férőhely felett az 59. § (8) b) pont további követelményét is vizsgálni kell. Erre a tartományra ez a részszámítás még nem ad végleges helyszámot; a teljes forrásszöveg alább olvasható.')
    else:
        out['notes'].append('Az akadálymentes helyek részszámításához a közhasználatú építményhez való kapcsolódást és pozitív parkolókapacitást meg kell adni. Ismeretlen adat vagy nem közhasználatú kapcsolat nem igazol nulla akadálymentes helyigényt.')
    out['notes'].append('A parkolóhelyigény, akadálymentesség, elektromos töltés, napelemes árnyékolás és méretezés teljes megfelelése nincs automatikusan igazolva. A teljes 59–60. § és mellékletei, a helyi szabályok és a terv együtt ellenőrizendők.')
    return out


def render_national_parking_review(result):
    st.subheader('Parkolás és parkolófásítás – országos részvizsgálat')
    if result['ok']:
        st.dataframe(result['rows'], hide_index=True, use_container_width=True,
                     column_config={'URL': st.column_config.LinkColumn('TÉKA-forrás')})
        if result['calculation']:
            st.write('**A megadott felszíni parkoló fásításának két alternatívája:**')
            st.dataframe(result['calculation'], hide_index=True, use_container_width=True)
        if result['accessible']:
            st.write('**Akadálymentes parkolóhelyek – számarány részszámítása:**')
            st.dataframe(result['accessible'], hide_index=True, use_container_width=True,
                         column_config={'URL': st.column_config.LinkColumn('TÉKA-forrás')})
        with st.expander('TÉKA 59–60. § – teljes, ellenőrzött forrásszöveg'):
            for source in result['sources']:
                st.link_button('TÉKA '+source['Forrás']+' – parkolási feltétel', source['URL'])
                st.write(source['Forrásszöveg'])
    for issue in result['issues']: st.warning(issue)
    for note in result['notes']: st.write(note)


PARKING_DEMAND_TYPES = ('Nem ismert', 'Ipari / üzemi egység', 'Raktározási / logisztikai egység', 'Irodai egység')


def parking_demand_review(case, national_rules, applicability):
    from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext
    out = {'ok': False, 'rows': [], 'sources': [], 'inputs': [], 'notes': [], 'issues': []}
    data = (case or {}).get('parking_demand') or {}
    input_labels = {'situation': 'Parkolóigény vizsgálati helyzete',
                    'type': 'Vizsgált önálló rendeltetési egység',
                    'workers': 'Legnagyobb műszak helyben dolgozó létszáma (fő)',
                    'area': 'Érintett helyiségek nettó alapterülete (m²)'}
    out['inputs'] = [{'Ügyadat': input_labels[key], 'Megadott adat': value if value is not None else 'Nem ismert'}
                     for key, value in data.items() if key in input_labels]
    if not national_parking_review(case, national_rules, applicability)['ok']:
        out['notes'].append('A parkolóigény részszámításához kiválasztott, új ügyhöz igazolt országos parkolási csomag szükséges.')
        return out
    profile = next((r for r in national_rules if r.get('key') == 'teka' and r.get('ok')), {})
    annex = profile.get('parking_annex') or {}
    if not annex.get('ok'):
        out['notes'].append('A TÉKA 4. mellékletének teljes forrásellenőrzése hiányzik; rendeltetési alapérték nem számítható.')
        return out
    if data.get('situation') != 'Új önálló rendeltetési egység létesítése':
        out['notes'].append('Ez a részszámítás új önálló rendeltetési egységre használható. Meglévő egység módosításának többletigénye és megtartandó parkolói külön vizsgálandók; az új hatósági eljárás önmagában nem jelent új rendeltetési egységet.')
        return out
    kind = data.get('type')
    if kind not in PARKING_DEMAND_TYPES[1:]:
        out['notes'].append('Válaszd ki a számítás tárgyát képező önálló rendeltetési egységet; a program nem állapítja meg a teljes épület parkolóigényét a rendeltetés szövegéből.')
        return out
    industrial = kind == 'Ipari / üzemi egység'
    raw = data.get('workers' if industrial else 'area')
    if raw is None:
        out['notes'].append('A legnagyobb létszámú műszak helyben dolgozó létszáma hiányzik.' if industrial else 'A számításban érintett helyiségek nettó alapterülete hiányzik.')
        return out
    try:
        value = Decimal(str(raw))
        if isinstance(raw, bool) or not value.is_finite() or not 0 < value <= (1000000 if industrial else 1000000000000): raise ValueError()
        if industrial and value != value.to_integral_value(): raise ValueError()
    except (InvalidOperation, ValueError, TypeError):
        out['issues'].append('A műszaklétszám pozitív egész szám, legfeljebb 1 000 000 fő lehet.' if industrial else 'A nettó helyiségterület pozitív, véges szám, legfeljebb 1 000 000 000 000 m² lehet.')
        return out
    divisor = 3 if industrial else (1500 if kind == 'Raktározási / logisztikai egység' else 20)
    ref = '4. melléklet I. 11. pont' if kind == 'Irodai egység' else '4. melléklet I. 14. pont'
    source = next((s for s in annex.get('sources', []) if s.get('Forrás') == ref), None)
    legal = {s['Forrás']: s for s in profile.get('parking', [])}
    if not source or any(r not in legal for r in ('59. § (1)', '59. § (2)', '59. § (5)', '59. § (6)')):
        out['notes'].append('A mellékleti alapérték forráskapcsolata hiányos; részszámítás nem készül.')
        return out
    with localcontext() as ctx:
        ctx.prec = max(28, len(value.as_tuple().digits) + 10)
        spaces = int((value / Decimal(divisor)).to_integral_value(rounding=ROUND_CEILING))
    basis = ('A legnagyobb létszámú műszak helyben dolgozó munkavállalói' if industrial else 'Raktárhelyiségek nettó alapterülete' if divisor == 1500 else 'Huzamos tartózkodásra szolgáló helyiségek nettó alapterülete')
    out['rows'] = [{'Vizsgált egység': kind, 'Számítási alap': basis, 'Megadott mennyiség': str(value) + (' fő' if industrial else ' m²'),
                    'Országos mellékleti parkoló-alapérték (db)': spaces, 'Forrás': 'TÉKA ' + ref, 'URL': source['URL']}]
    out['sources'] = [source] + [legal[r] for r in ('59. § (1)', '59. § (2)', '59. § (5)', '59. § (6)')]
    out['ok'] = True
    out['notes'].append('Egész parkolóhelyre felfelé kerekített országos mellékleti alapérték, egy megadott új egységre. A helyi parkolási előírás és az engedélyezett egyedi eltérés nincs ebből igazolva; ez nem a telek végleges kötelező parkolóhelyszáma.')
    out['notes'].append('Más önálló rendeltetési egységek, például iroda és raktár igényét külön kell vizsgálni. Az ipari számítás a legnagyobb műszak helyben dolgozó létszámára vonatkozik, nem az összes műszak vagy a teljes vállalat létszámára.')
    out['notes'].append('A megadott felszíni parkoló kapacitása és fásítása külön adat: a mellékleti alapértéket a program nem írja automatikusan a felszíni parkoló helyszámába.')
    return out


def render_parking_demand_review(result):
    st.subheader('Rendeltetés szerinti parkolóigény – mellékleti alapérték')
    if result['ok']:
        st.dataframe(result['rows'], hide_index=True, use_container_width=True,
                     column_config={'URL': st.column_config.LinkColumn('Mellékleti forrás')})
        with st.expander('A parkoló-alapérték és eltérések ellenőrzött forrásai'):
            for source in result['sources']:
                st.link_button('TÉKA '+source['Forrás']+' – parkoló-alapérték', source['URL'])
                st.write(source['Forrásszöveg'])
    for issue in result['issues']: st.warning(issue)
    for note in result['notes']: st.write(note)


def proposal_review(proposal, table_doc, zone, local_rules, source):
    """Compare declared design quantities with a hash-verified local table only."""
    from decimal import Decimal, InvalidOperation, localcontext
    out = {'rows': [], 'inputs': [], 'issues': [], 'notes': [], 'sources': []}
    proposal = proposal or {}
    labels = {'area_basis': 'Mutatószámítás telekterületi alapja (m²)',
              'built_area': 'Beépített terület összesen a tervezett állapotban (m²)',
              'green_area': 'Beszámítható zöldfelület a tervezett állapotban (m²)',
              'height': 'Tervezett épületmagasság (m)'}
    values = {}
    for key, label in labels.items():
        raw = proposal.get(key)
        out['inputs'].append({'Adat': label, 'Megadott érték': str(raw) if raw is not None else 'nincs megadva',
                              'Bizonyosság': 'felhasználói közlés; számítási alap nincs igazolva'})
        if raw is None:
            continue
        try:
            if isinstance(raw, bool): raise InvalidOperation
            value = Decimal(str(raw))
            if not value.is_finite() or value < 0 or value > Decimal('1e12') or (key == 'area_basis' and value == 0):
                raise InvalidOperation
            values[key] = value
        except (InvalidOperation, ValueError):
            out['issues'].append(label + ': véges, 0 és 10¹² közötti szám szükséges; a területi alapnak pozitívnak kell lennie.')
    if not any(v is not None for v in proposal.values()):
        out['notes'].append('A tervezett beépítés számszerű adatai még nincsenek megadva.')
        return out
    if not local_rules.get('ok') or local_rules.get('zone') != zone:
        out['notes'].append('Nincs igazolt helyi övezetkapcsolat; számszerű összevetés nem készül.')
        return out
    _, params, _ = verified_tisza_table_rows(table_doc, zone)
    if not params:
        out['notes'].append('Az övezeti paramétertábla kiadása vagy az övezeti sor nem igazolt; számszerű összevetés nem készül.')
        return out
    area = values.get('area_basis')
    for key in ('built_area', 'green_area'):
        if area is not None and key in values and values[key] > area:
            out['issues'].append(labels[key] + ': nagyobb a megadott számítási alapnál; ellenőrizd a területeket és a beszámítás módját.')
            values.pop(key)
    def shown(value):
        with localcontext() as context:
            context.prec = max(28, value.adjusted() + 8)
            return format(value.quantize(Decimal('0.001')), 'f').rstrip('0').rstrip('.').replace('.', ',')
    for key, param, unit, minimum in (
            ('built_area', 'Legnagyobb beépítettség', '%', False),
            ('green_area', 'Legkisebb zöldfelület', '%', True),
            ('height', 'Legnagyobb épületmagasság', 'm', False)):
        match = re.fullmatch(r'(\d+(?:,\d+)?) '+re.escape(unit)+r'(?:; 2\. lábjegyzet)?', params.get(param, ''))
        if not match: continue
        limit = Decimal(match.group(1).replace(',', '.'))
        value = values.get(key)
        if key != 'height':
            value = value * 100 / area if value is not None and area is not None else None
        row = {'Mutató': param, 'Helyi táblázati határ': params[param],
               'Számított / megadott érték': shown(value)+' '+unit if value is not None else 'nem számítható',
               'Eredmény': 'Hiányzó vagy hibás adat', 'Számítás': '',
               'Forrás': 'NJT – 1.2. melléklet, 2. PDF-oldal', 'URL': source}
        if value is not None:
            # Compare unrounded quantities; displayed rounding must not turn a
            # small excess or deficit into a false pass at the threshold.
            within = value >= limit if minimum else value <= limit
            row['Eredmény'] = 'Helyi táblázati határon belül' if within else 'Helyi táblázati határtól eltér'
            row['Számítás'] = (shown(values[key])+' / '+shown(area)+' × 100' if key != 'height'
                               else 'megadott épületmagasság')
            if key != 'height':
                row['Határ területben'] = shown(area * limit / 100) + ' m²'
            elif not within:
                row['Eredmény'] = 'Táblázati magasság felett; technológiai eltérés külön vizsgálandó'
        out['rows'].append(row)
    for r in local_rules.get('rows', []):
        if r['Forrás'] == '26. §': out['sources'].append(r)
    for r in local_rules.get('conditional', []):
        if r['Forrás'] in ('30. § (2)', '30. § (3)'): out['sources'].append(r)
    out['notes'].extend(['Az összevetés a helyi táblázat számaira és a megadott tervezett állapotra vonatkozik; nem igazolja a teljes építési megfelelést.',
        'A kijelzés három tizedesre kerekít; a határérték összevetése a kijelzési kerekítés előtt történik.',
        'A beépített területbe a megmaradó és tervezett beépítést együtt kell beszámítani. Az épületmagasság nem azonos a legmagasabb pont magasságával.',
        'A számítási telekterület és a zöldfelület beszámításának jogszerűségét külön igazolni kell. A nyilvános telekgeometria területe nem kerül automatikusan a számítás nevezőjébe.',
        'TVK/MOL-területi érintettség esetén a TÉSZ 30. § külön számítási és megállapodási feltételeket írhat elő; az érintettség nincs igazolva.',
        'A kötelező országos kiegészítések – így az alkalmazandó TÉKA-zöldfelületi követelmények – felülírhatják a helyi táblázati határt. Ezek teljesülését ez az összevetés nem állapítja meg.'])
    return out


def render_proposal_review(result):
    st.subheader('Tervezett beépítés – számszerű összevetés')
    for issue in result['issues']: st.warning(issue)
    if result['rows']:
        st.dataframe(result['rows'], hide_index=True, use_container_width=True,
                     column_config={'URL': st.column_config.LinkColumn('Helyi táblázat')})
    for note in result['notes']: st.write(note)
    if result['sources']:
        with st.expander('Magassági eltérés és sajátos területszámítás – pontos helyi források'):
            for source in result['sources']:
                st.link_button(source['Forrás']+' – számítási feltétel', source['URL'])
                st.write(source['Forrásszöveg'])


def render_case_review(case):
    review = case_data_review(case)
    st.subheader('Tervezett használat és ügyadatok')
    st.caption('Az ügyadatok alapján végzett OTÉK/TÉKA-kiválasztás eredménye fent olvasható. A felhasználói közlés nem dokumentummal igazolt ügytörténet és önmagában nem igazolja a rendeltetés megengedettségét.')
    st.dataframe(review['rows'], hide_index=True, use_container_width=True)
    for issue in review['issues']:
        st.warning(issue)
    if review['missing']:
        st.write('Még megadandó ügyadatok: ' + '; '.join(review['missing']) + '.')
    else:
        st.info('A szükséges ügyadatmezők kitöltve. A felhasználói közlés dokumentumokkal történő igazolása külön feladat; a tervi alap forrásellenőrzésének eredménye fent olvasható.')


def investigation_report(town, hrsz, zone, params, summary, local_rules,
                         national_rules, case, generated_at=None, parameter_source="", plan_source="", applicability=None, plan_basis=None, proposal_result=None, source_inventory=None, label_search=None):
    """Export the actual result with provenance and unresolved scope."""
    generated_at = generated_at or datetime.now(timezone.utc)
    lines = ['TelekElőírás AI v15.49 – vizsgálati adatlap',
             'Készült (UTC): ' + generated_at.isoformat(),
             'Telek: ' + str(town) + ' ' + normalize_hrsz(hrsz),
             'Övezet: ' + (zone or 'nincs igazolva'),
             '', 'A végleges építési lehetőség nem igazolt. A konkrét rendeltetés, beépítés és telekspecifikus feltételek további ellenőrzést igényelnek.',
             '', 'VIZSGÁLATI EREDMÉNY ÉS BIZONYOSSÁG']
    def add_row(row):
        for key, value in row.items():
            if value is not None and str(value):
                lines.append(str(key) + ': ' + str(value))
        lines.append('')
    for row in summary:
        add_row(row)
    if plan_source:
        lines.extend(['Szabályozási terv forrása: ' + plan_source, ''])
    if label_search is not None:
        lines.extend(['HRSZ-FELIRAT KERESÉSE – NEM TELEKHATÁR- VAGY ÖVEZETIGAZOLÁS'])
        scan=label_search.get('scan',{})
        if scan:
            add_row({'PDF SHA-256':scan.get('source_sha256',''),
                     'Feldolgozott rajzi feliratcsoport':scan.get('scanned',0),
                     'Lezárt PDF-oldal':scan.get('pages',0),
                     'Alkalmas feliratcsoportok keresése lezárult':scan.get('complete',False)})
            lines.append('A feldolgozás lezárása nem bizonyítja minden térképi felirat felismerését; a hiányzó találat nem bizonyítja a telek hiányát.')
        for hit in label_search.get('hits',[]):
            add_row({'Pontos HRSZ-felirat':normalize_hrsz(hrsz),'PDF-oldal':hit['page_number']+1,
                     'Felirat téglalapja PDF-pontban':str(tuple(hit['pdf_rect'])),
                     'Koordináták':hit.get('coordinate_space','natív PDF'),
                     'Felismerés':hit.get('method',''),
                     'Állapot':'Felirattalálat; telekhatár és övezet nincs igazolva'})
    lines.extend(['ÖVEZETI PARAMÉTEREK'])
    if parameter_source:
        lines.append('Paraméterek forrása: ' + parameter_source)
    if params:
        for key, value in params.items():
            lines.append(str(key) + ': ' + str(value))
    else:
        lines.append('Nincs ellenőrzött paraméteradat.')
    lines.extend(['', 'ÜGYADATOK – FELHASZNÁLÓI KÖZLÉS, NEM IGAZOLT'])
    review = case_data_review(case, generated_at.astimezone(ZoneInfo('Europe/Budapest')).date())
    for row in review['rows']:
        add_row(row)
    for issue in review['issues']:
        lines.append('Ellenőrizendő ügyadat: ' + issue)
    lines.extend(['', 'HELYI ELŐÍRÁSOK'])
    if local_rules.get('ok'):
        if local_rules.get('basis_evidence'):
            add_row(local_rules['basis_evidence'])
        if local_rules.get('industrial_type'):
            add_row(local_rules['industrial_type'])
        for row in local_rules.get('rows', []):
            add_row(row)
        lines.append('TERÜLETI ÉRINTETTSÉGHEZ KÖTÖTT HELYI FELTÉTELEK – ÉRINTETTSÉG NINCS IGAZOLVA')
        for row in local_rules.get('conditional', []):
            add_row(row)
    else:
        lines.append('Nincs ellenőrzött helyi szabálykapcsolat.')
    if source_inventory is not None:
        lines.extend(['', 'HELYI FORRÁSOK TÉMAKÖRI FELDOLGOZÁSA – ALKALMAZHATÓSÁG MÉG ELLENŐRIZENDŐ'])
        for row in source_inventory.get('coverage', []): add_row(row)
        for issue in source_inventory.get('errors', []): lines.append(issue)
        for row in source_inventory.get('rows', []): add_row(row)
    lines.extend(['', 'AUTOMATIKUS ORSZÁGOS KIVÁLASZTÁS'])
    selection=applicability or {}
    if selection.get('ok'):
        lines.append(NATIONAL_RULE_PROFILES[selection['base_key']]['label'] + (' + kötelező TÉKA-kiegészítések' if selection['overlay'] else ''))
    else:
        lines.append('Teljes alkalmazási csomag nincs kiválasztva.')
    lines.extend(selection.get('assumptions', []))
    lines.extend(selection.get('reasons', []))
    lines.extend('Hiányzó kiválasztási adat: ' + x for x in selection.get('missing', []))
    if (plan_basis or {}).get('ok'):
        lines.append(plan_basis['proof'])
        for source in plan_basis['sources']: add_row(source)
    for source in selection.get('sources', []): add_row(source)
    purpose = purpose_review(case, local_rules, national_rules, zone, selection)
    lines.extend(['', 'TERVEZETT RENDELTETÉS – FORRÁSOLT RÉSZVIZSGÁLAT',
                  'Megadott rendeltetés: ' + (purpose['purpose'] or 'nincs megadva'),
                  'Állapot: ' + purpose['status'], purpose['answer']])
    lines.extend('Vizsgálandó: ' + item for item in purpose['checks'])
    for source in purpose['sources']: add_row(source)
    green = national_green_review(case, national_rules, selection)
    lines.extend(['', 'ORSZÁGOS ZÖLDFELÜLETI ÉS FÁSÍTÁSI FELTÉTELEK'])
    for row in green['rows']: add_row(row)
    lines.extend(green['notes'])
    for source in green['sources']: add_row(source)
    parking = national_parking_review(case, national_rules, selection)
    lines.extend(['', 'PARKOLÁS ÉS PARKOLÓFÁSÍTÁS – ORSZÁGOS RÉSZVIZSGÁLAT'])
    add_row({'Megadott parkolási adat': 'Felhasználói közlés; tervből nincs igazolva', **((case or {}).get('parking') or {})})
    add_row({'Ügyadat': 'Közhasználatú építményhez kapcsolódik a parkoló?', 'Megadott adat': ((case or {}).get('parking') or {}).get('public_building', 'Nem ismert')})
    for row in parking['rows'] + parking['calculation'] + parking['accessible']: add_row(row)
    lines.extend(parking['issues'] + parking['notes'])
    for source in parking['sources']: add_row(source)
    demand = parking_demand_review(case,national_rules,selection)
    lines.extend(['', 'RENDELTETÉS SZERINTI PARKOLÓIGÉNY – MELLÉKLETI ALAPÉRTÉK'])
    for row in demand['inputs'] + demand['rows']: add_row(row)
    lines.extend(demand['issues'] + demand['notes'])
    for source in demand['sources']: add_row(source)
    if proposal_result is not None:
        lines.extend(['', 'TERVEZETT BEÉPÍTÉS – HELYI TÁBLÁZATI ÖSSZEVETÉS'])
        for row in proposal_result['inputs']: add_row(row)
        for row in proposal_result['rows']: add_row(row)
        lines.extend('Hibás adat: ' + item for item in proposal_result['issues'])
        lines.extend(proposal_result['notes'])
        for row in proposal_result['sources']: add_row(row)
    lines.extend(['', 'ORSZÁGOS FORRÁSOK – A KIVÁLASZTOTT CSOMAGGAL EGYÜTT ÉRTÉKELENDŐ'])
    for result in national_rules:
        cfg = NATIONAL_RULE_PROFILES[result['key']]
        lines.extend([cfg['label'], 'Forrás: ' + cfg['url'],
                      'Ellenőrzés: ' + ('teljes szöveg ellenőrizve' if result.get('ok') else result.get('error', 'nem sikerült')),
                      'Alkalmazási alap: ' + cfg['basis']])
        if result.get('ok'):
            for row in result.get('rows', []):
                add_row(row)
            if result.get('comparison'):
                lines.append('MÁS IPARI TÍPUS – CSAK ÖSSZEHASONLÍTÁS, NEM A HELYI BESOROLÁS MEGENGEDÉSE')
                for row in result['comparison']:
                    add_row(row)
            for row in result.get('transition', []):
                add_row(row)
    lines.extend(['', 'A VÉGLEGES VÁLASZHOZ MÉG SZÜKSÉGES'])
    missing = buildability_summary(local_rules, national_rules, zone).get('missing', [])
    if (applicability or {}).get('ok'):
        missing = [x for x in missing if not x.startswith(('A terv készítési', 'Az ügy típusa'))]
    if not missing:
        missing = ['A telek övezetének, a helyi és országos forráskapcsolatnak az igazolása.',
                   'A tervi alap, az ügyadatok és a telekspecifikus korlátozások ellenőrzése.']
    lines.extend('- ' + item for item in missing)
    lines.extend(['', 'A megadott ügyadatok nem hatósági igazolások. A dokumentum döntéstámogató vizsgálati adatlap.'])
    return '\n'.join(lines) + '\n'


@st.fragment
def render_report_download(report, hrsz):
    safe_hrsz = re.sub(r'[^0-9A-Za-z_-]', '_', normalize_hrsz(hrsz)) or 'telek'
    # Streamlit 1.44+ can suppress the frontend rerun directly; older
    # supported versions use the enclosing fragment for download isolation.
    version = tuple(int(part) for part in st.__version__.split('.')[:2])
    st.download_button('Teljes vizsgálati adatlap letöltése (.txt)',
                       data=report.encode('utf-8'),
                       file_name='telekvizsgalat_' + safe_hrsz + '_v15_49.txt',
                       mime='text/plain; charset=utf-8',
                       on_click='ignore' if version >= (1, 44) else None)


class InvestigationProgress:
    """Report completed stages and measured durations; never imply verified permission."""
    steps=('Telek és település azonosítása',
           'Hatályos helyi rendelet ellenőrzése',
           'Terv és paramétertábla betöltése',
           'Pontos telekhely és övezet ellenőrzése',
           'Helyi előírások összekapcsolása',
           'Országos előírások ellenőrzése',
           'Adatlap összeállítása')

    def __init__(self,parcel_label):
        self.parcel_label=parcel_label
        self.started=time.monotonic();self.last_started=self.started
        self.active=None;self.rows=[]
        self.bar=st.progress(0.0,text='Vizsgálat indul: '+parcel_label)
        st.caption('A folyamatjelző a feldolgozás szakaszait mutatja. A végleges keresési eredmény a feldolgozás lezárása után olvasható.')

    def stage(self,name):
        index=self.steps.index(name)
        if self.active is not None and index<=self.steps.index(self.active):
            raise ValueError('A vizsgálati szakaszok csak előre haladhatnak.')
        now=time.monotonic()
        if self.active is not None:
            self.rows.append({'Szakasz':self.active,'Idő (mp)':round(max(0,now-self.last_started),2)})
        self.active=name;self.last_started=now
        self.bar.progress(index/len(self.steps),text=f'{index+1}/{len(self.steps)} · {name} · {self.parcel_label}')

    def finish(self):
        now=time.monotonic()
        if self.active is not None:
            self.rows.append({'Szakasz':self.active,'Idő (mp)':round(max(0,now-self.last_started),2)})
            self.active=None
        elapsed=max(0,now-self.started)
        self.bar.progress(1.0,text=f'Feldolgozás lezárult · {self.parcel_label} · {elapsed:.1f} mp')
        with st.expander('Keresési idők – hol telt el a várakozás?'):
            st.write(f'Összes feldolgozási idő: {elapsed:.1f} másodperc.')
            st.dataframe(self.rows,hide_index=True,use_container_width=True)
            st.write('A nagy tervfájl első letöltése és egy új telek rajzi ellenőrzése hosszabb lehet. Azonos terv és változatlan telekgeometria mellett az ismételt térképi ellenőrzés korábbi eredménye legfeljebb 15 percig felhasználható.')
        return elapsed


@st.cache_resource(show_spinner=False)
def recoverable_investigation_requests():
    import threading
    return {},threading.RLock()


def investigation_request_fingerprint(town,hrsz,district,uploaded,manual_url,manual_zone,verified,case):
    data={'town':town,'hrsz':normalize_hrsz(hrsz),'district':district,
          'uploaded_sha':source_digest(uploaded.getvalue()) if uploaded is not None else '',
          'manual_url':manual_url,'manual_zone':manual_zone,'verified':verified,'case':case,
          'date':datetime.now(ZoneInfo('Europe/Budapest')).date().isoformat()}
    return source_digest(json.dumps(data,sort_keys=True,ensure_ascii=False,default=str).encode('utf-8'))


def matching_investigation_request(fingerprint,token='',start=False):
    """An opaque URL token resumes only the same restored form, for 15 minutes."""
    import uuid
    requests,lock=recoverable_investigation_requests();now=time.monotonic()
    with lock:
        for key in list(requests):
            if now-requests[key]['created']>900:requests.pop(key,None)
        if start:
            token=uuid.uuid4().hex
            requests[token]={'fingerprint':fingerprint,'created':now}
            while len(requests)>32:requests.pop(next(iter(requests)))
            return token
        record=requests.get(token)
        return token if record and record['fingerprint']==fingerprint else ''


class UncachedInvestigationResult(Exception):
    def __init__(self,result):
        super().__init__('A részleges vagy sikertelen vizsgálat nem kerül az eredmény-gyorsítótárba.')
        self.result=result


def main():
    st.title("TelekElőírás AI")
    st.caption(
        "v15.49 • nyilvános HRSZ API + telekgeometria • "
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

        with st.expander("Vizsgált ügy és tervezett használat"):
            scenario = st.selectbox('Vizsgálati forgatókönyv', ['Új ügy tervezése – mai napon induló hatósági eljárás', 'Folyamatban lévő vagy korábbi ügy'])
            case = {'scenario': 'prospective' if scenario.startswith('Új ügy') else 'existing',
                    'purpose': st.text_input('Tervezett rendeltetés', placeholder='pl. raktár, gyártócsarnok, iroda', max_chars=1000)}
            if case['scenario'] == 'prospective':
                st.caption('A forgatókönyv a vizsgálat napján induló új hatósági eljárás, korábbi engedély vagy bejelentés nélkül. Ez nem igazolt ügytörténet. Korábbi engedély vagy ismert kezdés esetén válaszd a másik forgatókönyvet.')
            else:
                st.caption('Az ismeretlen dátumot hagyd üresen; a program nem helyettesíti a mai nappal.')
                case.update({
                    'event': st.selectbox('Ügy / kezdő esemény típusa', ['Nem ismert', 'Hatósági eljárás kezdete', 'Engedélyhez / bejelentéshez nem kötött kivitelezési dokumentáció átadása', 'Kivitelezési dokumentációhoz nem kötött építési tevékenység kezdete', 'Építészeti tervtanácsi vagy egyéb eljárás kezdete', 'Területhasználat kezdete']),
                    'started': st.date_input('Kezdő esemény dátuma', value=None, min_value=date(1900, 1, 1), format='YYYY-MM-DD'),
                    'prior': st.selectbox('Korábbi engedély vagy bejelentés', ['Nem ismert', 'Nincs', 'Van']),
                    'current_status': st.selectbox('Ügy jelenlegi állapota', ['Nem ismert', 'Folyamatban / jelenleg vizsgált cselekmény', 'Lezárt ügy történeti vizsgálata'])})
                if case['prior'] == 'Van':
                    case['prior_date'] = st.date_input('Korábbi engedély / bejelentés dátuma', value=None, min_value=date(1900, 1, 1), format='YYYY-MM-DD')
                    case['occupied'] = st.selectbox('Használatbavételi állapot 2025. július 1-jén', ['Nem ismert', 'Használatba vett', 'Nem vették használatba'])
                    case['deviation'] = st.selectbox('Eltérés a korábbi engedélytől / bejelentéstől', ['Nem ismert', 'Nincs eltérés', 'Van eltérés; kötöttsége még vizsgálandó', 'Engedély- vagy bejelentésköteles eltérés', 'Engedélyhez / bejelentéshez nem kötött eltérés'])

        with st.expander('Tervezett beépítés – számítási adatok'):
            st.caption('Opcionális adatok a tervezett végállapotról, a megmaradó beépítéssel együtt. Az üres mező ismeretlen adatot jelent.')
            case['proposal'] = {
                'area_basis': st.number_input('Mutatószámítás telekterületi alapja (m²)', min_value=0.0, value=None, step=1.0),
                'built_area': st.number_input('Beépített terület összesen a tervezett állapotban (m²)', min_value=0.0, value=None, step=1.0),
                'green_area': st.number_input('Beszámítható zöldfelület a tervezett állapotban (m²)', min_value=0.0, value=None, step=1.0),
                'height': st.number_input('Tervezett épületmagasság (m)', min_value=0.0, value=None, step=0.1)}
            st.caption('A számítási alapot és a zöldfelületi beszámítást tervből kell megadni. A helyi táblázati összevetés mellett az országos és sajátos területi feltételeket is ellenőrizni kell.')

        with st.expander('Parkoló – tervezett kialakítás'):
            case['parking'] = {
                'surface': st.selectbox('Parkoló kialakítása', ['Nem ismert', 'Felszíni parkoló', 'Épületben vagy terepszint alatt']),
                'spaces': st.number_input('Vizsgált parkoló gépjármű-várakozóhelyeinek száma', min_value=0, max_value=1000000, value=None, step=1),
                'public_building': st.selectbox('Közhasználatú építményhez kapcsolódik a parkoló?', ['Nem ismert', 'Igen', 'Nem'])}
            st.caption('Egy adott parkoló kapacitását add meg. Ez nem a rendeltetés alapján szükséges parkolóhelyszám; a program nem következtet rá a beépített területből.')

        with st.expander('Parkolóigény – számítás egy rendeltetési egységre'):
            case['parking_demand'] = {
                'situation': st.selectbox('Parkolóigény vizsgálati helyzete', ['Nem ismert', 'Új önálló rendeltetési egység létesítése', 'Meglévő egység bővítése / átalakítása / rendeltetésváltozása']),
                'type': st.selectbox('A vizsgált önálló rendeltetési egység', PARKING_DEMAND_TYPES),
                'workers': st.number_input('Legnagyobb műszak helyben dolgozó létszáma (fő)', min_value=0, max_value=1000000, value=None, step=1),
                'area': st.number_input('Érintett helyiségek nettó alapterülete (m²)', min_value=0.0, value=None, step=1.0)}
            st.caption('Ipari egységnél a műszaklétszám, raktárnál csak a raktárhelyiségek, irodánál a huzamos tartózkodásra szolgáló helyiségek nettó területe a számítás alapja. A teljes beépített terület nem helyettesíti ezeket.')

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

    fingerprint=investigation_request_fingerprint(town,hrsz,budapest_district,uploaded_plan,
                                                  manual_njt_url,manual_zone,map_verified,case)
    active=matching_investigation_request(fingerprint,str(st.query_params.get('run','')),start=start)
    if start and active:st.query_params['run']=active
    if not active:
        if 'run' in st.query_params:del st.query_params['run']
        st.info(
            "Add meg a települést és a helyrajzi számot, majd indítsd el a vizsgálatot."
        )
        return

    if not town.strip() or not normalize_hrsz(hrsz):
        st.error("A település és a helyrajzi szám megadása kötelező.")
        return

    lock = pdf_processing_lock()
    if not lock.acquire(blocking=False):
        with st.spinner('Másik telekvizsgálat PDF-feldolgozása fut. A keresés sorra kerül; nem szükséges újraindítani.'):
            lock.acquire()
    try:
        try:
            result=run_investigation(town, hrsz, budapest_district, uploaded_plan,
                                    manual_njt_url, manual_zone, map_verified, case,
                                    request_day=datetime.now(ZoneInfo('Europe/Budapest')).date().isoformat())
        except UncachedInvestigationResult as exc:result=exc.result
    finally:
        lock.release()
    render_report_download(result['report'],hrsz)
    if result.get('minerva_geometry'):
        st.download_button('Övezeti geometria ellenőrzési adatainak letöltése',
            data=result['minerva_geometry'],file_name='minerva_geometry_snapshot.json',
            mime='application/json',on_click='ignore')


@st.cache_data(show_spinner=False,ttl=900,max_entries=8)
def run_investigation(town, hrsz, budapest_district, uploaded_plan,
                      manual_njt_url, manual_zone, map_verified, case, request_day=None):
    progress=InvestigationProgress(f"{town.strip()} {normalize_hrsz(hrsz)}")
    progress.stage(progress.steps[0])

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
            st.caption('Az övezeti geometria ellenőrzési adatai a vizsgálat lezárása után letölthetők.')
        if minerva_diag.get("parcel_geometry"):
            st.write("**OÉNY → MINERVA térbeli lekérdezés bemenete:** telek MultiPolygon/bounding box rendelkezésre áll.")
        if minerva_diag.get("candidates"):
            st.dataframe(minerva_diag["candidates"], hide_index=True)
            st.warning("A telekpolygonnal metsző réteg övezeti találatai rendelkezésre állnak. A réteg hatályos NJT-melléklethez tartozása még ellenőrzendő; ezért ezekből nem képezek automatikusan hatályos előírást.")
        elif minerva_diag.get("bootstrap"):
            st.caption("A térképi kapcsolat létrejött. Az övezet ellenőrzése a hatályos tervlap feldolgozásával folytatódik.")

    # 1. NJT
    progress.stage(progress.steps[1])
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
        if meta.get("scope_note"):
            st.warning(meta["scope_note"])
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
    progress.stage(progress.steps[2])
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
            plan_doc, plan_source, auto_plan_error = try_auto_plan(attachments, page.get("text", ""), meta if source_valid else None, hrsz)

        if plan_doc:
            st.success("A szabályozási terv PDF automatikusan betöltődött.")
        elif auto_plan_error:
            st.caption(f"A szabályozási terv automatikus feldolgozása nem sikerült: {auto_plan_error}")

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
            st.success("A beszkennelt 1.2 melléklet forráskiadása és gazdasági övezeti sorai ellenőrizve.")
        elif zone_table_doc and clean_text(zone_table_text):
            st.success("Az 1.2 melléklet övezeti paramétertáblája automatikusan betöltődött.")
        elif zone_table_error:
            st.caption(f"1.2 melléklet: {zone_table_error}")

    # 3. Telek
    progress.stage(progress.steps[3])
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
    if plan_zone.get('zone'):
        spatial={'status':'verified','hit':None,'candidates':[],'zone':''}
    else:
        try:
            label_progress=st.empty()
            def show_label_progress(page,pages,scanned,labels):
                label_progress.caption(f'Rajzi felismerés folyamatban: {page}/{pages}. tervlap, {scanned} feldolgozott feliratcsoport, {labels} számtartalmú jelölt. Az első keresés nagy tervlapon lassabb szerveren akár 10 percet is igényelhet.')
            with st.spinner('Pontos HRSZ-felirat keresése – az első rajzi feldolgozás akár 10 perc; az elkészült feliratindex egy óráig újra felhasználható…'):
                spatial=locate_parcel(plan_doc,hrsz,outlined=source_valid,on_progress=show_label_progress)
            label_progress.empty()
        except Exception as exc:
            spatial={'status':'parcel_not_found','hit':None,'candidates':[],'zone':'','scan':{'error':str(exc)}}
            st.warning('A rajzi HRSZ-felismerés nem fejeződött be: '+str(exc))
    scan=spatial.get('scan',{})
    if scan:
        st.caption(f"Rajzi keresés: {scan.get('scanned',0)} feliratcsoport feldolgozva, {scan.get('pages',0)} tervlap feldolgozása lezárult. Ez az alkalmas betűalakok keresése; nem bizonyítja, hogy minden térképi felirat felismerhető volt.")
        if not scan.get('complete'):
            st.warning('A rajzi feliratkeresés nem teljes; további találat nem zárható ki. A hiányzó találat nem bizonyítja a telek hiányát.')

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
            if scan and not scan.get('complete'):
                st.warning('A feldolgozott térképi feliratok között még nincs ellenőrzött HRSZ-találat. A keresés részleges, és automatikus telekgeometria sem áll rendelkezésre; ebből a telek hiányára nem lehet következtetni.')
            elif scan:
                st.warning('A natív szövegben és a feldolgozott rajzi feliratok között nincs ellenőrzött HRSZ-találat. Nem minden betűalak ismerhető fel; ez nem bizonyítja a telek hiányát. Automatikus telekgeometria sem áll rendelkezésre.')
            else:
                st.error('A helyrajzi számot nem találtam meg a szabályozási terv natív szövegrétegében, és automatikus telekgeometria sem áll rendelkezésre.')
    else:
        hit = spatial["hit"]
        st.success(f"A pontos HRSZ-felirat megtalálva a szabályozási terv {hit['page_number'] + 1}. PDF-oldalán.")
        st.caption(hit.get('method','Pontos szöveges találat')+'. Ez a felirat helyét igazolja; a telekhatár és az övezet összekapcsolása külön ellenőrzés.')
        if len(spatial.get('hits',[]))>1:
            st.warning(f"{len(spatial['hits'])} pontos felirattalálat van. A legelső találatot nem tekintem automatikusan a vizsgált teleknek.")
            st.dataframe([{'PDF-oldal':h['page_number']+1,'Felirat helye':str(tuple(h['pdf_rect'])),'Felismerés':h.get('method','')} for h in spatial['hits']],hide_index=True,use_container_width=True)
        page_obj = plan_doc[hit["page_number"]]
        st.image(
            label_crop(page_obj,hit),
            caption="A HRSZ-felirat környezete – a piros keret a feliratot jelöli, nem a telekhatárt",
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
        st.caption("Az alábbi feliratok csak térképi jelöltek; a telek övezeti besorolását önmagukban nem igazolják.")
        st.dataframe(
            spatial["candidates"],
            hide_index=True,
            use_container_width=True,
        )

    # 5. Előírások
    progress.stage(progress.steps[4])
    st.header("5. Mit mond a hatályos szabályzat?")

    njt_text = page.get("text", "")
    # Nearby prose can contain neighbouring zones and closing exceptions.
    # Only an exact structured table row supplies numeric parcel parameters.
    rows, params, contexts = [], {}, []
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
                             clip=fitz.Rect(62,534 if zone.startswith('Gksz/') else 730,576,783),alpha=False).tobytes('png'),
                             caption="A gazdasági övezetek eredeti forrássorai – 1.2. melléklet, 2. PDF-oldal",
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
    source_inventory=local_source_inventory(page,source_valid,zone,
        bool(plan_zone.get('zone') or (manual_zone.strip() and map_verified)),attachments)
    st.subheader('Helyi előírások – témaköri forrásellenőrzés')
    st.caption('A találatok teljes jogszabályi rendelkezések. A témaköri keresés önmagában nem igazolja a telekre alkalmazhatóságot; a hiányzó találat nem jelent előírásmentességet. Külön településképi rendelet és jogszabályi PDF-melléklet további forrásfeldolgozást igényelhet.')
    if source_inventory['coverage']:
        st.dataframe(source_inventory['coverage'],hide_index=True,use_container_width=True)
    for issue in source_inventory['errors']: st.info(issue)
    if source_inventory['rows']:
        with st.expander('Teljes helyi rendelkezések, pontos forrásokkal'):
            st.dataframe(source_inventory['rows'],hide_index=True,use_container_width=True)
    local_rules={'ok':False,'rows':[],'conditional':[]}
    national_rules=[]
    plan_basis={'ok':False,'error':'A tervi alap nincs igazolva.'}
    applicability={'ok':False,'base_key':'','overlay':False,'reasons':[],'sources':[],'missing':[],'assumptions':[]}
    if zone and ksh=='28352':
        local_rules=tisza_local_rules(page.get('html',''),page.get('url',''),zone,
            bool(source_valid and (plan_zone.get('zone') or (manual_zone.strip() and map_verified))))
        if local_rules['ok']:
            render_tisza_local_rules(local_rules,zone,combined_params)
        else:
            st.info(local_rules['error'])
    progress.stage(progress.steps[5])
    if local_rules['ok']:
        with st.spinner("Országos NJT-források ellenőrzése…"):
            national_rules=load_national_rules(zone,True,local_rules)
        if plan_zone.get('zone') == zone and not manual_zone.strip():
            with st.spinner('A jóváhagyott terv készítési alapjának ellenőrzése…'):
                plan_basis=load_tisza_plan_basis(local_rules,zone,TISZA_PLAN_SHA256)
        previous_overlay=None
        if (case.get('scenario')=='existing' and isinstance(case.get('started'),date)
                and date(2025,7,1)<=case['started']<date(2026,1,14)):
            with st.spinner('A korábbi TÉKA-kiegészítések időállapotának ellenőrzése…'):
                previous_overlay=load_previous_teka_overlay()
        applicability=select_national_applicability(case,plan_basis,national_rules,previous_overlay=previous_overlay)
        render_applicability(applicability,plan_basis)
        render_national_rules(national_rules,zone,local_rules,applicability)
        render_buildability_summary(local_rules,national_rules,zone,applicability)
    render_purpose_review(purpose_review(case,local_rules,national_rules,zone,applicability))
    proposal_result = proposal_review(case.get('proposal'),zone_table_doc,zone,local_rules,zone_table_source)
    render_proposal_review(proposal_result)
    render_national_green_review(national_green_review(case,national_rules,applicability))
    render_national_parking_review(national_parking_review(case,national_rules,applicability))
    render_parking_demand_review(parking_demand_review(case,national_rules,applicability))
    render_case_review(case)

    # 6. Korlátozások
    st.header("6. Telekspecifikus korlátozások")
    st.info(
        "Védősáv, védőterület, hidrogeológiai védőterület, veszélyességi övezet, "
        "szabályozási/építési vonal vagy más térbeli korlátozás csak akkor tekinthető "
        "telekspecifikusnak, ha a telekkel való térbeli érintettség igazolható."
    )

    # 7. Összegzés
    progress.stage(progress.steps[6])
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
                       else "nyilvános HRSZ-szolgáltatás – pontos település és HRSZ, telekgeometriával" if parcel_api
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
    if spatial.get('hits'):
        summary.append({'Adat':'Pontos HRSZ-felirat a terven',
                        'Eredmény':f"{len(spatial['hits'])} felirattalálat; telekhatár és övezet külön igazolandó",
                        'Forrás':plan_source,
                        'Bizonyosság':'pontos natív felirat vagy több felbontásban egyező OCR-jelölt'})
    if meta and meta.get('scope_note'):
        summary.append({'Adat': 'Helyi rendelet területi hatálya',
                        'Eredmény': meta['scope_note'],
                        'Forrás': meta['url'] + '#SZ1',
                        'Bizonyosság': 'a telekre vonatkozó területi hatály külön igazolandó'})
    if local_rules['ok']:
        summary.append({'Adat':'Helyi szöveges szabályok',
            'Eredmény':f"{len(local_rules['rows'])} forrásolt helyi szabály; {len(local_rules['conditional'])} területi feltétel külön ellenőrizendő",
            'Forrás':'TÉSZ – pontos § és bekezdés',
            'Bizonyosság':'ellenőrzött helyi szabálykapcsolat; az építési lehetőség teljeskörűen nem igazolt'})
    if national_rules:
        summary.append({"Adat":"Országos rendeltetési szabályok","Eredmény":f"{sum(r['ok'] for r in national_rules)}/4 ellenőrzött forráskiadás",
            "Forrás":"OTÉK és TÉKA – pontos § és bekezdés","Bizonyosság":("alkalmazási csomag kiválasztva; a konkrét építési feltételek még vizsgálandók" if applicability.get("ok") else "alkalmazási alap még igazolandó; építési jogosultság nem megállapított")})
    st.dataframe(summary, hide_index=True, use_container_width=True)

    st.caption(
        "A TelekElőírás AI döntéstámogató eszköz. Nem helyettesíti a hatósági, "
        "tervezői vagy jogi ellenőrzést. A program bizonytalan adatból nem készít "
        "biztos telekspecifikus állítást."
    )

    report = investigation_report(town, hrsz, zone, combined_params, summary,
                                  local_rules, national_rules, case,
                                  parameter_source=zone_table_source, plan_source=plan_source,
                                  applicability=applicability, plan_basis=plan_basis, proposal_result=proposal_result,
                                  source_inventory=source_inventory,label_search=spatial)
    progress.finish()
    result={'report':report,'minerva_geometry':json.dumps(minerva_diag['geometry_snapshots'],ensure_ascii=False).encode('utf-8') if minerva_diag.get('geometry_snapshots') else None}
    if not source_valid or not (plan_zone.get('zone') or spatial.get('hits')) or (spatial.get('scan') and not spatial['scan'].get('complete')):
        raise UncachedInvestigationResult(result)
    return result


if __name__ == "__main__":
    main()
