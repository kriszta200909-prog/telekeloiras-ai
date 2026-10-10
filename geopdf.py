"""Read source georeferencing and named polygon layers from official GeoPDFs.

No municipality, parcel number, control point or zoning answer is stored here.
Latitude/longitude GPTS values use the geographic datum in the PDF GCS, rather
than assuming WGS84. Unsupported transforms and open paths stay unverified.
"""
import math
import re

import fitz
import numpy as np
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Polygon, Point
from shapely.ops import polygonize, unary_union


def _numbers(doc, xref, key):
    kind, value = doc.xref_get_key(xref, key)
    if kind != 'array':
        raise ValueError('Hiányzó GeoPDF-koordinátalista: ' + key)
    return [float(v) for v in re.findall(r'[-+]?(?:\d*\.\d+|\d+)(?:[Ee][-+]?\d+)?', value)]


def page_registrations(page):
    """PDF page coordinates -> source projected CRS, with measured residual."""
    doc = page.parent
    kind, value = doc.xref_get_key(page.xref, 'VP')
    if kind != 'array' or page.rotation or page.cropbox != page.mediabox:
        return []
    result = []
    for reference in re.findall(r'(\d+)\s+0\s+R', value):
        try:
            viewport = int(reference)
            measure = doc.xref_get_key(viewport, 'Measure')
            if measure[0] != 'xref':
                continue
            mx = int(measure[1].split()[0])
            if doc.xref_get_key(mx, 'Subtype')[1] != '/GEO':
                continue
            gx = doc.xref_get_key(mx, 'GCS')
            if gx[0] != 'xref':
                continue
            wkt = doc.xref_get_key(int(gx[1].split()[0]), 'WKT')
            if wkt[0] != 'string':
                continue
            crs = CRS.from_wkt(wkt[1])
            # This identification engine operates in explicit EOV metres.
            if not crs.equals(CRS.from_epsg(23700), ignore_axis_order=True):
                continue
            bbox = _numbers(doc, viewport, 'BBox')
            local = np.array(_numbers(doc, mx, 'LPTS')).reshape(-1, 2)
            geo = np.array(_numbers(doc, mx, 'GPTS')).reshape(-1, 2)
            if len(bbox) != 4 or len(local) < 4 or len(local) != len(geo):
                continue
            x0, y0, x1, y1 = bbox
            pdf = np.column_stack([x0 + local[:, 0] * (x1-x0),
                                   page.mediabox.height - (y0 + local[:, 1] * (y1-y0))])
            transform = Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True)
            x, y = transform.transform(geo[:, 1], geo[:, 0], errcheck=True)
            world = np.column_stack([x, y])
            design = np.column_stack([pdf, np.ones(len(pdf))])
            matrix, _, rank, _ = np.linalg.lstsq(design, world, rcond=None)
            residual = float(np.linalg.norm(design @ matrix-world, axis=1).max())
            if rank != 3 or not math.isfinite(residual) or residual > 1:
                continue
            # GPTS is printed to five decimals in many ArcGIS exports. Include
            # its quantisation as well as the actual affine fit residual.
            error = residual + 1.0
            frame = Polygon([(x0, page.mediabox.height-y0), (x0, page.mediabox.height-y1),
                             (x1, page.mediabox.height-y1), (x1, page.mediabox.height-y0)])
            result.append({'matrix': matrix, 'crs': 'EPSG:23700', 'error_m': error,
                           'residual_m': residual, 'frame': frame,
                           'world_frame': Polygon([to_world(p, matrix) for p in frame.exterior.coords])})
        except (ValueError, TypeError, KeyError, RuntimeError):
            continue
    return result


def to_world(point, matrix):
    value = np.array([point[0], point[1], 1.0]) @ matrix
    return tuple(float(v) for v in value)


def to_pdf(point, matrix):
    value = (np.array(point)-matrix[2]) @ np.linalg.inv(matrix[:2])
    return tuple(float(v) for v in value)


def named_layer_paths(page, names):
    """Read straight segments only; curves require an explicit future adapter."""
    wanted = {name.casefold() for name in names}
    paths = []
    def collect(drawing):
        layer = drawing.get('layer', '').rstrip('\0').casefold()
        if layer not in wanted:
            return
        segments = []
        for item in drawing['items']:
            if item[0] == 'l':
                segments.append(LineString([item[1], item[2]]))
            elif item[0] == 're':
                r = fitz.Rect(item[1])
                segments.append(LineString([r.tl, r.tr, r.br, r.bl, r.tl]))
            else:
                return
        if segments:
            from plan_legend import drawing_style
            paths.append({'layer': layer, 'segments': segments, 'fill': drawing.get('fill'),
                          'style':drawing_style(drawing)})
    page.get_cdrawings(callback=collect)
    return paths


def legend_layer_paths(page, profile, role, clip=None):
    """Match own legend styles; preserve explicitly named cadastral CAD data.

    A cadastral layer may be printed thin/grey despite the legend's thicker
    black sample. Its explicit source name is accepted only when that same
    cadastral term appears in the verified legend. This exception never maps
    a zoning or regulatory line by its layer name.
    """
    from plan_legend import styles_for,matches_style,drawing_style
    from zone_parameters import compact
    styles=styles_for(profile,role);paths=[]
    # A native, verified cadastral layer remains usable even if legend
    # sampling cannot isolate its stroke (common in dense CAD PDF legends).
    # Do not generalise this exception to zoning/regulatory boundaries.
    cadastral_names=set()
    if role=='parcel_boundary':
        for record in profile.get('records',[]):
            if record['role']==role and record.get('label_verified') and record['recognition']=='native':
                term=compact(record['label'])
                if term.endswith('hatar'):cadastral_names.add(term[:-5])
    if not styles and not cadastral_names:
        return paths
    def collect(drawing):
        # Filtering keeps complete source paths, never creates artificial clip
        # edges or connects geometry. It avoids decoding an entire CAD town
        # when the caller only examines one parcel's immediate surroundings.
        if clip is not None and not fitz.Rect(clip).intersects(fitz.Rect(drawing['rect'])+(-.01,-.01,.01,.01)):
            return
        style=drawing_style(drawing)
        visual_match=any(matches_style(style,expected) for expected in styles)
        semantic_match=compact(drawing.get('layer','').rstrip('\0')) in cadastral_names
        if not visual_match and not semantic_match:return
        segments=[]
        for item in drawing['items']:
            if item[0]=='l':segments.append(LineString([item[1],item[2]]))
            elif item[0]=='re':
                r=fitz.Rect(item[1]);segments.append(LineString([r.tl,r.tr,r.br,r.bl,r.tl]))
            else:return  # Curves are not silently approximated by chords.
        if segments:paths.append({'layer':role,'segments':segments,'fill':drawing.get('fill'),'style':style,
                                  'visual_match':visual_match,'semantic_cadastral_layer':semantic_match})
    page.get_cdrawings(callback=collect)
    return paths


def polygon_faces(paths):
    segments = [line for path in paths for line in path['segments']]
    return list(polygonize(unary_union(segments))) if segments else []


def source_zone_polygons(paths):
    """Only explicit, closed filled plan polygons count as zone areas."""
    result = []
    for path in paths:
        if path['fill'] is None:
            continue
        # Polygonize each path separately. It preserves rings and never joins
        # unrelated paths merely because their bounding boxes are nearby.
        result.extend(polygon_faces([path]))
    return result
