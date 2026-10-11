import unittest
from app import build_partial_findings


class PartialFindingsRegression(unittest.TestCase):
    def test_failed_plan_processing_keeps_legal_source_and_attachments(self):
        result={'identification':{},'classification':{'category':'C'},
                'visual':{},'plan_result':{},'errors':['OCR failed'],
                'legal_source_verified':False}
        inputs={'source_url':'https://example.gov.hu/plan.pdf',
                'attachments':['https://example.gov.hu/annex.pdf'],
                'source_valid':False,'current_verified':False}
        output=build_partial_findings(result,{'title':'HÉSZ'},
                                      {'url':'https://example.gov.hu/law'},inputs)
        self.assertEqual(output['legal_source']['url'],'https://example.gov.hu/law')
        self.assertEqual(output['regulatory_plan']['url'],inputs['source_url'])
        self.assertEqual(output['regulatory_plan']['attachments'],inputs['attachments'])
        self.assertIn('OCR failed',output['errors'])
        self.assertTrue(any('Hatályosság' in w for w in output['warnings']))
        self.assertEqual(output['classification'],'C')

    def test_unverified_visual_zone_survives_failure(self):
        result={'identification':{'intersection_verified':False},
                'classification':{'category':'C','zone':''},
                'visual':{'zone':'Lf-2','candidate_labels':[{'code':'Lf-2'}]},
                'plan_result':{},'errors':['Georeferencing failed']}
        output=build_partial_findings(result,{}, {},{})
        self.assertEqual([x['zone'] for x in output['zone_candidates']],['Lf-2'])
        self.assertFalse(output['zone_candidates'][0]['verified'])
        self.assertIn('Georeferencing failed',output['errors'])

    def test_verified_zone_distinct_from_other_candidates(self):
        result={'identification':{'zone':'A-1','intersection_verified':True},
                'classification':{'category':'A','zone':'A-1'},
                'visual':{'candidate_labels':[{'code':'B-2'}]},'plan_result':{}}
        output=build_partial_findings(result,{}, {},{})
        self.assertTrue(next(x for x in output['zone_candidates'] if x['zone']=='A-1')['verified'])
        self.assertFalse(next(x for x in output['zone_candidates'] if x['zone']=='B-2')['verified'])

    def test_plan_candidate_kept_after_no_geometry(self):
        result={'identification':{},'classification':{'category':'C'},
                'visual':{},'plan_result':{'candidate_zone':'Gip/3'}}
        output=build_partial_findings(result,{}, {},{})
        self.assertEqual(output['zone_candidates'][0]['zone'],'Gip/3')
        self.assertFalse(output['zone_candidates'][0]['verified'])


if __name__=='__main__':
    unittest.main()
