"""Contract and geometry checks for the first automatic identification stage."""
import copy
import unittest

import app


def rect(left, right):
    return {'type': 'Polygon', 'coordinates': [[[left, 0], [right, 0],
            [right, 10], [left, 10], [left, 0]]]}


class ParcelZoneTests(unittest.TestCase):
    def setUp(self):
        self.parcel = dict(ksh_code='28352', hrsz='034/15', geometry=rect(0, 10),
                           crs='EPSG:23700', boundary_verified=True)
        self.source = dict(ksh_code='28352', edition='2026.01.01.',
                           source_url='https://njt.jog.gov.hu/document/plan.pdf',
                           source_hash='a' * 64, boundary_verified=True,
                           current_verified=True, features=[self.feature('A', 0, 10)])

    def feature(self, code, left, right):
        return dict(code=code, geometry=rect(left, right), crs='EPSG:23700')

    def identify(self, parcel=None, source=None):
        return app.identify_parcel_zones(
            self.parcel if parcel is None else parcel,
            self.source if source is None else source,
            ksh_code='28352', hrsz='034/15', edition='2026.01.01.')

    def assert_unverified(self, result):
        self.assertFalse(result['intersection_verified'])
        self.assertEqual(result['zone'], '')
        self.assertTrue(result['reason'])

    def test_full_coverage_returns_code_and_source_evidence(self):
        before = copy.deepcopy((self.parcel, self.source))
        result = self.identify()
        self.assertTrue(result['intersection_verified'])
        self.assertEqual(result['zone'], 'A')
        self.assertEqual(result['coverage'], 1)
        self.assertEqual(result['source']['source_hash'], 'a' * 64)
        self.assertEqual(before, (self.parcel, self.source))

    def test_multiple_zones_preserve_fractions_without_single_code(self):
        self.source['features'] = [self.feature('A', 0, 4), self.feature('B', 4, 10)]
        result = self.identify()
        self.assertTrue(result['intersection_verified'])
        self.assertEqual(result['status'], 'multiple_zones')
        self.assertEqual(result['zone'], '')
        self.assertEqual(result['zones'], [{'code': 'A', 'fraction': .4},
                                          {'code': 'B', 'fraction': .6}])

    def test_same_code_is_merged_before_coverage(self):
        self.source['features'] = [self.feature('A', 0, 6), self.feature('A', 4, 10)]
        self.assertEqual(self.identify()['zone'], 'A')

    def test_partial_and_conflicting_coverage_cannot_confirm_zone(self):
        for features, status in [([self.feature('A', 0, 5)], 'partial_coverage'),
                                 ([self.feature('A', 0, 7), self.feature('B', 5, 10)],
                                  'overlapping_zones')]:
            with self.subTest(status=status):
                self.source['features'] = features
                result = self.identify()
                self.assertEqual(result['status'], status)
                self.assert_unverified(result)

    def test_touching_boundaries_and_polygon_holes_do_not_prove_coverage(self):
        self.source['features'] = [self.feature('A', 10, 20)]
        self.assert_unverified(self.identify())
        hole = rect(0, 10)
        hole['coordinates'].append([[2, 2], [2, 8], [8, 8], [8, 2], [2, 2]])
        self.source['features'][0]['geometry'] = hole
        result = self.identify()
        self.assertEqual(result['status'], 'partial_coverage')
        self.assertEqual(result['coverage'], .64)
        self.assert_unverified(result)

    def test_exact_parcel_and_municipality_are_required(self):
        for changes in [dict(hrsz='34/15'), dict(hrsz='034'), dict(hrsz='034/150'),
                        dict(ksh_code='00000')]:
            with self.subTest(changes=changes):
                self.assert_unverified(self.identify(parcel={**self.parcel, **changes}))
        self.assert_unverified(self.identify(source={**self.source, 'ksh_code': '00000'}))

    def test_source_provenance_and_verified_boundaries_are_required(self):
        for changes in [dict(boundary_verified=False), dict(current_verified=False),
                        dict(current_verified='true'), dict(edition='old'),
                        dict(source_hash=''), dict(source_hash='z' * 64),
                        dict(source_url='https://example.com/plan.pdf'),
                        dict(source_url='http://njt.jog.gov.hu/document/plan.pdf'),
                        dict(source_url=None)]:
            with self.subTest(changes=changes):
                self.assert_unverified(self.identify(source={**self.source, **changes}))
        self.assert_unverified(self.identify(parcel={**self.parcel, 'boundary_verified': False}))

    def test_crs_and_actual_polygons_are_required(self):
        for changes in [dict(crs=''), dict(crs='EPSG:4326'),
                        dict(geometry={'type': 'Point', 'coordinates': [5, 5]}),
                        dict(geometry={}), dict(geometry=rect(0, 0))]:
            with self.subTest(changes=changes):
                self.assert_unverified(self.identify(parcel={**self.parcel, **changes}))
        self.assert_unverified(self.identify(source={**self.source, 'features': []}))


if __name__ == '__main__':
    unittest.main()
