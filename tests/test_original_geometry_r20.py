"""②R20: read-only VolumeChange localization — the review findings as executable checks.

Every assertion below pins a defect that the ②R20 independent review actually found in the first
submission (B1, B2) or a concern it raised (C1, C2, C4, C5), so the same shape cannot come back
unnoticed. AGENTS.md §2-O9.
"""
import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R20 = ROOT / 'test-evidence/original-geometry/r20'
MACRO = ROOT / 'macros/DiagnoseMrfLowVolumeChange.java'
PIPELINE = ROOT / 'cases/original-geometry/r20/localize-pipeline.json'
MANIFEST = ROOT / 'workflows/core/manifest.json'
MESH_INPUT = ROOT / 'cases/original-geometry/r20/mrf_diagnostic_meshed.sim'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class OriginalGeometryR20(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.macro = MACRO.read_text()
        cls.pipeline = json.loads(PIPELINE.read_text())
        cls.stage = cls.pipeline['stages'][0]

    # ---- B1: the round must not turn the record-escaping gate red ---------------------------------
    def test_error_path_uses_a_control_character_escaper(self):
        # the call site of the error field must pass through an escaper the gate recognises ...
        error_write = self.macro[self.macro.index('"status\\":\\"error'):]
        self.assertIn('escape(failure.toString())', error_write)
        # ... and that escaper must cover every C0 control character, not only CR/LF/TAB
        body = self.macro[self.macro.index('private static String escape(String s)'):]
        self.assertRegex(body, r'\bc\s*<\s*(?:0x20|32)\b')
        self.assertIn('\\u%04x', body)
        compiled = json.loads((R20 / 'DiagnoseMrfLowVolumeChange.java.compile.json').read_text())
        entry = compiled['DiagnoseMrfLowVolumeChange.java']
        self.assertTrue(entry['compiled'])
        self.assertEqual(entry['sha256'], sha(MACRO))

    # ---- B2: the packaged workflow stage that owns this macro must still be satisfiable ------------
    def test_packaged_stage_required_artifacts_are_still_written(self):
        manifest = json.loads(MANIFEST.read_text())
        stage = manifest['stages']['diagnose-mrf-low-volume-change']
        self.assertEqual(stage['macros'], ['DiagnoseMrfLowVolumeChange.java'])
        for required in stage['required']:
            self.assertIn(required, self.macro, 'required artifact %s is no longer written' % required)
        for name in ('mrf_low_volume_change.json', 'mrf_low_volume_change_cells.csv',
                     'mrf_low_volume_change_fallback.csv', 'mrf_low_volume_change.txt'):
            self.assertIn(name, self.macro)
        # the pipeline must verify the legacy name too, so the compatibility claim is machine-checked
        self.assertIn('mrf_low_volume_change.txt', self.stage['required'])

    # ---- C1: an empty or mis-scoped localization must not pass as evidence -------------------------
    def test_numeric_gate_pins_the_failure_direction_and_the_small_set(self):
        specs = self.stage['require_numeric']
        self.assertIn('mrf_low_volume_change.json::min_volume_change_rotating<=0.01', specs)
        self.assertIn('mrf_low_volume_change.json::exact_cells_below_threshold<=32', specs)
        self.assertIn('mrf_low_volume_change.json::exact_cells_below_threshold>=0', specs)
        # the bound in the gate must equal the bound in the code, or the gate is decorative
        self.assertIn('MAX_PER_CELL_LISTING = 32', self.macro)

    # ---- C2: a file that exists but holds no data row is not a success -----------------------------
    def test_header_only_export_is_treated_as_failure(self):
        self.assertIn('no_data_rows', self.macro)
        self.assertIn('export.startsWith("written")', self.macro)
        # "written" may only be claimed together with a measured data-row count
        self.assertRegex(self.macro, r'export = rows > 0 \? "written:" \+ rows \+ "_data_rows"')

    # ---- C4: the read-only claim must be gated, not merely asserted --------------------------------
    def test_read_only_is_gated_on_the_measured_solution_state(self):
        self.assertIn('"solution_initialized":false', ' '.join(self.stage['require_contains']))
        # in the Java source the emitted JSON appears with escaped quotes
        self.assertIn(r'\"read_only\":true,\"mesh_modified\":false', self.macro)
        self.assertNotIn('initializeSolution', self.macro)
        self.assertNotIn('saveState', self.macro)

    # ---- C5: the count evidence must be flushed before anything slow, and the listing bounded ------
    def test_count_evidence_is_written_before_the_optional_listing(self):
        first_write = self.macro.index('writeJson(dir, record(')
        listing = self.macro.index('locate(cells, volumeChange, position')
        self.assertLess(first_write, listing, 'the count JSON must be flushed before the listing')
        self.assertIn('"pending"', self.macro)
        # a partial listing must still be written with its own footer
        self.assertIn('CELL_COUNT\\t', self.macro)
        self.assertIn('LOCALIZED_COUNT\\t', self.macro)
        self.assertIn('cells_found_equals_localized', self.macro)

    # ---- N2/N3: scope and input provenance must be explicit ----------------------------------------
    def test_scopes_and_input_provenance_are_explicit(self):
        self.assertIn(r'\"count_scope\":\"whole_volume_mesh\"', self.macro)
        self.assertIn(r'\"minimum_scope\":\"per_region\"', self.macro)
        self.assertIn('"count_scope":"whole_volume_mesh"', ' '.join(self.stage['require_contains']))
        note = self.pipeline['note']
        self.assertIn('FAILED mesh stage', note)
        self.assertIn('not as a success certificate', note)
        # the registered input really is that failed stage's checkpoint
        self.assertEqual(self.pipeline['input_hashes']['mrf_diagnostic_meshed.sim'], sha(MESH_INPUT))
        self.assertEqual(sha(MESH_INPUT),
                         '32a129a05f9c7acc49a2d9f583b9f2fb5fc58071de11b4d1700f927ed75edb3d')

    def test_no_substring_needle_pins_a_java_scientific_notation_double(self):
        """Review finding B3: a require_contains needle cannot pin a double Java prints in E-notation
        (0.0003 prints as 3.0E-4), so every small numeric pin must go through require_numeric, which
        parses the artefact instead of substring-matching it."""
        import re as _re
        chain = json.loads((ROOT / 'cases/original-geometry/r20/pipeline.json').read_text())
        offenders = []
        for stage in chain['stages']:
            for needle in stage.get('require_contains') or []:
                for value in _re.findall(r':\s*(-?\d+\.\d+)', needle):
                    if abs(float(value)) < 1e-3:
                        offenders.append((stage['name'], needle))
        self.assertEqual([], offenders, 'fixed-notation needles below 1e-3 are unprintable: %s' % offenders)
        # and the values that ARE pinned by substring must exist in the real ②R19b artefact
        r19b = json.loads((ROOT / 'test-evidence/original-geometry/r19/run/original-r19-20261005b/'
                           'mrf_mesh_configured.json').read_text())
        self.assertIn('5.0E-4', (ROOT / 'test-evidence/original-geometry/r19/run/original-r19-20261005b/'
                                 'mrf_mesh_configured.json').read_text())
        self.assertEqual(r19b['prism_layers'], 8)
        self.assertEqual(r19b['rotating_prism_layers'], 5)

    def test_frozen_bytes_and_gate_accept_the_localization_pipeline(self):
        from tests.superseded_pins import is_recorded_change
        freeze = json.loads((R20 / 'frozen-files.json').read_text())
        for name, digest in freeze['files'].items():
            live = sha(ROOT / name)
            if live != digest:
                self.assertTrue(is_recorded_change(name, live, ROOT / 'test-evidence'),
                                'unrecorded drift in the ②R20 freeze: %s' % name)
        self.assertEqual(self.pipeline['macro_hashes']['DiagnoseMrfLowVolumeChange.java'], sha(MACRO))
        # The localization pipeline's CaseSpec pin is the file that ACTUALLY ran; the live
        # cases/original-geometry/r20/case_spec.json was later extended with the ②R20 mesh-repair
        # parameter, so the historical pin is validated against the hash-verified copy the run
        # itself downloaded. The repaired 6-stage chain registers the live file.
        self.assertEqual(self.pipeline['input_hashes']['case_spec.json'],
                         sha(ROOT / 'test-evidence/original-geometry/r20/run/original-r20-20261005a/case_spec.json'))
        self.assertEqual(sha(ROOT / 'test-evidence/original-geometry/r20/run/original-r20-20261005a/case_spec.json'),
                         '545b1255d5b27e01576e3cfd3fa691f709a24c7f990e5caad03d6182c42b6337')
        chain = json.loads((ROOT / 'cases/original-geometry/r20/pipeline.json').read_text())
        self.assertEqual(chain['input_hashes']['case_spec.json'],
                         sha(ROOT / 'cases/original-geometry/r20/case_spec.json'))
        self.assertEqual(chain['input_hashes']['source_input.sim'],
                         '517cfd27aa4cf69519470d301d9088d060ec1cbb85aff7e8b848207d8bd065d8')
        self.assertEqual([s['name'] for s in chain['stages']],
                         ['geometry', 'regions', 'mesh', 'mesh-audit', 'physics', 'solve'])
        self.assertIn('mrf_mesh_configured.json::"local_refinement":{"copies":3',
                      chain['stages'][2]['require_contains'])
        self.assertIn('mrf_mesh_configured.json::prism_total_m>=0.0005',
                      chain['stages'][2]['require_numeric'])
        self.assertIn('mrf_mesh_configured.json::prism_total_m<=0.0005',
                      chain['stages'][2]['require_numeric'])
        # the repair must not touch the prism stack it was wrongly blamed on
        chain_spec = json.loads((ROOT / 'cases/original-geometry/r20/case_spec.json').read_text())
        self.assertEqual(chain_spec['mesh']['prism_total_m'], 0.0005)
        self.assertNotIn('rotating_prism_total_m', chain_spec['mesh'])
        self.assertEqual(chain_spec['mesh']['rotating_prism_layers'], 5)
        self.assertEqual(chain_spec['mesh']['prism_stretch'], 1.2)
        self.assertEqual(chain_spec['mesh_local_refinement']['copies'], 3)
        self.assertLess(chain_spec['mesh_local_refinement']['size_m'],
                        chain_spec['mesh']['blade_min_size_m'])
        self.assertIn('mrf_low_volume_change.json::min_volume_change_rotating>=0.01',
                      chain['stages'][3]['require_numeric'])
        self.assertIn('mrf_low_volume_change.json::exact_cells_below_threshold<=0',
                      chain['stages'][3]['require_numeric'])
        self.assertEqual(sum(st['timeout_seconds'] for st in chain['stages']) + 60, 1800)
        # the ②R19 chain must be untouched by this round
        # The ②R19 pipeline stays pinned to the bytes IT executed (its own run/execution-source
        # snapshot). ②R20 legitimately moves one more live macro for the authorized mesh repair, and
        # the moved set is DERIVED from the two pipelines rather than hardcoded, so a further
        # authorized repair cannot silently make this stale (the ②R19 review caught exactly that).
        self.assertEqual(sha(ROOT / 'cases/original-geometry/r19/pipeline.json'),
                         'fa673a38eac60a0bca05324b1d23f6b6110f55e67147bbd79182e532a66ee267')
        r19 = json.loads((ROOT / 'cases/original-geometry/r19/pipeline.json').read_text())
        # The ②R18 snapshot pin lives in tests/test_original_r18_review.py; here the ②R19 pins are
        # taken as the executed bytes because the restricted harness hash-verified every uploaded
        # macro against them before each run.
        moved = {name for name, digest in r19['macro_hashes'].items()
                 if sha(ROOT / 'macros' / name) != digest}
        self.assertEqual(moved, {'ConfigureMrfMeshOperation.java'})
        self.assertNotRegex(self.macro, r'AUTO_MRF_ROTATING|0\.0100000001')


if __name__ == '__main__':
    unittest.main()
