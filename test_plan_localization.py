import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import fitz
from PIL import Image
import app
import plan_localization as localization
from plan_labels import exact_hrsz_token


class MapLocalizationTests(unittest.TestCase):
    def test_local_path_filter_keeps_entire_source_line_and_does_not_create_edges(self):
        from geopdf import legend_layer_paths
        doc=fitz.open();p=doc.new_page()
        p.draw_line((10,30),(200,30),color=(1,0,0),width=.6)
        p.draw_line((300,300),(500,300),color=(1,0,0),width=.6)
        profile={'records':[{'role':'zone_boundary','label_verified':True,'styles':[{
            'kind':'path','stroke':[1.,0.,0.],'fill':None,'width':.6,'dash':[],'closed':False}]}]}
        paths=legend_layer_paths(p,profile,'zone_boundary',clip=fitz.Rect(50,20,60,40))
        self.assertEqual(len(paths),1);self.assertEqual(list(paths[0]['segments'][0].coords),[(10.,30.),(200.,30.)])
    def test_terminal_ocr_punctuation_preserves_actual_parcel_suffix(self):
        self.assertTrue(localization.exact_map_token('034/15.','034/15'))
        for wrong in ('34/15.','034/150.','034/15.1','034/15/1.','0034/15.'):
            self.assertFalse(localization.exact_map_token(wrong,'034/15'))
        self.assertFalse(exact_hrsz_token('034/15.1','034/15'))

    def test_tile_coverage_and_overlap_in_display_coordinates(self):
        rect=fitz.Rect(20,30,1220,880)
        regions=list(localization.tiles(rect))
        for x in range(20,1220,10):
            for y in range(30,880,10):self.assertTrue(any(r.contains(fitz.Point(x,y)) for r in regions))
        self.assertEqual(regions[0].x0,20)
        self.assertLess(regions[1].x0,regions[0].x1)
        with self.assertRaises(ValueError):list(localization.tiles(rect,40,40))

    def test_index_is_reusable_for_different_numbers_but_bound_to_source(self):
        doc=fitz.open();doc.new_page(width=100,height=100)
        doc=fitz.open(stream=doc.tobytes(),filetype='pdf')
        with tempfile.TemporaryDirectory() as folder,patch.object(localization,'_boxes',return_value=[['034/15',10,10,40,20]]) as ocr:
            a=localization.tiled_index(doc,'unused',cache_dir=folder)
            b=localization.tiled_index(doc,'unused',cache_dir=folder)
            self.assertEqual(ocr.call_count,1);self.assertEqual(a,b)
            doc[0].insert_text((10,40),'other source')
            c=localization.tiled_index(doc,'unused',cache_dir=folder)
            self.assertEqual(ocr.call_count,2);self.assertNotEqual(a['source_sha256'],c['source_sha256'])

    def test_verifier_requires_all_three_independent_resolutions(self):
        doc=fitz.open();doc.new_page();index={'labels':[{'text':'034/15','page_number':0,'pdf_rect':[10,10,30,20]}],'complete':True}
        with patch.object(localization,'_ocr_words',side_effect=[[('034/15',0,0)],[('034/15.',0,0)],[('034/15',0,0)]]):
            result=localization.verify_candidates(doc,index,'034/15','unused')
        self.assertEqual(len(result['hits']),1);self.assertEqual(len(result['hits'][0]['source_crop_sha256']),3)
        with patch.object(localization,'_ocr_words',side_effect=[[('034/15',0,0)],[('034/150',0,0)]]):
            self.assertFalse(localization.verify_candidates(doc,index,'034/15','unused')['hits'])

    def test_tile_string_without_three_stable_glyph_boxes_is_not_a_location(self):
        with fitz.open() as doc:
            doc.new_page()
            index={'labels':[{'text':'123/4','page_number':0,'pdf_rect':[10,10,30,20],
                             'origin':'overlapping_map_tiles'}],'complete':True}
            with patch.object(localization,'_ocr_words',return_value=[('123/4',0,0)]),\
                 patch.object(localization,'sparse_words',side_effect=[
                     [('123/4',30,30,160,48)],[],[('123/4',30,30,320,96)]]):
                self.assertFalse(localization.verify_candidates(doc,index,'123/4','unused')['hits'])

    def test_sheet_duplicates_need_distributed_consistent_neighbours(self):
        doc=fitz.open();doc.new_page(width=500,height=400);doc.new_page(width=500,height=400)
        rows=[]
        def row(page,text,x,y):return {'page_number':page,'text':text,'pdf_rect':[x,y,x+10,y+3]}
        hits=[row(0,'123/4',450,100),row(1,'123/4',100,100)]
        for i in range(6):
            for p,x in ((0,450),(1,100)):rows.append(row(p,f'200/{i}',x-30+i*9,80+i*8))
        collapsed,audit=localization.repeated_sheet_labels(doc,{'labels':rows},hits,'123/4')
        self.assertEqual(len(collapsed),1);self.assertTrue(audit['equivalent'])
        self.assertEqual(collapsed[0]['page_number'],1);self.assertFalse(audit['geographic_registration_verified'])
        rows[-1]['pdf_rect']=[300,300,310,303]
        rows[-3]['pdf_rect']=[300,300,310,303]
        separate,audit=localization.repeated_sheet_labels(doc,{'labels':rows},hits,'123/4')
        self.assertEqual(len(separate),2);self.assertFalse(audit['equivalent'])

    def test_legal_literal_code_not_numeric_building_parameter(self):
        pattern=localization.source_zone_pattern('A Kb-Nk övezet napelempark céljára szolgál.',app.ZONE_PATTERN)
        self.assertTrue(pattern.fullmatch('Kb-Nk'))
        for parameter in ('12,5*20%','60','12.5','20%'):self.assertFalse(pattern.fullmatch(parameter))
        self.assertFalse(localization.source_zone_pattern('',app.ZONE_PATTERN).fullmatch('Kb-Nk'))
        embedded=localization.source_zone_pattern('prefixprefixprefixKb-Nk övezet',app.ZONE_PATTERN)
        self.assertFalse(embedded.fullmatch('prefixKb-Nk'))

    def test_map_label_is_reported_without_claiming_current_cadastral_geometry(self):
        rows=app.automatic_evidence_rows(None,{'source_url':'https://njt.jog.gov.hu/plan.pdf'},
            {'intersection_verified':False},visual={'location':{'verified_preliminary':True},'zone':'Kb-Nk'})
        self.assertTrue(rows[0]['Bizonyított']);self.assertIn('kataszteri',rows[0]['Eredmény'])
        self.assertFalse(rows[1]['Bizonyított']);self.assertFalse(rows[2]['Bizonyított'])
        self.assertIn('jelölt',rows[2]['Eredmény'])

    def test_same_page_duplicate_is_never_collapsed(self):
        doc=fitz.open();doc.new_page()
        hits=[{'page_number':0,'text':'1/1','pdf_rect':[10,10,20,13]},
              {'page_number':0,'text':'1/1','pdf_rect':[100,100,110,103]}]
        selected,audit=localization.repeated_sheet_labels(doc,{'labels':[]},hits,'1/1')
        self.assertEqual(len(selected),2);self.assertFalse(audit['equivalent'])

    def test_failed_sparse_ocr_cannot_invent_result(self):
        import subprocess
        with patch.object(localization.subprocess,'run',side_effect=subprocess.TimeoutExpired('tesseract',30)):
            self.assertEqual(localization.sparse_words(Image.new('RGB',(40,40),'white'),'unused'),[])

    def test_interrupted_tile_index_resumes_without_repeating_completed_tile(self):
        doc=fitz.open();doc.new_page(width=700,height=400)
        with tempfile.TemporaryDirectory() as folder,patch.object(localization,'_boxes',return_value=[]) as ocr:
            with self.assertRaises(RuntimeError):
                localization.tiled_index(doc,'unused',cache_dir=folder,on_progress=lambda *x:(_ for _ in ()).throw(RuntimeError('interrupted')))
            result=localization.tiled_index(doc,'unused',cache_dir=folder)
            self.assertTrue(result['complete']);self.assertEqual(ocr.call_count,2)

    def test_unproven_c_code_keeps_rules_conditional_and_classification_c(self):
        classification,scoped=app.visual_rule_identification({'intersection_verified':False},
            {'status':'not_identifiable','zone':'','candidate_labels':[{'code':'Kb-Nk','clear_paths':6}]})
        self.assertEqual(classification['category'],'C');self.assertEqual(classification['zone'],'')
        self.assertEqual(scoped['candidate_zone'],'Kb-Nk');self.assertFalse(scoped['intersection_verified'])

    def test_ambiguous_source_locations_get_a_real_crop_without_a_proved_parcel(self):
        from visual_plan import inspect_label_location,classification_result
        doc=fitz.open();page=doc.new_page(width=600,height=400)
        page.insert_text((200,200),'123/4');page.insert_text((240,200),'123/4')
        hits=[{'page_number':0,'pdf_rect':[200,188,225,201],'coordinate_space':'display'},
              {'page_number':0,'pdf_rect':[240,188,265,201],'coordinate_space':'display'}]
        result=inspect_label_location(doc,{'hits':hits},{},app.ZONE_PATTERN,'123/4',{},None,None)
        self.assertTrue(result['annotated_png']);self.assertEqual(result['pdf_page'],1)
        self.assertTrue(result['location']['plan_label_locations_verified'])
        self.assertFalse(result['location']['verified_preliminary']);self.assertFalse(result['intersection_verified'])
        self.assertEqual(classification_result({},result)['category'],'C')


if __name__=='__main__':unittest.main()
