"""Repeat the five official-source checks without interacting with Streamlit.

This is a live integration command, not an offline unit test. An unavailable
source is an explicit result and never an assertion that a parcel does not exist.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import app

CASES = [('Tiszaújváros', '2200/8'), ('Budapest XII. kerület', '8448/46'),
         ('Komádi', '1558'), ('Gersekarát', '034/15'), ('Miskolc', '4755/11')]


def public_result(result):
    """Retain proof and geometry; omit raw address records and local cache paths."""
    parcel = result.pop('parcel_api', None)
    if parcel:
        result['parcel_source'] = {key: parcel.get(key) for key in
            ('id', 'search_url', 'geometry_url', 'parcel_polygon_candidate',
             'parcel_boundary_verified', 'geometry_crs', 'geometry_role')}
        result['parcel_source']['geometry_sha256'] = app.source_digest(
            json.dumps(parcel.get('geometry',{}).get('outline'), sort_keys=True))
    result['plan_inputs'].pop('path', None)
    def strip_templates(value):
        if isinstance(value,dict):
            value.pop('png_base64',None)
            for child in value.values():strip_templates(child)
        elif isinstance(value,list):
            for child in value:strip_templates(child)
    strip_templates(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='work/reference-results.json')
    parser.add_argument('--outlined', action='store_true',
                        help='Run bounded/resumable outlined-label recognition when needed.')
    args = parser.parse_args()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'outlined_label_search': args.outlined,
              'code_sha256': {name:app.source_digest(Path(name).read_bytes())
                              for name in ('app.py','geopdf.py','plan_legend.py','plan_connections.py','zone_parameters.py','reference_checks.py')}, 'cases': []}
    for place, hrsz in CASES:
        print(place + ' ' + hrsz + ': hivatalos forráslekérés…', flush=True)
        def progress(page, pages, scanned, labels):
            print(f'  OCR: {page}/{pages} oldal; {scanned} feliratcsoport; {labels} jelölt', flush=True)
        result = app.inspect_official_parcel(place, hrsz, outlined=args.outlined, on_progress=progress)
        report['cases'].append(public_result(result))
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        for row in result['evidence']:
            print(f"  {row['Adat']}: {row['Bizonyított']} – {row['Eredmény']}", flush=True)
    print('Eredmény: ' + str(destination), flush=True)


if __name__ == '__main__':
    main()
