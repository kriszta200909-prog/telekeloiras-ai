import unittest
from rule_inventory import Clause, build_inventory, has_exact_zone, source_digest


class RuleInventoryTests(unittest.TestCase):
    def clause(self, **changes):
        values=dict(text='Lke1.2: legnagyobb beépítettség 30%. Lakóépület nem helyezhető el.',
                    source_url='https://njt.jog.gov.hu/jogszabaly/2007-1-SP-5Y1608',
                    locator='SZ1.@BE(1)', edition='2026.01.01.',source_hash=source_digest('source'))
        values.update(changes)
        return Clause(**values)

    def test_exact_zone_does_not_borrow_neighbor(self):
        for text in ['Lke1.2','Lke 1.2 övezet','(Lke1.2)']:
            self.assertTrue(has_exact_zone(text,'Lke1.2'))
        for text in ['Lke1.21','Lke2.1','XLke1.2','Lke1.2/3','Lke1.2-a']:
            self.assertFalse(has_exact_zone(text,'Lke1.2'))

    def test_full_closing_ban_and_duplicate(self):
        clause=self.clause()
        result=build_inventory([clause,clause], 'Lke1.2', True, True)
        self.assertEqual(len(result['rows']),2)
        self.assertTrue(all(row['Teljes rendelkezés']==clause.text for row in result['rows']))
        self.assertTrue(all('még nem igazolt' in row['Állapot'] for row in result['rows']))

    def test_unverified_and_invalid_evidence(self):
        self.assertFalse(build_inventory([self.clause()])['ok'])
        for changes in [dict(source_url='https://njt.jog.gov.hu.evil.test'),
                        dict(source_url='https://user@njt.jog.gov.hu/jogszabaly/x'),
                        dict(source_url='https://njt.jog.gov.hu:invalid/x'),
                        dict(edition=''),dict(locator=''),dict(source_hash='bad')]:
            self.assertFalse(build_inventory([self.clause(**changes)],source_verified=True)['ok'])

    def test_no_hit_is_not_absence_of_regulation(self):
        result=build_inventory([self.clause(text='Az önkormányzat neve.')],source_verified=True)
        self.assertEqual(len(result['coverage']),14)
        self.assertTrue(all('további forrásfeldolgozás' in row['Állapot'] for row in result['coverage']))


if __name__ == '__main__':
    unittest.main()
