import io
import unittest
from unittest.mock import patch

import fitz
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from shapely.geometry import box,Point,LineString

import app
from plan_connections import native_dotted_boundaries,bounded_zone_connections,closed_zone_faces
from plan_legend import font_registry,glyph_description,_colour
from zone_parameters import legend_tables,decode_zone,ORDINALS


def marker_pdf(square=False,rotate=0):
    fb=FontBuilder(1000,isTTF=True);fb.setupGlyphOrder(['.notdef','space','exclam'])
    fb.setupCharacterMap({32:'space',33:'exclam'});glyphs={}
    for name in ('.notdef','space','exclam'):
        pen=TTGlyphPen(None)
        if name=='exclam':
            if square:
                pen.moveTo((10,0));pen.lineTo((690,0));pen.lineTo((690,680));pen.lineTo((10,680))
            else:
                pen.moveTo((690,340));pen.qCurveTo((690,680),(350,680));pen.qCurveTo((10,680),(10,340));pen.qCurveTo((10,0),(350,0));pen.qCurveTo((690,0),(690,340))
            pen.closePath()
        glyphs[name]=pen.glyph()
    fb.setupGlyf(glyphs);fb.setupHorizontalMetrics({name:(1200,10 if name=='exclam' else 0) for name in glyphs})
    fb.setupHorizontalHeader(ascent=900,descent=-100)
    fb.setupNameTable({'familyName':'ESRIDefaultMarker','styleName':'Regular','fullName':'ESRIDefaultMarker','psName':'ESRIDefaultMarker'})
    fb.setupOS2(sTypoAscender=900,sTypoDescender=-100,usWinAscent=900,usWinDescent=100)
    fb.setupPost();fb.setupMaxp();raw=io.BytesIO();fb.save(raw)
    doc=fitz.open();p=doc.new_page(width=300,height=300);layer=doc.add_ocg('Ovezet_hatar')
    p.insert_font(fontname='markers',fontbuffer=raw.getvalue())
    p.insert_text((100,100),'!!!',fontname='markers',fontsize=10,color=(1,0,0),oc=layer,rotate=rotate)
    return doc


class ZoneEvidenceTests(unittest.TestCase):
    def test_native_circle_centre_and_rotation_come_from_embedded_font(self):
        for rotate,expected in ((0,(103.5,96.6)),(90,(96.6,96.5))):
            with self.subTest(rotate=rotate),marker_pdf(rotate=rotate) as doc:
                trace=doc[0].get_texttrace()[0];font=next(iter(font_registry(doc[0]).values()))[0]
                style={'kind':'marker','glyph_hash':glyph_description(font,trace['chars'][0][1])['glyph_hash'],'colour':_colour(trace['color']),'size':trace['size']}
                result=native_dotted_boundaries(doc[0],[style]);self.assertTrue(result['supported'],result)
                self.assertEqual(result['marker_count'],3)
                self.assertAlmostEqual(result['centres'][0][0],expected[0],places=3)
                self.assertAlmostEqual(result['centres'][0][1],expected[1],places=3)
                self.assertAlmostEqual(result['spacing_points'],12,places=3)

    def test_non_circular_marker_cannot_be_zone_boundary_evidence(self):
        with marker_pdf(square=True) as doc:
            trace=doc[0].get_texttrace()[0];font=next(iter(font_registry(doc[0]).values()))[0]
            style={'kind':'marker','glyph_hash':glyph_description(font,trace['chars'][0][1])['glyph_hash'],
                   'colour':_colour(trace['color']),'size':trace['size']}
            result=native_dotted_boundaries(doc[0],[style]);self.assertFalse(result['supported'])

    def markers(self,extras=()):
        return {'supported':True,'spacing_points':1,'trace_count':1,'marker_count':20,
                'lines':[LineString([(10,0),(10,30)]),*extras]}

    def test_source_road_barrier_prevents_borrowing_label_across_road(self):
        parcel=box(20,10,50,40);labels=[('A',Point(55,25),(54,24,56,26)),('B',Point(70,25),(69,24,71,26))]
        blocked=self.markers([LineString([(60,0),(60,100)])])
        result=bounded_zone_connections(parcel,box(0,0,100,100),blocked,labels,uncertainty_points=.1)
        self.assertTrue(result['verified']);self.assertEqual(result['supported_codes'],['A'])
        self.assertFalse(result['complete_parcel_verified'])

    def test_two_connected_codes_or_masked_boundary_remain_unverified(self):
        parcel=box(20,10,50,40);labels=[('A',Point(55,25),(54,24,56,26)),('B',Point(56,25),(55,24,57,26))]
        result=bounded_zone_connections(parcel,box(0,0,100,100),self.markers(),labels,uncertainty_points=.1)
        self.assertFalse(result['verified'])
        masked=self.markers();masked['gap_envelopes']=[box(51,0,52,100)]
        result=bounded_zone_connections(parcel,box(0,0,100,100),masked,labels[:1],uncertainty_points=.1)
        self.assertFalse(result['verified'])

    def test_source_boundary_inside_parcel_cannot_prove_one_zone(self):
        markers=self.markers([LineString([(25,0),(25,100)])])
        result=bounded_zone_connections(box(20,10,50,40),box(0,0,100,100),markers,
                    [('A',Point(35,15),(34,14,36,16))],uncertainty_points=.1)
        self.assertFalse(result['verified'])

    def legend(self):
        names=('első','második','harmadik','negyedik','ötödik')
        return '\n'.join('Építési övezeti kód '+name+' száma/jele:\nTitle\n1\nvalue-'+str(i)+'-1\n6\nvalue-'+str(i)+'-6\n0\nunknown\nX, Y, Z\nspecial' for i,name in enumerate(names))

    def test_parameters_use_current_legend_values_and_code_positions(self):
        tables=legend_tables(self.legend());rows=decode_zone('Test-61.61.6',tables)
        self.assertEqual([r['Érték'] for r in rows],['value-0-6','value-1-1','value-2-6','value-3-1','value-4-6'])
        changed=self.legend().replace('value-2-6','source-changed')
        self.assertEqual(decode_zone('Other-61.61.6',legend_tables(changed))[2]['Érték'],'source-changed')

    def test_ambiguous_or_missing_source_parameter_never_gets_a_default(self):
        with self.assertRaises(ValueError):legend_tables(self.legend()+'\n'+self.legend())
        with self.assertRaises(ValueError):decode_zone('Test-62.61.6',legend_tables(self.legend()))
        self.assertEqual(decode_zone('Test-61.61.6.X',legend_tables(self.legend())),[])

    def test_unlinked_parameter_legend_never_downloads_or_becomes_applicable(self):
        inputs={'source_valid':True,'edition':'2026.01.01.','attachments':[]}
        with patch.object(app,'http_get') as get:
            result=app.automatic_zone_rule_evidence({'parameter_legend_url':'https://njt.jog.gov.hu/document/legend.pdf'},
                {'url':'official','html':''},inputs,{'candidate_zone':'Test-61.61.6'})
        get.assert_not_called();self.assertTrue(result['errors']);self.assertFalse(result['complete'])
        self.assertEqual(result['parameter_rows'],[])
        self.assertTrue(result['missing_evidence'])

    def test_partial_connection_cannot_promote_zone_or_complete_rules(self):
        result=app.connect_automatic_zone({'geometry':{}},{'candidate_zone':'A',
            'boundary_connection':{'verified':True,'supported_codes':['A'],'complete_parcel_verified':False}},
            {},'00001','034/15')
        self.assertFalse(result['intersection_verified']);self.assertEqual(result['zone'],'')
        self.assertTrue(result['boundary_connection']['verified'])

    def test_actual_closed_zone_graph_uses_internal_label_not_nearest_label(self):
        parcel=box(20,20,40,40)
        boundary=box(10,10,50,50).boundary
        markers={'supported':True,'lines':[boundary]}
        labels=[('A',Point(45,45),(44,44,46,46)),('B',Point(19,19),(18,18,20,20))]
        # B is inside the same zone too: contradictory labels cannot certify it.
        result=closed_zone_faces(parcel,box(0,0,100,100),markers,[],labels)
        self.assertEqual(result['faces'],[])
        labels[1]=('B',Point(51,21),(50,20,52,22))
        result=closed_zone_faces(parcel,box(0,0,100,100),markers,[],labels)
        self.assertEqual(result['faces'][0][0],'A')
        self.assertTrue(result['audit']['complete_parcel_coverage'])

    def test_open_corner_mask_and_frame_edge_cannot_close_zone(self):
        parcel=box(20,20,40,40);frame=box(0,0,100,100)
        labels=[('A',Point(45,45),(44,44,46,46))]
        markers={'supported':True,'lines':[LineString([(10,10),(50,10),(50,50),(10,50),(10,10.001)])]}
        self.assertEqual(closed_zone_faces(parcel,frame,markers,[],labels)['faces'],[])
        markers['lines']=[box(10,10,50,50).boundary]
        self.assertEqual(closed_zone_faces(parcel,frame,markers,[],labels,[box(9,30,11,35)])['faces'],[])
        markers['lines']=[LineString([(0,0),(50,0),(50,50),(0,50)])]
        self.assertEqual(closed_zone_faces(parcel,frame,markers,[],labels)['faces'],[])

    def test_source_landuse_edge_separates_label_and_partial_parcel(self):
        markers={'supported':True,'lines':[box(10,10,50,50).boundary]}
        road=box(30,5,35,55);labels=[('A',Point(20,20),(19,19,21,21)),('B',Point(40,20),(39,19,41,21))]
        result=closed_zone_faces(box(20,20,40,40),box(0,0,100,100),markers,[road],labels)
        self.assertEqual({code for code,_ in result['faces']},{'A','B'})
        self.assertFalse(result['audit']['complete_parcel_coverage'])

    def test_mixed_native_and_marker_boundaries_close_only_at_real_junctions(self):
        markers={'supported':True,'lines':[LineString([(10,10),(50,10),(50,50)])]}
        native=[LineString([(50,50),(10,50),(10,10)])]
        labels=[('A',Point(30,30),(29,29,31,31))]
        result=closed_zone_faces(box(20,20,40,40),box(0,0,100,100),markers,[],labels,native_paths=native)
        self.assertTrue(result['audit']['complete_parcel_coverage'])
        self.assertEqual(result['audit']['topology']['open_endpoints'],[])
        native=[LineString([(50,50),(10,50),(10,10.001)])]
        result=closed_zone_faces(box(20,20,40,40),box(0,0,100,100),markers,[],labels,native_paths=native)
        self.assertEqual(result['faces'],[])
        self.assertEqual(result['audit']['endpoint_repairs'],0)

    def test_native_strokes_work_without_circle_font_and_crossings_are_noded(self):
        native=[LineString([(10,10),(50,10),(50,50),(10,50),(10,10)]),
                LineString([(30,5),(30,55)])]
        labels=[('A',Point(20,30),(19,29,21,31)),('B',Point(40,30),(39,29,41,31))]
        result=closed_zone_faces(box(15,20,25,40),box(0,0,100,100),{'supported':False},[],labels,native_paths=native)
        self.assertEqual([code for code,_ in result['faces']],['A'])
        self.assertTrue(result['audit']['complete_parcel_coverage'])

    def test_endpoint_audit_measures_gap_without_connecting_it(self):
        from plan_connections import boundary_topology_audit
        lines=[LineString([(10,10),(50,10),(50,50),(10,50),(10,10.001)])]
        result=boundary_topology_audit(box(9,9,11,11),lines,[],neighbourhood_points=1)
        self.assertEqual(len(result['open_endpoints']),2)
        self.assertFalse(any(row['connection_verified'] for row in result['open_endpoints']))
        # Ends of the same run still have a real gap: identify its opposite end.
        self.assertAlmostEqual(result['open_endpoints'][0]['gap_points'],.001)
        self.assertEqual(result['endpoint_repairs'],0)

    def test_pdf_dash_variants_are_selected_from_own_legend_before_polygonizing(self):
        from geopdf import legend_layer_paths
        from plan_legend import drawing_style
        with fitz.open() as doc:
            page=doc.new_page(width=100,height=100)
            for points,colour,dashes in [([(10,10),(50,10),(50,50)],(.2,.3,.4),'[3 2] 0'),
                                          ([(50,50),(10,50),(10,10)],(.6,.4,.2),None)]:
                drawing=page.new_shape();drawing.draw_polyline(points)
                drawing.finish(color=colour,width=1,dashes=dashes,closePath=False);drawing.commit()
            styles=[drawing_style(d) for d in page.get_drawings()]
            profile={'records':[{'role':'zone_boundary','label_verified':True,'styles':styles}]}
            paths=legend_layer_paths(page,profile,'zone_boundary')
            native=[segment for path in paths for segment in path['segments']]
            result=closed_zone_faces(box(20,20,40,40),box(0,0,100,100),{'supported':False},[],
                [('A',Point(30,30),(29,29,31,31))],native_paths=native)
            self.assertTrue(result['audit']['complete_parcel_coverage'])
            # Another plan's dash sample cannot silently borrow this variant.
            profile['records'][0]['styles']=styles[:1]
            self.assertEqual(len(legend_layer_paths(page,profile,'zone_boundary')),1)

    def test_pdf_explicit_closepath_is_preserved_without_inventing_a_corner(self):
        from geopdf import legend_layer_paths,polygon_faces
        from plan_legend import drawing_style
        with fitz.open() as doc:
            page=doc.new_page(width=100,height=100)
            drawing=page.new_shape();drawing.draw_polyline([(10,10),(50,10),(50,50),(12,50)])
            drawing.finish(color=(.2,.3,.4),width=1,closePath=True);drawing.commit()
            profile={'records':[{'role':'zone_boundary','label_verified':True,
                                'styles':[drawing_style(page.get_drawings()[0])]}]}
            paths=legend_layer_paths(page,profile,'zone_boundary')
            self.assertEqual(len(polygon_faces(paths)),1)
            self.assertTrue(polygon_faces(paths)[0].covers(box(20,20,40,40)))


if __name__=='__main__':unittest.main()
