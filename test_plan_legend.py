import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
    def test_ujpest_official_zone_legend_captions(self):
        self.assertEqual(role_for_label('Építési övezet, övezet jele'), 'zone_code')
        self.assertEqual(role_for_label('Építési övezet, övezet határa'), 'zone_boundary')

    def test_thumbnail_miss_cannot_replace_first_sheet_legend(self):
        with tempfile.TemporaryDirectory() as cache,fitz.open() as doc:
            for _ in range(2):doc.new_page(width=600,height=300)
            def rows(page,tessdata,crop=None,scale=None):
                if scale is not None and scale<=2 and page.number==0:return []
                return [{'role':'legend_title','label':'Jelmagyarazat','rect':[100,30,200,40],
                         'recognition':'native'}]
            def parsed(page,rows):
                return [{'role':'zone_boundary','label':'Ovezethatar','label_verified':True,'recognition':'native',
                         'styles':[{'source_sheet':page.number+1}],'PDF-oldal':page.number+1}]
            with patch('plan_legend.caption_rows',return_value=[]),\
                 patch('plan_legend.ocr_rows',side_effect=rows),\
                 patch('plan_legend.parse_legend',side_effect=parsed):
                result=self.read(doc,cache,tessdata='available')
            self.assertEqual(result['records'][0]['PDF-oldal'],1)

    def test_distant_framed_sample_column_is_found_for_ocr_captions(self):
        from plan_legend import parse_legend
        doc=fitz.open();page=doc.new_page(width=1200,height=600)
        rows=[{'role':'legend_title','label':'Jelmagyarazat','rect':[100,30,180,40],'recognition':'OCR candidate'}]
        for i in range(3):
            y=100+i*40
            page.draw_rect(fitz.Rect(300,y,350,y+20),color=(0,0,0),width=.2)
            page.draw_line((300,y+10),(350,y+10),color=(.7,.1,.3),width=1)
            rows.append({'role':'zone_boundary','label':'Ovezethatar','rect':[100,y+3,160,y+17],'recognition':'two-scale OCR'})
        parsed=parse_legend(page,rows)
        self.assertEqual(len(parsed),3)
        self.assertTrue(all(r['sample_rect'][0]>290 and r['styles'] for r in parsed))
        self.assertTrue(all(r['sample_column_evidence']['aligned_caption_rows']==3 for r in parsed))

    def test_source_dot_strokes_do_not_match_same_colour_contours(self):
        from plan_legend import drawing_style
        dot=drawing_style({'items':[('l',(10,10),(10.12,10))],'color':(1,0,0),'width':.6})
        contour=drawing_style({'items':[('l',(10,10),(30,20))],'color':(1,0,0),'width':.6})
        self.assertEqual(dot['primitive'],'short_dot_strokes')
        self.assertFalse(matches_style(contour,dot));self.assertTrue(matches_style(dot,dot))

    def test_ocr_caption_between_two_framed_columns_stays_ambiguous(self):
        from plan_legend import parse_legend
        doc=fitz.open();page=doc.new_page(width=1200,height=600)
        rows=[{'role':'legend_title','label':'Jelmagyarazat','rect':[100,30,180,40],'recognition':'OCR candidate'}]
        for i in range(3):
            y=100+i*40
            for x in (50,300):
                page.draw_rect(fitz.Rect(x,y,x+45,y+20),color=(0,0,0),width=.2)
                page.draw_line((x,y+10),(x+45,y+10),color=(.7,.1,.3),width=1)
            rows.append({'role':'zone_boundary','label':'Ovezethatar','rect':[150,y+3,230,y+17],'recognition':'two-scale OCR'})
        parsed=parse_legend(page,rows)
        self.assertTrue(all(r['ambiguous_sample'] and not r['styles'] for r in parsed))

    def test_existing_vector_sample_is_not_overridden_by_unrelated_frames(self):
        from plan_legend import parse_legend
        doc=fitz.open();page=doc.new_page(width=1200,height=600)
        rows=[{'role':'legend_title','label':'Jelmagyarazat','rect':[100,30,180,40],'recognition':'OCR candidate'}]
        for i in range(3):
            y=100+i*40
            page.draw_line((60,y+10),(90,y+10),color=(1,0,0),width=.6)
            for x in (250,320):page.draw_rect(fitz.Rect(x,y,x+45,y+20),color=(0,0,0),width=.2)
            rows.append({'role':'zone_boundary','label':'Ovezethatar','rect':[100,y+3,160,y+17],'recognition':'two-scale OCR'})
        parsed=parse_legend(page,rows)
        self.assertTrue(all(not r['ambiguous_sample'] and r['styles'][0]['stroke']==[1.,0.,0.] for r in parsed))

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

    def test_sample_container_is_not_a_black_boundary_but_black_symbols_remain(self):
        from plan_legend import sample_styles
        doc=fitz.open();p=doc.new_page()
        p.draw_rect(fitz.Rect(20,20,70,40),color=(0,0,0),width=.12)
        p.draw_line((25,30),(65,30),color=(1,0,0),width=1)
        styles=sample_styles(p,fitz.Rect(18,18,72,42))
        self.assertEqual(len(styles),1);self.assertEqual(styles[0]['stroke'],[1.,0.,0.])
        p.draw_rect(fitz.Rect(100,20,150,40),color=(0,0,0),width=.12)
        black=sample_styles(p,fitz.Rect(98,18,152,42))
        self.assertEqual(black[0]['stroke'],[0.,0.,0.])

    def test_closed_legend_symbol_does_not_match_an_open_line_of_same_colour(self):
        expected={'kind':'path','stroke':[0,0,0],'fill':None,'width':1,'dash':[],'closed':True}
        self.assertFalse(matches_style(dict(expected,closed=False),expected))
        self.assertTrue(matches_style(expected,expected))

    def test_source_code_layout_captions_are_recognised(self):
        for label in ('Építési övezeti paraméterek','Övezeti paraméterek','Szabályozási jel'):
            self.assertEqual(role_for_label(label),'zone_code')

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

    def test_sample_column_is_inferred_from_own_aligned_native_rows(self):
        with tempfile.TemporaryDirectory() as cache,legend_document() as doc:
            page=doc[0]
            page.draw_line((180,80),(205,80),color=(.8,.1,.5),width=2)
            for y,label in ((124,'Szabalyozasi vonal'),(164,'Foldreszlet hatar'),(204,'Natura 2000 terulet')):
                page.insert_text((100,y),label)
                page.draw_line((65,y-4),(90,y-4),color=(.2,.7,.1),width=2)
            result=self.read(doc,cache)
            self.assertTrue(result['zone_style_verified'])
            self.assertEqual(styles_for(result,'zone_boundary')[0]['stroke'],[.2,.7,.1])
            self.assertEqual(result['records'][0]['sample_side_basis']['aligned_native_rows'],3)

    def test_wrapped_caption_uses_whole_caption_height_and_not_next_column(self):
        with tempfile.TemporaryDirectory() as cache,fitz.open() as doc:
            page=doc.new_page(width=600,height=300)
            page.insert_text((100,40),'Jelmagyarazat')
            page.insert_text((100,84),'Kozuti kozlekedesi terulet',fontsize=11)
            page.insert_text((100,94),'orszagos kozut',fontsize=11)
            page.draw_rect((65,79,90,94),fill=(.8,.4,.2),color=None)
            # A foreign column graphic is not a replacement for the tall
            # original sample next to the two-line caption.
            page.draw_line((290,80),(315,80),color=(.1,.9,.2),width=2)
            result=self.read(doc,cache)
            road=next(r for r in result['records'] if r['role']=='road_area')
            self.assertIn('orszagos kozut',road['label'])
            self.assertEqual(road['styles'][0]['fill'],[.8,.4,.2])

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
        self.assertEqual(role_for_label('Elsődleges levezető sáv'),'restriction')
        self.assertEqual(role_for_label('Erozió érzékeny terület'),'restriction')
        self.assertEqual(role_for_label('Középfeszültségű villamosenergia kábel'),'utility_line')
        self.assertEqual(role_for_label('Építési vonal'),'building_line')

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
