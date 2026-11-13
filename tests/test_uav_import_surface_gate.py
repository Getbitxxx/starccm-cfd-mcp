"""Pin the import mapping gate: the patch map must cover every STEP face.

The real E387 import produced 1 PartSurface. The SolidModelManager native count is a cross-check
whose availability is not proven for PartImportManager.importCadParts; when it is unavailable, a
CadPart import is accepted only when the patch map covers all STEP faces (PATCH_MAP_ONLY_CADPART).
A non-CadPart without native count, or a patch-count shortfall, is rejected.
"""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ImportMappingGate(unittest.TestCase):
    def _run_helper(self, mode: int) -> subprocess.CompletedProcess:
        source = (ROOT / 'macros/ImportStepToSim.java').read_text()
        start = source.index('    static String requireImportMapping')
        end = source.index('    static String sha', start)
        helper = source[start:end]
        harness = r'''
public class ImportGateTest {
''' + helper + r'''
 public static void main(String[] args) {
  int mode=Integer.parseInt(args[0]);
  try {
   if(mode==0) requireImportMapping("star.meshing.CadPart",false,0,0,4,1,4);
   else if(mode==1) requireImportMapping("star.meshing.CadPart",true,1,4,4,1,4);
   else if(mode==2) requireImportMapping("star.meshing.MeshPart",false,0,0,4,1,4);
   else if(mode==3) requireImportMapping("star.meshing.CadPart",true,1,5,4,1,4);
   else if(mode==4) requireImportMapping("star.meshing.CadPart",false,0,0,4,1,3);
   else if(mode==5) requireImportMapping("star.meshing.CadPart",false,0,0,4,0,4);
   else requireImportMapping("star.meshing.CadPart",true,2,4,4,1,4);
   if(mode>1) throw new AssertionError("invalid mapping accepted");
  } catch (IllegalStateException expectedFailure) {
   if(mode<=1) throw expectedFailure;
  }
 }
}
'''
        with tempfile.TemporaryDirectory(prefix='e387-import-mapping-') as folder:
            path = Path(folder) / 'ImportGateTest.java'
            path.write_text(harness)
            r = subprocess.run(['javac', str(path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return subprocess.run(['java', '-cp', folder, 'ImportGateTest', str(mode)],
                                  capture_output=True, text=True)

    def test_cadpart_patch_map_and_native_available_with_one_body_are_accepted(self):
        for mode in (0, 1):
            result = self._run_helper(mode)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_non_cadpart_native_shortfall_patch_shortfall_no_partsurface_two_bodies_are_rejected(self):
        for mode in (2, 3, 4, 5, 6):
            result = self._run_helper(mode)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_one_group_failure_is_kept_as_the_motivating_fixture(self):
        fixture = json.loads((ROOT / 'campaigns/uav-2026/evidence/uav-airfoil-e387/p1.3-fix-20261024/fixtures/import-single-surface-error.json').read_text())
        self.assertEqual(fixture['status'], 'error')
        self.assertIn('surfaces=1', fixture['error'])
        self.assertIn('expected_faces=63', fixture['error'])

    def test_source_records_the_mapping_and_native_fields(self):
        source = (ROOT / 'macros/ImportStepToSim.java').read_text()
        self.assertNotIn('getPartSurfaces().size()!=expectedFaces', source)
        self.assertIn('requireImportMapping(partClass,nativeAvailable,nativeBodies,nativeFaces,expectedFaces,partSurfaces,patchCount)', source)
        self.assertIn('part_class', source)
        self.assertIn('native_model_count', source)
        self.assertIn('surface_patch_ids', source)
        self.assertIn('mapping_class', source)
        self.assertIn('diagnostic_sim_sha256', source)
        self.assertIn('gate_failed', source)
        self.assertIn('patchCount!=expectedFaces', source)
        self.assertIn('native_mapping_basis', source)
        self.assertIn('PATCH_MAP_ONLY_CADPART', source)


if __name__ == '__main__':
    unittest.main()
