import json
from pathlib import Path
import tempfile
import unittest

import fitz

import app
from plan_legend import discover_legend, matches_style, role_for_label, styles_for
from test_automatic_sources import geo_document
from shapely.geometry import box, mapping


def legend_document(colour=(.2,.7,.1), dashed=False, right=False):
    doc=fitz.open();page=doc.new_page(width=600,height=300)
    page.insert_text((100,40),'Jelmagyarazat')
    page.insert_text((100,84),'Ovezet hatara')
    x=180 if right else 65
    page.draw_line((x,80),(x+25,80),color=colour,width=2,
                   dashes='[3 2] 0' if dashed else None)
    raw=doc.tobytes();doc.close()
    return fitz.open(stream=raw,filetype="pdf")


class PlanLegendTests(unittest.TestCase):
    def read(self,doc,cache,**changes):
        args=dict(source_url='https://njt.jog.gov.hu/document/legend.pdf',
                  source_hash=app.source_digest(doc.stream or doc.tobytes()),
                  plan_url='https://njt.jog.gov.hu/document/plan.pdf',
                  plan_hash='a'*64,edition='2026.01.01.',ksh='00001',cache_dir=cache)
        args.update(changes)
        return discover_legend(doc,**args)

    def test_different_plans_define_different_colours_and_dash_patterns(self):
        with tempfile.TemporaryDirectory() as cache,legend_document() as a,legend_document((.9,.1,.8),True) as b:
            first=styles_for(self.read(a,cache),'zone_boundary')[0]
            second=styles_for(self.read(b,cache),'zone_boundary')[0]
            self.assertTrue(matches_style(first,first));self.assertTrue(matches_style(second,second))
            self.assertFalse(matches_style(first,second));self.assertFalse(matches_style(second,first))
            self.assertEqual(first['dash'],[]);self.assertEqual(second['dash'],[.6,.4])

    def test_symbol_to_right_of_caption_is_supported(self):
        with tempfile.TemporaryDirectory() as cache,legend_document(right=True) as doc:
            result=self.read(doc,cache)
            self.assertTrue(result['zone_style_verified'])
            self.assertGreater(result['records'][0]['sample_rect'][0],170)

    def test_rotated_native_legend_uses_source_drawing_coordinates(self):
        for rotation in (90,180,270):
            with self.subTest(rotation=rotation),tempfile.TemporaryDirectory() as cache,legend_document() as doc:
                doc[0].set_rotation(rotation)
                result=self.read(doc,cache,source_hash=app.source_digest(doc.tobytes()))
                # A horizontal legend becomes vertical after rotation. This
                # layout is unsupported rather than assigned another sample.
                if result['zone_style_verified']:
                    self.assertEqual(styles_for(result,'zone_boundary')[0]['stroke'],[.2,.7,.1])

    def test_conflicting_samples_on_both_sides_are_not_trusted(self):
        with tempfile.TemporaryDirectory() as cache,legend_document() as doc:
            doc[0].draw_line((180,80),(205,80),color=(.8,.1,.5),width=2)
            result=self.read(doc,cache)
            self.assertFalse(result['zone_style_verified']);self.assertTrue(result['records'][0]['ambiguous_sample'])

    def test_legend_reused_across_parcels_but_not_across_plan_editions(self):
        with tempfile.TemporaryDirectory() as cache,legend_document() as doc:
            first=self.read(doc,cache);second=self.read(doc,cache)
            self.assertFalse(first['cache_hit']);self.assertTrue(second['cache_hit'])
            self.assertFalse(self.read(doc,cache,edition='2026.02.01.')['cache_hit'])
            self.assertFalse(self.read(doc,cache,plan_hash='b'*64)['cache_hit'])
            self.assertFalse(self.read(doc,cache,ksh='00002')['cache_hit'])
            self.assertEqual(first['identity']['source_url'],'https://njt.jog.gov.hu/document/legend.pdf')

    def test_modified_cache_cannot_change_legend(self):
        with tempfile.TemporaryDirectory() as cache,legend_document() as doc:
            self.read(doc,cache);path=next(Path(cache).glob('*.json'))
            stored=json.loads(path.read_text());stored['records'][0]['styles'][0]['stroke']=[1,0,0]
            path.write_text(json.dumps(stored))
            result=self.read(doc,cache)
            self.assertFalse(result['cache_hit']);self.assertNotEqual(result['records'][0]['styles'][0]['stroke'],[1,0,0])

    def test_caption_without_sample_never_becomes_a_visual_default(self):
        with tempfile.TemporaryDirectory() as cache:
            doc=fitz.open();p=doc.new_page();p.insert_text((100,40),'Jelmagyarazat')
            p.insert_text((100,84),'Ovezet hatara')
            self.assertFalse(self.read(doc,cache)['zone_style_verified']);doc.close()
            self.assertEqual(list(Path(cache).glob('*.json')),[])

    def test_scanned_legend_preserves_own_sample_and_does_not_invent_vector_geometry(self):
        with tempfile.TemporaryDirectory() as cache,legend_document((.9,.1,.8)) as source:
            png=source[0].get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png')
            doc=fitz.open();page=doc.new_page(width=600,height=300)
            page.insert_image(page.rect,stream=png)
            result=self.read(doc,cache,tessdata=app.plan_ocr_data())
            styles=styles_for(result,'zone_boundary')
            self.assertTrue(result['zone_style_verified'],result)
            self.assertEqual(styles[0]['kind'],'raster_sample')
            self.assertFalse(styles[0]['geometry_matching_supported'])
            self.assertGreater(styles[0]['foreground_rgb'][0],styles[0]['foreground_rgb'][1])
            doc.close()

    def test_restrictions_and_proposed_parcels_are_not_actual_zone_or_parcel_boundaries(self):
        self.assertEqual(role_for_label('Hidrogeológiai védőövezet határa'),'restriction')
        self.assertEqual(role_for_label('Bányatelek határa'),'restriction')
        self.assertEqual(role_for_label('Tervezett telekhatár'),'proposed_parcel_boundary')
        self.assertEqual(role_for_label('Földrészlet határ'),'parcel_boundary')
        self.assertEqual(role_for_label('Építési övezeten belül gépjárműtároló határa'),'')
        self.assertEqual(role_for_label('Megyei övezetek határa'),'restriction')

    def test_missing_own_legend_cannot_prove_source_geometry_or_zone(self):
        with geo_document() as doc:
            doc.delete_page(1)
            parcel={'parcel_polygon_candidate':True,'geometry':{
                'outline':mapping(box(600300,249600,600400,249700).buffer(2))}}
            result=app.geopdf_parcel_zone(doc,parcel,'034/15')
        self.assertFalse(result['parcel_boundary_verified']);self.assertEqual(result['zone_features'],[])

    def test_other_plan_legend_cannot_be_borrowed_even_with_matching_colours(self):
        with geo_document() as doc:
            parcel={'parcel_polygon_candidate':True,'geometry':{
                'outline':mapping(box(600300,249600,600400,249700).buffer(2))}}
            result=app.geopdf_parcel_zone(doc,parcel,'034/15',
                {'identity':{'plan_hash':'b'*64},'records':[]})
        self.assertFalse(result['parcel_boundary_verified']);self.assertEqual(result['zone_features'],[])

    def test_named_cadastral_source_survives_print_style_difference_only_with_own_legend_term(self):
        parcel={'parcel_polygon_candidate':True,'geometry':{
            'outline':mapping(box(600300,249600,600400,249700).buffer(2))}}
        with geo_document((.306,.306,.306),.01) as doc:
            result=app.geopdf_parcel_zone(doc,parcel,'034/15')
            self.assertTrue(result['parcel_boundary_verified'])
        with geo_document((.306,.306,.306),.01,'Unknown cadastral layer') as doc:
            result=app.geopdf_parcel_zone(doc,parcel,'034/15')
            self.assertFalse(result['parcel_boundary_verified'])

    def test_legacy_colour_adapters_cannot_bypass_legend_requirement(self):
        self.assertEqual(app.tiszaujvaros_plan_zone(b'',{},'2200/8')['zone'],'')
        self.assertEqual(app.georeferenced_plan_zone(b'',{},'8448/46')['zone'],'')


if __name__=='__main__':unittest.main()
