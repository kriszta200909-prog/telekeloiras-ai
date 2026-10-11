import unittest

import fitz
from shapely.geometry import box

from plan_geometry_audit import audit_plan_geometry


class PlanGeometryAuditTests(unittest.TestCase):
    def profile(self, colour):
        style={'kind':'path','stroke':list(colour),'fill':None,'width':1,'dash':[],'closed':False}
        return {'identity':{'plan_url':'plan','plan_hash':'a'*64,'source_url':'legend','source_hash':'b'*64},
            'records':[{'role':'utility_line','label':label,'label_verified':True,'styles':[style]}
                       for label in ('Nagyfeszültségű villamosenergia vezeték',
                                     'Középfeszültségű villamosenergia kábel')]}

    def test_reused_native_style_does_not_choose_utility_voltage(self):
        with fitz.open() as doc:
            page=doc.new_page(width=100,height=100)
            page.draw_line((10,50),(90,50),color=(.2,.3,.7),width=1)
            result=audit_plan_geometry(page,box(20,20,60,60),self.profile((.2,.3,.7)))
        self.assertEqual(len(result['rows']),1)
        self.assertFalse(result['rows'][0]['Jelentés egyértelmű'])
        self.assertEqual(len(result['rows'][0]['Lehetséges jelmagyarázati feliratok']),2)
        self.assertFalse(result['complete'])
        self.assertEqual(result['legend_sha256'],'b'*64)

    def test_unmatched_colour_remains_unclassified_and_not_absence_proof(self):
        with fitz.open() as doc:
            page=doc.new_page(width=100,height=100)
            page.draw_rect((10,10,70,70),fill=(.8,.4,.2),color=None)
            page.draw_line((10,50),(90,50),color=(.9,.1,.5),width=1)
            result=audit_plan_geometry(page,box(20,20,60,60),self.profile((.2,.3,.7)))
        self.assertEqual(result['rows'],[])
        self.assertEqual(len(result['unmatched_fills']),1)
        self.assertFalse(result['unmatched_fills'][0]['semantic_role_verified'])
        self.assertFalse(result['complete'])

    def test_filled_polygon_bbox_is_not_parcel_intersection(self):
        with fitz.open() as doc:
            page=doc.new_page(width=100,height=100)
            shape=page.new_shape()
            shape.draw_polyline([(10,10),(70,10),(70,15),(15,15),(15,70),(10,70),(10,10)])
            shape.finish(fill=(.8,.4,.2),color=None);shape.commit()
            result=audit_plan_geometry(page,box(20,20,60,60),self.profile((.2,.3,.7)))
        self.assertEqual(result['unmatched_fills'],[])
        self.assertFalse(result['complete'])


if __name__=='__main__':unittest.main()
