"""Safety and comparison tests; mocked transport is never a real AI result."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from vision_trial import run, digest, estimate, trial_request
from visual_plan import vision_request


class VisionTrialTests(unittest.TestCase):
    def fixture(self, root):
        folder = Path(root)/'case'
        folder.mkdir()
        payload = trial_request([b'original plan', b'own legend'], '999/7')
        raw = json.dumps(payload).encode()
        (folder/'request.json').write_bytes(raw)
        (folder/'control.json').write_text(json.dumps({'zone':'CONTROL/9'}))
        entry = {'folder':'case', 'hrsz':'999/7', 'request_sha256':digest(raw),
                 'cost':{'usd_estimate_upper':.01}}
        (Path(root)/'manifest.json').write_text(json.dumps({'cases':[entry]}))
        return folder

    def test_no_approval_or_budget_never_calls_network(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.fixture(root)
            with patch.dict(os.environ, {'OPENAI_API_KEY':'mock-only'}), patch('urllib.request.build_opener') as opener:
                for consent, budget in [(False, 1), (True, 0), (True, float("nan"))]:
                    with self.assertRaises(ValueError):run(folder, consent, budget)
                opener.assert_not_called()

    def test_missing_key_never_calls_network(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.fixture(root)
            with patch.dict(os.environ, {}, clear=True), patch('urllib.request.build_opener') as opener:
                with self.assertRaises(ValueError):run(folder, True, 1)
                opener.assert_not_called()

    def test_changed_payload_is_not_sent(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.fixture(root)
            (folder/'request.json').write_text('{}')
            with patch.dict(os.environ, {'OPENAI_API_KEY':'mock-only'}), patch('urllib.request.build_opener') as opener:
                with self.assertRaises(ValueError):run(folder, True, 1)
                opener.assert_not_called()

    def test_blind_request_and_disagreement_are_preserved_without_proof(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.fixture(root)
            answer = {'zone':'OTHER/2', 'hrsz':'999/7','boundary_explanation':'actual line',
                'legend_explanation':'own legend','neighbour_zones':[], 'uncertainties':['open corner'], 'parcel_identified':True,
                'visual_evidence':['parcel number at centre'], 'boundary_state':'open'}
            data = {'status':'completed', 'usage':{'input_tokens':100, 'output_tokens':50},
                'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(answer)}]}]}
            with patch.dict(os.environ, {'OPENAI_API_KEY':'mock-only'}), patch('urllib.request.build_opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps(data).encode()
                result = run(folder, True, 1)
                request = opener.return_value.open.call_args.args[0]
                self.assertNotIn(b'CONTROL/9',request.data)
                self.assertEqual(request.full_url,'https://api.openai.com/v1/responses')
                self.assertFalse(result['agrees_with_local_zone'])
                self.assertEqual(result['answer']['zone'],'OTHER/2')
                self.assertFalse(result['legal_classification_verified'])
                self.assertFalse(result['intersection_verified'])
                self.assertAlmostEqual(result['actual_usd'], .00012)

    def test_failure_has_unknown_cost_and_no_retry(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.fixture(root)
            with patch.dict(os.environ, {'OPENAI_API_KEY':'mock-only'}), patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = TimeoutError()
                result = run(folder, True, 1)
                self.assertEqual(opener.return_value.open.call_count, 1)
                self.assertEqual(result['status'],'failed')
                self.assertIsNone(result['actual_usd'])
                self.assertFalse(result['intersection_verified'])

    def test_existing_attempt_cannot_accidentally_spend_again(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.fixture(root)
            (folder/'result.json').write_text('{}')
            with patch.dict(os.environ, {'OPENAI_API_KEY':'mock-only'}), patch('urllib.request.build_opener') as opener:
                with self.assertRaises(ValueError):run(folder, True, 1)
                opener.assert_not_called()

    def test_prompt_requests_independent_evidence_and_parcel_verification(self):
        payload = trial_request([b'raw plan', b'legend'], '999/7')
        schema = payload['text']['format']['schema']
        for field in ('parcel_identified','visual_evidence','boundary_state'):
            self.assertIn(field, schema['required'])
        self.assertEqual(schema['properties']['boundary_state']['enum'], ['closed','open','unclear'])


if __name__ == '__main__':
    unittest.main()
