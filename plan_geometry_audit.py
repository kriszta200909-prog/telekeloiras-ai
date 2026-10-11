"""Trace plan symbols to their own legend without claiming complete overlays.

An intersecting stroke proves only a depicted symbol. Reused visual styles are
reported together; their legal meaning is never selected by proximity or CAD
layer names. Unsupported markers, clipped patterns and unmatched fills remain
explicit gaps in the territorial-restriction audit.
"""
import fitz

from geopdf import legend_layer_paths, source_zone_polygons
from plan_legend import styles_for, matches_style, drawing_style


def audit_plan_geometry(page, parcel, profile):
    roles=('restriction','prohibition','utility_line','building_line')
    identity=profile.get('identity',{})
    out={'complete':False,'rows':[], 'unmatched_fills':[],
         'plan_source':identity.get('plan_url',''), 'plan_sha256':identity.get('plan_hash',''),
         'legend_source':identity.get('source_url',''), 'legend_sha256':identity.get('source_hash',''),
         'pdf_page':page.number+1, 'missing_evidence':[
             'A pontjelek, szöveges kiegészítések és kivágott sraffozások teljes területi értelmezése',
             'Minden térképi érintettség jogi feltétele és hatályos védőtávolsága']}
    for role in roles:
        for path in legend_layer_paths(page,profile,role):
            areas=source_zone_polygons([path])
            geometry=areas if areas else path['segments']
            hits=[shape for shape in geometry if shape.intersects(parcel)]
            if not hits:continue
            # Identical strokes may encode different voltages or different
            # protection types. Preserve every matching caption, not a guess.
            labels=sorted({record['label'] for record in profile.get('records',[])
                if record.get('label_verified') and any(matches_style(path['style'],style)
                    for style in record.get('styles',[]))})
            out['rows'].append({'Jelmagyarázati szerep':role,'Lehetséges jelmagyarázati feliratok':labels,
                'Jelentés egyértelmű':len(labels)==1,
                'Érintkező forrásgeometria':[shape.wkt for shape in hits],
                'Állapot':'A tervi jel telekkel metsződik; jogi alkalmazhatóság és védőtávolság külön igazolandó'})
    known=[style for role in ('road_area','landuse_area','restriction','prohibition')
           for style in styles_for(profile,role)]
    from shapely.geometry import box
    def unmatched(drawing):
        if drawing.get('fill') is None or not drawing.get('items'):return
        rect=box(*drawing['rect'])
        if not rect.intersects(parcel) or rect.covers(box(*page.rect)):return
        actual=drawing_style(drawing)
        if not any(matches_style(actual,style) for style in known):
            # A large river/road polygon's bounding box can include a parcel
            # far outside the painted area. It is not an intersection witness.
            from shapely.geometry import LineString
            segments=[]
            for item in drawing['items']:
                if item[0]=='l':segments.append(LineString([item[1],item[2]]))
                elif item[0]=='re':
                    r=fitz.Rect(item[1]);segments.append(LineString([r.tl,r.tr,r.br,r.bl,r.tl]))
                else:return  # Unknown curves/clip extents stay in missing evidence.
            areas=source_zone_polygons([{'fill':drawing['fill'],'segments':segments}])
            hits=[area for area in areas if area.intersects(parcel)]
            if not hits:return
            record={'fill':actual['fill'],'bounds':list(drawing['rect']),
                    'semantic_role_verified':False,'source_pdf_wkt':[area.wkt for area in hits]}
            if record not in out['unmatched_fills']:out['unmatched_fills'].append(record)
    page.get_cdrawings(callback=unmatched)
    return out
