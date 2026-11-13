"""R17 original-sector role contradiction and real recovery Boolean failure fixtures."""
import collections
import hashlib
import json
import math
from pathlib import Path
import struct
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'test-evidence/original-geometry/r17'


def shells(raw):
    """Independent exact binary-STL topology/volume reduction of the real failure."""
    n = struct.unpack_from('<I', raw, 80)[0]
    if n <= 0 or len(raw) != 84 + 50 * n:
        raise ValueError('invalid binary STL')
    faces = [[struct.unpack_from('<3f', raw, 84 + 50 * i + 12 + 12 * j)
              for j in range(3)] for i in range(n)]
    edges = collections.defaultdict(list)
    parent = list(range(n))
    for i, face in enumerate(faces):
        for j in range(3):
            edges[tuple(sorted((face[j], face[(j + 1) % 3])))].append(i)

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for adjacent in edges.values():
        for i in adjacent[1:]:
            parent[root(i)] = root(adjacent[0])
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[root(i)].append(i)
    result = []
    for group in groups.values():
        points = [p for i in group for p in faces[i]]
        volume = 0
        for i in group:
            (x, y, z), (X, Y, Z), (a, c, d) = faces[i]
            volume += (x * (Y * d - Z * c) - y * (X * d - Z * a)
                       + z * (X * c - Y * a)) / 6
        counts = collections.Counter(tuple(sorted((faces[i][j], faces[i][(j + 1) % 3])))
                                     for i in group for j in range(3))
        result.append({'triangles': len(group), 'volume': volume,
                       'bounds': [min(p[a] for p in points) for a in range(3)]
                                 + [max(p[a] for p in points) for a in range(3)],
                       'boundary': sum(c == 1 for c in counts.values()),
                       'nonmanifold': sum(c > 2 for c in counts.values())})
    return result


def select_material(components, bounds, volume_min, volume_max, artifact_ratio_max):
    selected = [c for c in components if c['boundary'] == c['nonmanifold'] == 0
                and volume_min <= c['volume'] <= volume_max
                and all(c['bounds'][a] >= bounds[a] and c['bounds'][a + 3] <= bounds[a + 3]
                        for a in range(3))]
    if len(selected) != 1:
        raise ValueError('material shell is missing or ambiguous')
    solid = selected[0]
    residue = sum(abs(c['volume']) for c in components if c is not solid)
    if residue / solid['volume'] > artifact_ratio_max:
        raise ValueError('artifact volume budget exceeded')
    return solid


class OriginalGeometryR17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((EVIDENCE / 'geometry-preparation.json').read_text())
        cls.source = (EVIDENCE / 'geometry-fixtures/actual_original_sector.stl').read_bytes()
        cls.failed = (EVIDENCE / 'geometry-fixtures/actual_failed_boolean_recovery.stl').read_bytes()
        cls.components = shells(cls.failed)

    def test_actual_original_fluid_sector_cannot_be_called_blade_by_name(self):
        self.assertEqual(hashlib.sha256(self.source).hexdigest(),
                         '9f6672c5911df8bd9de9f093e42b943f8acaf245fa84d4f77fa7467db95929a3')
        source = shells(self.source)
        self.assertEqual(len(source), 1)
        fraction = source[0]['volume'] / (math.pi * .205**2 * .04 / 3)
        self.assertGreater(fraction, .97)
        self.assertLess(fraction, 1)
        preflight = json.loads((ROOT / 'test-evidence/full-360-geometry-preflight.json').read_text())
        original = next(r for r in preflight['regions'] if r['name'] == 'PROPELLER')
        self.assertEqual(original['regionTypeClass'], 'star.common.FluidRegion')
        # Historical decision is a real counterexample: closed/manifold does not imply solid role.
        old = json.loads((ROOT / 'test-evidence/full-360-geometry-decision.json').read_text())
        self.assertTrue(old['geometry_audit']['parts'][0]['isClosed'])
        self.assertEqual(self.plan['roles']['source_role'], 'rotating_fluid_sector')

    def test_real_failed_boolean_contains_physical_shell_and_nonmanifold_residue(self):
        self.assertEqual(hashlib.sha256(self.failed).hexdigest(),
                         '72f2108672f1ee3db472298be04cda5cb144e39e912790a6e1c8f17e995b0ecb')
        self.assertEqual(len(self.components), 73)
        self.assertGreater(sum(c['nonmanifold'] for c in self.components), 0)
        selected = select_material(self.components, [-.201, -.201, -.016, .201, .201, .016],
                                   .00012, .00015, 1e-4)
        self.assertEqual(selected['triangles'], 31092)
        self.assertEqual(selected['boundary'], 0)
        self.assertEqual(selected['nonmanifold'], 0)
        self.assertAlmostEqual(selected['volume'], .0001369352586398209)
        self.assertLess(selected['bounds'][5], .016)

    def test_selection_rejects_ambiguous_or_large_unowned_material(self):
        expected = [-.201, -.201, -.016, .201, .201, .016]
        selected = select_material(self.components, expected, .00012, .00015, 1e-4)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            select_material(self.components + [dict(selected)], expected, .00012, .00015, 1e-4)
        extra = dict(selected, volume=.00001, bounds=[-.21, -.21, -.02, .21, .21, .02])
        with self.assertRaisesRegex(ValueError, 'budget'):
            select_material(self.components + [extra], expected, .00012, .00015, 1e-4)

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'Local Java compiler unavailable')
    def test_actual_production_java_shell_gate_on_real_failed_boolean(self):
        macro = (ROOT / 'macros/RecoverRotorSolidsFromSectorFluid.java').read_text()
        # Run the exact pure methods in the delivered macro without loading STAR; this is not
        # another implementation of its selection gate.
        methods = macro[macro.index('    static final class Shell {'):macro.index('    private static String sha(')]
        source = ("import java.io.*;import java.nio.*;import java.nio.file.*;import java.util.*;"
                  "public class R17ShellHarness {" + methods +
                  " public static void main(String[]args)throws Exception {"
                  "List<Shell>s=shells(Files.readAllBytes(Paths.get(args[0])));"
                  "double[]bounds={-.201,-.201,-.016,.201,.201,.016};"
                  "Shell chosen=selectShell(s,bounds,.00012,.00015,1e-4);"
                  "System.out.println(chosen.json(true));"
                  "try{List<Shell>a=new ArrayList<>(s);a.add(chosen);selectShell(a,bounds,.00012,.00015,1e-4);"
                  "throw new AssertionError(\"duplicate must fail\");}catch(IllegalStateException expected){}"
                  "try{selectShell(s,bounds,.00012,.00015,1e-6);"
                  "throw new AssertionError(\"artifact budget must fail\");}catch(IllegalStateException expected){}"
                  "} }")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            harness = directory / 'R17ShellHarness.java'
            harness.write_text(source)
            subprocess.run(['javac', str(harness)], check=True, capture_output=True, text=True)
            result = subprocess.run(['java', '-cp', str(directory), 'R17ShellHarness',
                                     str(EVIDENCE / 'geometry-fixtures/actual_failed_boolean_recovery.stl')],
                                    check=True, capture_output=True, text=True)
            actual = json.loads(result.stdout)
        self.assertEqual(actual['triangles'], 31092)
        self.assertEqual(actual['boundary_edges'], 0)
        self.assertEqual(actual['nonmanifold_edges'], 0)
        self.assertAlmostEqual(actual['signed_volume_m3'], .0001369352586398209)

    def test_entry_and_recovery_do_not_depend_on_sample_names_or_old_solution(self):
        full = (ROOT / 'macros/BuildFullRotor360.java').read_text()
        recovery = (ROOT / 'macros/RecoverRotorSolidsFromSectorFluid.java').read_text()
        volumes = (ROOT / 'macros/BuildMrfFluidVolumes.java').read_text()
        for text in (full, recovery, volumes):
            for name in ('"PROPELLER"', '"FLUID"', '"AIR3B_', '"AUTO_FULL_ROTOR_BLADE_'):
                self.assertNotIn(name, text)
        self.assertIn('clearSolution()', full)
        self.assertIn('clearGeneratedMeshes()', full)
        self.assertIn('sourceRegion.getRegionType() instanceof FluidRegion', full)
        self.assertIn('owned.containsAll(source.getPartSurfaces())', full)
        self.assertIn('solid.isClosed() || !solid.isManifold()', recovery)
        self.assertIn('rereadShells.size()!=1', recovery)
        self.assertIn('recoveryName.equals(string(recovery,"output_part"))', volumes)

    def test_actual_report_dependency_failure_remains_failed_and_catalogue_is_retained(self):
        from scripts.run_record_gate import citation_status, NOT_CITABLE_NO_CLAIM
        run = EVIDENCE / 'run/original-r17-20261005a'
        failed_record = run / 'full_rotor_360_build.json'
        actual = json.loads(failed_record.read_text())
        self.assertEqual(actual['status'], 'error')
        self.assertIn('PrimitiveFieldFunction Report: Force Z', actual['error'])
        self.assertIn('ReportMonitor Force Z Monitor', actual['error'])
        self.assertIn('\n', actual['error'])
        self.assertEqual(citation_status(failed_record)['state'], NOT_CITABLE_NO_CLAIM)
        stage = json.loads((run / 'stage-geometry.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(stage['state'], 'failed')
        self.assertTrue(stage['macros'][0]['ran'])
        self.assertEqual(stage['macros'][0]['seconds'], 15.11)
        self.assertEqual([m['status'] for m in stage['macros'][1:]], ['not_run', 'not_run'])
        full = (ROOT / 'macros/BuildFullRotor360.java').read_text()
        self.assertNotIn('getReportManager().removeObjects(', full)
        self.assertIn('legacy_report_catalogue_retained', full)
        self.assertIn('legacy_reports_reused_for_new_solution', full)
        # Removing the unnecessary report deletion must preserve every cold-entry gate.
        for gate in ('sim.getRegionManager().getRegions().isEmpty() &&',
                     'sim.getInterfaceManager().getObjects().isEmpty() &&',
                     'sim.getContinuumManager().getObjects().isEmpty() &&',
                     'sim.isInitialized()', 'getCurrentIteration()==0',
                     'coldMeshEvidenceAllows(afterCells,afterRegions,anyFvRepresentation)'):
            self.assertIn(gate, full)

    def test_actual_disconnected_representation_failure_is_not_a_zero_cell_measurement(self):
        from scripts.run_record_gate import citation_status, NOT_CITABLE_NO_CLAIM
        run = EVIDENCE / 'run-b/original-r17-20261005b'
        failed_record = run / 'full_rotor_360_build.json'
        actual = json.loads(failed_record.read_text())
        self.assertEqual(actual['status'], 'error')
        self.assertIn('DisconnectedException', actual['error'])
        self.assertIn('FvRepresentation_2539', actual['error'])
        self.assertEqual(citation_status(failed_record)['state'], NOT_CITABLE_NO_CLAIM)
        stage = json.loads((run / 'stage-geometry.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(stage['state'], 'failed')
        self.assertTrue(stage['macros'][0]['ran'])
        self.assertEqual(stage['macros'][0]['seconds'], 15.15)
        full = (ROOT / 'macros/BuildFullRotor360.java').read_text()
        after_cleanup = full.split('clearGeneratedMeshes();', 1)[1]
        self.assertNotIn('volumeMesh.getCellCount()', after_cleanup)
        self.assertIn('sim.getRepresentationManager().getObjects()', after_cleanup)
        self.assertNotIn('sim.getRepresentationManager().getObject("Volume Mesh")', after_cleanup)
        self.assertIn('currentVolume=representation', after_cleanup)
        self.assertIn('geometry_only_verified', after_cleanup)
        self.assertIn('if(!geometryOnlyVerified)', after_cleanup)
        self.assertIn('afterCells=(long)((FvRepresentation)currentVolume).getCellCount()', after_cleanup)
        self.assertIn('Long afterCells=null', after_cleanup)

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'Local Java compiler unavailable')
    def test_production_cold_mesh_policy_requires_measured_zero_or_proven_absence(self):
        macro = (ROOT / 'macros/BuildFullRotor360.java').read_text()
        method = macro[macro.index('    static boolean coldMeshEvidenceAllows('):
                       macro.index('    private static double stlVolume(')]
        source = ('public class R17ColdMeshHarness {' + method +
                  ' public static void main(String[]a){'
                  'if(!coldMeshEvidenceAllows(0L,0,true))throw new AssertionError();'
                  'if(coldMeshEvidenceAllows(1L,0,true))throw new AssertionError();'
                  'if(coldMeshEvidenceAllows(-1L,0,true))throw new AssertionError();'
                  'if(coldMeshEvidenceAllows(null,0,true))throw new AssertionError();'
                  'if(coldMeshEvidenceAllows(null,1,false))throw new AssertionError();'
                  'if(!coldMeshEvidenceAllows(null,0,false))throw new AssertionError();'
                  '} }')
        with tempfile.TemporaryDirectory() as temporary:
            harness = Path(temporary) / 'R17ColdMeshHarness.java'
            harness.write_text(source)
            subprocess.run(['javac', str(harness)], check=True, capture_output=True, text=True)
            subprocess.run(['java', '-cp', temporary, 'R17ColdMeshHarness'],
                           check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
