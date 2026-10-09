"""Read native dotted boundary symbols and test bounded plan connections.

A connection is additional map evidence, never a replacement zone polygon.
The module does not know municipalities, cadastral numbers or expected codes.
"""
import io
import math

import fitz
import numpy as np
from fontTools.ttLib import TTFont
from fontTools.pens.boundsPen import BoundsPen
from shapely.geometry import Point, LineString, box
from shapely.ops import unary_union, polygonize


def native_dotted_boundaries(page, expected_styles=()):
    """Use the embedded circle's true centre, not its text origin/bounding box.

    Trace direction rotates the font coordinates. Only circular, one-contour
    markers with constant native spacing are supported; unknown symbols fail.
    Joining consecutive symbols within a trace follows the source's own order.
    No unrelated endpoints, label gaps or page edges are joined.
    """
    from plan_legend import font_registry,glyph_description,matches_style,_colour
    fonts=font_registry(page)
    if not expected_styles:
        return {'supported':False,'reason':'A saját jelmagyarázat igazolt határjelmintája hiányzik.','lines':[]}
    traces=[]
    for trace in page.get_texttrace():
        candidates=fonts.get(__import__('zone_parameters').compact(trace['font']),[])
        if len(candidates)!=1 or not trace['chars']:continue
        try:
            glyph=glyph_description(candidates[0],trace['chars'][0][1])
            actual={'kind':'marker','glyph_hash':glyph['glyph_hash'],'colour':_colour(trace['color']),'size':trace['size']}
            if any(matches_style(actual,style) for style in expected_styles):traces.append(trace)
        except (ValueError,KeyError,IndexError,TypeError):continue
    if not traces:
        return {'supported':False,'reason':'Nincs natív pontozott övezethatárréteg.','lines':[]}
    source_steps=[math.dist(a[2],b[2]) for t in traces
                  for a,b in zip(t['chars'],t['chars'][1:])]
    if not source_steps:
        return {'supported':False,'reason':'Nem olvasható a pontsor ismétlési távolsága.','lines':[]}
    typical=float(np.median(source_steps))
    lines=[];centres=[];spacings=[];gaps=[]
    for trace in traces:
        candidates=fonts.get(__import__('zone_parameters').compact(trace['font']),[])
        font=candidates[0] if len(candidates)==1 else None
        if (font is None or trace['type']!=0 or trace['opacity']!=1 or not trace['chars']):
            return {'supported':False,'reason':'Nem támogatott övezethatár-jel.','lines':[]}
        points=[]
        for char in trace['chars']:
            try:
                glyph_name=font.getGlyphOrder()[char[1]]
                glyph=font['glyf'][glyph_name]
                pen=BoundsPen(font.getGlyphSet());font.getGlyphSet()[glyph_name].draw(pen)
                x0,y0,x1,y1=pen.bounds
                width,height=x1-x0,y1-y0
                if glyph.numberOfContours!=1 or min(width,height)<=0 or abs(width-height)>.02*max(width,height):
                    raise ValueError('Nem körjel.')
                coords,_,_=glyph.getCoordinates(font['glyf'])
                cx,cy=(x0+x1)/2,(y0+y1)/2
                # Off-curve control points can extend outside a circle. The
                # on-curve points must nevertheless lie on its circumference.
                on=[q for q,flag in zip(coords,glyph.flags) if flag&1]
                if not on or any(abs(math.dist(q,(cx,cy))-width/2)>.02*width for q in on):
                    raise ValueError('Nem kör alakú kontúr.')
                units=font['head'].unitsPerEm;size=trace['size'];dx,dy=trace['dir']
                points.append((char[2][0]+size*(cx*dx+cy*dy)/units,
                               char[2][1]+size*(cx*dy-cy*dx)/units))
            except (ValueError,KeyError,IndexError,TypeError):
                return {'supported':False,'reason':'A beágyazott határjel alakja nem igazolt.','lines':[]}
        centres.extend(points)
        if len(points)>1:
            step=[math.dist(a,b) for a,b in zip(points,points[1:])]
            direction=np.array(trace['dir'])
            if (min(step)<=0
                    or any(np.linalg.norm((np.array(b)-a)-s*direction)>.02
                           for a,b,s in zip(points,points[1:],step))):
                return {'supported':False,'reason':'Az övezethatár pontsora nem egyenes.','lines':[]}
            for a,b,s in zip(points,points[1:],step):
                if abs(s-typical)<.02:
                    spacings.append(s);lines.append(LineString([a,b]))
                else:
                    # A source label cut is not joined into a boundary.
                    gaps.append(box(min(a[0],b[0]),min(a[1],b[1]),
                                    max(a[0],b[0]),max(a[1],b[1])).buffer(typical))
        else:
            lines.append(Point(points[0]))
    if not spacings or max(spacings)-min(spacings)>.05:
        return {'supported':False,'reason':'Az övezethatár pontsűrűsége nem egyértelmű.','lines':[]}
    return {'supported':True,'lines':lines,'centres':centres,
            'spacing_points':float(np.median(spacings)), 'trace_count':len(traces),
            'marker_count':len(centres),'gap_envelopes':gaps}


def bounded_zone_connections(parcel, frame, markers, labels, masks=(), *, uncertainty_points):
    """Find inscriptions reachable through a boundary-free source corridor.

    This deliberately returns *support*, not complete parcel classification.
    The uncertainty band and any missing closure remain explicit. A whole
    source zone polygon is still needed by the shared intersection engine.
    """
    result={'verified':False,'supported_codes':[],'reason':'','witnesses':[],
            'complete_parcel_verified':False}
    if not markers.get('supported'):
        result['reason']=markers.get('reason','Nem támogatott övezethatár.');return result
    spacing=markers['spacing_points']
    core=parcel.buffer(-(uncertainty_points+spacing/2+.02))
    if core.is_empty or core.area<.8*parcel.area:
        result['reason']='A telek belseje a rajzi bizonytalansággal nem vizsgálható.';return result
    # Half the native repeat interval covers gaps between individual symbols,
    # and is conservative for bends/endpoints. This is a barrier, not a zone.
    barrier=unary_union(markers['lines']).buffer(spacing/2+.02)
    if barrier.intersects(core):
        result['reason']='Övezethatár vagy annak bizonytalansági sávja érinti a telek belsejét.';return result
    records=[]
    for code,position,rect in labels:
        if not frame.contains(position) or parcel.distance(position)>200:
            continue
        corridor=unary_union([core,position.buffer(uncertainty_points)]).convex_hull
        if (not frame.contains(corridor) or barrier.intersects(corridor)
                or any(gap.intersects(corridor) for gap in markers.get('gap_envelopes',[]))):continue
        # A white source mask near a boundary may hide its continuation.
        # It must not become a shortcut through a cut or an OCR label.
        relevant=[mask for mask in masks if mask.distance(barrier)<spacing]
        if any(mask.intersects(corridor) for mask in relevant):continue
        records.append({'code':code,'label_rect':list(rect),'corridor_wkt':corridor.wkt})
    codes=sorted({r['code'] for r in records})
    result.update(supported_codes=codes,witnesses=records,
                  marker_count=markers['marker_count'],trace_count=markers['trace_count'],
                  spacing_points=spacing,core_fraction=core.area/parcel.area)
    if len(codes)==1:
        result['verified']=True
        result['reason']='Egyetlen övezeti felirat és a telekbelső közötti határmentes tervi kapcsolat ellenőrizve; a teljes határsáv és a zárt övezetpoligon nem igazolt.'
    else:result['reason']='Nincs egyértelmű, határmentes övezeti feliratkapcsolat.'
    return result


def boundary_topology_audit(parcel, lines, filled_areas, *, neighbourhood_points):
    """Report actual dangling endpoints and their nearest source connections.

    Distances are in PDF points. Nearby endpoints are evidence of a gap, not
    permission to snap. Crossings are noded before identifying open ends.
    The same source-coordinate graph is independent of world registration.
    """
    from shapely import line_merge
    from shapely.ops import nearest_points
    graph=unary_union(list(lines)+[area.boundary for area in filled_areas])
    if graph.is_empty:return {'open_endpoints':[], 'endpoint_repairs':0}
    merged=line_merge(graph)
    runs=[merged] if merged.geom_type=='LineString' else list(merged.geoms)
    runs=[run for run in runs if run.geom_type=='LineString']
    records=[]
    for index,run in enumerate(runs):
        if run.is_ring:continue
        for side,coordinate in ((0,run.coords[0]),(-1,run.coords[-1])):
            point=Point(coordinate)
            if point.distance(parcel)>neighbourhood_points:continue
            others=[other for j,other in enumerate(runs) if j!=index]
            # A degree-three node is not an open endpoint.
            if any(point.intersects(other) for other in others):continue
            candidates=others+[Point(run.coords[-1 if side==0 else 0])]
            other=min(candidates,key=point.distance)
            record={'point_pdf':list(coordinate), 'distance_to_parcel_points':point.distance(parcel),
                    'run_wkt':run.wkt, 'connection_verified':False}
            if other is not None:
                record.update(nearest_source_point_pdf=list(nearest_points(point,other)[1].coords[0]),
                              gap_points=point.distance(other))
            records.append(record)
    return {'open_endpoints':records, 'endpoint_repairs':0,
            'coordinate_basis':'source PDF points; no snapping or registration correction'}


def closed_zone_faces(parcel, frame, markers, filled_areas, labels, masks=(), *, native_paths=()):
    """Polygonize only actual source boundaries; never repair their endpoints.

    Filled areas must already have been matched against this plan's own legend.
    Roads and other filled land uses separate faces, but their inscriptions do
    not transfer across their boundary. Native circle arrays may contribute
    actual consecutive segments; isolated circles and missing corners cannot
    close a face. The caller still verifies complete parcel coverage in EOV.
    """
    out={'faces':[], 'audit':{'closed_labelled_faces':[], 'endpoint_repairs':0,
                            'complete_parcel_coverage':False}}
    lines=[line for line in markers.get('lines',[]) if line.geom_type=='LineString'] if markers.get('supported') else []
    # The caller has matched each complete native stroke (including its dash
    # pattern) to this plan's own legend. Combine actual source intersections
    # across symbol types, never infer a segment across a physical gap.
    lines.extend(native_paths)
    out['audit']['topology']=boundary_topology_audit(parcel,lines,filled_areas,
        neighbourhood_points=max(markers.get('spacing_points',0)*2,1))
    lines.extend(area.boundary for area in filled_areas)
    if not lines:return out
    graph=unary_union(lines)
    for face in polygonize(graph):
        if not frame.contains(face) or not face.intersects(parcel):continue
        if any(mask.intersects(face.boundary) for mask in masks):continue
        codes={code for code,point,_ in labels if face.contains(point)}
        if len(codes)!=1:continue
        code=next(iter(codes));coverage=face.intersection(parcel).area/parcel.area
        out['faces'].append((code,face))
        out['audit']['closed_labelled_faces'].append({'code':code,
            'parcel_area_fraction':coverage,'source_pdf_wkt':face.wkt})
    if out['faces']:
        out['audit']['complete_parcel_coverage']=unary_union(
            [face for _,face in out['faces']]).covers(parcel)
    out['audit']['reason']=('Zárt, feliratozott forrásterület található; a teljes fedés külön ellenőrizendő.'
        if out['faces'] else 'A saját jelmagyarázattal egyező forráshatárokból nem zárható feliratozott övezet a teleknél; a hiányzó sarkok és eltérő minták nincsenek kiegészítve.')
    return out
