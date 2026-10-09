"""Check live reference receipts against original downloaded official PDFs.

Run reference_checks.py first. These checks are intentionally separate from
offline unittest discovery and require its actual source downloads.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from reference_checks import CASES


def check_report(report, cache):
    checks=[]
    def check(name, condition):
        if not condition:raise AssertionError(name)
        checks.append(name)
    check('Mind az öt kért telek szerepel',
          [(c['place'],c['hrsz']) for c in report['cases']]==CASES)
    for filename,expected in report['code_sha256'].items():
        check('Feldolgozó lenyomata: '+filename,
              app.source_digest(Path(filename).read_bytes())==expected)
    for case in report['cases']:
        name=case['place']+' '+case['hrsz'];inputs=case['plan_inputs']
        check(name+': hivatalos, hatályos NJT-terv',
              inputs.get('current_verified') is True and inputs.get('source_valid') is True
              and app.is_official_njt_url(inputs['source_url']))
        url=inputs['source_url'];path=cache/(hashlib.sha256(url.encode()).hexdigest()+'.pdf')
        check(name+': eredeti tervbájtok lenyomata',
              path.is_file() and app.source_digest(path.read_bytes())==inputs['source_hash'])
        profile=case['legend'];identity=profile.get('identity',{})
        check(name+': saját jelmagyarázat ugyanahhoz a tervhez és időállapothoz kötve',
              identity.get('plan_url')==url and identity.get('plan_hash')==inputs['source_hash']
              and identity.get('edition')==inputs['edition'] and identity.get('ksh')==case['ksh'])
        source=identity['source_url'];legend_path=cache/(hashlib.sha256(source.encode()).hexdigest()+'.pdf')
        check(name+': eredeti jelmagyarázat-bájtok lenyomata',
              legend_path.is_file() and app.source_digest(legend_path.read_bytes())==identity['source_hash'])
        evidence=case['evidence'];identification=case['identification']
        check(name+': külön bizonyítottsági állapotok',len(evidence)==4)
        check(name+': a jelölt nem bizonyított övezet',
              bool(evidence[2]['Bizonyított'])==bool(identification.get('intersection_verified')))
        check(name+': hiányzó feltételekből nincs teljes előíráslista',
              not evidence[3]['Bizonyított'] if case['zone_rules']['missing_evidence'] else True)
    miskolc=report['cases'][-1]
    check('Miskolc: önállóan azonosított 4755/11 telekhatár',
          miskolc['identification']['parcel_boundary_verified'] is True)
    check('Miskolc: Gipe-60.63.5 kizárólag jelölt',
          miskolc['identification']['candidate_zone']=='Gipe-60.63.5'
          and not miskolc['identification']['intersection_verified'])
    values=[row['Érték'] for row in miskolc['zone_rules']['parameter_rows']]
    check('Miskolc: saját paraméterjelmagyarázat öt kódpozíciója',
          len(values)==5 and '12,5' in values[0] and 'Adotts' in values[1]
          and '50' in values[2] and '25' in values[3] and '1200' in values[4])
    check('Miskolc: teljes forrásbekezdések, kiadáseltérés nélkül',
          bool(miskolc['zone_rules']['clause_rows']) and not miskolc['zone_rules']['errors'])
    return checks


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report');parser.add_argument('--output')
    parser.add_argument('--pdf-cache',default=str(Path(tempfile.gettempdir())/'telekeloiras_pdf_cache'))
    args=parser.parse_args();report=json.loads(Path(args.report).read_text())
    checks=check_report(report,Path(args.pdf_cache))
    result={'report_sha256':app.source_digest(Path(args.report).read_bytes()),
            'passed':len(checks),'checks':checks}
    if args.output:Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(str(len(checks))+'/'+str(len(checks))+' forrásalapú ellenőrzés sikeres.')
