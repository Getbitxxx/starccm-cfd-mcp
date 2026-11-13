"""Executable P1.3 review counterexamples, using the actual embedded Java kernel."""
import copy
import json
from pathlib import Path
import re
import subprocess
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from casespec import validate_case_spec


class CaseContract(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((ROOT / 'cases/uav-airfoil-e387/case_spec.json').read_text())

    def test_multiple_inlets_outlets_rejected(self):
        for index, role in [(2, 'inlet'), (3, 'outlet')]:
            spec = copy.deepcopy(self.spec)
            spec['boundary_roles']['box_face_roles'][index] = role
            result = validate_case_spec(spec)
            self.assertFalse(result.valid)
            self.assertTrue(any('exactly one' in s for s in result.errors))

    def test_malformed_role_items_are_structured_invalid(self):
        for value in [{}, [], None, 3, True]:
            with self.subTest(value=value):
                spec = copy.deepcopy(self.spec)
                spec['boundary_roles']['box_face_roles'][2] = value
                result = validate_case_spec(spec)
                self.assertFalse(result.valid)
                self.assertTrue(any('box_face_roles' in s for s in result.errors))

    def test_aliases_rejected(self):
        for key in ['region', 'inlet', 'outlet', 'body_wall']:
            spec = copy.deepcopy(self.spec)
            spec['boundary_roles'][key] = 'CUSTOM_NAME'
            self.assertFalse(validate_case_spec(spec).valid)

    def test_two_declared_bodies_rejected(self):
        self.spec['geometry']['body_part_name'] = 'BODY_ROOT'
        self.spec['geometry']['body_parts'] = ['E387_WING', 'EXTRA_BODY']
        self.assertFalse(validate_case_spec(self.spec).valid)

    def test_step_planes_vertices_tail_and_bbox(self):
        step = (ROOT / 'cases/uav-airfoil-e387/geometry/E387_WING.step').read_text()
        self.assertEqual(len(re.findall(r'=PLANE\(', step)), 63)
        self.assertEqual(len(re.findall(r'=ADVANCED_FACE\(', step)), 63)
        points = {int(k): tuple(map(float, v.split(','))) for k, v in
                  re.findall(r"#(\d+)=CARTESIAN_POINT\('[^']*',\(([^)]+)\)\);", step)}
        refs = [int(k) for k in re.findall(r"=VERTEX_POINT\('[^']*',#(\d+)\)", step)]
        self.assertEqual(len(refs), 122)
        vertices = [points[k] for k in refs]
        self.assertEqual(len(set(vertices)), 122)
        bounds = [min(p[a] for p in vertices) * .001 for a in range(3)]
        bounds += [max(p[a] for p in vertices) * .001 for a in range(3)]
        for actual, expected in zip(bounds, self.spec['geometry']['expected_bounds_m']):
            self.assertAlmostEqual(actual, expected, places=14)
        tail = [p for p in vertices if p[0] == 200]
        self.assertEqual(len(tail), 4)
        self.assertEqual(max(p[2] for p in tail) - min(p[2] for p in tail), .5)
        self.assertIn('SI_UNIT(.MILLI.,.METRE.)', step)

    def test_real_step_float32_cannot_meet_signed_volume_table(self):
        step = (ROOT / 'cases/uav-airfoil-e387/geometry/E387_WING.step').read_text()
        entities = {int(i): v for i, v in re.findall(r'#(\d+)=([^;]+);', step)}
        points = {int(k): tuple(map(float, v.split(','))) for k, v in
                  re.findall(r"#(\d+)=CARTESIAN_POINT\('[^']*',\(([^)]+)\)\);", step)}
        loop = next(v for v in entities.values() if v.startswith('EDGE_LOOP('))
        profile = []
        for ref in map(int, re.findall(r'#(\d+)', loop)):
            orientation = re.fullmatch(r"ORIENTED_EDGE\('',\*,\*,#(\d+),\.(T|F)\.\)", entities[ref])
            edge = entities[int(orientation[1])]
            v1, v2 = map(int, re.findall(r'#(\d+)', edge)[:2])
            vertex = entities[v1 if orientation[2] == 'T' else v2]
            profile.append(points[int(re.search(r'#(\d+)', vertex)[1])])
        self.assertEqual(len(profile), 61)
        def area(vertices):
            return abs(sum(a[0]*b[2]-b[0]*a[2] for a, b in zip(vertices, vertices[1:]+vertices[:1]))) / 2
        si = [[x*.001 for x in p] for p in profile]
        f32 = lambda x: struct.unpack('<f', struct.pack('<f', x))[0]
        quantized = [[f32(x) for x in p] for p in si]
        true_volume = (8 - area(si)) * .01
        binary_volume = (8 - area(quantized)) * (2*f32(.005))
        self.assertEqual(round(true_volume, 9), .079977084)
        self.assertNotEqual(round(binary_volume, 9), .079977084)
        self.assertEqual(round(binary_volume, 9), .079977083)


HARNESS = r'''
    static final String COMPLETE="EXTERNAL_DOMAIN_BUILD_COMPLETE";
    static Geo.Data rect(String name,int axis,double fixed,double u0,double u1,double v0,double v1) {
        int u=(axis+1)%3,v=(axis+2)%3;
        double[][] p=new double[4][3];for(double[] a:p)a[axis]=fixed;
        p[0][u]=u0;p[0][v]=v0;p[1][u]=u1;p[1][v]=v0;
        p[2][u]=u1;p[2][v]=v1;p[3][u]=u0;p[3][v]=v1;
        Geo.Data d=new Geo.Data(name);d.add(new Geo.Tri(new double[][]{p[0],p[1],p[2]}));d.add(new Geo.Tri(new double[][]{p[0],p[2],p[3]}));return d;
    }
    static java.util.List<Geo.Data> source() {
        return new java.util.ArrayList<Geo.Data>(java.util.Arrays.asList(
            rect("LE",0,.1,-.005,.005,-.01,.01),rect("TE",0,.2,-.005,.005,-.01,.01),
            rect("lower",2,-.01,.1,.2,-.005,.005),rect("upper",2,.01,.1,.2,-.005,.005),
            rect("cap_min",1,-.005,-.01,.01,.1,.2),rect("cap_max",1,.005,-.01,.01,.1,.2)));
    }
    static java.util.List<Geo.Data> domain() {
        java.util.List<Geo.Data> ds=new java.util.ArrayList<Geo.Data>(source().subList(0,4));
        ds.add(rect("in",0,-1,-.005,.005,-1,1));ds.add(rect("out",0,3,-.005,.005,-1,1));
        ds.add(rect("far_min",2,-1,-1,3,-.005,.005));ds.add(rect("far_max",2,1,-1,3,-.005,.005));
        for(int side=0;side<2;side++) {
            double y=side==0?-.005:.005;
            double[] xs={-1,-.9,-.8,.1},zs={-1,-.9,-.8,1};
            for(int i=0;i<3;i++)for(int j=0;j<3;j++)ds.add(rect("side_"+side+"_"+i+"_"+j,1,y,zs[j],zs[j+1],xs[i],xs[i+1]));
            ds.add(rect("right_"+side,1,y,-1,1,.2,3));
            ds.add(rect("below_"+side,1,y,-1,-.01,.1,.2));
            ds.add(rect("above_"+side,1,y,.01,1,.1,.2));
        }
        return ds;
    }
    static void check(boolean b,String message){if(!b)throw new AssertionError(message);}
    public static void main(String[] args) {
        double[] bounds={-1,-.005,-1,3,.005,1};
        java.util.List<Geo.Data> src=source(),ds=domain();
        String mode=args[0];
        if(mode.equals("complete")) {
            Geo.Partition p=Geo.partition(src,ds,bounds);
            for(int i:p.groups.get(2))check(!ds.get(i).name.equals("TE"),"TE was symmetry");
            for(int i:p.groups.get(3))check(!ds.get(i).name.equals("TE"),"TE was symmetry");
            for(int i:p.groups.get(6))check(i<4,"box was wall");
            check(p.groups.get(2).size()==12&&p.groups.get(3).size()==12,"side patches not exhaustive");
            check(p.removedCaps==2&&p.retainedCaps==0,"cap mode");
        } else if(mode.equals("retained")) {
            ds.addAll(src.subList(4,6));Geo.Partition p=Geo.partition(src,ds,bounds);
            check(p.retainedCaps==2&&p.groups.get(6).size()==6,"retained cap mode");
        } else if(mode.equals("missing")) {
            ds.removeIf(d->d.name.equals("side_0_1_1"));
            try{Geo.partition(src,ds,bounds);throw new AssertionError("missed tiny side patch accepted");}
            catch(IllegalStateException expected){check(expected.getMessage().contains("Incomplete box face"),expected.toString());}
        } else if(mode.equals("unowned")) {
            ds.add(rect("foreign",0,.7,-.001,.001,-.001,.001));
            try{Geo.partition(src,ds,bounds);throw new AssertionError("unowned was body");}catch(IllegalStateException expected){}
        } else if(mode.equals("mixed")) {
            Geo.Data mixed=ds.remove(8);for(Geo.Tri t:src.get(4).tris)mixed.add(t);ds.add(mixed);
            try{Geo.partition(src,ds,bounds);throw new AssertionError("mixed cap/box accepted");}catch(IllegalStateException expected){}
        } else if(mode.equals("single")) {
            requireSingleSolid(1,1);
            for(int[] n:new int[][]{{2,2},{1,2},{2,1}})try{requireSingleSolid(n[0],n[1]);throw new AssertionError("multi-leaf accepted");}catch(IllegalStateException expected){}
        } else if(mode.equals("error_json")) {
            try{writeEvidence(new java.io.File(args[1]),null,null,null,"error","line1\nline2\t\"quoted\"",null,0);}catch(Exception e){throw new RuntimeException(e);}
        } else if(mode.equals("file")) {
            try {Geo.Data data=Geo.read(new java.io.File(args[1]),"box");System.out.println(Math.abs(data.signedVolume));}
            catch(Exception e){throw new RuntimeException(e);}
        } else if(mode.equals("bad_ascii")) {
            String[] bad={"solid x\nfacet normal 0 0 1\nouter loop\nvertex NaN 0 0\nendloop\nendfacet\nendsolid x",
              "solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nendloop\nendfacet\nendsolid x"};
            for(String text:bad)try{Geo.readAscii(text,"bad");throw new AssertionError("malformed ASCII accepted");}catch(IllegalStateException expected){}
        } else if(mode.equals("names")) {
            String[] roles=resolveRoles(null);
            for(int f=0;f<6;f++)System.out.println((f>1?"farfield_"+Geo.FACES[f]:roles[f])+"="+boundaryName(roles[f],f,true));
            try{resolveRoles(new String[]{"inlet","outlet","inlet","symmetry","farfield","farfield"});throw new AssertionError("multiple inlets accepted");}catch(IllegalArgumentException expected){}
        } else throw new AssertionError(mode);
    }
'''


class MeasuredOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='e387-ownership-test-')
        cls.folder = Path(cls.tmp.name)
        region = (ROOT / 'macros/BuildExternalFlowRegion.java').read_text()
        geometry = (ROOT / 'macros/lib/SurfaceOwnershipGeometry.inc').read_text()
        assert geometry in region
        audit = (ROOT / 'macros/AuditStaticBodyGeometry.java').read_text()
        single = audit[audit.index('    static void requireSingleSolid('):audit.index('    private static void readFully(')]
        roles = region[region.index('    static String[] resolveRoles('):region.index('    static void validateAliases(')]
        domain = (ROOT / 'macros/BuildExternalDomain.java').read_text()
        evidence = domain[domain.index('    private static void writeEvidence('):domain.index('    // BEGIN GENERATED SURFACE OWNERSHIP SUPPORT')]
        (cls.folder / 'OwnershipTest.java').write_text('import java.io.*; import java.util.*; import java.nio.charset.*; public class OwnershipTest {\n' + geometry + roles + single + evidence + HARNESS + '}')
        compiled = subprocess.run(['javac', str(cls.folder / 'OwnershipTest.java')], capture_output=True, text=True)
        if compiled.returncode:
            raise AssertionError(compiled.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_mode(self, mode, *args):
        r = subprocess.run(['java', '-cp', str(self.folder), 'OwnershipTest', mode, *map(str, args)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_te_is_wall_and_all_side_faces_covered(self): self.run_mode('complete')
    def test_retained_caps_are_wall(self): self.run_mode('retained')
    def test_missing_unsampled_patch_is_rejected(self): self.run_mode('missing')
    def test_unowned_surface_is_not_wall(self): self.run_mode('unowned')
    def test_mixed_body_box_surface_rejected(self): self.run_mode('mixed')
    def test_two_resolved_leaves_fail_closed(self): self.run_mode('single')
    def test_malformed_ascii_is_rejected(self): self.run_mode('bad_ascii')

    def test_error_evidence_has_no_success_marker_and_is_parseable(self):
        path = self.folder / 'error.json'
        self.run_mode('error_json', path)
        record = json.loads(path.read_text())
        self.assertEqual(record['status'], 'error')
        self.assertIsNone(record['complete_marker'])
        self.assertEqual(record['error'], 'line1\nline2\t"quoted"')

    def test_ascii_geometry_retains_double_span_volume(self):
        bounds = [-1, -.005, -1, 3, .005, 1]
        lines = ['solid precision_box']
        for axis in range(3):
            u, v = (axis+1)%3, (axis+2)%3
            for side in range(2):
                points = [[0., 0., 0.] for _ in range(4)]
                for p in points: p[axis] = bounds[axis+3*side]
                for p, (i, j) in zip(points, [(0, 0), (1, 0), (1, 1), (0, 1)]):
                    p[u], p[v] = bounds[u+3*i], bounds[v+3*j]
                for tri in [(0, 1, 2), (0, 2, 3)]:
                    if side == 0: tri = tri[::-1]
                    lines.extend(['facet normal 0 0 0', 'outer loop'])
                    lines.extend('vertex ' + ' '.join(map(repr, points[i])) for i in tri)
                    lines.extend(['endloop', 'endfacet'])
        lines.append('endsolid precision_box')
        path = self.folder / 'precision.stl'
        path.write_text('\n'.join(lines))
        actual = float(self.run_mode('file', path)[0])
        self.assertAlmostEqual(actual, .08, places=15)

    def test_default_names_match_old_sphere_record(self):
        old = ROOT / 'test-evidence/static-external-flow/round10-e2e/s1-static-sphere-e2e-20261003a/external_flow_region.json'
        self.assertEqual(self.run_mode('names'), json.loads(old.read_text())['roles'])

    def test_all_macros_embed_same_support(self):
        for name in ['BuildExternalFlowRegion', 'BuildExternalDomain', 'AuditBoundaryRoles', 'AuditBoundaryPhysics']:
            source = (ROOT / 'macros' / (name + '.java')).read_text()
            for part in ['SurfaceOwnershipGeometry.inc', 'SurfaceOwnershipSupport.inc']:
                self.assertIn((ROOT / 'macros/lib' / part).read_text(), source)


if __name__ == '__main__':
    unittest.main()
