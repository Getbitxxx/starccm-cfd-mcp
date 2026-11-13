"""Focused S1 admission/selector checks; never starts STAR."""
import copy
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'test-evidence/benchmark-re100-s1-20261020/pilot-b'
sys.path.insert(0, str(ROOT / 'scripts'))
import casespec


def load_before():
    loader = importlib.machinery.SourceFileLoader('casespec_re100_before', str(EVIDENCE/'casespec.py.before'))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


class Re100S1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads((ROOT/'cases/benchmark-laminar-sphere-re100/case_spec.json').read_text())
        cls.before = load_before()
        cls.code = (ROOT/'macros/ConfigureExternalFlowPhysics.java').read_text()
        cls.old_code = (EVIDENCE/'ConfigureExternalFlowPhysics.java.before').read_text()
        cls.java = tempfile.TemporaryDirectory(prefix='selector-', dir=EVIDENCE)
        text = cls.code[cls.code.index('    private static String physicsBranch('):cls.code.index('    private static void configureConstantLaminar(')]
        optional = cls.code[cls.code.index('    private static String optional('):cls.code.index('    private static double number(')]
        source = ('import java.util.regex.*; public class PhysicsSelectorProbe {\n' + text + optional
                  + 'public static void main(String[] args) { System.out.println(physicsBranch(args[0])); }}')
        p = Path(cls.java.name)/'PhysicsSelectorProbe.java'; p.write_text(source)
        subprocess.run(['/usr/bin/javac', str(p)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.java.cleanup()

    def validate(self, spec):
        return casespec.validate_case_spec(spec, set(casespec.required_operations('external_static')))

    def select(self, spec):
        return subprocess.run(['/usr/bin/java', '-cp', self.java.name, 'PhysicsSelectorProbe', json.dumps(spec)],
                              capture_output=True, text=True)

    def test_positive_casespec_and_real_java_selector(self):
        result = self.validate(self.spec)
        self.assertTrue(result.valid, result.as_dict())
        self.assertNotIn('mesh_study', result.required_operations)
        self.assertNotIn('domain_study', result.required_operations)
        self.assertEqual(casespec.physics_branch(self.spec['numerics']), 'constant_laminar')
        selected = self.select(self.spec)
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(selected.stdout.strip(), 'constant_laminar')

    def test_missing_and_nonpositive_material_values(self):
        for key in ('rho_kg_m3', 'mu_pa_s'):
            for value in (None, 0, -1, float('nan'), True):
                with self.subTest(key=key, value=value):
                    spec = copy.deepcopy(self.spec)
                    if value is None: del spec['flow'][key]
                    else: spec['flow'][key] = value
                    self.assertFalse(self.validate(spec).valid)

    def test_wrong_re_area_energy_and_unknown_models(self):
        for section, key, value in [('flow','velocity_m_s',[.02,0,0]),
                                     ('geometry','reference_area_m2',.01),
                                     ('numerics','energy_equation',True),
                                     ('numerics','density_model','unknown'),
                                     ('numerics','turbulence_model','unknown')]:
            with self.subTest(key=key):
                spec = copy.deepcopy(self.spec); spec[section][key] = value
                self.assertFalse(self.validate(spec).valid)

    def test_selector_missing_unknown_and_energy_rejected(self):
        fixtures = [{}, {'numerics':{}}, {'numerics':{'density_model':'constant'}},
                    {'numerics':{'density_model':'unknown','turbulence_model':'laminar','energy_equation':False}},
                    {'numerics':{'density_model':'constant','turbulence_model':'unknown','energy_equation':False}},
                    {'numerics':{'density_model':'constant','turbulence_model':'laminar','energy_equation':True}}]
        for spec in fixtures:
            with self.subTest(spec=spec): self.assertNotEqual(self.select(spec).returncode, 0)

    def test_s1_is_explicit_and_not_a_product_default(self):
        spec = copy.deepcopy(self.spec); del spec['validation_scope']
        result = self.validate(spec)
        self.assertFalse(result.valid)
        self.assertIn('engineering verification requires at least three mesh levels', result.errors)
        self.assertIn('engineering verification requires domain sensitivity', result.errors)
        self.assertIn('mesh_study', result.required_operations)
        spec = copy.deepcopy(self.spec); spec['medium'] = 'air'
        spec['numerics'] = {'density_model':'ideal_gas','turbulence_model':'SST (Menter) k-omega','energy_equation':True}
        self.assertFalse(self.validate(spec).valid)

    def test_air_sst_validation_and_errors_unchanged(self):
        original = json.loads((ROOT/'cases/static-sphere-external-flow/case_spec.json').read_text())
        for edit in (None, 'temperature_k', 'pressure_pa'):
            spec = copy.deepcopy(original)
            if edit: del spec['flow'][edit]
            installed = set(casespec.required_operations('external_static'))
            self.assertEqual(casespec.validate_case_spec(spec, installed).as_dict(),
                             self.before.validate_case_spec(spec, installed).as_dict())
        selected = self.select(original)
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(selected.stdout.strip(), 'ideal_gas_sst')

    def test_gas_execution_body_bytes_and_enable_disable_unchanged(self):
        old = self.old_code[self.old_code.index('            String regionName ='):self.old_code.index('    private static void set(')]
        new = self.code[self.code.index('            String regionName ='):self.code.index('    private static String physicsBranch(')]
        self.assertEqual(new, old)
        self.assertEqual(re.findall(r'continuum\.(?:enable|disable)\([^;]+;', new),
                         re.findall(r'continuum\.(?:enable|disable)\([^;]+;', old))

    def test_liquid_enable_order_has_no_energy_or_turbulence(self):
        liquid = self.code[self.code.index('    private static void configureConstantLaminar('):self.code.index('    private static String stringRequired(')]
        self.assertEqual(re.findall(r'continuum.enable\((\w+)\.class\)', liquid),
                         ['ThreeDimensionalModel','SingleComponentLiquidModel','SegregatedFlowModel',
                          'ConstantDensityModel','SteadyModel','LaminarModel'])
        self.assertNotIn('continuum.disable(', liquid)
        self.assertNotIn('TurbulenceIntensityProfile', liquid)
        self.assertNotIn('StaticTemperatureProfile', liquid)


if __name__ == '__main__': unittest.main(verbosity=2)
