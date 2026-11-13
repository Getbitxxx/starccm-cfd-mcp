"""O9 ratchets for the F1 plan externalisation (phase-3 P30): plans are data, the engine is pinned bytes.

How the golden fixtures were captured (BEFORE the engine refactor; scratch extractor deleted afterwards).
The fixtures are provenance-checked by ``test_fixtures_came_from_the_pre_refactor_engine``:

    git show 2a895ba:scripts/run_mrf_chain.py > /tmp/old_engine.py
    python3 - <<'PY'
    import ast, hashlib, json, sys
    sys.path.insert(0, 'scripts')
    import run_mrf_chain as old            # the HEAD bytes of 2a895ba
    canon = lambda v: hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    config_golden  = {p: canon(old.config(p)) for p in old.PLANS}     # SystemExit -> {"error": str}
    # contract: AST-extract the `contract` dict(...) call out of prepare(), compile it as an expression,
    # eval it per plan with {'plan': plan, 'spec': spec}; if plan in old.CONTRACT_OVERRIDES use
    # dict({'plan': plan, 'steps': [(s['name'], s['macro']) ...]}, **override) else the eval result.
    # Both are written with source_engine_sha256 = sha256(old engine bytes).
    PY
    git show 2a895ba:scripts/run_mrf_chain.py | shasum -a 256   # 116c9475... == fixture provenance

The measured defect this ratchet guards: a plan edit used to change scripts/run_mrf_chain.py, whose
sha256 is pinned by every reviewed staging, so adding a plan invalidated "bytes changed after review".
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import plan_registry  # noqa: E402
import run_mrf_chain as chain  # noqa: E402

FIXTURES = ROOT / 'tests/fixtures'
CONFIG_GOLDEN = FIXTURES / 'mrf-plan-config-golden-20261030.json'
CONTRACT_GOLDEN = FIXTURES / 'mrf-plan-contract-golden-20261030.json'
CAMPAIGN = 'uav-prop-apc10x7e'
PLANS_DIR = ROOT / 'campaigns/uav-2026/plans' / CAMPAIGN
ENGINE = ROOT / 'scripts/run_mrf_chain.py'
LIVE_CASE_SPEC = ROOT / 'cases/uav-prop-apc10x7e/case_spec.json'
# Stagings prepared under an older, correctly aligned ternary chain than HEAD's (HEAD's chain is
# shifted after part34 because the part35 branch dict was removed while its guards remained).  Their
# pinned contracts are recorded in PIN-IMPACT.json; the plan data keeps HEAD prepare() identity.
STAGING_DIVERGENCE = {'mrf-almcond-20261029a': 'part35',
                      'mrf-j0601-velocity-20261030a': 'part37',
                      'mrf-te03-variant-20261030a': 'part75'}


def canon(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _new_plan(plan: str, **overrides) -> dict:
    """A structurally valid NEW plan (not in the legacy manifest) for a throwaway registry."""
    data = {
        'plan': plan,
        'run_id': 'mrf-registry-test-%s-20261030a' % plan,
        'job_dir': 'C:\\Users\\chen\\Documents\\CodexCFD\\service_v1\\adhoc\\prop-%s' % plan,
        'macros': ['AuditMrfRotorSolid.java'],
        'inputs': {'case_spec.json': None},
        'steps': [{'name': 'audit-rotor', 'macro': 'AuditMrfRotorSolid.java', 'input': None,
                   'output': 'recovered_rotor_solids.sim', 'env': {},
                   'marker': 'MRF_ROTOR_SOLID_AUDITED', 'records': ['recovered_rotor_solids.json']}],
        'record_gates': [{'record': 'recovered_rotor_solids.json',
                          'fields': [{'field': 'status', 'op': 'eq', 'value': 'ok'}]}],
        'required_downloads': ['run.json'],
        'optional_downloads': [],
        'contract': {'plan': plan, 'steps': [['audit-rotor', 'AuditMrfRotorSolid.java']]},
        'spike_evidence': {'kind': 'spike_record',
                           'path': 'cases/uav-prop-apc10x7e/case_spec.json',
                           'sha256': sha_file(LIVE_CASE_SPEC), 'duration_min': 12.5, 'verdict': 'PASS'},
    }
    data.update(overrides)
    return data


class GoldenConfigIdentity(unittest.TestCase):
    """F1: externalising the plans did not change one byte of the 87 golden configs or contracts."""

    @classmethod
    def setUpClass(cls):
        cls.config_golden = json.loads(CONFIG_GOLDEN.read_text(encoding='utf-8'))
        cls.contract_golden = json.loads(CONTRACT_GOLDEN.read_text(encoding='utf-8'))

    def test_the_golden_lists_all_87_plans(self):
        for fixture in (self.config_golden, self.contract_golden):
            self.assertEqual(87, fixture['plan_count'])
            self.assertEqual(87, len(fixture['plans']))
            self.assertEqual(sorted(chain.PLANS), fixture['plan_ids'])
            self.assertEqual(fixture['plan_ids'], sorted(fixture['plans']))

    def test_fixtures_came_from_the_pre_refactor_engine(self):
        self.assertEqual(self.config_golden['source_engine_sha256'],
                         self.contract_golden['source_engine_sha256'])
        try:
            head_bytes = subprocess.run(['git', 'show', '2a895ba:scripts/run_mrf_chain.py'], cwd=ROOT,
                                        check=True, capture_output=True).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            self.skipTest('git history unavailable: %s' % exc)
        self.assertEqual(self.config_golden['source_engine_sha256'],
                         hashlib.sha256(head_bytes).hexdigest(),
                         'golden fixtures must come from the pre-refactor engine bytes')
        self.assertNotEqual(sha_file(ENGINE), self.config_golden['source_engine_sha256'],
                            'the refactor must actually have changed the engine')

    def test_every_config_is_byte_identical(self):
        mismatched = []
        for plan in sorted(chain.PLANS):
            try:
                actual = canon(chain.config(plan))
            except SystemExit as exc:
                actual = {'error': str(exc)}
            if actual != self.config_golden['plans'][plan]:
                mismatched.append(plan)
        self.assertEqual([], mismatched, 'config() identity lost for %r' % mismatched)

    def test_every_contract_is_byte_identical(self):
        mismatched = [plan for plan in sorted(chain.PLANS)
                      if canon(chain.PLANS[plan]['contract']) != self.contract_golden['plans'][plan]]
        self.assertEqual([], mismatched, 'contract identity lost for %r' % mismatched)

    def test_materialised_contracts_match_the_pinned_stagings(self):
        """Independent of my extraction: every aligned REVIEW-INPUTS pin must equal the plan data."""
        evidence = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
        compared, diverging = 0, {}
        for review_inputs in sorted(evidence.glob('*/staging/REVIEW-INPUTS.json')):
            payload = json.loads(review_inputs.read_text(encoding='utf-8'))
            plan = payload.get('plan')
            if plan not in chain.PLANS:
                continue
            compared += 1
            if payload.get('contract') != chain.PLANS[plan]['contract']:
                diverging[review_inputs.parent.parent.name] = plan
        self.assertGreaterEqual(compared, 70, 'expected the pinned staging corpus')
        self.assertEqual(STAGING_DIVERGENCE, diverging,
                         'staging contract drift outside the three documented PIN-IMPACT divergences')


class RegistryIsDataNotEngineBytes(unittest.TestCase):
    """The engine keeps its reviewed API but holds no plans; the loader holds no plan ids."""

    def test_engine_has_no_plan_literals_builders_or_ternary_chain(self):
        source = ENGINE.read_text(encoding='utf-8')
        self.assertNotIn('PLANS = {', source, 'plans may not live in the engine')
        self.assertNotRegex(source, r'_build_[a-z0-9_]+_plan', 'no deepcopy builders in the engine')
        self.assertNotRegex(source, r"if plan == 'part", 'no per-plan contract ternary in the engine')
        self.assertNotRegex(source, r"PLANS\['part", 'no plan-id keyed assignments')
        self.assertIn("PLANS = plan_registry.load_all(CAMPAIGN)", source)
        self.assertIn('plan_registry.assert_preparable(plan, spec, ROOT)', source)

    def test_loader_has_no_plan_ids(self):
        source = (ROOT / 'scripts/plan_registry.py').read_text(encoding='utf-8')
        self.assertIsNone(re.search(r"'part[0-9]", source), 'plan ids must be data, not loader code')
        self.assertIsNone(re.search(r'uav-prop-apc10x7e', source), 'campaign ids are passed in, not coded')

    def test_campaign_manifest_lists_the_87_legacy_plans(self):
        manifest = json.loads((PLANS_DIR / '_campaign.json').read_text(encoding='utf-8'))
        self.assertEqual(87, len(manifest['legacy_plans']))
        self.assertEqual(sorted(chain.PLANS), sorted(manifest['legacy_plans']))
        self.assertEqual(40, manifest['spike_gate']['max_duration_min'])
        for plan, spec in chain.PLANS.items():
            self.assertIs(True, spec['legacy'], plan)
            self.assertEqual('legacy_pre_split', spec['spike_evidence']['kind'], plan)
            self.assertTrue(spec['spike_evidence']['reason'].strip(), plan)


class AddingAPlanDoesNotTouchTheEngine(unittest.TestCase):
    """The whole point of F1: a new plan is a new JSON file, never an engine edit."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='p30-registry-'))
        self.plans_dir = self.tmp / CAMPAIGN
        self.plans_dir.mkdir()
        shutil.copyfile(PLANS_DIR / '_campaign.json', self.plans_dir / '_campaign.json')
        self.engine_sha_before = sha_file(ENGINE)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, payload):
        (self.plans_dir / name).write_text(json.dumps(payload, indent=1), encoding='utf-8')

    def test_register_a_plan_and_the_engine_bytes_do_not_change(self):
        self._write('part91.json', _new_plan('part91'))
        registry = plan_registry.load_all(CAMPAIGN, plans_dir=self.plans_dir)
        self.assertEqual(['part91'], sorted(registry))
        self.assertIs(False, registry['part91']['legacy'])
        self.assertEqual(self.engine_sha_before, sha_file(ENGINE),
                         'registering a plan must not touch the pinned controller bytes')
        config = chain.config_from(registry['part91'])
        self.assertEqual('part91', config['plan'])
        self.assertEqual(['audit-rotor'], [step['name'] for step in config['steps']])
        self.assertIn('case_spec.json', config['inputs'])
        self.assertEqual(sha_file(LIVE_CASE_SPEC), config['inputs']['case_spec.json'])
        # reloading the real registry is untouched
        self.assertEqual(87, len(chain.PLANS))
        self.assertEqual(self.engine_sha_before, sha_file(ENGINE))

    def test_template_inheritance_deep_merges_and_lets_the_plan_win(self):
        template = _new_plan('part92')
        template.update({'run_id': 'template-run-id', 'optional_downloads': ['template-opt.json'],
                         'inputs': {'case_spec.json': None,
                                    'inherited_template_input.json': 'cases/uav-prop-apc10x7e/case_spec.json'}})
        template.pop('contract')
        self._write('_template.test.json', template)
        child = {'extends': '_template.test.json', 'plan': 'part92', 'run_id': 'child-run-id',
                 'required_downloads': ['run.json', 'child.json'],
                 'inputs': {'case_spec.json': None},
                 'contract': {'plan': 'part92', 'steps': [['audit-rotor', 'AuditMrfRotorSolid.java']]}}
        self._write('part92.json', child)
        registry = plan_registry.load_all(CAMPAIGN, plans_dir=self.plans_dir)
        plan = registry['part92']
        self.assertEqual('child-run-id', plan['run_id'], 'explicit override wins')
        self.assertEqual(['run.json', 'child.json'], plan['required_downloads'], 'lists are replaced')
        self.assertEqual(['template-opt.json'], plan['optional_downloads'], 'unset keys are inherited')
        self.assertIn('inherited_template_input.json', plan['inputs'], 'nested dicts deep merge')
        self.assertEqual(Path('cases/uav-prop-apc10x7e/case_spec.json').resolve(),
                         plan['inputs']['inherited_template_input.json'].resolve())

    def test_a_new_plan_with_a_literal_tolerance_gate_is_refused(self):
        plan = _new_plan('part93')
        plan['record_gates'].append({'record': 'mrf_trial_audit.json', 'fields': [
            {'field': 'ct_ref', 'op': 'eq', 'value': 0.0656},
            {'field': 'cp_ref', 'op': 'eq', 'value': 0.0476},
            {'field': 'ct_tolerance', 'op': 'eq', 'value': 0.005248},
        ]})
        self._write('part93.json', plan)
        with self.assertRaises(plan_registry.PlanRegistryError) as caught:
            plan_registry.load_all(CAMPAIGN, plans_dir=self.plans_dir)
        self.assertIn('literal', str(caught.exception))
        self.assertIn('ct_tolerance', str(caught.exception))

    def test_a_new_plan_reference_gate_without_derivation_is_refused(self):
        plan = _new_plan('part94')
        plan['record_gates'].append({'record': 'mrf_trial_audit.json', 'fields': [
            {'field': 'ct_ref', 'op': 'eq', 'value': 0.0656},
            {'field': 'cp_ref', 'op': 'eq', 'value': 0.0476}]})
        self._write('part94.json', plan)
        with self.assertRaises(plan_registry.PlanRegistryError) as caught:
            plan_registry.load_all(CAMPAIGN, plans_dir=self.plans_dir)
        self.assertIn('gates_derivation', str(caught.exception))

    def test_a_derived_gate_contains_the_exact_ieee_double(self):
        plan = _new_plan('part95')
        plan['record_gates'].append({'record': 'mrf_trial_audit.json', 'fields': [
            {'field': 'ct_ref', 'op': 'eq', 'value': 0.0656},
            {'field': 'cp_ref', 'op': 'eq', 'value': 0.0476}]})
        plan['gates_derivation'] = {'rule': 'signed_tolerance_bands',
                                    'case_spec': 'cases/uav-prop-apc10x7e/case_spec.json',
                                    'records': ['mrf_trial_audit.json'], 'eps': 1e-12}
        self._write('part95.json', plan)
        registry = plan_registry.load_all(CAMPAIGN, plans_dir=self.plans_dir)
        gate = next(g for g in registry['part95']['record_gates'] if g['record'] == 'mrf_trial_audit.json')
        rule = json.loads(LIVE_CASE_SPEC.read_text(encoding='utf-8'))['acceptance']['reference_tolerance']
        for field, floor_key, rel_key, ref in (
                ('ct_tolerance', 'ct_abs_floor', 'ct_rel', 0.0656),
                ('cp_tolerance', 'cp_abs_floor', 'cp_rel', 0.0476)):
            exact = max(rule[floor_key], rule[rel_key] * ref)  # the exact double the Java macro emits
            lo = [f['value'] for f in gate['fields'] if f['field'] == field and f['op'] == 'ge']
            hi = [f['value'] for f in gate['fields'] if f['field'] == field and f['op'] == 'le']
            self.assertEqual(1, len(lo), field)
            self.assertEqual(1, len(hi), field)
            self.assertLessEqual(lo[0], exact, '%s lower %.17g > exact %.17g' % (field, lo[0], exact))
            self.assertGreaterEqual(hi[0], exact, '%s upper %.17g < exact %.17g' % (field, hi[0], exact))
            self.assertLessEqual(hi[0] - lo[0], 1e-11, '%s band too wide' % field)
            # the derived band must follow THIS CaseSpec (12%), not a stale 8% literal
            self.assertNotAlmostEqual(max(rule[floor_key], 0.08 * ref), exact, places=15)
            self.assertFalse(any('eq' == f['op'] for f in gate['fields'] if f['field'] == field),
                             '%s must not use exact float equality' % field)


if __name__ == '__main__':
    unittest.main()
