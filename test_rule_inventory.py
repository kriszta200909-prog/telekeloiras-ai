import unittest
from rule_inventory import Clause, build_inventory, has_exact_zone, source_digest


class RuleInventoryTests(unittest.TestCase):
    def clause(self, **changes):
        values=dict(text='Lke1.2: legnagyobb beépítettség 30%. Lakóépület nem helyezhető el.',
                    source_url='https://njt.jog.gov.hu/jogszabaly/2007-1-SP-5Y1608',
                    locator='SZ1.@BE(1)', edition='2026.01.01.',source_hash=source_digest('source'))
        values.update(changes)
        return Clause(**values)

    def test_plan_filename_hyphenated_njt_annex(self):
        import ast
        import re
        import unicodedata
        import urllib.parse
        from pathlib import Path
        source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "choose_plan_attachment")
        def key_text(value):
            value = unicodedata.normalize("NFKD", str(value))
            return "".join(c for c in value if not unicodedata.combining(c)).casefold()
        scope = {"key_text": key_text, "re": re, "urllib": urllib}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<app>", "exec"), scope)
        rows = [{"Megnevezés": "NJT melléklet",
                 "URL": "https://njt.jog.gov.hu/document/abc-szabalyozasi-terv.pdf"}]
        self.assertEqual(scope["choose_plan_attachment"](rows), rows[0])

    def test_nationwide_plan_choice_excludes_legend(self):
        import ast
        import re
        import unicodedata
        from pathlib import Path
        source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "choose_plan_attachment")
        def key_text(value):
            value = unicodedata.normalize("NFKD", str(value))
            return "".join(c for c in value if not unicodedata.combining(c)).casefold()
        scope = {"key_text": key_text, "re": re, "urllib": __import__("urllib")}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<app>", "exec"), scope)
        rows = [
            {"Megnevezés": "NJT melléklet", "URL": "https://njt.jog.gov.hu/document/abc-Jelmagyarazat.pdf"},
            {"Megnevezés": "NJT melléklet", "URL": "https://njt.jog.gov.hu/document/abc-Belteruleti_szabalyozasi_tervlap.pdf"},
        ]
        self.assertEqual(scope["choose_plan_attachment"](rows), rows[1])

    def test_first_annex_is_not_automatically_zoning_plan(self):
        import ast
        import re
        import unicodedata
        from pathlib import Path
        source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "choose_plan_attachment")
        def key_text(value):
            value = unicodedata.normalize("NFKD", str(value))
            return "".join(c for c in value if not unicodedata.combining(c)).casefold()
        scope = {"key_text": key_text, "re": re, "urllib": __import__("urllib")}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<app>", "exec"), scope)
        rows = [{"Megnevezés": "1. melléklet", "URL": "https://njt.jog.gov.hu/document/tabla.pdf"}]
        self.assertIsNone(scope["choose_plan_attachment"](rows))

    def test_plan_selection_uses_official_pdf_filename_without_label(self):
        import ast
        import re
        import unicodedata
        from pathlib import Path
        source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "choose_plan_attachment")
        def key_text(value):
            value = unicodedata.normalize("NFKD", str(value))
            return "".join(c for c in value if not unicodedata.combining(c)).casefold()
        scope = {"key_text": key_text, "re": re, "urllib": __import__("urllib")}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<app>", "exec"), scope)
        attachments = [{"Megnevezés": "NJT melléklet",
                        "URL": "https://njt.jog.gov.hu/document/abc-belteruleti_szabalyozasi_tervlap.pdf"}]
        self.assertEqual(scope["choose_plan_attachment"](attachments), attachments[0])

    def test_annex_discovery_only_accepts_official_njt_https(self):
        import ast
        import re
        import unicodedata
        import urllib.parse
        from pathlib import Path
        source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "discover_attachments")
        def key_text(value):
            value = unicodedata.normalize("NFKD", str(value))
            return "".join(c for c in value if not unicodedata.combining(c)).casefold()
        scope = {"urllib": urllib, "urljoin": urllib.parse.urljoin, "key_text": key_text}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<app>", "exec"), scope)
        page = {"url": "https://njt.jog.gov.hu/jogszabaly/2022-38-SP-5Y1070",
                "links": [("melléklet", "https://evil.example/plan.pdf"),
                          ("melléklet", "http://njt.jog.gov.hu/document/x.pdf"),
                          ("melléklet", "https://njt.jog.gov.hu.evil.example/document/x.pdf"),
                          ("melléklet", "https://njt.jog.gov.hu/jogszabaly/other"),
                          ("melléklet", "https://njt.jog.gov.hu/document/good.pdf")]}
        rows = scope["discover_attachments"](page)
        self.assertEqual([row["URL"] for row in rows],
                         ["https://njt.jog.gov.hu/document/good.pdf"])

    def test_nationwide_source_discovery_rejects_wrong_budapest_district(self):
        import ast
        from pathlib import Path
        source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "discover_njt_source")
        import re
        import unicodedata
        def key_text(value):
            value = unicodedata.normalize("NFKD", str(value).strip())
            return "".join(c for c in value if not unicodedata.combining(c)).casefold()
        def clean_text(value):
            return str(value).strip()
        def fake_search(_):
            return ["https://njt.jog.gov.hu/jogszabaly/wrong", "https://njt.jog.gov.hu/jogszabaly/right"]
        def fake_fetch(url):
            district = "XI." if url.endswith("wrong") else "XII."
            return {"ok": True, "url": url, "text": f"Budapest {district} kerület helyi építési szabályzat"}
        scope = dict(key_text=key_text, clean_text=clean_text,
                     discover_budapest_district=lambda hrsz: ("XII. kerület", "teszt"),
                     _search_web=fake_search, is_official_njt_url=lambda url: True,
                     fetch_njt_page=fake_fetch)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<app>", "exec"), scope)
        meta, page = scope["discover_njt_source"]("Budapest", "8448/46")
        self.assertTrue(meta["url"].endswith("/right"), meta)
        self.assertTrue(page["url"].endswith("/right"), page)

    def test_multiple_parcel_labels_are_not_arbitrarily_resolved(self):
        import ast
        from pathlib import Path
        source = Path(__file__).with_name('app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'locate_parcel')
        body = ast.get_source_segment(source, fn)
        self.assertIn('if len(hits) > 1:', body)
        self.assertIn('"status": "ambiguous_parcel_labels"', body)
        self.assertIn('"hits": hits', body)

    def test_parcel_lookup_does_not_confuse_id_with_hrsz(self):
        import ast
        from pathlib import Path
        source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(source).body
                  if isinstance(n, ast.FunctionDef) and n.name == "_find_parcel_record")
        scope = {"normalize_hrsz": lambda value: str(value).strip()}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<app>", "exec"), scope)
        lookup = scope["_find_parcel_record"]
        self.assertIsNone(lookup({"id": "2200/8", "lotNumber": "2200/9"}, "2200/8"))
        self.assertIsNone(lookup({"displayName": "2200/8", "lotNumber": "2200/9"}, "2200/8"))
        self.assertEqual(lookup({"id": "17", "lotNumber": "2200/8"}, "2200/8")["id"], "17")
        self.assertEqual(lookup([{"id": "1", "hrsz": "2200/9"},
                                 {"id": "2", "hrsz": "2200/8"}], "2200/8")["id"], "2")

    def test_all_budapest_districts_are_selectable(self):
        from pathlib import Path
        import ast
        source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "main")
        options = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Tuple) and len(node.elts) == 23:
                if all(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in node.elts):
                    options = [x.value for x in node.elts]
        self.assertEqual(len(options), 23)
        self.assertEqual(len(set(options)), 23)
        self.assertIn("XII", options)
        self.assertIn("XXIII", options)

    def test_multiple_zone_coverage(self):
        import ast
        from pathlib import Path
        source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(source).body
                  if isinstance(n, ast.FunctionDef) and n.name == "parcel_zone_coverage")
        scope = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<app>", "exec"), scope)
        classify = scope["parcel_zone_coverage"]
        def rect(a, b):
            return {"type": "Polygon", "coordinates": [[
                [a,0],[b,0],[b,10],[a,10],[a,0]]]}
        parcel = rect(0,10)
        def item(code, a, b, crs="EPSG:23700"):
            return {"code": code, "geometry": rect(a,b), "crs": crs}
        self.assertEqual(classify(parcel,[item("A",0,10)],"EPSG:23700")["status"],"single_zone_spatial")
        self.assertEqual(classify(parcel,[item("A",0,5),item("A",5,10)],"EPSG:23700")["status"],"single_zone_spatial")
        self.assertEqual(classify(parcel,[item("A",0,5),item("B",5,10)],"EPSG:23700")["status"],"multiple_zones")
        self.assertEqual(classify(parcel,[item("A",0,5)],"EPSG:23700")["status"],"partial_coverage")
        self.assertEqual(classify(parcel,[item("A",0,7),item("B",5,10)],"EPSG:23700")["status"],"overlapping_zones")
        self.assertEqual(classify(parcel,[item("A",0,10,"EPSG:4326")],"EPSG:23700")["status"],"unverified")
        self.assertEqual(classify(parcel,[{"code":"A","crs":"EPSG:23700","geometry":{"type":"Point","coordinates":[1,1]}}],"EPSG:23700")["status"],"unverified")
        self.assertEqual(classify(parcel,[{"code":"A","crs":"EPSG:23700"}],"EPSG:23700")["status"],"unverified")
        self.assertEqual(classify(parcel,[{"code":"","crs":"EPSG:23700","geometry":rect(0,10)}],"EPSG:23700")["status"],"unverified")

    def test_polygon_intersection_requires_matching_crs(self):
        import app
        def polygon(x0, y0, x1, y1):
            return {"type": "Polygon", "coordinates": [[
                [x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]]]}
        parcel = polygon(0, 0, 10, 10)
        half = polygon(5, 0, 15, 10)
        result = app.parcel_zone_overlap(parcel, half, "EPSG:23700", "EPSG:23700")
        self.assertTrue(result["verified"])
        self.assertEqual(result["overlap_ratio"], 0.5)
        self.assertFalse(app.parcel_zone_overlap(parcel, half, "EPSG:23700", "EPSG:4326")["verified"])
        self.assertFalse(app.parcel_zone_overlap(parcel, polygon(10,0,20,10), "EPSG:23700", "EPSG:23700")["verified"])
        self.assertFalse(app.parcel_zone_overlap(parcel, {"type":"Point","coordinates":[5,5]}, "EPSG:23700", "EPSG:23700")["verified"])

    def test_spatial_evidence_is_explicitly_unverified(self):
        import fitz
        import app
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((40, 60), '034/15')
        page.insert_text((65, 80), 'Gip/3')
        result = app.locate_parcel(doc, '034/15')
        self.assertEqual(result['zone'], '')
        self.assertFalse(result['spatial_evidence']['parcel_boundary_verified'])
        self.assertFalse(result['spatial_evidence']['zone_boundary_verified'])
        self.assertFalse(result['spatial_evidence']['intersection_verified'])

    def test_nearby_zone_is_not_automatically_confirmed(self):
        import ast
        from pathlib import Path
        source = Path(__file__).with_name('app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'locate_parcel')
        body = ast.get_source_segment(source, function)
        self.assertIn('"status": "candidate_unverified" if candidates else "zone_not_found"', body)
        self.assertIn('"zone": ""', body)

    def test_map_zone_candidates_do_not_join_unrelated_words(self):
        import ast
        from pathlib import Path
        source = Path(__file__).with_name('app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'zone_candidates')
        body = ast.get_source_segment(source, function)
        self.assertNotIn('ZONE_PATTERN.finditer(joined)', body)
        self.assertIn('ZONE_PATTERN.finditer(text)', body)

    def test_miskolc_hyphenated_zone_pattern(self):
        import ast
        import re
        from pathlib import Path
        source = Path(__file__).with_name('app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        assign = next(node for node in tree.body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'ZONE_PATTERN' for t in node.targets))
        expression = ast.literal_eval(assign.value.args[0])
        pattern = re.compile(expression, re.I)
        self.assertEqual(pattern.findall('4755/11 Gipe-60.63.5'), ['Gipe-60.63.5'])
        self.assertEqual(pattern.findall('Gip/3'), ['Gip/3'])

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
