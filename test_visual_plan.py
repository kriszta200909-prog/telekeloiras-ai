import hashlib
import io
import tempfile
from pathlib import Path
import unittest
import fitz
import numpy as np
from PIL import Image
from shapely.geometry import box,LineString
from visual_plan import supported_labels,inspect_visual_plan,add_source_legend,model_availability


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
