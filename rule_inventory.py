"""Municipality-independent source inventory, never a building permission.

The inventory preserves complete source clauses and explicitly separates
retrieval from spatial and temporal applicability. No municipality or parcel
identifiers are embedded in this module.
"""
from dataclasses import dataclass
import hashlib
import re
import unicodedata
from urllib.parse import urlsplit


def key(value):
    value = unicodedata.normalize('NFKD', str(value or ''))
    return ''.join(c for c in value if not unicodedata.combining(c)).casefold()


CATEGORIES = {
    'zone_map': ('Övezet, szabályozási vonal és térképi érintettség', ('szabalyozasi vonal', 'ovezethatar', 'szabalyozasi terv')),
    'protection': ('Környezet-, természet-, örökségvédelem, régészet és honvédelem', ('kornyezetved', 'termeszetved', 'oroksegved', 'regesz', 'honvedel', 'natura 2000')),
    'buffers': ('Védőterületek és védőtávolságok', ('vedoterulet', 'vedotavolsag', 'vedosav', 'hidrogeolog')),
    'parameters': ('Beépítési mutatók, közműellátás és parkolás', ('beepitettseg', 'beepitesi mod', 'epitmenymagassag', 'epuletmagassag', 'beepitesi magassag', 'zoldfelulet', 'kozmu', 'parkol', 'gepjarmu')),
    'uses': ('Elhelyezhető és tiltott rendeltetések', ('rendeltetes', 'elhelyezheto', 'nem helyezheto', 'tilos', 'letesitheto')),
    'placement': ('Építési hely, kertek és telekalakítás', ('epitesi hely', 'elokert', 'oldalkert', 'hatsokert', 'telekalakit', 'telekterulet', 'telekszelesseg', 'telekmelyseg')),
    'public_space': ('Közterület és zöldinfrastruktúra', ('kozterulet-alakit', 'kozterulet alakit', 'zoldinfrastruktura', 'zoldhalozat')),
    'appearance': ('Anyaghasználat, szín, tömeg, homlokzat és zöldfelület', ('anyaghasznal', 'szinez', 'tomegformal', 'homlokzat', 'tetohejazat', 'telepuleskep')),
    'enforcement': ('Településkép-érvényesítési eszközök', ('telepuleskep-ervenyesit', 'telepuleskep ervenyesit')),
    'consultation': ('Tájékoztatás és szakmai konzultáció', ('szakmai konzultacio', 'tajekoztatas')),
    'procedures': ('Településképi véleményezés és bejelentés', ('telepuleskepi velemenyez', 'telepuleskepi bejelent')),
    'penalties': ('Településképi kötelezés és bírság', ('telepuleskepi kotelez', 'telepuleskep-vedelmi birsag', 'telepuleskep vedelmi birsag')),
    'local_heritage': ('Helyi emlékek védelme és védetté nyilvánítás', ('helyi vedelem', 'helyi vedett', 'vedette nyilvanit', 'vedettseg megszun')),
    'compensation': ('Kártalanítás és településrendezési kötelezés', ('kartalanit', 'telepulesrendezesi kotelez')),
}


@dataclass(frozen=True)
class Clause:
    text: str
    source_url: str
    locator: str
    edition: str
    source_hash: str

    @property
    def citation(self):
        return self.source_url.split('#')[0] + ('#' + self.locator if self.locator else '')


def zone_key(code):
    return re.sub(r'\s+', '', key(code))


def has_exact_zone(text, zone):
    """Spacing may differ; identifiers and their punctuation may not.

    In particular Lke 1.2 must never match Lke 1.21 or Lke 2.1. Hyphen,
    slash and dot are meaningful and are not silently interchanged.
    """
    if not zone:
        return False
    pattern = r'\s*'.join(re.escape(character) for character in zone_key(zone))
    return bool(re.search(r'(?<![\w/])' + pattern + r'(?![\w./-])', key(text)))


def official_clause(clause):
    try:
        url = urlsplit(clause.source_url)
        port = url.port
    except (ValueError, TypeError):
        return False
    return (url.scheme == 'https' and not url.username and not url.password
            and port in {None, 443}
            and url.hostname in {'njt.jog.gov.hu', 'njt.hu', 'or.njt.hu'}
            and bool(clause.locator) and bool(clause.edition)
            and bool(re.fullmatch(r'[0-9a-f]{64}', clause.source_hash)))


def build_inventory(clauses, zone='', zone_verified=False, source_verified=False):
    """Return evidence and coverage, without inferring applicability.

    Exact zone mentions are document references, not automatic overrides of
    conditions, exclusions, territorial jurisdiction or effective-date rules.
    A missing hit is a retrieval gap, never proof that a requirement is absent.
    """
    out = {'ok': False, 'rows': [], 'coverage': [], 'errors': [], 'zone': zone,
           'zone_verified': bool(zone_verified), 'source_verified': bool(source_verified)}
    if not source_verified:
        out['errors'].append('A helyi jogszabályforrás tartalmi ellenőrzése hiányzik.')
        return out
    accepted = []
    seen = set()
    for clause in clauses:
        if not isinstance(clause, Clause) or not official_clause(clause):
            out['errors'].append('Hiányos vagy nem hivatalos forrású rendelkezés kimaradt.')
            continue
        identity = (clause.source_url, clause.edition, clause.locator, clause.text)
        if identity in seen:
            continue
        seen.add(identity)
        # Keep the entire provision: no number borrowing across neighbours,
        # no sentence truncation that could drop a closing ban or exception.
        normalized = key(clause.text)
        exact = has_exact_zone(clause.text, zone)
        relation = ('A megadott övezeti kód kifejezetten szerepel' if exact
                    else 'Általános vagy más hatályú rendelkezés – alkalmazhatósága vizsgálandó')
        for category, (title, needles) in CATEGORIES.items():
            if any(needle in normalized for needle in needles):
                accepted.append((category, {
                    'Téma': title, 'Kapcsolat az övezettel': relation,
                    'Teljes rendelkezés': clause.text,
                    'Forrás': clause.citation, 'Forrás időállapota': clause.edition,
                    'Forrás SHA-256': clause.source_hash,
                    'Állapot': 'Forrásból kinyert; telekre és ügyre alkalmazhatósága még nem igazolt',
                }))
    out['rows'] = [row for _, row in accepted]
    for category, (title, _) in CATEGORIES.items():
        matches = [row for name, row in accepted if name == category]
        explicit = sum(row['Kapcsolat az övezettel'].startswith('A megadott') for row in matches)
        out['coverage'].append({'Vizsgálat': title,
                                'Kinyert rendelkezés': len(matches),
                                'Pontos övezeti hivatkozás': explicit,
                                'Állapot': ('Forrástalálat; alkalmazhatóság ellenőrizendő' if matches else
                                           'Nincs kinyert találat; további forrásfeldolgozás szükséges')})
    out['ok'] = bool(out['rows'])
    return out


def source_digest(raw):
    return hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode('utf-8')).hexdigest()
