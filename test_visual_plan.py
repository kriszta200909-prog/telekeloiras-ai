import hashlib
import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import fitz
import numpy as np
from PIL import Image
from shapely.geometry import box,LineString
from visual_plan import (supported_labels,inspect_visual_plan,add_source_legend,model_availability,
    classification_result,vision_request,validate_vision_answer,optional_vision_review)


class VisualPlanTests(unittest.TestCase):
    def labels(self):return [('A/1',fitz.Rect(15,40,25,50))]
    def styles(self,colour=(1,0,0)):return [{'stroke':colour}]
    def test_colour_is_taken_from_local_legend(self):
        rgb=np.ones((100,100,3),dtype=np.uint8)*255;rgb[:,45,:]=[255,0,0]
        parcel=box(60,40,80,60)
        red=supported_labels(rgb,(0,0),1,parcel,self.labels(),self.styles())
        blue=supported_labels(rgb,(0,0),1,parcel,self.labels(),self.styles((0,0,1)))
        self.assertEqual(red[0]['clear_paths'],0);self.assertGreater(blue[0]['clear_paths'],0)

    def test_native_dotted_trace_blocks_white_pixel_gaps(self):
        rgb=np.ones((100,100,3),dtype=np.uint8)*255
        result=supported_labels(rgb,(0,0),1,box(60,40,80,60),self.labels(),self.styles(),
            [LineString([(45,0),(45,100)])])
        self.assertEqual(result[0]['clear_paths'],0)

    def test_black_legend_lines_are_not_assumed_to_be_background(self):
        rgb=np.ones((100,100,3),dtype=np.uint8)*255;rgb[:,45,:]=0
        result=supported_labels(rgb,(0,0),1,box(60,40,80,60),self.labels(),self.styles((0,0,0)))
        self.assertEqual(result[0]['clear_paths'],0)

    def test_missing_legend_is_not_nearest_label_matching(self):
        result=supported_labels(np.ones((100,100,3),dtype=np.uint8)*255,(0,0),1,
            box(60,40,80,60),self.labels(),[])
        self.assertEqual(result,[])

    def test_all_codes_are_considered_not_only_expected_one(self):
        result=supported_labels(np.ones((100,100,3),dtype=np.uint8)*255,(0,0),1,
            box(60,40,80,60),self.labels()+[('B/2',fitz.Rect(15,60,25,70))],self.styles())
        self.assertEqual({r['code'] for r in result},{'A/1','B/2'})

    def test_unverified_parcel_never_yields_visual_proof(self):
        result=inspect_visual_plan(None,{}, {},None,'034/15')
        self.assertEqual(result['status'],'not_identifiable');self.assertFalse(result['intersection_verified'])

    def test_legend_source_bytes_must_match_before_image_is_appended(self):
        doc=fitz.open();page=doc.new_page();page.insert_text((20,30),'Legend own source')
        raw=doc.tobytes();doc.close();url='https://example.test/legend.pdf'
        canvas=io.BytesIO();Image.new('RGB',(100,100),'white').save(canvas,format='PNG')
        profile={'identity':{'source_url':url,'source_hash':hashlib.sha256(raw).hexdigest()},
                 'records':[{'role':'zone_boundary','PDF-oldal':1,'rect':[20,10,120,40],'sample_rect':[0,10,19,40]}]}
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,hashlib.sha256(url.encode()).hexdigest()+'.pdf').write_bytes(raw)
            result=add_source_legend({'annotated_png':canvas.getvalue()},profile,folder)
            self.assertTrue(result['legend_image_source_verified'])
            profile['identity']['source_hash']='0'*64
            failed=add_source_legend({'annotated_png':canvas.getvalue()},profile,folder)
            self.assertNotIn('legend_image_source_verified',failed)

    def test_no_fictional_multimodal_model_or_paid_requests(self):
        result=model_availability();self.assertFalse(result['multimodal_model_verified'])
        self.assertEqual(result['external_ai_requests'],0);self.assertEqual(result['external_ai_cost'],0)

    def test_three_categories_preserve_proof_and_conditional_applicability(self):
        visual={'status':'probable','zone':'Test/7','legend_bound':True,'exact_hrsz_in_parcel':True}
        self.assertEqual(classification_result({},visual)['category'],'B')
        self.assertFalse(classification_result({},visual)['zone_verified'])
        self.assertEqual(classification_result({'zone':'Other/8','intersection_verified':True},visual)['category'],'A')
        visual['legend_bound']=False
        self.assertEqual(classification_result({},visual)['category'],'C')

    def test_visual_candidate_drives_conditional_rules_without_mutating_geometry(self):
        import app
        identification={'candidate_zone':'Wrong/1','intersection_verified':False}
        visual={'status':'probable','zone':'Test/7','legend_bound':True,'exact_hrsz_in_parcel':True}
        classification,scoped=app.visual_rule_identification(identification,visual)
        self.assertEqual(scoped['candidate_zone'],'Test/7');self.assertFalse(scoped['intersection_verified'])
        self.assertEqual(identification['candidate_zone'],'Wrong/1')
        self.assertEqual(classification['category'],'B')

    def test_preliminary_label_location_can_be_B_without_polygon_but_never_A(self):
        visual={'status':'probable','zone':'Lke/7','legend_bound':True,
                'location':{'verified_preliminary':True,'parcel_boundary_verified':False}}
        result=classification_result({},visual)
        self.assertEqual(result['category'],'B');self.assertFalse(result['zone_verified'])
        visual['location']['verified_preliminary']=False
        self.assertEqual(classification_result({},visual)['category'],'C')

    def test_incomplete_or_ambiguous_label_search_does_not_select_location(self):
        from visual_plan import inspect_label_location
        for spatial in ({'hits':[{},{}]}, {'hits':[{}],'scan':{'complete':False}}):
            result=inspect_label_location(None,spatial,{},None,'999/7',{},None,None)
            self.assertFalse(result['location']['verified_preliminary'])
            self.assertEqual(result['status'],'not_identifiable')

    def test_caption_alone_does_not_establish_circle_code_layout(self):
        from visual_plan import source_circle_layout
        doc=fitz.open();p=doc.new_page();p.insert_text((180,100),'SZABALYOZASI JEL')
        profile={'identity':{'source_url':'same','plan_url':'same'},'records':[
            {'label':'SZABALYOZASI JEL','role':'zone_code','label_verified':True,'PDF-oldal':1,'rect':[180,88,290,102]}]}
        self.assertEqual(source_circle_layout(doc,profile),[])
        p.draw_circle((140,95),12,color=(0,0,0),width=.6)
        p.draw_line((128,95),(152,95),color=(0,0,0),width=.6)
        examples=source_circle_layout(doc,profile)
        self.assertTrue(examples);self.assertTrue(examples[0]['divider_verified'])

    def test_api_payload_contains_original_images_but_no_reference_answer(self):
        payload=vision_request([b'source plan',b'source legend'],'999/7')
        self.assertFalse(payload['store']);self.assertEqual(payload['max_output_tokens'],1200)
        content=payload['input'][0]['content']
        self.assertEqual([c['type'] for c in content],['input_text','input_image','input_image'])
        self.assertNotIn('Gipe-60.63.5',content[0]['text'])
        self.assertNotIn('4755/11',content[0]['text'])

    def test_ai_hallucination_and_disagreement_never_become_proof(self):
        visual={'status':'probable','zone':'Test/7','candidate_labels':[{'code':'Test/7'}]}
        answer={'zone':'Fake/2','hrsz':'999/7','boundary_explanation':'boundary','legend_explanation':'legend'}
        self.assertFalse(validate_vision_answer(answer,visual,'999/7')['agrees_with_local_evidence'])
        answer['zone']='Test/7'
        result=validate_vision_answer(answer,visual,'999/7')
        self.assertTrue(result['agrees_with_local_evidence']);self.assertFalse(result['intersection_verified'])

    def test_api_never_runs_without_explicit_enable_even_if_key_present(self):
        with patch.dict('os.environ',{'OPENAI_API_KEY':'test','TELEKELOIRAS_VISION_ENABLED':'0'}):
            with patch('urllib.request.build_opener') as opener:
                self.assertEqual(optional_vision_review({},'999/7')['external_ai_requests'],0)
                opener.assert_not_called()

    def test_paid_api_disabled_even_with_old_enable_and_key(self):
        with patch.dict('os.environ',{'OPENAI_API_KEY':'mock-only','TELEKELOIRAS_VISION_ENABLED':'1'}):
            with patch('urllib.request.build_opener') as opener:
                result=optional_vision_review({'context_png':b'plan','legend_png':b'legend'},'999/7')
                self.assertEqual(result['status'],'not_run')
                self.assertEqual(result['external_ai_requests'],0)
                opener.assert_not_called()
