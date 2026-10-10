"""Public OGC discovery with source receipts; availability is never legal proof.

Only published links are followed. No session RPC, credentials, endpoint
enumeration or certificate bypass is used. Raster and existing land use cannot
be promoted to legally effective planned zoning.
"""
from datetime import datetime, timezone
import hashlib
import io
from html.parser import HTMLParser
import json
from pathlib import Path
import urllib.error
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET
import zipfile
import shapefile

CATALOG = 'https://inspire.lechnerkozpont.hu/geonetwork/srv/eng/csw'
CSW = '{http://www.opengis.net/cat/csw/2.0.2}'
OFFICIAL_HOSTS = frozenset({'inspire.lechnerkozpont.hu', 'data.lechnerkozpont.hu',
    'www.oeny.hu', 'eter.e-epites.hu', 'ekozmu.e-epites.hu',
    'geoportal.vizugy.hu', 'www.miskolc.hu', 'map.tiszaujvaros.hu',
    'minerva.bp12ker.hu', 'www.komadi.hu', 'www.gersekarat.hu'})
# These are official entry pages, not municipality-specific zone rules.
ENTRY_PAGES = {'Miskolc': ['https://www.miskolc.hu/varoshaza/onkormanyzat/strategiak-koncepciok/miskolc-megyei-jogu-varos-epitesi-szabalyzata'],
    'Tiszaújváros': ['https://map.tiszaujvaros.hu/'],
    'Budapest XII. kerület': ['https://minerva.bp12ker.hu/minerva/bp12ker/internet.php'],
    'Komádi': ['https://www.komadi.hu/'],
    'Gersekarát': ['https://www.gersekarat.hu/hu/onkormanyzat/letoltheto-dokumentumok']}


def public_url(url, hosts=OFFICIAL_HOSTS):
    """Reject malformed authorities as well as non-allowlisted destinations."""
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname in hosts
                and parsed.port in (None, 443) and not parsed.username
                and not parsed.password)
    except ValueError:
        # urlsplit / hostname / port can raise for invalid IPv6 or ports.
        return False


class OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_url(newurl):
            raise ValueError('Nem engedélyezett hivatalos HTTPS-átirányítás: '+newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_source(url, cache='work/gis-cache', timeout=20):
    """Bounded, certificate-verified fetch and hash-addressed audit bytes."""
    if not public_url(url):
        raise ValueError('A cím nincs az ellenőrzött hivatalos forrásjegyzékben.')
    receipt = {'url': url, 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
               'status': 0, 'sha256': '', 'error': ''}
    try:
        with build_opener(OfficialRedirect()).open(Request(url, headers={
                'User-Agent': 'TelekEloirasAI public OGC source verification',
                'Accept': 'application/xml,application/json,text/html,*/*'}), timeout=timeout) as response:
            body = response.read(10*1024*1024+1)
            if len(body) > 10*1024*1024:
                raise ValueError('A válasz meghaladja a 10 MiB ellenőrzési korlátot.')
            receipt.update(status=response.status, final_url=response.url,
                           content_type=response.headers.get('Content-Type',''),
                           bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
            folder = Path(cache); folder.mkdir(parents=True, exist_ok=True)
            (folder/(receipt['sha256']+'.bin')).write_bytes(body)
            return body, receipt
    except urllib.error.HTTPError as exc:
        receipt.update(status=exc.code, error=str(exc))
    except (OSError, ValueError) as exc:
        receipt['error'] = str(exc)
    return b'', receipt


def query_url(url, **parameters):
    parts = urlsplit(url)
    replaced = {key.lower() for key in parameters}
    query = [(k,v) for k,v in parse_qsl(parts.query) if k.lower() not in replaced]
    query.extend(parameters.items())
    return urlunsplit(parts._replace(query=urlencode(query), fragment=''))


def catalog_page(body):
    root = ET.fromstring(body)
    results = root.find(CSW+'SearchResults')
    if results is None:
        raise ValueError('A CSW-válasz nem tartalmaz SearchResults elemet.')
    records = []
    for item in results.findall(CSW+'Record'):
        fields = {name: [e.text or '' for e in item if e.tag.rsplit('}',1)[-1] == name]
                  for name in ('identifier','title','abstract','subject','rights','date')}
        fields['links'] = [dict(e.attrib, url=e.text or '') for e in item
                           if e.tag.rsplit('}',1)[-1] == 'URI']
        records.append(fields)
    returned = int(results.get('numberOfRecordsReturned','0'))
    if returned != len(records):
        raise ValueError('A CSW rekordszáma és a feldolgozott rekordok nem egyeznek.')
    return records, int(results.get('numberOfRecordsMatched','0')), int(results.get('nextRecord','0'))


def discover_catalog(fetch=read_source):
    records, receipts, seen = [], [], set()
    position, total = 1, None
    try:
        while position and len(receipts) < 10:
            if position in seen: raise ValueError('Ismétlődő CSW lapozás.')
            seen.add(position)
            body, receipt = fetch(query_url(CATALOG, service='CSW', version='2.0.2',
                request='GetRecords', resultType='results', typeNames='csw:Record',
                elementSetName='full', maxRecords='100', startPosition=str(position)))
            receipts.append(receipt)
            if receipt.get('error'): raise ValueError(receipt['error'])
            page, matched, position = catalog_page(body)
            if total is not None and total != matched: raise ValueError('A katalógus lapozás közben megváltozott.')
            total = matched; records.extend(page)
        ids = [r['identifier'] for r in records]
        complete = not position and len(records) == total and all(ids) and len({tuple(i) for i in ids}) == len(ids)
        return {'records': records, 'receipts': receipts, 'complete': complete,
                'matched': total, 'error': '' if complete else 'Hiányos katalógus.'}
    except (ValueError, ET.ParseError) as exc:
        return {'records': records, 'receipts': receipts, 'complete': False,
                'matched': total, 'error': str(exc)}


def service_kind(link):
    protocol = link.get('protocol','').upper()
    if 'WFS' in protocol: return 'WFS'
    if 'WMS' in protocol or 'WMTS' in protocol: return 'raster'
    if 'ATOM' in protocol: return 'ATOM'
    return 'download' if 'DOWNLOAD' in protocol else 'link'


def capabilities(body):
    root = ET.fromstring(body)
    kind = root.tag.rsplit('}',1)[-1]
    if kind not in ('WFS_Capabilities', 'WMS_Capabilities', 'WMT_MS_Capabilities'):
        raise ValueError('Nem WFS/WMS GetCapabilities válasz.')
    vector = kind == 'WFS_Capabilities'
    layers = []
    for node in root.iter():
        if node.tag.rsplit('}',1)[-1] != ('FeatureType' if vector else 'Layer'): continue
        fields = {e.tag.rsplit('}',1)[-1]: e.text or '' for e in node}
        if fields.get('Name'): layers.append(fields)
    return {'kind': 'WFS' if vector else 'raster', 'version': root.get('version',''),
            'layers': layers, 'zoning_verified': False}


class PublishedLinks(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href')
            if href: self.links.append(href)


def discover_place(place, *, catalog=None, fetch=read_source):
    """Inspect official entry pages and published relevant catalogue services.

    Discovery is an evidence inventory. It cannot attest current legal zoning
    from a dataset title, service availability or a land-cover classification.
    """
    catalog = discover_catalog(fetch) if catalog is None else catalog
    result = {'place': place, 'catalog_complete': catalog['complete'],
              'catalog_receipts': catalog['receipts'], 'catalog_error': catalog['error'],
              'entries': [], 'datasets': [], 'services': [], 'zoning_verified': False,
              'missing': ['Hatályos HÉSZ-hez kötött övezeti vektorforrás.',
                          'Teljes területi korlátozáskészlet és alkalmazhatóság.']}
    for url in ENTRY_PAGES.get(place, []):
        body, receipt = fetch(url)
        parser = PublishedLinks(); parser.feed(body.decode('utf-8','replace'))
        result['entries'].append(dict(receipt, published_gis_links=[urljoin(url,l)
            for l in parser.links if any(s in l.lower() for s in
            ('service=wfs','service=wms','.geojson','.gml','.shp','.gpkg'))]))
    selected = []
    for record in catalog['records']:
        text = ' '.join(record['title']+record['abstract']+record['subject']).lower()
        if not any(term in text for term in ('cadastral','protected sites','land use - national','planned land use',place.lower())): continue
        result['datasets'].append(record)
        for link in record['links']:
            if service_kind(link) in ('WFS','raster') and public_url(link['url']): selected.append(link['url'])
    for url in dict.fromkeys(selected):
        body, receipt = fetch(url)
        service = dict(receipt, zoning_verified=False)
        if not receipt.get('error'):
            try: service.update(capabilities(body))
            except (ValueError, ET.ParseError) as exc: service['error']=str(exc)
        result['services'].append(service)
    return result


def decode_polygon_features(body, *, code_field, expected_crs):
    """Decode complete GeoJSON WFS data with explicit CRS and caller's schema.

    This produces geometry candidates, not boundary/legal verification flags.
    Missing counts, pagination, CRS or any invalid member reject the whole set.
    """
    from pyproj import CRS
    from shapely.geometry import shape
    document = json.loads(body)
    if document.get('type') != 'FeatureCollection': raise ValueError('Nem FeatureCollection.')
    source_crs = document.get('crs',{}).get('properties',{}).get('name')
    if not source_crs or CRS(source_crs) != CRS(expected_crs): raise ValueError('Hiányzó vagy eltérő CRS.')
    features = document.get('features',[])
    matched = document.get('numberMatched',document.get('totalFeatures'))
    if str(matched) != str(len(features)) or document.get('numberReturned',len(features)) != len(features):
        raise ValueError('Hiányos vagy nem igazoltan teljes WFS-válasz.')
    if any(link.get('rel') == 'next' for link in document.get('links',[])):
        raise ValueError('További WFS-oldal szükséges.')
    result=[]
    for feature in features:
        code = feature.get('properties',{}).get(code_field)
        geometry = feature.get('geometry')
        polygon = shape(geometry)
        if not isinstance(code,str) or not code.strip() or polygon.geom_type not in ('Polygon','MultiPolygon') or not polygon.is_valid or polygon.is_empty:
            raise ValueError('Érvénytelen övezeti kód vagy poligon.')
        result.append({'code':code.strip(),'geometry':geometry,'crs':expected_crs})
    return result


def preferred_source(sources):
    """Choose verified evidence, never let an unverified vector displace PDF."""
    priority={'official_vector':0,'official_spatial_plan':1,'official_pdf_legend':2}
    eligible=[s for s in sources if s.get('current_verified') is True
              and s.get('boundary_verified') is True and s.get('source_hash')
              and s.get('kind') in priority]
    return min(eligible,key=lambda s:priority[s['kind']]) if eligible else None


def shapefile_intersections(body, parcel_geometry, parcel_crs):
    """Read every SHP member in memory, transform by PRJ, retain intersections.

    No archive paths are extracted. Null/invalid source geometries are counted
    without repairing them; an empty result never proves absence of protection.
    """
    from pyproj import CRS, Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    parcel=shape(parcel_geometry)
    if not parcel.is_valid or parcel.is_empty or parcel.geom_type not in ('Polygon','MultiPolygon'):
        raise ValueError('Érvénytelen telekpoligon.')
    archive=zipfile.ZipFile(io.BytesIO(body))
    members=archive.infolist()
    if sum(m.file_size for m in members)>50*1024*1024:
        raise ValueError('Túl nagy kicsomagolt téradat.')
    names={m.filename for m in members}
    layers=[]; hits=[]; missing_geometry=0; invalid_geometry=0
    for name in sorted(names):
        if not name.lower().endswith('.shp'):continue
        stem=name[:-4]
        if not all(stem+ext in names for ext in ('.dbf','.shx','.prj')):
            raise ValueError('Hiányos SHP-adatkészlet: '+stem)
        source_crs=CRS.from_wkt(archive.read(stem+'.prj').decode('utf-8'))
        convert=Transformer.from_crs(source_crs,CRS(parcel_crs),always_xy=True)
        encoding=archive.read(stem+'.cpg').decode().strip() if stem+'.cpg' in names else 'utf-8'
        with shapefile.Reader(shp=io.BytesIO(archive.read(name)),
                shx=io.BytesIO(archive.read(stem+'.shx')),
                dbf=io.BytesIO(archive.read(stem+'.dbf')), encoding=encoding) as reader:
            count=0; null_count=0; invalid_count=0
            for item in reader.iterShapeRecords():
                count+=1
                if item.shape.shapeType == shapefile.NULL:
                    null_count+=1;missing_geometry+=1
                    continue
                geometry=shape(item.shape.__geo_interface__)
                if not geometry.is_valid or geometry.is_empty:
                    invalid_count+=1;invalid_geometry+=1
                    continue
                projected=transform(convert.transform,geometry)
                if parcel.intersects(projected):
                    hits.append({'layer':stem,'attributes':item.record.as_dict(),
                                 'legal_applicability_verified':False})
            layers.append({'layer':stem,'features':count,'missing_geometry':null_count,
                           'invalid_geometry':invalid_count,
                           'source_crs':source_crs.to_string()})
    if not layers:raise ValueError('Nincs feldolgozható SHP-réteg.')
    return {'layers':layers,'intersections':hits,'spatial_snapshot_checked':True,
            'spatial_snapshot_complete':missing_geometry==0 and invalid_geometry==0,
            'missing_geometry':missing_geometry,'invalid_geometry':invalid_geometry,
            'current_legal_protection_verified':False,'complete_restrictions_verified':False}


_shared_catalog=None
_shared_heritage=None


def dataset_license(record, fetch=read_source):
    identifier=record['identifier'][0]
    url=query_url(CATALOG,service='CSW',version='2.0.2',request='GetRecordById',
        id=identifier,elementSetName='full',outputSchema='http://www.isotc211.org/2005/gmd')
    body,receipt=fetch(url)
    result={'receipt':receipt,'public_reuse_verified':False,'license':'',
            'license_url':'','attribution':'Lechner Tudásközpont; TelekElőírás AI térbeli metszésvizsgálata.'}
    if receipt.get('error'):return result
    try:
        root=ET.fromstring(body)
        identifiers=[ ''.join(e.itertext()).strip() for e in root.iter()
                     if e.tag.endswith('}fileIdentifier')]
        legal=' '.join(' '.join(e.itertext()) for e in root.iter()
                       if e.tag.endswith('}MD_LegalConstraints'))
        if (identifier in identifiers and 'Creative Commons Attribution 4.0' in legal
                and 'No limitations on public access' in legal):
            result.update(public_reuse_verified=True,license='CC BY 4.0',
                          license_url='https://creativecommons.org/licenses/by/4.0/')
    except ET.ParseError as exc:result['error']=str(exc)
    return result


def automatic_gis_evidence(place, parcel_geometry=None, *, parcel_verified=False):
    """Shared national discovery plus municipality-specific live receipts."""
    global _shared_catalog, _shared_heritage
    now=datetime.now(timezone.utc)
    if _shared_catalog is None or (now-_shared_catalog[0]).total_seconds()>900:
        _shared_catalog=(now,discover_catalog())
    result=discover_place(place,catalog=_shared_catalog[1])
    heritage_record=next((record for record in result['datasets']
        if any('Protected Sites - Cultural heritage' in title for title in record['title'])),None)
    heritage=next((link for link in (heritage_record or {}).get('links',[])
        if service_kind(link)=='download' and link['url'].endswith('.zip')),None)
    if heritage and parcel_geometry:
        url=heritage['url']
        if _shared_heritage is None or _shared_heritage[1]!=url or (now-_shared_heritage[0]).total_seconds()>900:
            license=dataset_license(heritage_record)
            if license['public_reuse_verified']:body,receipt=read_source(url)
            else:body,receipt=b'',{'url':url,'error':'A nyilvános újrafelhasználási engedély nincs igazolva.'}
            _shared_heritage=(now,url,body,receipt,license)
        body,receipt,license=_shared_heritage[2:]
        audit=dict(receipt,parcel_boundary_verified=parcel_verified,
                   reuse_evidence=license,
                   current_legal_protection_verified=False,complete_restrictions_verified=False)
        if not receipt.get('error'):
            try:audit.update(shapefile_intersections(body,parcel_geometry,'EPSG:23700'))
            except (ValueError,OSError,zipfile.BadZipFile,shapefile.ShapefileException) as exc:audit['error']=str(exc)
        result['heritage_snapshot']=audit
    result['selected_method']='official_pdf_legend'
    result['selection_reason']='A feltárt GIS-adatok hatályos övezeti tervkapcsolata nincs igazolva; a saját jelmagyarázatú hivatalos terv vizsgálata folytatódik.'
    return result
