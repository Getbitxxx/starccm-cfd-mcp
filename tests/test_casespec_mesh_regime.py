"""O9 ratchet for the 2026-10-30 CaseSpec mesh family-gate REGIME (F3).

The standard (`scripts/mesh_acceptance.py`) judges a DECLARED family.  This module's subject is the
regime around it (`scripts/plan_mesh_gate.py`): a CaseSpec must declare quality.mesh_acceptance
unless it is grandfathered by exact bytes against campaigns/uav-2026/case-regime-legacy.json.

Fixtures are the REAL CaseSpecs and the REAL 2026-10-30 declarations/records - a "new CaseSpec" is a
real frozen CaseSpec copied to a tmp directory with one byte mutated, never a hand-made dict.  The
one-byte mutation is the measured failure mode this ratchet exists for: a legacy case edited after
the split must stop being grandfathered, so it cannot quietly skip the family standard.
"""
import copy
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import casespec  # noqa: E402
import mesh_regime  # noqa: E402
import plan_mesh_gate as gate  # noqa: E402

EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
DECLARATIONS = EVIDENCE / 'MESH-FAMILY-DECLARATIONS-20261030a.json'
FAMILY_RECORD = EVIDENCE / 'MESH-FAMILY-GATE-20261030a.json'
FROZEN_SPEC = ROOT / 'cases/uav-prop-apc10x7e/case_spec.json'
SNAPSHOT = ROOT / 'campaigns/uav-2026/case-regime-legacy.json'


def mutate_one_byte(data: bytes) -> bytes:
    """One-byte mutation of a real CaseSpec file that still parses (APC 10x7E -> APC 10x7F)."""
    needle = b'APC 10x7E'
    index = data.find(needle)
    if index < 0:  # pragma: no cover - the frozen fixture carries the product name
        raise AssertionError('positive fixture changed: APC 10x7E not found in the frozen CaseSpec')
    return data[:index] + b'APC 10x7F' + data[index + len(needle):]


def declared_block() -> dict:
    """The real 2026-10-30 acceptance block, copied (not invented) from the archived declarations."""
    declarations = json.loads(DECLARATIONS.read_text(encoding='utf-8'))
    return {
        'family': {
            'id': 'declared-family',
            'rule': 'ALL_PASS',
            'members': [
                {'id': 'production', 'role': 'production',
                 'quality_record': 'mrf-meshb13-20261030a/mrf_volume_mesh_quality.json',
                 'prism_record': 'mrf-meshb13-20261030a/mrf_wall_prism_coverage.json'},
            ],
        },
        'margin': dict(declarations['acceptance']['margin']),
        'localization_required_on_failure': True,
        'distribution_bands': list(declarations['acceptance']['distribution_bands']),
    }


class RegimeFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.tmp = Path(cls._tmp.name)
        original = FROZEN_SPEC.read_bytes()
        cls.new_spec_bytes = mutate_one_byte(original)
        cls.new_spec_path = cls.tmp / 'case_spec.json'
        cls.new_spec_path.write_bytes(cls.new_spec_bytes)
        cls.frozen_spec = json.loads(original)
        cls.new_spec = json.loads(cls.new_spec_bytes)

    def test_the_mutated_copy_is_a_parseable_real_spec_and_not_grandfathered(self):
        """Precondition of every 'new CaseSpec' test below: a real file, one byte changed."""
        self.assertNotEqual(FROZEN_SPEC.read_bytes(), self.new_spec_bytes)
        self.assertEqual(1, sum(a != b for a, b in zip(FROZEN_SPEC.read_bytes(), self.new_spec_bytes)))
        self.assertTrue(gate.is_grandfathered(self.frozen_spec, ROOT, case_spec_path=FROZEN_SPEC))
        self.assertFalse(gate.is_grandfathered(self.new_spec, ROOT, case_spec_path=self.new_spec_path))


class LegacyGrandfathering(RegimeFixtures):
    def test_byte_identical_legacy_spec_is_grandfathered_and_validates(self):
        self.assertIsNone(gate.require_declared_acceptance(
            self.frozen_spec, repo_root_path=ROOT, case_spec_path=FROZEN_SPEC))
        result = casespec.validate_case_spec(self.frozen_spec, require_mesh_acceptance=True)
        self.assertTrue(result.valid, result.errors)

    def test_a_one_byte_change_drops_grandfathering_and_fails_validation(self):
        with self.assertRaises(gate.MeshRegimeError) as raised:
            gate.require_declared_acceptance(self.new_spec, repo_root_path=ROOT,
                                             case_spec_path=self.new_spec_path)
        self.assertEqual('MESH_ACCEPTANCE_MISSING_BLOCK', raised.exception.code)
        # With the real case identity the rule can name the failure precisely: the pinned bytes changed.
        with self.assertRaises(gate.MeshRegimeError) as changed:
            gate.require_declared_acceptance(self.new_spec, 'uav-prop-apc10x7e', ROOT)
        self.assertEqual('LEGACY_SPEC_CHANGED', changed.exception.code)
        self.assertEqual('cases/uav-prop-apc10x7e/case_spec.json',
                         changed.exception.details['case_spec_path'])
        result = casespec.validate_case_spec(self.new_spec, require_mesh_acceptance=True)
        self.assertFalse(result.valid)
        self.assertTrue(any('[MESH_ACCEPTANCE_MISSING_BLOCK]' in error for error in result.errors), result.errors)

    def test_default_validation_behaviour_is_unchanged(self):
        """No existing caller opts in, so the same real spec stays valid and its byte-identical copy too."""
        self.assertTrue(casespec.validate_case_spec(self.frozen_spec).valid)
        self.assertTrue(casespec.validate_case_spec(self.new_spec).valid)
        default = inspect.signature(casespec.validate_case_spec).parameters['require_mesh_acceptance'].default
        self.assertIs(False, default, 'the regime rule must be opt-in for the 100+ existing callers')


class NewCaseSpecMustDeclareTheBlock(RegimeFixtures):
    def test_new_spec_without_the_block_fails(self):
        result = casespec.validate_case_spec(self.new_spec, require_mesh_acceptance=True)
        self.assertFalse(result.valid)
        self.assertTrue(any('[MESH_ACCEPTANCE_MISSING_BLOCK]' in error for error in result.errors), result.errors)

    def test_new_spec_with_the_real_declared_block_passes(self):
        spec = copy.deepcopy(self.new_spec)
        spec['quality'] = dict(spec['quality'], mesh_acceptance=declared_block())
        self.assertTrue(casespec.validate_case_spec(spec).valid)
        result = casespec.validate_case_spec(spec, require_mesh_acceptance=True)
        self.assertTrue(result.valid, result.errors)
        block = gate.require_declared_acceptance(spec, repo_root_path=ROOT, case_spec_path=self.new_spec_path)
        self.assertEqual('declared-family', block['family']['id'])

    def test_incomplete_block_fails_with_the_missing_key_list(self):
        spec = copy.deepcopy(self.new_spec)
        block = declared_block()
        del block['family']
        del block['distribution_bands']
        spec['quality'] = dict(spec['quality'], mesh_acceptance=block)
        result = casespec.validate_case_spec(spec, require_mesh_acceptance=True)
        self.assertFalse(result.valid)
        regime = [error for error in result.errors if 'MESH_ACCEPTANCE_INCOMPLETE' in error]
        self.assertEqual(1, len(regime), result.errors)
        self.assertIn('family', regime[0])
        self.assertIn('distribution_bands', regime[0])
        with self.assertRaises(gate.MeshRegimeError) as raised:
            gate.require_declared_acceptance(spec, repo_root_path=ROOT)
        self.assertEqual(['family', 'distribution_bands'], raised.exception.details['missing'])

    def test_a_block_that_is_not_an_object_is_refused(self):
        spec = copy.deepcopy(self.new_spec)
        spec['quality'] = dict(spec['quality'], mesh_acceptance=[])
        result = casespec.validate_case_spec(spec, require_mesh_acceptance=True)
        self.assertFalse(result.valid)
        self.assertTrue(any('MESH_ACCEPTANCE_INVALID' in error for error in result.errors), result.errors)


class RepoRatchet(RegimeFixtures):
    def test_every_case_spec_is_grandfathered_or_declares_the_block(self):
        """A future CaseSpec cannot skip the standard: it is either byte-pinned or declares the block."""
        paths = mesh_regime.iter_case_specs(ROOT)
        snapshot = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
        pinned = {entry['case_spec_path'] for entry in snapshot['entries']}
        current = {path.relative_to(ROOT).as_posix() for path in paths}
        self.assertEqual(64, len(pinned), 'the split snapshot covers the 64 CaseSpecs that existed on 2026-10-30')
        self.assertTrue(pinned <= current, 'a grandfathered CaseSpec disappeared: %r' % sorted(pinned - current))
        self.assertGreaterEqual(len(paths), 64)
        failures = []
        for path in paths:
            spec = json.loads(path.read_text(encoding='utf-8'))
            relative = path.relative_to(ROOT).as_posix()
            if gate.is_grandfathered(spec, ROOT, case_spec_path=path):
                continue
            try:
                gate.require_declared_acceptance(spec, repo_root_path=ROOT, case_spec_path=path)
            except gate.MeshRegimeError as exc:
                failures.append(f'{relative}: {exc}')
        self.assertEqual([], failures)

    def test_committed_snapshot_still_pins_the_historical_specs_and_new_specs_declare_the_block(self):
        """A post-split case is ALLOWED (it must declare the block); the historical pin is never rewritten."""
        committed = SNAPSHOT.read_bytes()
        document = json.loads(committed)
        self.assertEqual(64, len(document['entries']))
        rebuilt = json.loads(mesh_regime.serialize(
            mesh_regime.build_snapshot(ROOT, document['snapshot_date'])))
        old = {entry['case_spec_path']: entry for entry in document['entries']}
        new = {entry['case_spec_path']: entry for entry in rebuilt['entries']}
        for relative, entry in old.items():
            self.assertIn(relative, new, 'a grandfathered CaseSpec disappeared: %s' % relative)
            self.assertEqual(entry, new[relative], 'a historical pin changed: %s' % relative)
        report = mesh_regime.check_snapshot(ROOT)
        self.assertTrue(report['ok'], report)
        self.assertEqual([], report['changed'])
        self.assertEqual([], report['missing'])
        # every post-split new CaseSpec must carry the family block (grandfathering is impossible for it)
        added = set(report['new'])
        self.assertTrue(added <= set(new) - set(old))
        for relative in sorted(added):
            spec = json.loads((ROOT / relative).read_text(encoding='utf-8'))
            self.assertIsInstance(spec['quality']['mesh_acceptance'], dict, relative)

    def test_entries_are_sorted_and_the_pinned_hashes_are_the_real_file_hashes(self):
        import hashlib
        document = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
        paths = [entry['case_spec_path'] for entry in document['entries']]
        self.assertEqual(sorted(paths), paths)
        for entry in document['entries']:
            data = (ROOT / entry['case_spec_path']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'], entry['case_spec_path'])
            self.assertEqual(gate.canonical_spec_sha256(json.loads(data)), entry['canonical_sha256'])


class SnapshotWriterGuard(RegimeFixtures):
    def _snapshot(self, root, date='2026-10-30'):
        return mesh_regime.write_snapshot(root, date)

    def test_snapshot_refuses_to_re_pin_a_changed_legacy_spec(self):
        root = self.tmp / 'changed-root'
        (root / 'cases/legacy-case').mkdir(parents=True)
        (root / 'cases/legacy-case/case_spec.json').write_bytes(FROZEN_SPEC.read_bytes())
        first = self._snapshot(root)
        self.assertEqual(1, len(first['entries']))
        changed = root / 'cases/legacy-case/case_spec.json'
        changed.write_bytes(mutate_one_byte(FROZEN_SPEC.read_bytes()))
        with self.assertRaises(SystemExit) as raised:
            self._snapshot(root)
        self.assertIn('CHANGED_LEGACY_SPEC cases/legacy-case/case_spec.json', str(raised.exception))
        # --force is the reviewed exception path, not a silent re-pin.
        forced = mesh_regime.write_snapshot(root, '2026-10-30', force=True)
        self.assertNotEqual(first['entries'][0]['sha256'], forced['entries'][0]['sha256'])

    def test_snapshot_refuses_to_launder_a_new_block_less_spec_into_legacy(self):
        root = self.tmp / 'new-root'
        (root / 'cases/legacy-case').mkdir(parents=True)
        (root / 'cases/legacy-case/case_spec.json').write_bytes(FROZEN_SPEC.read_bytes())
        self._snapshot(root)
        (root / 'cases/new-case').mkdir(parents=True)
        (root / 'cases/new-case/case_spec.json').write_bytes(mutate_one_byte(FROZEN_SPEC.read_bytes()))
        with self.assertRaises(SystemExit) as raised:
            self._snapshot(root)
        self.assertIn('NEW_SPEC_WITHOUT_BLOCK cases/new-case/case_spec.json', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
