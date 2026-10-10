import io
import json
import unittest
import zipfile
from pyproj import CRS
import shapefile
from shapely.geometry import box, mapping
import gis_sources as gis


class GISSourceTests(unittest.TestCase):
    def test_only_registered_https_hosts_and_ports(self):
        for url in ('http://inspire.lechnerkozpont.hu/x','https://127.0.0.1/x',
                    'https://inspire.lechnerkozpont.hu.evil.test/x',
                    'https://user@inspire.lechnerkozpont.hu/x',
                    'https://inspire.lechnerkozpont.hu:8000/x'):
            self.assertFalse(gis.public_url(url))
        for url in ('https://inspire.lechnerkozpont.hu:abc/x',
                    'https://inspire.lechnerkozpont.hu:99999/x',
                    'https://[invalid/x'):
            self.assertFalse(gis.public_url(url))
        self.assertTrue(gis.public_url(gis.CATALOG))

    def test_query_replaces_case_insensitive_parameter(self):
        url=gis.query_url('https://inspire.lechnerkozpont.hu/x?SERVICE=WMS&token=a',service='WFS')
        self.assertNotIn('WMS',url);self.assertIn('token=a',url)

    def test_exception_report_is_not_capabilities(self):
        with self.assertRaises(ValueError):gis.capabilities(b'<ExceptionReport/>')

    def test_wms_never_proves_zoning(self):
        result=gis.capabilities(b'<WMS_Capabilities version="1.3.0"><Capability><Layer><Name>ovezet</Name></Layer></Capability></WMS_Capabilities>')
        self.assertEqual(result['kind'],'raster');self.assertFalse(result['zoning_verified'])

    def test_catalog_repeated_pagination_rejected(self):
        body=b'<csw:GetRecordsResponse xmlns:csw="http://www.opengis.net/cat/csw/2.0.2"><csw:SearchResults numberOfRecordsMatched="2" numberOfRecordsReturned="1" nextRecord="1"><csw:Record xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier>x</dc:identifier></csw:Record></csw:SearchResults></csw:GetRecordsResponse>'
        result=gis.discover_catalog(lambda url:(body,{'url':url,'error':''}))
        self.assertFalse(result['complete']);self.assertIn('Ismétlődő',result['error'])

    def feature_collection(self):
        return {'type':'FeatureCollection','crs':{'properties':{'name':'EPSG:23700'}},
                'numberMatched':1,'numberReturned':1,'features':[{'type':'Feature',
                'geometry':mapping(box(0,0,10,10)),'properties':{'local_code':'A/1'}}]}

    def test_complete_vector_requires_schema_crs_and_counts(self):
        data=self.feature_collection()
        parsed=gis.decode_polygon_features(json.dumps(data),code_field='local_code',expected_crs='EPSG:23700')
        self.assertEqual(parsed[0]['code'],'A/1');self.assertNotIn('boundary_verified',parsed[0])
        for changed in (dict(data,numberMatched=2),dict(data,crs={}),
                        dict(data,links=[{'rel':'next'}]),dict(data,numberReturned=0)):
            with self.assertRaises(ValueError):gis.decode_polygon_features(json.dumps(changed),code_field='local_code',expected_crs='EPSG:23700')
        with self.assertRaises(ValueError):gis.decode_polygon_features(json.dumps(data),code_field='guessed',expected_crs='EPSG:23700')

    def test_unverified_vector_does_not_displace_verified_pdf(self):
        pdf={'kind':'official_pdf_legend','current_verified':True,'boundary_verified':True,'source_hash':'abc'}
        vector=dict(pdf,kind='official_vector',current_verified=False)
        self.assertEqual(gis.preferred_source([vector,pdf]),pdf)
        self.assertIsNone(gis.preferred_source([vector]))

    def archive(self, complete=True, null=False, invalid=False):
        shp,shx,dbf=io.BytesIO(),io.BytesIO(),io.BytesIO()
        writer=shapefile.Writer(shp=shp,shx=shx,dbf=dbf,shapeType=shapefile.POLYGON)
        writer.field('id','C');writer.poly([[[0,0],[0,10],[10,10],[10,0],[0,0]]]);writer.record('protected')
        if null:writer.null();writer.record('missing')
        if invalid:
            writer.poly([[[0,0],[10,10],[0,10],[10,0],[0,0]]]);writer.record('crossed')
        writer.close()
        target=io.BytesIO()
        with zipfile.ZipFile(target,'w') as archive:
            for ext,raw in (('.shp',shp.getvalue()),('.shx',shx.getvalue()),('.dbf',dbf.getvalue())):archive.writestr('nested/area'+ext,raw)
            if complete:archive.writestr('nested/area.prj',CRS.from_epsg(23700).to_wkt())
        return target.getvalue()

    def test_shp_intersection_is_snapshot_not_current_legal_proof(self):
        result=gis.shapefile_intersections(self.archive(),mapping(box(2,2,3,3)),'EPSG:23700')
        self.assertEqual(len(result['intersections']),1)
        self.assertFalse(result['current_legal_protection_verified'])
        self.assertFalse(result['complete_restrictions_verified'])
        empty=gis.shapefile_intersections(self.archive(),mapping(box(20,20,21,21)),'EPSG:23700')
        self.assertEqual(empty['intersections'],[]);self.assertTrue(empty['spatial_snapshot_checked'])

    def test_incomplete_shp_does_not_mean_no_restrictions(self):
        with self.assertRaises(ValueError):gis.shapefile_intersections(self.archive(False),mapping(box(2,2,3,3)),'EPSG:23700')

    def test_null_source_geometry_preserves_hits_but_marks_incomplete(self):
        result=gis.shapefile_intersections(self.archive(null=True),mapping(box(2,2,3,3)),'EPSG:23700')
        self.assertEqual(len(result['intersections']),1)
        self.assertEqual(result['missing_geometry'],1)
        self.assertFalse(result['spatial_snapshot_complete'])

    def test_invalid_source_polygon_is_not_repaired_or_used_as_proof(self):
        result=gis.shapefile_intersections(self.archive(invalid=True),mapping(box(2,2,3,3)),'EPSG:23700')
        self.assertEqual(len(result['intersections']),1)
        self.assertEqual(result['invalid_geometry'],1)
        self.assertFalse(result['spatial_snapshot_complete'])

    def test_projection_failure_cannot_prove_absence_of_restrictions(self):
        from unittest.mock import patch
        from pyproj.exceptions import ProjError
        with patch('shapely.ops.transform',side_effect=ProjError('invalid transform')):
            result=gis.shapefile_intersections(self.archive(),mapping(box(2,2,3,3)),'EPSG:23700')
        self.assertEqual(result['invalid_geometry'],1)
        self.assertFalse(result['spatial_snapshot_complete'])
        self.assertFalse(result['complete_restrictions_verified'])

    def test_unavailable_official_page_does_not_prove_missing_parcel(self):
        result=gis.discover_place('Budapest XII. kerület',catalog={'complete':True,'receipts':[],'error':'','records':[]},
            fetch=lambda url:(b'',{'url':url,'status':503,'error':'Service Unavailable'}))
        self.assertEqual(result['entries'][0]['status'],503)
        self.assertFalse(result['zoning_verified'])

    def test_public_reuse_requires_license_and_matching_metadata_identity(self):
        record={'identifier':['dataset-id']}
        def metadata(identifier,legal):
            return ('<root xmlns:gmd="http://www.isotc211.org/2005/gmd"><gmd:fileIdentifier>'+identifier+
                    '</gmd:fileIdentifier><gmd:MD_LegalConstraints>'+legal+'</gmd:MD_LegalConstraints></root>').encode()
        legal='Creative Commons Attribution 4.0 International. No limitations on public access.'
        for identifier,text,expected in [('dataset-id',legal,True),('other',legal,False),('dataset-id','license',False)]:
            result=gis.dataset_license(record,lambda url:(metadata(identifier,text),{'url':url,'error':''}))
            self.assertEqual(result['public_reuse_verified'],expected)
