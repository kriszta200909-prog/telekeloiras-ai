import unittest
from unittest.mock import patch
import fitz

from plan_labels import native_hrsz_hits, exact_hrsz_token


class PlanLabelTests(unittest.TestCase):
    def test_exact_native_number_not_prefix_or_parent(self):
        doc=fitz.open();page=doc.new_page()
        page.insert_text((30,40),'1558 15580 1558/1 11558 (1558) 15 58')
        hits=native_hrsz_hits(doc,'1558')
        self.assertEqual(len(hits),2)
        self.assertTrue(all(hit['label_only'] for hit in hits))

    def test_spaced_suffix_is_not_parent_parcel(self):
        doc=fitz.open();page=doc.new_page()
        page.insert_text((30,40),'1558 /1 1558 - 2 1558 .3 (1558)')
        hits=native_hrsz_hits(doc,'1558')
        self.assertEqual(len(hits),1)
        self.assertTrue(hits[0]['label_only'])

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
