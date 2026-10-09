import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
from pyproj import CRS, Transformer
from shapely.geometry import mapping, box

import app
from geopdf import page_registrations, to_pdf, to_world


def geo_document():
    """Actual PDF GEO dictionaries and named layers, without municipality data."""
    doc = fitz.open()
    page = doc.new_page(width=1000, height=800)
    crs = CRS.from_epsg(23700)
    geographic = Transformer.from_crs(crs, crs.geodetic_crs, always_xy=True)
    controls = [(20, 780), (20, 20), (980, 20), (980, 780)]
    gpts = []
    for x, y in controls:
        lon, lat = geographic.transform(600000+x, 250000-y)
        gpts.extend((lat, lon))
    gcs, measure, viewport = [doc.get_new_xref() for _ in range(3)]
    doc.update_object(gcs, '<< /Type /PROJCS /WKT ' + fitz.get_pdf_str(crs.to_wkt()) + ' >>')
    doc.update_object(measure, f'<< /Type /Measure /Subtype /GEO /GCS {gcs} 0 R '
                      '/LPTS [0 0 0 1 1 1 1 0] /GPTS [' +
                      ' '.join(str(v) for v in gpts) + '] >>')
    doc.update_object(viewport, f'<< /Type /Viewport /BBox [20 20 980 780] /Measure {measure} 0 R >>')
    doc.xref_set_key(page.xref, 'VP', f'[{viewport} 0 R]')
    cadastral = doc.add_ocg('Földrészlet')
    zones = doc.add_ocg('Epitesi_ovezet_polygon')
    page.draw_rect(fitz.Rect(300, 300, 400, 400), color=(0, 0, 0), oc=cadastral)
    page.insert_text((325, 355), '034/15', fontsize=10)
    page.draw_rect(fitz.Rect(200, 200, 500, 500), fill=(1, 1, 1), color=None,
                   oc=zones, overlay=False)
    page.insert_text((230, 250), 'Gip/3', fontsize=10)
    # Reopening also exercises the real PDF optional-content layer reader.
    raw = doc.tobytes()
    doc.close()
    return fitz.open(stream=raw, filetype='pdf')


class AutomaticSourceTests(unittest.TestCase):
    def page(self, district='XII', edition='2026.01.01.'):
        html = (f'<title>Budapest Főváros {district}. Kerület önkormányzati rendelete</title>'
                f'<div class="hataly">{edition}</div><p>Kerületi építési szabályzat</p>')
        return dict(ok=True, url='https://njt.jog.gov.hu/jogszabaly/test', html=html,
                    text=f'Budapest Főváros {district}. Kerület kerületi építési szabályzat',
                    links=[('szabályozási terv', 'https://njt.jog.gov.hu/document/test.pdf')])

    def test_official_budapest_header_matches_exact_district(self):
        meta={'municipality':'Budapest XII. kerület'}
        self.assertTrue(app.validate_njt_source(meta['municipality'],meta,self.page())[0])
        self.assertFalse(app.validate_njt_source('Budapest XI. kerület',meta,self.page())[0])
        self.assertTrue(app.validate_njt_source('Budapest 12. kerület',meta,self.page())[0])
        page=self.page('XI');page['text']+=' Budapest XII. kerület'
        self.assertFalse(app.validate_njt_source(meta['municipality'],meta,page)[0])

    def test_plan_loader_binds_download_to_current_link_and_hash(self):
        doc=fitz.open();doc.new_page();raw=doc.tobytes()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'plan.pdf';path.write_bytes(raw)
            with patch.object(app,'try_auto_plan',return_value=(doc,'https://njt.jog.gov.hu/document/test.pdf','')), \
                 patch.object(app,'download_pdf_location',return_value=(str(path),'https://njt.jog.gov.hu/document/test.pdf')):
                result=app.load_official_plan(self.page(),{'municipality':'Budapest XII. kerület'},'034/15')
        self.assertTrue(result['current_verified'])
        self.assertEqual(result['source_hash'], app.source_digest(raw))
        self.assertEqual(result['edition'],'2026.01.01.')
        self.assertTrue(doc.is_closed)

    def test_wrong_or_future_source_never_downloads_plan(self):
        for page in (self.page('XI'),self.page(edition='2099.01.01.'),self.page(edition='unknown')):
            with self.subTest(page=page['text']), patch.object(app,'try_auto_plan') as download:
                result=app.load_official_plan(page,{'municipality':'Budapest XII. kerület'},'034/15')
                self.assertFalse(result['current_verified']);self.assertTrue(result['error'])
                download.assert_not_called()

    def test_unlinked_or_external_plan_cannot_be_promoted(self):
        for url in ['https://example.com/plan.pdf','https://njt.jog.gov.hu/document/not-linked.pdf']:
            doc=fitz.open();doc.new_page()
            with patch.object(app,'try_auto_plan',return_value=(doc,url,'')), \
                 patch.object(app,'download_pdf_location') as download:
                result=app.load_official_plan(self.page(),{'municipality':'Budapest XII. kerület'},'034/15')
            self.assertFalse(result['current_verified']);download.assert_not_called()
            self.assertTrue(doc.is_closed)

    def test_unavailable_plan_is_an_explicit_partial_result(self):
        with patch.object(app,'try_auto_plan',return_value=(None,'','HTTP 503')):
            result=app.load_official_plan(self.page(),{'municipality':'Budapest XII. kerület'},'034/15')
        self.assertTrue(result['source_valid']);self.assertFalse(result['current_verified'])
        self.assertEqual(result['error'],'HTTP 503')

    def test_gersekarat_index_selects_plan_instead_of_text_annex(self):
        source=app.source_for_town('Gersekarát')
        self.assertIn('Gersekar_t-szt.pdf',source['plan_url'])
        self.assertNotIn('H_SZ.pdf',source['plan_url'])

    def test_geometry_failure_preserves_proven_hrsz_and_retries(self):
        app.public_parcel_geometry.clear()
        with patch.object(app,'_json_get',side_effect=lambda url: [{"id":"1","lotNumber":"034/15"}]
                          if '/search?' in url else (_ for _ in ()).throw(RuntimeError('HTTP 503'))) as get:
            for attempt in range(2):
                with self.assertRaises(app.ParcelGeometryUnavailable) as caught:
                    app.public_parcel_geometry('00001','034/15')
                self.assertEqual(caught.exception.result['id'],'1')
                self.assertEqual(caught.exception.result['geometry'],{})
                rows=app.automatic_evidence_rows(caught.exception.result,{}, {})
                self.assertTrue(rows[0]['Bizonyított']);self.assertFalse(rows[1]['Bizonyított'])
            self.assertEqual(get.call_count,4)

    def test_geo_coordinates_use_source_datum_and_round_trip(self):
        with geo_document() as doc:
            regs=page_registrations(doc[0]);self.assertEqual(len(regs),1)
            point=to_world((350,350),regs[0]['matrix'])
            self.assertAlmostEqual(point[0],600350,places=1)
            self.assertAlmostEqual(point[1],249650,places=1)
            self.assertLess(regs[0]['residual_m'],.1)
            restored=to_pdf(point,regs[0]['matrix'])
            self.assertAlmostEqual(restored[0],350,places=6)

    def test_plain_or_rotated_pdf_cannot_invent_coordinates(self):
        doc=fitz.open();doc.new_page()
        self.assertEqual(page_registrations(doc[0]),[]);doc.close()
        with geo_document() as doc:
            doc[0].set_rotation(90)
            self.assertEqual(page_registrations(doc[0]),[])

    def test_closed_source_parcel_and_zone_reach_shared_engine(self):
        with geo_document() as doc:
            outline=mapping(box(600300,249600,600400,249700).buffer(2))
            parcel={'parcel_polygon_candidate':True,'geometry':{'outline':outline}}
            result=app.geopdf_parcel_zone(doc,parcel,'034/15')
        self.assertTrue(result['parcel_boundary_verified'])
        self.assertEqual(len(result['zone_features']),1)
        inputs=dict(current_verified=True,edition='2026.01.01.',source_hash='a'*64,
                    source_url='https://njt.jog.gov.hu/document/test.pdf')
        linked=app.connect_automatic_zone(parcel,result,inputs,'28352','034/15')
        self.assertTrue(linked['intersection_verified']);self.assertEqual(linked['zone'],'Gip/3')

    def test_wrong_hrsz_cannot_borrow_source_boundary(self):
        with geo_document() as doc:
            parcel={'parcel_polygon_candidate':True,'geometry':{
                'outline':mapping(box(600300,249600,600400,249700).buffer(2))}}
            result=app.geopdf_parcel_zone(doc,parcel,'034/150')
        self.assertFalse(result['parcel_boundary_verified'])
        self.assertEqual(result['zone_features'],[])

    def test_display_outline_and_recognised_zone_are_not_polygon_proof(self):
        result=app.connect_automatic_zone(
            {'geometry':{'outline':mapping(box(0,0,10,10))}},
            {'zone':'Gip/3','parcel_wkt':box(0,0,10,10).wkt,'hrsz_method':'OCR'},
            dict(current_verified=True,edition='2026.01.01.',source_hash='a'*64,
                 source_url='https://njt.jog.gov.hu/document/test.pdf'),'28352','034/15')
        self.assertFalse(result['intersection_verified'])
        self.assertFalse(result['parcel_boundary_verified'])
        self.assertEqual(result['candidate_zone'],'Gip/3')

    def test_source_failure_does_not_prevent_independent_rule_fetch(self):
        with patch.object(app,'resolve_settlement_code',side_effect=RuntimeError('offline')), \
             patch.object(app,'source_for_town',return_value={'url':'url'}), \
             patch.object(app,'fetch_njt_page',return_value={}) as fetch, \
             patch.object(app,'load_official_plan',return_value={'error':'no plan'}), \
             patch.object(app,'local_source_inventory',return_value={'rows':[],'errors':[]}):
            result=app.inspect_official_parcel('Test','034/15')
        fetch.assert_called_once();self.assertTrue(result['errors'])
        self.assertEqual(len(result['evidence']),4)
        self.assertTrue(all(not row['Bizonyított'] for row in result['evidence']))

    def test_rules_inventory_is_not_complete_parcel_applicability(self):
        rows=app.automatic_evidence_rows({'search_url':'url'}, {'edition':'2026.01.01.'},
            {'intersection_verified':True,'zone':'A','parcel_boundary_verified':True},rules_available=True)
        self.assertTrue(rows[2]['Bizonyított']);self.assertFalse(rows[3]['Bizonyított'])

    def test_file_backed_plan_keeps_original_source_hash(self):
        with geo_document() as source, tempfile.TemporaryDirectory() as folder:
            raw=source.tobytes();path=Path(folder)/'plan.pdf';path.write_bytes(raw)
            inputs={'path':str(path),'source_hash':app.source_digest(raw)}
            with fitz.open(path) as doc:
                self.assertEqual(app.original_plan_bytes(doc,inputs),raw)
                path.write_bytes(raw+b'changed')
                with self.assertRaises(ValueError):app.original_plan_bytes(doc,inputs)

    def test_streamlit_investigation_displays_and_exports_separate_evidence(self):
        from streamlit.testing.v1 import AppTest
        script = """
import tempfile
from pathlib import Path
from unittest.mock import patch
from shapely.geometry import box,mapping
from test_automatic_sources import geo_document
import app
with geo_document() as source, tempfile.TemporaryDirectory() as folder:
    raw=source.tobytes();path=Path(folder)/'plan.pdf';path.write_bytes(raw)
    parcel={'id':'exact','search_url':'official-search','geometry_url':'official-geometry',
            'parcel_polygon_candidate':True,
            'geometry':{'outline':mapping(box(600300,249600,600400,249700).buffer(2))}}
    inputs={'path':str(path),'current_verified':True,'source_valid':True,'edition':'2026.01.01.',
            'source_hash':app.source_digest(raw),'source_url':'https://njt.jog.gov.hu/document/test.pdf'}
    meta={'municipality':'Test','url':'https://njt.jog.gov.hu/jogszabaly/test',
          'title':'Test','plan_url':inputs['source_url']}
    with patch.object(app,'resolve_settlement_code',return_value='00001'), \
         patch.object(app,'public_parcel_geometry',return_value=parcel), \
         patch.object(app,'source_for_town',return_value=meta), \
         patch.object(app,'fetch_njt_page',return_value={'ok':True,'text':'rules','html':'','url':meta['url']}), \
         patch.object(app,'validate_njt_source',return_value=(True,{})), \
         patch.object(app,'discover_attachments',return_value=[]), \
         patch.object(app,'load_official_plan',return_value=inputs), \
         patch.object(app,'local_source_inventory',return_value={'rows':[],'coverage':[],'errors':[]}):
        result=app.run_investigation('Test','034/15','',None,'','',False,{})
    app.render_automatic_download(result['automatic_evidence'])
"""
        at=AppTest.from_string(script,default_timeout=30).run()
        self.assertEqual(len(at.exception),0, [item.message for item in at.exception])
        evidence=[frame.value for frame in at.dataframe
                  if 'Bizonyított' in frame.value.columns and len(frame.value)==4]
        self.assertEqual(len(evidence),1)
        self.assertTrue(evidence[0].iloc[2]['Bizonyított'])
        self.assertFalse(evidence[0].iloc[3]['Bizonyított'])
        self.assertTrue(any(item.label=='Az automatikus azonosítás bizonyítékai (JSON)'
                            for item in at.get('download_button')))


if __name__=='__main__':
    unittest.main()
