"""Repeat the six official-source checks without interacting with Streamlit.

This is a live integration command, not an offline unit test. An unavailable
source is an explicit result and never an assertion that a parcel does not exist.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import app

CASES = [('Tiszaújváros', '2200/8'), ('Budapest XII. kerület', '8448/46'),
         ('Komádi', '1558'), ('Gersekarát', '034/15'), ('Miskolc', '4755/11'),
         ('Budapest', '76561/152'), ('Kondoros', '1570')]

# Human-reviewed benchmark evidence is deliberately kept separate from live
# application inputs. Never pass these codes into inspect_official_parcel.
KNOWN_EVIDENCE = {
    ('Tiszaújváros', '2200/8'): {'zone': 'Gip/3', 'level': 'previously identified'},
    ('Miskolc', '4755/11'): {'zone': 'Gipe-60.63.5', 'level': 'previously identified'},
    ('Komádi', '1558'): {'zone': 'Lke/1.2', 'level': 'previously identified'},
    ('Gersekarát', '034/15'): {'zone': 'Kb-Nk', 'level': 'preliminary candidate'},
    ('Budapest', '76561/152'): {'zone': 'Ln-T/IV-9/K', 'level': 'reviewed official plan',
        'source': 'https://or.njt.hu/onkormanyzati-rendelet/2025-20-SP-253'},
    ('Budapest XII. kerület', '8448/46'): {'zone': 'Lke-2/D-2', 'level': 'reviewed official plan',
        'source': 'https://njt.jog.gov.hu/document/c3/c3f0LL_EJR_99708274-20250806_D-Hegyvid_k_K_SZ_1_mell_klet.pdf',
        'supersedes_candidate': 'L6-XII/IK1', 'regulation': '36/2021. (XII. 14.)'},
}




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
    result['plan_inputs'].pop('legal_zone_text', None)
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
    parser.add_argument('--resume-attempts',type=int,default=3,help='Resume bounded OCR searches before recording a partial result.')
    parser.add_argument('--output', default='work/reference-results.json')
    parser.add_argument('--case-index', type=int, choices=range(1, len(CASES)+1), help='Run one independent reference parcel (1-7).')
    parser.add_argument('--outlined', action='store_true',
                        help='Run bounded/resumable outlined-label recognition when needed.')
    parser.add_argument('--visual-images',help='Save source-bound illustrations for every localised case.')
    parser.add_argument('--visual-image',help='Save the Miskolc reference illustration with its original legend.')
    args = parser.parse_args()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'outlined_label_search': True,
              'legacy_outlined_flag': args.outlined,
              'code_sha256': {name:app.source_digest(Path(name).read_bytes())
                              for name in ('app.py','plan_labels.py','plan_localization.py','plan_ocr_worker.py','geopdf.py','plan_legend.py','plan_connections.py','plan_geometry_audit.py','zone_parameters.py','gis_sources.py','visual_plan.py','reference_checks.py')}, 'cases': []}
    for place, hrsz in ([CASES[args.case_index-1]] if args.case_index else CASES):
        print(place + ' ' + hrsz + ': hivatalos forráslekérés…', flush=True)
        def progress(page, pages, scanned, labels):
            print(f'  OCR: {page}/{pages} oldal; {scanned} feliratcsoport; {labels} jelölt', flush=True)
        for attempt in range(max(1,args.resume_attempts)):
            result = app.inspect_official_parcel(place, hrsz, outlined=args.outlined, on_progress=progress)
            scan=result.get('label_search',{}).get('scan',{})
            alternatives=result.get('plan_inputs',{}).get('alternative_plan_search',[])
            if (not scan or scan.get('complete')) and all(r.get('complete') or r.get('error') for r in alternatives):break
            print('  Részleges index folytatása: '+str(attempt+1),flush=True)
        png=result.get('visual',{}).pop('annotated_png',None)
        if png and args.visual_images:
            folder=Path(args.visual_images);folder.mkdir(parents=True,exist_ok=True)
            name=place.lower().replace(' ','-')+'-'+hrsz.replace('/','-')+'.png'
            if (place,hrsz)==('Miskolc','4755/11') and args.visual_image:name=Path(args.visual_image).name
            (folder/name).write_bytes(png)
            result['visual']['illustration_file']=name
        if png and args.visual_image and (place,hrsz)==('Miskolc','4755/11'):
            Path(args.visual_image).write_bytes(png)
        result['benchmark'] = KNOWN_EVIDENCE.get((place, hrsz), {})
        # Benchmark is attached only AFTER the full independent live run.
        # Do not mistake the stored human evidence for a program finding.
        report['cases'].append(public_result(result))
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        for row in result['evidence']:
            print(f"  {row['Adat']}: {row['Bizonyított']} – {row['Eredmény']}", flush=True)
    print('Eredmény: ' + str(destination), flush=True)


if __name__ == '__main__':
    main()
