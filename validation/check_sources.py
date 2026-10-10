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


def check_report(report, cache, history=None):
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
        inventory=case['source_inventory']
        check(name+': teljes forrásbekezdések megőrzött hivatkozásokkal',
              bool(inventory['rows']) and inventory['source_verified'] is True
              and all(row['Teljes rendelkezés'] and app.is_official_njt_url(row['Forrás'])
                      and row['Forrás időállapota']==inputs['edition'] for row in inventory['rows']))
        gis=case.get('gis_sources')
        if gis:
            check(name+': GIS-elérhetőségből nincs állított övezeti bizonyítás',
                  gis['zoning_verified'] is False)
            receipts=gis['catalog_receipts']+gis['entries']+gis['services']
            if gis.get('heritage_snapshot'):
                receipts.append(gis['heritage_snapshot'])
                receipts.append(gis['heritage_snapshot']['reuse_evidence']['receipt'])
            for receipt in receipts:
                if receipt.get('sha256'):
                    original=Path('work/gis-cache')/(receipt['sha256']+'.bin')
                    check(name+': eredeti GIS-forrásbájtok '+receipt['sha256'][:12],
                          original.is_file() and app.source_digest(original.read_bytes())==receipt['sha256'])
                else:
                    check(name+': GIS-forráshiba kifejezetten megőrizve',bool(receipt.get('error')))
            heritage=gis.get('heritage_snapshot',{})
            check(name+': GIS-pillanatkép nem teljes jogi korlátozáslista',
                  heritage.get('complete_restrictions_verified') is not True
                  and heritage.get('current_legal_protection_verified') is not True)
        visual=case.get('visual')
        if visual:
            check(name+': képi kapcsolat nem övezeti bizonyítás',
                  visual['intersection_verified'] is False)
            check(name+': vizuális vizsgálat forráskötése megőrizve',
                  visual.get('source',{})==case['legend'].get('identity',{}))
    miskolc=report['cases'][-1]
    check('Miskolc: önállóan azonosított 4755/11 telekhatár',
          miskolc['identification']['parcel_boundary_verified'] is True)
    if miskolc.get('visual',{}).get('candidate_labels'):
        import fitz
        path=cache/(hashlib.sha256(miskolc['plan_inputs']['source_url'].encode()).hexdigest()+'.pdf')
        with fitz.open(path) as doc:
            rebuilt_visual=app.visual_plan_evidence(doc,miskolc['plan_result'],miskolc['legend'],
                miskolc['plan_inputs'],miskolc['ksh'],miskolc['hrsz'])
        visual=miskolc['visual']
        check('Miskolc: eredeti tervrészlet és saját jelmagyarázat képe újraszámítva',
              rebuilt_visual['annotated_image_sha256']==visual['annotated_image_sha256']
              and rebuilt_visual['legend_image_source_verified'])
        check('Miskolc: helyi képi kapcsolat újraszámítása nem közeli feliratválasztás',
              rebuilt_visual['candidate_labels']==visual['candidate_labels'])
        check('Miskolc: Gipe vizuálisan valószínű, továbbra sem bizonyított',
              visual['status']=='probable' and visual['zone']=='Gipe-60.63.5'
              and visual['legend_bound'] and visual['exact_hrsz_in_parcel'])
        check('Miskolc: túloldali felirat nem halad át a saját jelmagyarázat szerinti határon',
              any(r['code']=='Gksz-71.62.6' and r['clear_paths']==0 for r in visual['candidate_labels']))
    if miskolc.get('gis_sources',{}).get('heritage_snapshot',{}).get('spatial_snapshot_checked'):
        from gis_sources import shapefile_intersections
        from shapely import wkt
        from shapely.geometry import mapping
        heritage=miskolc['gis_sources']['heritage_snapshot']
        body=(Path('work/gis-cache')/(heritage['sha256']+'.bin')).read_bytes()
        reconstructed=shapefile_intersections(body,mapping(wkt.loads(miskolc['plan_result']['parcel_wkt'])),'EPSG:23700')
        check('Miskolc: eredeti országos SHP területi metszése újraszámítva',
              reconstructed['layers']==heritage['layers'] and reconstructed['intersections']==heritage['intersections'])
    check('Miskolc: Gipe-60.63.5 kizárólag jelölt',
          miskolc['identification']['candidate_zone']=='Gipe-60.63.5'
          and not miskolc['identification']['intersection_verified'])
    values=[row['Érték'] for row in miskolc['zone_rules']['parameter_rows']]
    check('Miskolc: saját paraméterjelmagyarázat öt kódpozíciója',
          len(values)==5 and '12,5' in values[0] and 'Adotts' in values[1]
          and '50' in values[2] and '25' in values[3] and '1200' in values[4])
    check('Miskolc: teljes forrásbekezdések, kiadáseltérés nélkül',
          bool(miskolc['zone_rules']['clause_rows']) and not miskolc['zone_rules']['errors'])
    from plan_legend import styles_for
    road_styles=styles_for(miskolc['legend'],'road_area')
    check('Miskolc: saját jelmagyarázati oszlop alapján felismert két útterületminta',
          any(s.get('fill')==[1.0,0.761,0.0] for s in road_styles)
          and any(s.get('fill')==[1.0,1.0,0.451] for s in road_styles))
    closure=miskolc['identification']['closure_audit']
    check('Miskolc: hiányzó sarkok javítása nélkül nincs állított teljes fedés',
          closure['endpoint_repairs']==0 and not closure['complete_parcel_coverage'])
    # Reconstruct from the actual official target page, not from expected zone
    # text or a fabricated test polygon. Recompute source gaps independently
    # of the persisted receipt and test their world-coordinate measurements.
    import fitz
    import numpy as np
    from shapely import wkt
    from shapely.geometry import Polygon,Point,box
    from geopdf import page_registrations,to_pdf,to_world,legend_layer_paths,source_zone_polygons
    from plan_connections import native_dotted_boundaries,boundary_topology_audit,closed_zone_faces
    plan_path=cache/(hashlib.sha256(miskolc['plan_inputs']['source_url'].encode()).hexdigest()+'.pdf')
    with fitz.open(plan_path) as doc:
        page=doc[miskolc['plan_result']['pdf_page']-1]
        registration=page_registrations(page)[0];matrix=registration['matrix']
        world=wkt.loads(miskolc['plan_result']['parcel_wkt'])
        parcel=Polygon([to_pdf(q,matrix) for q in world.exterior.coords])
        markers=native_dotted_boundaries(page,styles_for(miskolc['legend'],'zone_boundary'))
        areas=[area for role in ('road_area','landuse_area')
               for area in source_zone_polygons(legend_layer_paths(page,miskolc['legend'],role))]
        lines=[line for line in markers['lines'] if line.geom_type=='LineString']
        lines.extend(segment for path in legend_layer_paths(page,miskolc['legend'],'zone_boundary')
                     if path['fill'] is None for segment in path['segments'])
        topology=boundary_topology_audit(parcel,lines,areas,
            neighbourhood_points=max(markers.get('spacing_points',0)*2,1))
        labels=[(word[4],Point((word[0]+word[2])/2,(word[1]+word[3])/2),word[:4])
                for word in page.get_text('words') if app.ZONE_PATTERN.fullmatch(word[4])
                or word[4] in ('Ev','Eg','Ve','V','kt.')]
        native=[segment for path in legend_layer_paths(page,miskolc['legend'],'zone_boundary')
                if path['fill'] is None for segment in path['segments']]
        masks=[]
        def read_mask(drawing):
            if drawing.get('fill') is None or drawing.get('fill_opacity',1)!=1:return
            if len(drawing['items'])!=1 or drawing['items'][0][0]!='re':return
            rectangle=fitz.Rect(drawing['items'][0][1])
            if any(rectangle.contains(fitz.Rect(label[2])) for label in labels):
                masks.append(box(*rectangle))
        page.get_cdrawings(callback=read_mask)
        rebuilt=closed_zone_faces(parcel,registration['frame'],markers,areas,labels,masks,native_paths=native)
        check('Miskolc: tényleges forráshatárokból újrafuttatott poligonzárás nem bizonyít övezetet',
              rebuilt['audit']['closed_labelled_faces']==closure['closed_labelled_faces']
              and not rebuilt['audit']['complete_parcel_coverage'])
        expected=closure['topology']['open_endpoints'];actual=topology['open_endpoints']
        check('Miskolc: nyitott csatlakozások újramérése az eredeti tervlapból',
              bool(actual) and len(actual)==len(expected)
              and all(a['point_pdf']==b['point_pdf']
                      and abs(a['gap_points']-b['gap_points'])<1e-9 for a,b in zip(actual,expected)))
        check('Miskolc: a tervlapi rések EOV-mérete nem becsült javítás',
              all(abs(float(np.linalg.norm(np.array(to_world(a['point_pdf'],matrix))-
                  to_world(a['nearest_source_point_pdf'],matrix)))-b['gap_m'])<1e-9
                  for a,b in zip(actual,expected)))
        check('Miskolc: közös invertálható GEO-illesztés nem zárhat forrásrést',
              abs(float(np.linalg.det(matrix[:2])))>0
              and closure['georeferencing']['affine_matrix']==matrix.tolist()
              and not closure['georeferencing']['registration_closes_source_gaps']
              and max(np.linalg.norm(np.array(to_world(to_pdf(q,matrix),matrix))-q)
                      for q in world.exterior.coords)<1e-7)
    audit=miskolc['identification']['territorial_audit']
    check('Miskolc: területi audit mindkét eredeti forráshoz kötve',
          audit['plan_sha256']==miskolc['plan_inputs']['source_hash']
          and audit['legend_sha256']==miskolc['legend']['identity']['source_hash']
          and not audit['complete'])
    if history is not None:
        import fitz
        from plan_legend import caption_rows,parse_legend
        from zone_parameters import compact
        path=cache/(hashlib.sha256(history['historical_legend_url'].encode()).hexdigest()+'.pdf')
        check('Miskolc: korábbi saját jelmagyarázat eredeti NJT-bájtjai',
              path.is_file() and app.source_digest(path.read_bytes())==history['historical_legend_sha256']
              and history['historical_legal_source_verified'] is True)
        with fitz.open(path) as doc:
            records=[row for page in doc for row in parse_legend(page,caption_rows(page))]
        expected=history['historical_record']
        check('Miskolc: korábbi kétsoros felirat és valódi bal oldali minta',
              any(row['label']==expected['label'] and row['styles']==expected['styles'] for row in records)
              and any(compact(row['label'])==compact(expected['label'])
                      for row in miskolc['legend']['records']))
        check('Miskolc: történeti jelváltozat nem válik automatikusan hatályos övezetbizonyítékká',
              history['zone_verified'] is False and history['current_style_compatibility_verified'] is False
              and history['current_legend_sha256']==miskolc['legend']['identity']['source_hash']
              and history['plan_sha256']==miskolc['plan_inputs']['source_hash'])
    return checks


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report');parser.add_argument('--output')
    parser.add_argument('--history-report',help='Separate historical own-legend receipt; never an active zoning source.')
    parser.add_argument('--pdf-cache',default=str(Path(tempfile.gettempdir())/'telekeloiras_pdf_cache'))
    args=parser.parse_args();report=json.loads(Path(args.report).read_text())
    history=json.loads(Path(args.history_report).read_text()) if args.history_report else None
    checks=check_report(report,Path(args.pdf_cache),history)
    result={'report_sha256':app.source_digest(Path(args.report).read_bytes()),
            'passed':len(checks),'checks':checks}
    if history is not None:result['history_report_sha256']=app.source_digest(Path(args.history_report).read_bytes())
    if args.output:Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(str(len(checks))+'/'+str(len(checks))+' forrásalapú ellenőrzés sikeres.')
