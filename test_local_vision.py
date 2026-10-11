"""Offline safety tests; real model receipts are separate source checks."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import local_vision as vlm


class LocalVisionTests(unittest.TestCase):
    def answer(self, **overrides):
        answer={'hrsz':'999/7','parcel_identified':True,'zone':'Test/7','boundary_state':'open',
                'visual_evidence':['number in centre'], 'uncertainties':['missing corner']}
        answer.update(overrides)
        return {'status':'completed','raw_answer':json.dumps(answer)}

    def test_blind_prompt_has_no_reference_zone(self):
        text=vlm.prompt('999/7')
        self.assertIn('999/7',text)
        self.assertNotIn('Test/7',text)
        self.assertNotIn('Gipe-60.63.5',text)
        self.assertIn('own official legend',text)

    def test_agreement_does_not_establish_legal_proof(self):
        result=vlm.compare(self.answer(),{'zone':'Test/7','candidate_labels':[{'code':'Test/7'}]},'999/7')
        self.assertTrue(result['agrees_with_local_zone'])
        self.assertFalse(result['intersection_verified'])
        self.assertFalse(result['legal_classification_verified'])

    def test_wrong_parcel_boolean_string_and_truncated_output_rejected(self):
        for result in [self.answer(hrsz='999/8'),self.answer(parcel_identified='true'),
                       {'raw_answer':'{"zone":"Test/7"'}]:
            result=vlm.compare(result,{'zone':'Test/7'},'999/7')
            self.assertFalse(result['structured_answer_valid'])
            self.assertEqual(result['zone_suggestion'],'')

    def test_disagreement_is_preserved_and_never_promotes_category(self):
        result=vlm.compare(self.answer(zone='Other/3'),{'zone':'Test/7'},'999/7')
        self.assertEqual(result['zone_suggestion'],'Other/3')
        self.assertFalse(result['agrees_with_local_zone'])
        self.assertFalse(result['legal_classification_verified'])

    def test_unavailable_model_has_no_worker_or_network_call(self):
        with patch.object(vlm,'availability',return_value={'available':False}),patch('subprocess.run') as run:
            result=vlm.run_local([b'plan',b'legend'],'999/7',{})
            self.assertEqual(result['status'],'not_run')
            run.assert_not_called()

    def test_worker_receives_original_images_without_control_and_offline_environment(self):
        def worker(command, **kwargs):
            job=json.loads(Path(command[-1]).read_text())
            self.assertNotIn('Test/7',json.dumps(job))
            self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'],'1')
            self.assertEqual(kwargs['env']['TRANSFORMERS_OFFLINE'],'1')
            self.assertEqual(kwargs['env']['OMP_THREAD_LIMIT'],str(vlm.cpu_threads()))
            self.assertEqual(Path(job['jobs'][0]['images'][0]).read_bytes(),b'original source')
            Path(job['output']).write_text(json.dumps({'cases':[self.answer()]}))
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0)
        with patch.object(vlm,'availability',return_value={'available':True}),patch('subprocess.run',side_effect=worker):
            result=vlm.run_local([b'original source'],'999/7',{'zone':'Test/7'})
            self.assertEqual(result['external_ai_requests'],0)
            self.assertTrue(result['agrees_with_local_zone'])

    def test_unidentified_parcel_cannot_supply_accepted_zone(self):
        result=vlm.compare(self.answer(parcel_identified=False),{'zone':'Test/7'},'999/7')
        self.assertEqual(result['reported_zone'],'Test/7')
        self.assertEqual(result['zone_suggestion'],'')
        self.assertFalse(result['agrees_with_local_zone'])

    def test_closed_boundary_claim_cannot_hide_actual_source_gaps(self):
        result=vlm.compare(self.answer(boundary_state='closed'),{'zone':'Test/7','source_gaps':[{'point_pdf':[1,2]}]},'999/7')
        self.assertTrue(result['boundary_claim_requires_gap_review'])
        self.assertFalse(result['closed_boundary_claim_verified'])
        self.assertFalse(result['legal_classification_verified'])

    def test_cpu_quota_limits_worker_threads(self):
        with patch('pathlib.Path.read_text',return_value='200000 100000'),patch('os.cpu_count',return_value=8):
            self.assertEqual(vlm.cpu_threads(),2)
        with patch('pathlib.Path.read_text',return_value='max 100000'),patch('os.cpu_count',return_value=2):
            self.assertEqual(vlm.cpu_threads(),2)

    def test_concurrent_model_requests_do_not_start_another_memory_heavy_worker(self):
        vlm._MODEL_LOCK.acquire()
        try:
            with patch('subprocess.run') as run:
                result=vlm.run_local([b'source'],'999/7',{})
                self.assertEqual(result['status'],'busy')
                run.assert_not_called()
        finally:vlm._MODEL_LOCK.release()

    def test_worker_failure_preserves_control(self):
        from types import SimpleNamespace
        control={'zone':'Test/7'}
        with patch.object(vlm,'availability',return_value={'available':True}),patch('subprocess.run',return_value=SimpleNamespace(returncode=137)):
            result=vlm.run_local([b'source'],'999/7',control)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(control['zone'],'Test/7')
            self.assertEqual(result['external_ai_cost'],0)

    def test_uncached_free_panel_can_start_without_user_programming(self):
        from streamlit.testing.v1 import AppTest
        script = """
from unittest.mock import patch
from pathlib import Path
import local_vision as vlm
visual={'legend_bound':True,'legend_image_source_verified':True,'clip_pdf':[0,0,1,1],'context_image_sha256':'source'}
with patch.object(vlm,'availability',return_value={'available':True}), patch.object(vlm,'source_images',return_value=[b'source',b'legend']), patch.object(vlm,'run_local',return_value={'status':'failed','reason':'controlled failure'}):
    vlm.render_free_review(None,visual,{},Path('/tmp'),'999/7')
"""
        at=AppTest.from_string(script,default_timeout=30).run()
        self.assertEqual(len(at.exception),0)
        at.button[0].click().run()
        self.assertEqual(len(at.exception),0,[e.message for e in at.exception])
        self.assertTrue(any('controlled failure' in item.value for item in at.info))
