import unittest
from unittest.mock import patch
import fitz

from plan_labels import native_hrsz_hits, exact_hrsz_token


class PlanLabelTests(unittest.TestCase):
    def test_spaced_suffix_is_not_parent_parcel(self):
        doc=fitz.open();page=doc.new_page()
        page.insert_text((30,40),'1558 /1 1558 - 2 1558 .3 (1558)')
        hits=native_hrsz_hits(doc,'1558')
        self.assertEqual(len(hits),1)
        self.assertTrue(hits[0]['label_only'])


    def test_four_reference_parcels_native_matching(self):
        cases = [
            ('Tiszaújváros', '2200/8', '2200/80'),
            ('Budapest XII.', '8448/46', '8448/460'),
            ('Komádi', '1558', '1558/1'),
            ('Gersekarát', '034/15', '34/15'),
            ('Miskolc', '4755/11', '4755/110'),
        ]
        for town, target, distractor in cases:
            with self.subTest(town=town, hrsz=target):
                doc = fitz.open()
                page = doc.new_page()
                page.insert_text((30, 40), f'({target}) {distractor}')
                hits = native_hrsz_hits(doc, target)
                self.assertEqual(len(hits), 1)
                self.assertTrue(exact_hrsz_token(target, target))
                self.assertFalse(exact_hrsz_token(distractor, target))
                doc.close()


    def test_partial_report_is_downloadable_and_next_rerun_retries(self):
        import app
        from streamlit.testing.v1 import AppTest
        original=app.run_investigation
        script='''import streamlit as st
import app
@st.cache_data(show_spinner=False)
def partial_result(*args,**kwargs):
    st.session_state['partial_calls']=st.session_state.get('partial_calls',0)+1
    st.warning('Részleges felismerés – folytatható.')
    raise app.UncachedInvestigationResult({'report':'részleges adatlap','minerva_geometry':None})
app.run_investigation=partial_result
app.main()
'''
        try:
            a=AppTest.from_string(script).run()
            a.button[0].click().run()
            self.assertFalse(a.exception)
            self.assertEqual(len(a.get('download_button')),1)
            a.run()
            self.assertFalse(a.exception)
            self.assertEqual(a.session_state['partial_calls'],2)
        finally:app.run_investigation=original

    def test_ocr_worker_error_does_not_terminate_parent(self):
        from PIL import Image
        from plan_labels import OCRWorker, _ocr_words
        with OCRWorker() as worker:
            with self.assertRaises(RuntimeError):
                _ocr_words(Image.new('RGB',(40,40),'white'),'/nonexistent-ocr-test-data',worker)
        self.assertFalse(exact_hrsz_token('034/150','034/15'))

    def test_checkpoint_resumes_after_interrupted_progress(self):
        from PIL import Image
        import plan_labels
        doc=fitz.open();doc.new_page(width=300,height=300)
        groups=[{'rect':(10+i,10,15+i,12),'angle':0,'pieces':4} for i in range(70)]
        saved=[];rendered=[]
        def render(display_list,group,scale):
            rendered.append(group['rect'][0]);return Image.new('RGB',(50,20),'white')
        def interrupt(*args):raise RuntimeError('simulated connection interruption')
        with patch.object(plan_labels,'outlined_label_groups',return_value=groups),patch.object(plan_labels,'_label_image',side_effect=render),patch.object(plan_labels,'_ocr_words',return_value=[]):
            with self.assertRaisesRegex(RuntimeError,'connection'):
                plan_labels.outlined_label_index(doc,'unused',on_progress=interrupt,on_checkpoint=saved.append)
            self.assertEqual(saved[-1]['scanned'],64)
            result=plan_labels.outlined_label_index(doc,'unused',resume=saved[-1])
        self.assertTrue(result['complete'])
        self.assertEqual((result['scanned'],result['candidates'],result['pages']),(70,70,1))
        self.assertEqual(len(rendered),70)

    def test_request_recovery_rejects_changed_inputs_and_expired_token(self):
        import app
        app.recoverable_investigation_requests.clear()
        with patch.object(app.time,'monotonic',return_value=10):
            token=app.matching_investigation_request('same-request',start=True)
            self.assertEqual(app.matching_investigation_request('same-request',token),token)
            self.assertEqual(app.matching_investigation_request('other-request',token),'')
            self.assertEqual(app.matching_investigation_request('same-request','unknown'),'')
        with patch.object(app.time,'monotonic',return_value=911):
            self.assertEqual(app.matching_investigation_request('same-request',token),'')
        app.recoverable_investigation_requests.clear()

    def test_exact_native_number_not_prefix_or_parent(self):
        doc=fitz.open();page=doc.new_page()
        page.insert_text((30,40),'1558 15580 1558/1 11558 (1558) 15 58')
        hits=native_hrsz_hits(doc,'1558')
        self.assertEqual(len(hits),2)
        self.assertTrue(all(hit['label_only'] for hit in hits))

    def test_zero_and_separator_are_preserved(self):
        doc=fitz.open();page=doc.new_page()
        page.insert_text((30,40),'034/15 34/15 034/150 034 / 15 034-15')
        self.assertEqual(len(native_hrsz_hits(doc,'034/15')),2)
        for token in ['034/15','(034/15)','034/15,','034 / 15']:
            self.assertTrue(exact_hrsz_token(token,'034/15'))
        for token in ['34/15','034/150','034-15','0034/15','0 34/15','034/15/1']:
            self.assertFalse(exact_hrsz_token(token,'034/15'))

    def test_multiple_pages_remain_distinct_candidates(self):
        doc=fitz.open()
        for _ in range(2):doc.new_page().insert_text((30,40),'1558')
        self.assertEqual([h['page_number'] for h in native_hrsz_hits(doc,'1558')],[0,1])

    def test_rotated_pdf_retains_native_coordinates(self):
        doc=fitz.open();page=doc.new_page()
        page.insert_text((30,40),'034/15');before=native_hrsz_hits(doc,'034/15')
        page.set_rotation(90)
        self.assertEqual(before,native_hrsz_hits(doc,'034/15'))

    def test_index_cache_is_source_bound_and_shared_between_numbers(self):
        import app
        app.cached_outlined_plan_index.clear()
        doc=fitz.open();doc.new_page().insert_text((30,40),'1558')
        doc=fitz.open(stream=doc.tobytes(),filetype='pdf')
        complete={'labels':[],'complete':True,'scanned':1,'candidates':1,'pages':1}
        with patch.object(app,'outlined_label_index',return_value=complete) as scan:
            a=app.load_outlined_plan_labels(doc,'1558')
            b=app.load_outlined_plan_labels(doc,'1559')
            self.assertEqual(scan.call_count,1)
            self.assertEqual(a['source_sha256'],b['source_sha256'])
            doc[0].insert_text((30,70),'1559')
            c=app.load_outlined_plan_labels(doc,'1559')
            self.assertEqual(scan.call_count,2)
            self.assertNotEqual(a['source_sha256'],c['source_sha256'])
        app.cached_outlined_plan_index.clear()

    def test_incomplete_scan_is_not_cached(self):
        import app
        app.cached_outlined_plan_index.clear()
        doc=fitz.open();doc.new_page();doc=fitz.open(stream=doc.tobytes(),filetype='pdf')
        partial={'labels':[],'complete':False,'scanned':1,'candidates':5,'pages':0}
        with patch.object(app,'outlined_label_index',return_value=partial) as scan:
            self.assertFalse(app.load_outlined_plan_labels(doc,'1558')['complete'])
            self.assertFalse(app.load_outlined_plan_labels(doc,'1558')['complete'])
            self.assertEqual(scan.call_count,2)
        app.cached_outlined_plan_index.clear()


if __name__=='__main__':unittest.main()
