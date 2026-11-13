"""②R19: envelope-discretisation correction, executable containment proof and record shape.

The round's review findings become tests here, per AGENTS.md §2-O9:
  * the localization was recomputed from the real STL bytes rather than quoted;
  * the containment proof is compiled from the production method body, not re-implemented;
  * the JSON record builder is executed verbatim, because hand-written JSON concatenation is
    exactly where this round introduced (and then caught) a stray quote.
"""
import collections
import hashlib
import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from tests.superseded_pins import is_recorded_change
R17 = ROOT / 'test-evidence/original-geometry/r17'
R18 = ROOT / 'test-evidence/original-geometry/r18'
R19 = ROOT / 'test-evidence/original-geometry/r19'
RUN18 = R18 / 'run/original-r18-20261005a'
MACRO = ROOT / 'macros/RecoverRotorSolidsFromSectorFluid.java'
SOURCE_STL = RUN18 / 'original_sector_fluid.stl'
RAW_STL = RUN18 / 'recovered_rotor_raw.stl'


def load_stl(path):
    raw = Path(path).read_bytes()
    n = struct.unpack_from('<I', raw, 80)[0]
    if n <= 0 or len(raw) != 84 + 50 * n:
        raise ValueError('invalid binary STL')
    return raw, [[struct.unpack_from('<3f', raw, 84 + 50 * i + 12 + 12 * j)
                  for j in range(3)] for i in range(n)]


def shells(tris):
    """Exact float32-bit undirected edge connectivity, signed volume and per-shell topology."""
    def key(p):
        return struct.pack('<3f', *p)
    edges = collections.defaultdict(list)
    verts = []
    for i, v in enumerate(tris):
        k = [key(p) for p in v]
        verts.append(k)
        for j in range(3):
            edges[tuple(sorted((k[j], k[(j + 1) % 3])))].append(i)
    parent = list(range(len(tris)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for adjacent in edges.values():
        for i in adjacent[1:]:
            parent[root(i)] = root(adjacent[0])
    groups = collections.defaultdict(list)
    for i in range(len(tris)):
        groups[root(i)].append(i)
    out = []
    for group in groups.values():
        points = []
        volume = 0.0
        for i in group:
            v = [list(map(float, p)) for p in tris[i]]
            points += v
            (x, y, z), (X, Y, Z), (a, c, d) = v
            volume += (x * (Y * d - Z * c) - y * (X * d - Z * a) + z * (X * c - Y * a)) / 6
        counts = collections.Counter()
        for i in group:
            k = verts[i]
            for j in range(3):
                counts[tuple(sorted((k[j], k[(j + 1) % 3])))] += 1
        radii = [math.hypot(p[0], p[1]) for p in points]
        out.append({'triangles': len(group), 'volume': volume,
                    'boundary_edges': sum(1 for c in counts.values() if c == 1),
                    'nonmanifold_edges': sum(1 for c in counts.values() if c > 2),
                    'min_radius': min(radii), 'max_radius': max(radii),
                    'min_z': min(p[2] for p in points), 'max_z': max(p[2] for p in points),
                    'bounds': [min(p[a] for p in points) for a in range(3)]
                              + [max(p[a] for p in points) for a in range(3)]})
    return out


def polygon(radius, n, phase_deg):
    return [(radius * math.cos(math.radians(phase_deg + 360.0 * k / n)),
             radius * math.sin(math.radians(phase_deg + 360.0 * k / n))) for k in range(n)]


def area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                   - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2


def clip(subject, clipper):
    """Independent Sutherland-Hodgman clip of a convex subject by a convex clipper."""
    def inside(p, a, b):
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= 0

    def cross(p, q, a, b):
        x1, y1 = p
        x2, y2 = q
        x3, y3 = a
        x4, y4 = b
        d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if d == 0:
            return q
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / d
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))

    out = subject
    for i in range(len(clipper)):
        a = clipper[i]
        b = clipper[(i + 1) % len(clipper)]
        inp = out
        out = []
        if not inp:
            break
        for j in range(len(inp)):
            cur = inp[j]
            prv = inp[j - 1]
            if inside(cur, a, b):
                if not inside(prv, a, b):
                    out.append(cross(prv, cur, a, b))
                out.append(cur)
            elif inside(prv, a, b):
                out.append(cross(prv, cur, a, b))
    return out


def extract(source, first, last):
    start = source.index(first)
    return source[start:source.index(last, start)]


class OriginalGeometryR19(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.macro = MACRO.read_text()
        cls.spec = json.loads((ROOT / 'cases/original-geometry/r19/case_spec.json').read_text())
        cls.base = json.loads((ROOT / 'cases/original-geometry/r17/case_spec.json').read_text())

    # ---- localization: recompute, do not quote ------------------------------------------------
    def test_r18_residue_is_the_envelope_minus_fluid_polygon_difference(self):
        self.assertEqual(hashlib.sha256(RAW_STL.read_bytes()).hexdigest(),
                         'a6e17f3f34b23ce1993953bf8a022aba259938348685178b251ecfda6fb22759')
        self.assertEqual(hashlib.sha256(SOURCE_STL.read_bytes()).hexdigest(),
                         '9f6672c5911df8bd9de9f093e42b943f8acaf245fa84d4f77fa7467db95929a3')
        _, source = load_stl(SOURCE_STL)
        source_points = {p for v in source for p in v}
        outer = sorted({round(math.degrees(math.atan2(p[1], p[0])), 6) for p in source_points
                        if abs(math.hypot(p[0], p[1]) - 0.205) < 1e-6})
        self.assertEqual(len(outer), 32)               # 31 facets per 120 degrees
        self.assertAlmostEqual(outer[1] - outer[0], 120.0 / 31, places=5)
        _, raw = load_stl(RAW_STL)
        components = shells(raw)
        self.assertEqual(len(components), 74)
        material = [c for c in components if c['triangles'] == 8712]
        self.assertEqual(len(material), 1)
        material = material[0]
        self.assertEqual(material['boundary_edges'], 0)
        self.assertEqual(material['nonmanifold_edges'], 0)
        self.assertAlmostEqual(material['volume'], 1.3665646368124016e-04, places=17)
        residue_volume = sum(abs(c['volume']) for c in components if c is not material)
        self.assertAlmostEqual(residue_volume, 6.420347208404547e-07, places=19)
        # The 78-gon envelope against the 93-gon sector outer boundary, both inscribed in 0.205.
        predicted = (area(polygon(0.205, 78, 0.0))
                     - area(clip(polygon(0.205, 78, 0.0), polygon(0.205, 93, -60.0)))) * 0.04
        self.assertLess(abs(predicted - residue_volume) / residue_volume, 1e-5)
        # The end-cap question: the fragments span the FULL envelope height and every one of them
        # touches the envelope wall. Their volume is the lateral polygon mismatch; the low-radius
        # vertices some of them also carry are the zero-volume coplanar cap/periodic-plane sheets.
        residue = [c for c in components if c is not material]
        self.assertTrue(all(c['min_z'] < -0.0199999 and c['max_z'] > 0.0199999 for c in residue))
        self.assertTrue(all(c['max_radius'] > 0.2048 for c in residue))
        self.assertEqual(sum(1 for c in residue if c['min_radius'] > 0.2049), 12)
        wall_angles = {round(math.degrees(math.atan2(q[1], q[0])) % 360, 3)
                       for v in raw for q in v
                       if abs(math.hypot(q[0], q[1]) - 0.205) < 1e-7}
        lattice = [k * 360.0 / 78 for k in range(78)]
        self.assertEqual(len(wall_angles), 75)
        for angle in wall_angles:
            self.assertLess(min(abs(angle - g) for g in lattice), 5e-3, angle)
        for absent in (60.0, 180.0, 300.0):
            # those three ARE lattice angles; they are exactly where a fluid periodic plane sits,
            # so the two polygons share a vertex there and no sliver can exist
            self.assertLess(min(abs(absent - g) for g in lattice), 1e-9)
            self.assertFalse(any(abs(absent - a) < 5e-3 for a in wall_angles))

    def test_four_nonmanifold_edges_are_on_the_top_cap_plane(self):
        _, raw = load_stl(RAW_STL)
        counts = collections.defaultdict(int)
        for v in raw:
            for j in range(3):
                counts[tuple(sorted((struct.pack('<3f', *v[j]),
                                     struct.pack('<3f', *v[(j + 1) % 3]))))] += 1
        bad = [e for e, c in counts.items() if c > 2]
        self.assertEqual(len(bad), 4)
        endpoints = []
        for edge in bad:
            a, b = (struct.unpack('<3f', end) for end in edge)
            self.assertEqual(counts[edge], 4)
            self.assertAlmostEqual(a[2], 0.02, places=7)
            self.assertAlmostEqual(b[2], 0.02, places=7)
            # every endpoint lies on the azimuth-180 deg periodic-plane trace of the sector caps
            for p in (a, b):
                angle = math.degrees(math.atan2(p[1], p[0])) % 360
                self.assertLess(abs(180.0 - angle), 8.0)
                self.assertGreater(math.hypot(p[0], p[1]), 0.06)
                self.assertLess(math.hypot(p[0], p[1]), 0.08)
                endpoints.append(round(angle, 6))
        # the exact 180.000000 deg fluid-grid vertex is one of them, and the remaining vertices
        # sit on the sector's 31-facet angular grid (3.8709677j - 1.9354839) or are intersections
        # of that grid with the envelope polygon (172.258, 176.129, 176.489, 180.000)
        self.assertIn(180.0, {round(v, 6) for v in endpoints})
        grid = [3.870967741935484 * j - 1.935483870967742 for j in range(-1000, 1000)]
        for angle in {round(x, 6) for x in endpoints}:
            self.assertLess(min(abs(angle - g) for g in grid), 0.5, angle)

    # ---- the fix: containment arithmetic and the CaseSpec delta ---------------------------------
    def test_envelope_is_inside_the_fluid_and_contains_the_material(self):
        spec = self.spec
        radius = spec['recovery_disk_radius_m']
        inside_radius = spec['recovery_envelope_inside_fluid_radius_m']
        self.assertAlmostEqual(inside_radius, 0.205 * math.cos(math.pi / 93), places=9)
        self.assertLess(radius, inside_radius)                      # envelope inside the fluid
        self.assertLess(max(abs(spec['recovery_disk_z_min_m']), abs(spec['recovery_disk_z_max_m'])),
                        spec['recovery_envelope_inside_fluid_z_m'])
        proven = spec['recovery_envelope_provenance']
        self.assertLess(proven['material_max_radius_m'], radius * math.cos(math.pi / 78))
        self.assertLess(proven['material_max_abs_z_m'],
                        min(abs(spec['recovery_disk_z_min_m']), abs(spec['recovery_disk_z_max_m'])))
        # the a-priori material bound really is the one the source inner wall shows
        _, source = load_stl(SOURCE_STL)
        points = {p for v in source for p in v}
        inner = [p for p in points
                 if math.hypot(p[0], p[1]) < 0.2049 and abs(p[2]) < 0.0199]
        self.assertAlmostEqual(max(math.hypot(p[0], p[1]) for p in inner),
                               proven['material_max_radius_m'], places=9)
        self.assertAlmostEqual(max(abs(p[2]) for p in inner), proven['material_max_abs_z_m'], places=9)

    def test_case_spec_changes_only_the_envelope_and_relaxes_no_gate(self):
        def leaves(node, prefix=''):
            flat = {}
            if isinstance(node, dict):
                for k, v in node.items():
                    flat.update(leaves(v, prefix + '/' + k))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    flat.update(leaves(v, prefix + '/%d' % i))
            else:
                flat[prefix] = node
            return flat
        old, new = leaves(self.base), leaves(self.spec)
        changed = {k for k in set(old) | set(new) if old.get(k, '<absent>') != new.get(k, '<absent>')}
        allowed = {
            '/objective', '/resources/round_budget_seconds',
            '/recovery_disk_radius_m', '/recovery_disk_z_min_m', '/recovery_disk_z_max_m',
            '/recovery_envelope_inside_fluid_radius_m', '/recovery_envelope_inside_fluid_z_m',
            '/recovery_envelope_lateral_facets_min',
            '/recovery_envelope_material_max_radius_m',
            '/recovery_envelope_material_max_abs_z_m',
            '/source_role_evidence/3',
        }
        for key in changed:
            if key.startswith('/assumptions/') or key.startswith('/recovery_envelope_provenance/'):
                continue
            self.assertIn(key, allowed)
        # every gate leaf is byte-identical
        for gate in ('/recovered_solid_bounds_m', '/recovered_solid_volume_min_m3',
                     '/recovered_solid_volume_max_m3', '/recovery_artifact_volume_ratio_max',
                     '/quality', '/mesh', '/numerics', '/physics', '/domain', '/rotation'):
            for key in [k for k in set(old) | set(new) if k == gate or k.startswith(gate + '/')]:
                self.assertEqual(old[key], new[key], key)

    # ---- the containment proof, executed from the production body --------------------------------
    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'),
                         'Local Java compiler unavailable')
    def test_production_containment_method_accepts_the_material_and_rejects_a_clipped_shell(self):
        body = extract(self.macro, '    static double[] requireInsideEnvelope(',
                       '    private static int root(')
        source = ('import java.util.*; public class R19Containment {'
                  'static class Shell { List<double[]> vertices=new ArrayList<>(); }'
                  + body +
                  'public static void main(String[]a){'
                  'Shell ok=new Shell(); ok.vertices.add(new double[]{0.200189865,0.0,0.015008343});'
                  'double in=0.203*Math.cos(Math.PI/64.0); double h=0.0175;'
                  'double[] m=requireInsideEnvelope(ok,in,h);'
                  'if(!(m[0]>0.20018 && m[1]>0.01500))throw new AssertionError("measure");'
                  'Shell radial=new Shell(); radial.vertices.add(new double[]{0.2029,0.0,0.0});'
                  'try{requireInsideEnvelope(radial,in,h);throw new AssertionError("radial clip not caught");}'
                  'catch(IllegalStateException expected){}'
                  'Shell axial=new Shell(); axial.vertices.add(new double[]{0.0,0.0,0.0175});'
                  'try{requireInsideEnvelope(axial,in,h);throw new AssertionError("cap clip not caught");}'
                  'catch(IllegalStateException expected){}'
                  'System.out.print("PASS");}}')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'R19Containment.java'
            path.write_text(source)
            subprocess.run(['javac', str(path)], check=True, capture_output=True, text=True)
            result = subprocess.run(['java', '-cp', temporary, 'R19Containment'],
                                    check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout, 'PASS')
        self.assertIn('requireInsideEnvelope(selected,containedRadius,envelopeHalfHeight)', self.macro)
        self.assertIn('if(!(radius<insideRadius))', self.macro)
        self.assertIn('if(!(Math.max(Math.abs(zmin),Math.abs(zmax))<insideZ))', self.macro)
        # independent-review finding C1: the proof's own hypotheses are executable preconditions, so a
        # declared facet floor too coarse to contain the declared material stops the macro outright.
        self.assertIn('if(!(containedRadius>materialRadius))', self.macro)
        self.assertIn('if(!(envelopeHalfHeight>materialZ))', self.macro)
        self.assertLess(self.spec['recovery_disk_radius_m']
                        * math.cos(math.pi / self.spec['recovery_envelope_lateral_facets_min']),
                        self.spec['recovery_disk_radius_m'] * math.cos(math.pi / 78))
        self.assertGreaterEqual(self.spec['recovery_envelope_lateral_facets_min'], 19)
        # the declared bound and the provenance block must agree with the measured source wall
        self.assertAlmostEqual(self.spec['recovery_envelope_material_max_radius_m'],
                               self.spec['recovery_envelope_provenance']['material_max_radius_m'], places=8)
        self.assertAlmostEqual(self.spec['recovery_envelope_material_max_abs_z_m'],
                               self.spec['recovery_envelope_provenance']['material_max_abs_z_m'], places=8)
        _, source = load_stl(SOURCE_STL)
        points = {p for v in source for p in v}
        inner = [p for p in points
                 if math.hypot(p[0], p[1]) < 0.2049 and abs(p[2]) < 0.0199]
        # 2026-10-14（C4）：这两条是几何包络门。用圆整后的点与 spec 里的十进制阈值比较时，双精度
        # 末位差（实测 0.200189865153376 vs 0.20018986515337597，差 1e-17）会随机翻红。给显式容差，
        # 容差量级取阈值本身的 1e-12（远小于任何几何意义，又大于双精度噪声）。
        self.assertLessEqual(max(math.hypot(p[0], p[1]) for p in inner),
                             self.spec['recovery_envelope_material_max_radius_m'] * (1 + 1e-12))
        self.assertLessEqual(max(abs(p[2]) for p in inner),
                             self.spec['recovery_envelope_material_max_abs_z_m'] * (1 + 1e-12))

    # ---- the record builder really emits parseable JSON ------------------------------------------
    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'),
                         'Local Java compiler unavailable')
    def test_json_record_builder_emits_parseable_json(self):
        statement = extract(self.macro, '            String json=',
                            '            Files.writeString(record.toPath(),json,')
        preamble = '''import java.io.*;import java.util.*;
public class R19Json {
 static class Part { boolean isClosed(){return true;} boolean isManifold(){return true;} }
 static class Shell { List<Integer> triangles=new ArrayList<>(); double[] bounds={0,0,0,0,0,0}; double signedVolume=1.0; }
 static String jsonStrings(List<String> v){return "[]";}
 static String escape(String s){return s;}
 static String sha(File f){return "sha";}
 public static void main(String[] a)throws Exception{
  String rawName="r",solidName="s"; List<String> sourceNames=Arrays.asList("x");
  Part raw=new Part();
  double radius=0.203,zmin=-0.0175,zmax=0.0175,insideRadius=0.204883046,insideZ=0.02;
  int facetFloor=64; double containedRadius=radius*Math.cos(Math.PI/facetFloor),envelopeHalfHeight=0.0175;
  Shell selected=new Shell(); double[] contained={0.2,0.015}; List<Shell> shells=Arrays.asList(selected);
  int discardedTriangles=0; double artifactVolume=0.0,artifactRatio=0.0,artifactRatioMax=1e-4;
  File rawStl=new File("a"),solidStl=new File("b"),reread=new File("c");
  List<Shell> rereadShells=Arrays.asList(new Shell()); final String COMPLETE="X";
  List<String> componentRecords=new ArrayList<>();
'''
        source = preamble + statement + '  System.out.print(json);\n }}\n'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'R19Json.java'
            path.write_text(source)
            subprocess.run(['javac', str(path)], check=True, capture_output=True, text=True)
            result = subprocess.run(['java', '-cp', temporary, 'R19Json'],
                                    check=True, capture_output=True, text=True)
        record = json.loads(result.stdout)
        self.assertTrue(record['envelope_containment']['no_envelope_facet_in_result'])
        for key in ('disk_radius_m', 'disk_z_min_m', 'disk_z_max_m', 'inside_fluid_radius_m',
                    'inside_fluid_z_m', 'lateral_facets_min', 'contained_radius_m',
                    'envelope_half_height_m', 'selected_max_radius_m', 'selected_max_abs_z_m'):
            self.assertIn(key, record['envelope_containment'])
        self.assertEqual(record['envelope_containment']['disk_radius_m'], 0.203)
        self.assertEqual(record['readback_surface_volume_m3'], 1.0)

    # ---- the round's own frozen bookkeeping ------------------------------------------------------
    def test_a_test_file_pinned_by_this_freeze_may_drift_only_when_recorded(self):
        """独立审查 F3 的反例：②R19 的冻结集里有 `tests/` 前缀的条目，原实现对它们无条件 `continue`。

        现在它们与其他条目同规则：**漂移必须被 supersession 记录登记**。本条把这条规则变成可执行判据：
        逐项列出该冻结集里 `tests/` 条目的漂移集合，并对每一个漂移断言"某条记录声明过它的当前字节"。
        把那条豁免加回去也不会让本条变绿——它直接查记录，不查测试的分支。
        """
        freeze = json.loads((R19 / 'frozen-files.json').read_text())
        pinned = {name: digest for name, digest in freeze['files'].items()
                  if name.startswith('tests/')}
        self.assertTrue(pinned, '②R19 的冻结集必须包含 tests/ 条目，否则本条判据无对象')
        drifted = {name for name, digest in pinned.items()
                   if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest}
        self.assertTrue(drifted, '至少一个被 pin 的测试文件已经移动，否则本条判据无对象')
        for name in sorted(drifted):
            live = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            self.assertTrue(is_recorded_change(name, live, ROOT / 'test-evidence'),
                            '未被登记的测试文件漂移（原豁免会把它藏起来）：' + name)

    def test_r19_freeze_and_plan_are_consistent(self):
        freeze = json.loads((R19 / 'frozen-files.json').read_text())
        # ②R20 legitimately re-pins the mesh-operation macro for its authorized mesh repair, so the
        # historical freeze is validated with the same derived-exception rule the ②R18 test uses.
        r20 = json.loads((ROOT / 'cases/original-geometry/r20/pipeline.json').read_text())
        repinned = {'macros/' + name for name in r20['macro_hashes']
                    if name in {n.split('/')[-1] for n in freeze['files']}
                    and freeze['files'].get('macros/' + name) != r20['macro_hashes'][name]}
        for name, digest in freeze['files'].items():
            actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            if actual == digest:
                self.assertNotIn(name, repinned)
                continue
            # 2026-10-14（选项 a，用户裁定）：历史证据字节不动；后续轮次对共享执行文件的合法改动
            # 由 supersession 记录声明（含 before/after、原因、授权依据），冻结门读取该记录后放行。
            # 未记录的漂移仍然失败。
            #
            # 2026-10-15（独立审查 F3）：这里原有一条 `if name.startswith('tests/'): continue` 的**无条件豁免**——
            # 测试文件因此可以任意漂移且没有任何反例。已删除；测试文件与其他被 pin 的文件同规则：
            # 要么与 pin 相等，要么被 supersession 记录登记，要么在 repinned 里（后续 pipeline 重钉）。
            if is_recorded_change(name, actual, ROOT / 'test-evidence'):
                continue
            self.assertIn(name, repinned, 'unre-pinned drift in the ②R19 freeze: ' + name)
        pipeline = json.loads((ROOT / 'cases/original-geometry/r19/pipeline.json').read_text())
        for name, digest in pipeline['macro_hashes'].items():
            actual = hashlib.sha256((ROOT / 'macros' / name).read_bytes()).hexdigest()
            if actual != digest:
                if is_recorded_change('macros/' + name, actual, ROOT / 'test-evidence'):
                    continue
                self.assertIn('macros/' + name, repinned, 'unre-pinned ②R19 macro drift: ' + name)
        self.assertEqual(pipeline['input_hashes']['source_input.sim'],
                         '517cfd27aa4cf69519470d301d9088d060ec1cbb85aff7e8b848207d8bd065d8')
        self.assertEqual(pipeline['input_hashes']['case_spec.json'],
                         hashlib.sha256((ROOT / 'cases/original-geometry/r19/case_spec.json').read_bytes()).hexdigest())
        geometry = pipeline['stages'][0]
        self.assertIn('recovered_rotor_solids.json::"no_envelope_facet_in_result":true',
                      geometry['require_contains'])
        self.assertEqual(pipeline['stages'][1]['require_contains'],
                         json.loads((ROOT / 'cases/original-geometry/r18/pipeline.json').read_text())
                         ['stages'][1]['require_contains'])
        plan = json.loads((R19 / 'execution-plan.json').read_text())
        self.assertEqual(plan['geometry_verification_jobs_authorized'], 2)
        self.assertEqual(plan['new_star_seconds_authorized'], 0)
        self.assertAlmostEqual(plan['budget_seconds'], 2846.96, places=2)
        self.assertEqual(plan['other_stage_repairs_remaining']['mesh'], 0)
        self.assertFalse(plan['engineering_criterion_changed'])

    def test_compiled_bytes_match_the_frozen_hash(self):
        record = json.loads((R19 / 'RecoverRotorSolidsFromSectorFluid.java.compile.json').read_text())
        entry = record['RecoverRotorSolidsFromSectorFluid.java']
        self.assertTrue(entry['compiled'])
        self.assertEqual(entry['sha256'],
                         hashlib.sha256(MACRO.read_bytes()).hexdigest())

    # ---- the regions-stage ordering defect the first ②R19 run actually measured -------------------
    def test_measured_motion_specification_ordering_defect_is_fixed_in_the_regions_macro(self):
        """②R19 trial original-r19-20261005a reached the regions stage for the first time ever and
        failed at ConfigureMrfInterfaces because a bare geometry region has no MotionSpecification.
        The failure record is the real fixture; the production macro must no longer read it there,
        while the physics stage must still assign and read the frame back."""
        failure = (R19 / 'run/r19a-mrf-interface-failure.txt').read_text()
        self.assertEqual(hashlib.sha256((R19 / 'run/r19a-mrf-interface-failure.txt').read_bytes()).hexdigest(),
                         '32a89d02d1834158ef06b5101353931e7f92e152b0cf60ce9f5611889d21f4a3')
        self.assertIn('Object not found in TypedObjectManager', failure)
        self.assertIn('star.common.PhysicsValueManager', failure)
        self.assertIn('ConfigureMrfInterfaces.execute(ConfigureMrfInterfaces.java:75)', failure)
        stage = json.loads((R19 / 'run/original-r19-20261005a/stage-regions.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(stage['state'], 'failed')
        self.assertEqual([m['status'] for m in stage['macros']], ['ok', 'ok', 'failed'])
        self.assertEqual(stage['macros'][2]['exit_code'], 1)
        # the geometry stage of the same run passed, with zero residue
        geometry = json.loads((R19 / 'run/original-r19-20261005a/stage-geometry.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(geometry['state'], 'ok')
        self.assertEqual([m['status'] for m in geometry['macros']], ['ok', 'ok', 'ok'])
        recovery = json.loads((R19 / 'run/original-r19-20261005a/recovered_rotor_solids.json').read_text())
        self.assertEqual(recovery['status'], 'ok')
        self.assertEqual(recovery['discarded_shells'], 0)
        self.assertEqual(recovery['discarded_triangles'], 0)
        self.assertAlmostEqual(recovery['artifact_volume_ratio'], 0.0)
        self.assertTrue(recovery['envelope_containment']['no_envelope_facet_in_result'])
        self.assertLess(recovery['envelope_containment']['selected_max_radius_m'],
                        recovery['envelope_containment']['contained_radius_m'])
        self.assertLess(recovery['envelope_containment']['selected_max_abs_z_m'],
                        recovery['envelope_containment']['envelope_half_height_m'])
        # the correction: regions macro no longer touches a region's MotionSpecification ...
        interfaces = (ROOT / 'macros/ConfigureMrfInterfaces.java').read_text()
        self.assertNotIn('getValues().get(MotionSpecification.class)', interfaces)
        self.assertIn('rotation_specification_assignment_deferred_to', interfaces)
        self.assertIn('createReferenceFrame(UserRotatingReferenceFrame.class, frameName)', interfaces)
        self.assertIn('getRotationRate().getSIValue()', interfaces)
        # ... while the physics stage still assigns it and reads the assignment back
        physics = (ROOT / 'macros/ConfigureAir3bMrfPhysics.java').read_text()
        self.assertIn('rotating.getValues().get(MotionSpecification.class).setReferenceFrame(frame)', physics)
        self.assertIn('getValues().get(MotionSpecification.class).getReferenceFrame()', physics)
        # no other macro in the pipeline reads a region MotionSpecification before physics
        for name in ('AuditMrfSurfaceMap.java', 'CreateMrfRegions.java', 'ConfigureMrfMeshOperation.java',
                     'ExecuteMrfDiagnosticMesh.java', 'AuditMrfVolumeMeshQuality.java'):
            self.assertNotIn('MotionSpecification', (ROOT / 'macros' / name).read_text(), name)

    def test_regions_repair_is_charged_to_the_carried_quota(self):
        plan = json.loads((R19 / 'execution-plan.json').read_text())
        self.assertEqual(plan['other_stage_repairs_remaining']['regions'], 1)
        self.assertEqual(plan['other_stage_repairs_remaining']['mesh'], 0)
        auth = json.loads((R19 / 'authorization.json').read_text())
        self.assertEqual(auth['derived_authorization']['geometry_verification_jobs_max'], 2)
        self.assertEqual(auth['derived_authorization']['new_star_seconds_beyond_carry'], 0)


if __name__ == '__main__':
    unittest.main()
