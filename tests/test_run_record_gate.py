# -*- coding: utf-8 -*-
"""Tests for the single rule that decides whether a record may certify a success.

The counter-example is not invented. It is the real round-6 failed run that exposed the defect:
`starccm+ -batch A,B,C` kept executing the mesh macro after the physics macro threw, so a stage the
runner marked FAILED contains a self-consistent `"status":"ok"` mesh record. Before this file, the
only rule about that situation was prose, and the prose had already failed once - a third review
predicted the unparseable failure record verbatim and it shipped anyway.
"""
import json
from functools import lru_cache
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from run_record_gate import (CITABLE, NOT_CITABLE_FAILED_RUN, NOT_CITABLE_NO_CLAIM,  # noqa: E402
                             UNKNOWN_RUN_STATE, UNPARSEABLE, RecordNotCitable, assert_citable,
                             check_citations, citation_status, known_unparseable, scan_run)

ROUND6 = (ROOT / 'test-evidence' / 'static-external-flow' / 'batch-region-physics-mesh'
          / 's1-static-sphere-region-physics-mesh-20261003a')
# The one known loss of evidence in this repository, ratcheted: it must still be detected, and a
# second one anywhere under test-evidence must fail this suite.
KNOWN_UNPARSEABLE = {str(ROUND6 / 'external_flow_physics.json')}


class Round6FailedRun(unittest.TestCase):
    """R1/R2 against the real failed run, so the rule is held to the case that broke it."""

    def test_the_run_state_is_read_even_though_the_runner_wrote_a_bom(self):
        # The trial runner writes case-probe.json with Windows PowerShell Set-Content -Encoding UTF8,
        # which prefixes a BOM. Reading it as plain utf-8 made the gate blind to the failure - the
        # checker's own fail-open, found by pointing it at this fixture.
        scan = scan_run(ROUND6)
        self.assertEqual('FAILURE', scan['run_kind'])
        self.assertEqual('FAILED', scan['run_state'])
        self.assertIn('exit code 1', scan['run_detail'])

    def test_the_mesh_record_really_does_claim_success(self):
        payload = json.loads((ROUND6 / 'external_flow_mesh.json').read_text(encoding='utf-8-sig'))
        self.assertEqual('ok', payload['status'])
        self.assertTrue(payload['mesh_generated'])
        self.assertEqual(58724, payload['cells'])

    def test_a_success_claim_from_the_failed_run_may_not_be_cited(self):
        verdict = citation_status(ROUND6 / 'external_flow_mesh.json')
        self.assertEqual(NOT_CITABLE_FAILED_RUN, verdict['state'])
        self.assertTrue(verdict['record_claims_ok'])
        self.assertIs(verdict['run_kind'], 'FAILURE')
        with self.assertRaises(RecordNotCitable):
            assert_citable(ROUND6 / 'external_flow_mesh.json')

    def test_every_ok_record_of_the_failed_stage_is_uncitable(self):
        scan = scan_run(ROUND6)
        self.assertEqual({'external_flow_mesh.json', 'external_flow_region.json',
                          'external_flow_region_probe.json'},
                         {entry['name'] for entry in scan['not_citable']})

    def test_the_failure_record_itself_is_not_parseable_and_is_reported(self):
        scan = scan_run(ROUND6)
        self.assertEqual({'external_flow_physics.json'},
                         {entry['name'] for entry in scan['unparseable']})
        kinds = {violation['kind'] for violation in scan['violations']}
        self.assertEqual({UNPARSEABLE, NOT_CITABLE_FAILED_RUN}, kinds)

    def test_check_citations_reports_the_pair(self):
        report = check_citations([ROUND6 / 'external_flow_mesh.json',
                                  ROUND6 / 'external_flow_physics.json'])
        self.assertFalse(report['passed'])
        self.assertEqual({NOT_CITABLE_FAILED_RUN, UNPARSEABLE},
                         {violation['state'] for violation in report['violations']})


class ControlThatTheGateIsNotVacuous(unittest.TestCase):
    """The same records, in a run that succeeded, must be citable - otherwise the rule proves nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for name in ('external_flow_mesh.json', 'external_flow_region.json'):
            shutil.copy2(ROUND6 / name, self.tmp / name)

    def write_state(self, state):
        (self.tmp / 'case-probe.json').write_text(json.dumps({'state': state, 'error': 'x'}),
                                                  encoding='utf-8')

    def test_successful_run_records_are_citable(self):
        self.write_state('AIR3B_PIPELINE_COMPLETE')
        scan = scan_run(self.tmp)
        self.assertEqual('SUCCESS', scan['run_kind'])
        self.assertEqual([], scan['not_citable'])
        self.assertEqual(CITABLE, citation_status(self.tmp / 'external_flow_mesh.json')['state'])

    def test_gate_failed_run_records_are_not_citable(self):
        self.write_state('GATE_FAILED')
        self.assertEqual(NOT_CITABLE_FAILED_RUN,
                         citation_status(self.tmp / 'external_flow_mesh.json')['state'])

    def test_a_record_with_no_run_state_is_never_certified(self):
        (self.tmp / 'case-probe.json').write_text(json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE'}),
                                                  encoding='utf-8')
        self.assertEqual(CITABLE, citation_status(self.tmp / 'external_flow_mesh.json')['state'])
        (self.tmp / 'case-probe.json').unlink()
        verdict = citation_status(self.tmp / 'external_flow_mesh.json')
        self.assertEqual(UNKNOWN_RUN_STATE, verdict['state'])
        self.assertIn('no run-state file', verdict['reason'])
        with self.assertRaises(RecordNotCitable):
            assert_citable(self.tmp / 'external_flow_mesh.json')

    def test_an_unrelated_state_file_three_levels_up_is_not_borrowed(self):
        # MAX_STATE_WALK: test-evidence/air-three-blade/state.json sits three levels above an AIR-3B
        # record; borrowing it would attribute the record to the wrong run.
        deep = self.tmp / 'a' / 'b' / 'c'
        deep.mkdir(parents=True)
        shutil.copy2(ROUND6 / 'external_flow_mesh.json', deep / 'record.json')
        (self.tmp / 'case-probe.json').write_text(json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE'}),
                                                  encoding='utf-8')
        self.assertEqual(UNKNOWN_RUN_STATE, citation_status(deep / 'record.json')['state'])

    def test_a_state_file_two_levels_up_is_not_borrowed(self):
        # MAX_STATE_WALK = 1. A state file two levels up may belong to a different run entirely.
        deep = self.tmp / 'job' / 'stages'
        deep.mkdir(parents=True)
        shutil.copy2(ROUND6 / 'external_flow_mesh.json', deep / 'record.json')
        (self.tmp / 'case-probe.json').write_text(json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE'}),
                                                  encoding='utf-8')
        self.assertEqual(UNKNOWN_RUN_STATE, citation_status(deep / 'record.json')['state'])

    def test_a_state_file_one_level_up_is_used(self):
        nested = self.tmp / 'stage-dir'
        nested.mkdir()
        shutil.copy2(ROUND6 / 'external_flow_mesh.json', nested / 'record.json')
        (self.tmp / 'case-probe.json').write_text(json.dumps({'state': 'FAILED'}), encoding='utf-8')
        self.assertEqual(NOT_CITABLE_FAILED_RUN, citation_status(nested / 'record.json')['state'])

    def test_a_state_file_beside_the_record_is_used(self):
        self.write_state('FAILED')
        self.assertEqual(NOT_CITABLE_FAILED_RUN,
                         citation_status(self.tmp / 'external_flow_region.json')['state'])


class StageRecordPrecedence(unittest.TestCase):
    """Citability is decided at STAGE granularity, which is the granularity of the rule.

    Without this, a failure in one stage of a run would invalidate the records of every other stage in
    the same run - the mirror image of the round-6 defect. The stage record lists the artefacts its
    stage declared, so a record belongs to the stage that names it.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        shutil.copy2(ROUND6 / 'external_flow_mesh.json', self.tmp / 'external_flow_mesh.json')
        shutil.copy2(ROUND6 / 'external_flow_region.json', self.tmp / 'external_flow_region.json')

    def run_state(self, state):
        (self.tmp / 'case-probe.json').write_text(json.dumps({'state': state}), encoding='utf-8')

    def stage_record(self, name, state, artifacts):
        (self.tmp / ('stage-%s.json' % name)).write_text(json.dumps(
            {'schema_version': 1, 'stage': name, 'state': state, 'error': None,
             'artifacts': artifacts, 'macro_outputs': [None]}), encoding='utf-8')

    def test_a_failed_stage_outranks_a_successful_run(self):
        self.run_state('AIR3B_PIPELINE_COMPLETE')
        self.stage_record('region-physics-mesh', 'failed', ['external_flow_mesh.json'])
        verdict = citation_status(self.tmp / 'external_flow_mesh.json')
        self.assertEqual(NOT_CITABLE_FAILED_RUN, verdict['state'])
        self.assertEqual('stage', verdict['decided_by'])

    def test_a_successful_stage_survives_a_failed_run(self):
        self.run_state('FAILED')
        self.stage_record('region-physics-mesh', 'ok', ['external_flow_mesh.json'])
        verdict = citation_status(self.tmp / 'external_flow_mesh.json')
        self.assertEqual(CITABLE, verdict['state'])
        self.assertEqual('stage', verdict['decided_by'])

    def test_a_record_named_by_no_stage_falls_back_to_the_run_state(self):
        self.run_state('FAILED')
        self.stage_record('mesh-quality-audit', 'ok', ['external_flow_mesh_quality.json'])
        verdict = citation_status(self.tmp / 'external_flow_mesh.json')
        self.assertEqual('run', verdict['decided_by'])
        self.assertEqual(NOT_CITABLE_FAILED_RUN, verdict['state'])

    def test_two_stages_naming_one_record_fall_back_to_the_run_state(self):
        # Independent review round 7, second pass: two OK stages can both list one artefact in
        # `required` by accident. Refusing there would make the artefact permanently uncertifiable, so
        # the attribution falls back to the run state and the ambiguity is reported.
        self.run_state('AIR3B_PIPELINE_COMPLETE')
        self.stage_record('first', 'ok', ['external_flow_mesh.json'])
        self.stage_record('second', 'ok', ['external_flow_mesh.json'])
        verdict = citation_status(self.tmp / 'external_flow_mesh.json')
        self.assertEqual('run', verdict['decided_by'])
        self.assertEqual(CITABLE, verdict['state'])
        self.assertIn('several successful stage records', verdict['stage_note'])

    def test_two_stages_naming_one_record_do_not_excuse_a_failed_run(self):
        self.run_state('FAILED')
        self.stage_record('first', 'ok', ['external_flow_mesh.json'])
        self.stage_record('second', 'ok', ['external_flow_mesh.json'])
        self.assertEqual(NOT_CITABLE_FAILED_RUN,
                         citation_status(self.tmp / 'external_flow_mesh.json')['state'])

    def test_one_failing_stage_among_several_naming_the_record_refuses(self):
        # Independent review round 7, third pass, Concern 1: the ambiguity fallback discarded the
        # matching stages' verdicts, so run ok + ONE FAILED stage (or even two failed stages) yielded
        # CITABLE. All four combinations the reviewer demonstrated are pinned here.
        self.run_state('AIR3B_PIPELINE_COMPLETE')
        for first, second in (('ok', 'failed'), ('failed', 'ok'), ('failed', 'failed')):
            with self.subTest(first=first, second=second):
                for name in ('stage-a.json', 'stage-b.json'):
                    (self.tmp / name).unlink(missing_ok=True)
                self.stage_record('a', first, ['external_flow_mesh.json'])
                self.stage_record('b', second, ['external_flow_mesh.json'])
                verdict = citation_status(self.tmp / 'external_flow_mesh.json')
                self.assertEqual(NOT_CITABLE_FAILED_RUN, verdict['state'])
                self.assertIn('at least one did not succeed', verdict['reason'])

    def test_a_nested_record_is_not_attributed_to_the_parent_stage(self):
        # Independent review round 7, second pass: matching by basename across directory levels let a
        # record in audit/ be certified by the OK stage in the parent job directory while
        # audit/stage-audit.json said the stage failed.
        self.run_state('AIR3B_PIPELINE_COMPLETE')
        self.stage_record('region-physics-mesh', 'ok', ['external_flow_mesh.json'])
        nested = self.tmp / 'audit'
        nested.mkdir()
        shutil.copy2(ROUND6 / 'external_flow_mesh.json', nested / 'external_flow_mesh.json')
        (nested / 'stage-audit.json').write_text(json.dumps(
            {'stage': 'audit', 'state': 'failed', 'artifacts': ['external_flow_mesh.json'],
             'macro_outputs': []}), encoding='utf-8')
        verdict = citation_status(nested / 'external_flow_mesh.json')
        self.assertEqual(NOT_CITABLE_FAILED_RUN, verdict['state'])
        self.assertEqual('stage', verdict['decided_by'])
        self.assertEqual('audit', verdict['stage'])

    def test_the_declared_output_of_a_stage_also_attributes_its_records(self):
        self.run_state('FAILED')
        (self.tmp / 'stage-second.json').write_text(json.dumps(
            {'stage': 'second', 'state': 'ok', 'artifacts': [],
             'macro_outputs': ['external_flow_mesh.json']}), encoding='utf-8')
        verdict = citation_status(self.tmp / 'external_flow_mesh.json')
        self.assertEqual(CITABLE, verdict['state'])
        self.assertEqual('stage', verdict['decided_by'])

    def test_a_stage_record_is_not_itself_a_citation(self):
        # A stage record reports what ran; it does not assert the success of a measurement, so citing
        # it as a certificate is refused even when its own state is ok.
        self.run_state('AIR3B_PIPELINE_COMPLETE')
        (self.tmp / 'stage-second.json').write_text(json.dumps(
            {'stage': 'second', 'state': 'ok', 'artifacts': [], 'macro_outputs': [None]}),
            encoding='utf-8')
        self.assertEqual(NOT_CITABLE_NO_CLAIM,
                         citation_status(self.tmp / 'stage-second.json')['state'])

    def test_scan_run_attributes_each_record_separately(self):
        self.run_state('FAILED')
        self.stage_record('region-physics-mesh', 'ok', ['external_flow_mesh.json'])
        scan = scan_run(self.tmp)
        by_name = {entry['name']: entry for entry in scan['records']}
        self.assertTrue(by_name['external_flow_mesh.json']['citable'])
        self.assertFalse(by_name['external_flow_region.json']['citable'])
        self.assertEqual(['external_flow_region.json'],
                         [entry['name'] for entry in scan['not_citable']])

class ARecordMustAssertSuccess(unittest.TestCase):
    """Independent review round 7, blocker 1: a record declaring its own gate FAILED was certified.

    `claims_ok(payload)` was computed and then ignored on the success path, so a record with
    `"status":"gate_failed"` - and physically impossible measurements - passed whenever the surrounding
    stage said ok, and `progress_matrix` reported nothing. This is the "recorded but never compared"
    defect in the one file that is supposed to be the rule.
    """

    RECORD = {'schema_version': 1, 'status': 'gate_failed', 'measured_cell_quality_min': -5.0,
              'measured_face_validity_min': 0.0, 'measured_volume_change_min': -1.0,
              'measured_wall_prism_layers_min': 0.0}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / 'external_flow_mesh_quality.json').write_text(json.dumps(self.RECORD),
                                                                  encoding='utf-8')
        (self.tmp / 'stage-mesh-quality-audit.json').write_text(json.dumps(
            {'stage': 'mesh-quality-audit', 'state': 'ok', 'artifacts': [],
             'macro_outputs': ['external_flow_mesh_quality.json']}), encoding='utf-8')

    def test_a_gate_failed_record_is_refused_even_inside_a_successful_stage(self):
        verdict = citation_status(self.tmp / 'external_flow_mesh_quality.json')
        self.assertEqual(NOT_CITABLE_NO_CLAIM, verdict['state'])
        self.assertIn('cannot certify success', verdict['reason'])
        with self.assertRaises(RecordNotCitable):
            assert_citable(self.tmp / 'external_flow_mesh_quality.json')

    def test_an_error_record_is_refused_even_inside_a_successful_run(self):
        (self.tmp / 'external_flow_mesh_quality.json').write_text(json.dumps(
            {'schema_version': 1, 'status': 'error', 'usable': False}), encoding='utf-8')
        (self.tmp / 'case-probe.json').write_text(json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE'}),
                                                  encoding='utf-8')
        (self.tmp / 'stage-mesh-quality-audit.json').unlink()
        self.assertEqual(NOT_CITABLE_NO_CLAIM,
                         citation_status(self.tmp / 'external_flow_mesh_quality.json')['state'])

    def test_a_runner_state_file_is_not_a_certificate(self):
        # Independent review round 7, second pass: a PASSED stage with `record: case-probe.json` and no
        # requires_record_keys was certified by a run-state file, which asserts nothing about any
        # measurement - the same objection that made a stage record non-certifying.
        (self.tmp / 'external_flow_mesh_quality.json').unlink()
        (self.tmp / 'stage-mesh-quality-audit.json').unlink()
        (self.tmp / 'case-probe.json').write_text(json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE'}),
                                                  encoding='utf-8')
        verdict = citation_status(self.tmp / 'case-probe.json')
        self.assertEqual(NOT_CITABLE_NO_CLAIM, verdict['state'])
        self.assertIn('runner state file', verdict['reason'])

    def test_scan_run_lists_only_measurement_level_claims(self):
        # A run record is the authority and a `gate_failed` record makes no success claim: neither is a
        # false success claim, so neither may clutter the list of uncitable records - while a status:ok
        # record inside a FAILED run must appear there.
        shutil.copy2(ROUND6 / 'external_flow_mesh.json', self.tmp / 'external_flow_mesh.json')
        (self.tmp / 'case-probe.json').write_text(json.dumps({'state': 'FAILED'}), encoding='utf-8')
        names = [entry['name'] for entry in scan_run(self.tmp)['not_citable']]
        self.assertIn('external_flow_mesh.json', names)
        self.assertNotIn('case-probe.json', names)
        self.assertNotIn('external_flow_mesh_quality.json', names)

    def test_scan_run_does_not_list_it_as_citable(self):
        scan = scan_run(self.tmp)
        entry = {item['name']: item for item in scan['records']}['external_flow_mesh_quality.json']
        self.assertFalse(entry['citable'])
        self.assertEqual([], scan['not_citable'])   # it never claimed ok, so it is not a false claim



class ParseRatchet(unittest.TestCase):
    """R1 for the whole evidence tree: exactly the known loss, and never a second one."""

    def test_only_the_known_record_fails_to_parse(self):
        findings = {entry['path'] for entry in known_unparseable(ROOT / 'test-evidence')}
        self.assertEqual(KNOWN_UNPARSEABLE, findings,
                         'a new unparseable failure record, or a repaired one, changes this set')

    def test_the_known_record_is_the_pre_fix_escape_bug(self):
        raw = (ROUND6 / 'external_flow_physics.json').read_bytes()
        self.assertIn(b'ReferenceFrameOption', raw)
        self.assertIn(b'\n', raw.split(b'"error":"')[1][:200])


def _java_string_helpers(text):
    """Extract real standalone String helpers, balancing Java literals and braces."""
    found = []
    pattern = r'((?:private|public|protected|static|final)\s+)*String\s+(\w+)\s*\(\s*String\s+\w+\s*\)\s*\{'
    for m in re.finditer(pattern, text):
        if 'static' not in (m.group(1) or ''):
            continue
        depth, quote, escaped = 1, None, False
        for i in range(m.end(), len(text)):
            c = text[i]
            if quote:
                if escaped: escaped = False
                elif c == '\\': escaped = True
                elif c == quote: quote = None
            elif c in ('"', "'"): quote = c
            elif c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if not depth:
                    found.append((m.group(2), text[m.start():i+1]))
                    break
    return found


def _class_blocks(text):
    """{class name: full source incl. header} for every class declared in a macro.

    2026-10-14（C1）：这些宏常被压成一行，按“方法 → 大括号平衡”抽取会被吞。类块是稳定的边界，
    并且把整块原样嵌进探针即可真跑其中的转义器。
    """
    blocks = {}
    for m in re.finditer(r'(?:static\s+)?(?:final\s+)?class\s+(\w+)\s*\{', text):
        depth, quote, escaped = 1, None, False
        for i in range(m.end(), len(text)):
            c = text[i]
            if quote:
                if escaped: escaped = False
                elif c == '\\': escaped = True
                elif c == quote: quote = None
            elif c in ('"', "'"): quote = c
            elif c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if not depth:
                    blocks[m.group(1)] = text[m.start():i+1]
                    break
    return blocks


def _qualified_escapers(text):
    """{Class.method: (method source, method name)} for nested-class escapers in THIS file.

    `ProbeSurfaceFluidBridge` 把 error 值交给本文件内嵌套类 `Guard` 的 `quote`；原检查既不识别
    `Class.method(...)` 调用，也在同名方法上被覆盖，于是把一个正确的转义器判为缺失。
    只抽**那个方法**而不是整个类块：类块里通常还有引用 STAR API 的其它方法，整块编译不过。
    """
    resolved = {}
    for class_name, block in _class_blocks(text).items():
        for m in re.finditer(r'static\s+String\s+(\w+)\s*\(\s*String\s+\w+\s*\)\s*\{', block):
            depth, quote, escaped = 1, None, False
            for i in range(m.end(), len(block)):
                c = block[i]
                if quote:
                    if escaped: escaped = False
                    elif c == '\\': escaped = True
                    elif c == quote: quote = None
                elif c in ('"', "'"): quote = c
                elif c == '{': depth += 1
                elif c == '}':
                    depth -= 1
                    if not depth:
                        resolved['%s.%s' % (class_name, m.group(1))] = (block[m.start():i+1], m.group(1))
                        break
    return resolved


def _probe_escaper(helper_source, call_expression):
    """Compile `helper_source` with a probe that runs `call_expression` over C0 + quote + backslash."""
    expected = ''.join(chr(i) for i in range(32)) + '"' + chr(92)
    with tempfile.TemporaryDirectory() as d:
        source = Path(d) / 'EscaperProbe.java'
        source.write_text('public class EscaperProbe { ' + helper_source +
            ' public static void main(String[] a){StringBuilder b=new StringBuilder();'
            'for(int i=0;i<32;i++)b.append((char)i);b.append((char)34).append((char)92);'
            'System.out.print(' + call_expression + '(b.toString()));}}')
        compiled = subprocess.run(['javac', str(source)], capture_output=True)
        if compiled.returncode:
            return False
        out = subprocess.run(['java', '-cp', d, 'EscaperProbe'], capture_output=True, text=True)
        try:
            raw = out.stdout if out.stdout.startswith('"') else '"' + out.stdout + '"'
            return out.returncode == 0 and json.loads(raw) == expected
        except (ValueError, TypeError):
            return False


@lru_cache(maxsize=256)
def _verified_java_escapers(text):
    """Execute the escaper(s) this macro can use; names are never authority.

    2026-10-14（C1）：删掉“源码里必须出现单反斜杠 \\u%04x 字面”的语法预筛——它才是误报来源
    （Java 源码里写作双反斜杠，嵌套类里的转义器又不在同一抽取路径上）。现在唯一判据是真跑：
    C0 全部、引号、反斜杠必须往返；裸方法名与 `Class.method` 都登记。
    """
    if not shutil.which('javac') or not shutil.which('java'):
        raise AssertionError('Java required to validate newly encountered escapers')
    valid = set()
    for name, helper in _java_string_helpers(text):
        if _probe_escaper(helper, name):
            valid.add(name)
    for qualified, (method_source, method_name) in _qualified_escapers(text).items():
        if _probe_escaper(method_source, method_name):
            valid.add(qualified)
            valid.add(method_name)
    return valid


def _only_verified_call(expression, verified):
    expression = expression.strip()
    m = re.match(r'(\w+(?:\.\w+)?)\s*\(', expression)
    if not m or m.group(1) not in verified:
        return False
    depth, quote, escaped = 1, None, False
    for i in range(m.end(), len(expression)):
        c = expression[i]
        if quote:
            if escaped: escaped = False
            elif c == chr(92): escaped = True
            elif c == quote: quote = None
        elif c in ('"', "'"): quote = c
        elif c == '(': depth += 1
        elif c == ')':
            depth -= 1
            if not depth:
                return not expression[i+1:].strip()
    return False


def _safe_error_callsite(text, match, verified):
    # Inspect the immediate emitted value, never a nearby unrelated helper call.
    tail = text[match.end():]
    # A fully quoted helper may be the last append of a statement. A null-or-
    # helper expression is also safe, but neither a raw branch nor a raw suffix is.
    conditional = re.match(r'\s*(?:\\")?"\s*\+\s*\(', tail)
    if conditional:
        depth, quote, escaped = 1, None, False
        for i in range(conditional.end(), len(tail)):
            c = tail[i]
            if quote:
                if escaped: escaped = False
                elif c == chr(92): escaped = True
                elif c == quote: quote = None
            elif c in ('"', "'"): quote = c
            elif c == '(': depth += 1
            elif c == ')':
                depth -= 1
                if not depth:
                    value = tail[conditional.end():i]
                    branch = re.fullmatch(r'\s*\w+\s*==\s*null\s*\?\s*"null"\s*:\s*(.+)', value, re.S)
                    return bool(branch and _only_verified_call(branch.group(1), verified)
                                and re.match(r'^\s*\+\s*"(?:\\")?[,}]', tail[i+1:]))
        return False
    emitted = re.match(r'\s*(?:\\")?"\s*\+\s*(\w+(?:\.\w+)?)\s*(\(|\+)|\s*(?:\\")?"\s*\)\s*\.append\(\s*(\w+(?:\.\w+)?)\s*(\(|\))', tail)
    if not emitted:
        return False
    name = emitted.group(1) or emitted.group(3)
    delimiter = emitted.group(2) or emitted.group(4)
    if delimiter == '(':
        if name not in verified:
            # 2026-10-14（C1）：嵌套类（如本文件内的 Guard）里的转义器以 `Guard.quote(...)` 调用。
            # 裸方法名已通过行为探针、且该类确实定义在本文件内时，应视为已转义。
            if '.' not in name:
                return False
            bare = name.split('.')[-1]
            owner = name.split('.')[0]
            if bare not in verified or not re.search(r'class\s+%s\b' % re.escape(owner), text):
                return False
        # Balance the entire helper call; a verified prefix is insufficient.
        depth, quote, escaped = 1, None, False
        for i in range(emitted.end(), len(tail)):
            c = tail[i]
            if quote:
                if escaped: escaped = False
                elif c == chr(92): escaped = True
                elif c == quote: quote = None
            elif c in ('"', "'"): quote = c
            elif c == '(': depth += 1
            elif c == ')':
                depth -= 1
                if not depth:
                    suffix = tail[i+1:]
                    # The immediately following emission must be a structural
                    # JSON separator literal, not any raw Java expression.
                    if emitted.group(1):
                        return bool(re.match(r'^\s*\+\s*"(?:\\")?[,}]', suffix))
                    return bool(re.match(r'''^\s*\)\s*(?:;|\.append\(\s*(?:"(?:\\")?[,}]|'"'\s*\)))''', suffix))
        return False
    if delimiter == '+':
        if not re.match(r'^\s*"(?:\\")?[,}]', tail[emitted.end():]):
            return False
    elif not re.match(r'''^\s*\.append\(\s*(?:"(?:\\")?[,}]|'"'\s*\))''', tail[emitted.end():]):
        return False
    # Pre-escaped local: restrict every assignment before emission to JSON null or
    # a verified helper, including reassignment. No raw overwrite may be ignored.
    prefix = text[:match.start()]
    assignments = list(re.finditer(r'\b'+re.escape(name)+r'\s*=(?!=)\s*([^;]+);', prefix))
    if not assignments:
        return False
    return all(a.group(1).strip() == '"null"' or
               _only_verified_call(a.group(1), verified) for a in assignments)


class FailureRecordEscaping(unittest.TestCase):
    """Every macro that can write an error field must escape control characters, at the call site.

    The round-6 failure record was unparseable because a STAR exception message contains newlines and
    the writer emitted them raw. A third review had predicted exactly this. The rule is now a test:

    * the value written into an error/status field must pass through an escaper (call-site check, so a
      macro cannot merely *contain* an escape function and write raw bytes anyway - independent review
      round 7, concern C4), and
    * the escaper must cover every control character, not just \r and \n (a tab is enough to destroy
      the record, and the two legacy escapers that only handled CR/LF were fixed because of this test).
    """

    ERROR_FIELD = re.compile(r'error\\":(?:\\")?|"status":"error|status\\":\\"error')

    def error_writing_macros(self):
        found = []
        for path in sorted((ROOT / 'macros').glob('*.java')):
            text = path.read_text(encoding='utf-8', errors='replace')
            if self.ERROR_FIELD.search(text):
                found.append((path, text))
        self.assertTrue(found, 'the detector found no error-writing macro; the test would be vacuous')
        return found

    def test_every_error_field_goes_through_an_escaper(self):
        offenders = []
        for path, text in self.error_writing_macros():
            verified = _verified_java_escapers(text)
            for match in re.finditer(r'error\\":(?:\\")?', text):
                if not _safe_error_callsite(text, match, verified):
                    offenders.append('%s:%d' % (path.name, text[:match.start()].count('\n') + 1))
        self.assertEqual([], offenders, 'error fields written without an escaper: %s' % offenders)

    def test_actual_new_helpers_and_preescaped_locals_reject_raw_mutations(self):
        # Actual false positives must pass only after executing their real helpers.
        for filename in ('ConfigureNative2DBenchmark.java', 'ProbeMaterialUnits.java', 'QualityReadonly.java'):
            text = (ROOT/'macros'/filename).read_text()
            verified = _verified_java_escapers(text)
            matches = list(re.finditer(r'error\\":(?:\\")?', text))
            self.assertTrue(matches)
            self.assertTrue(all(_safe_error_callsite(text, m, verified) for m in matches))
        text = (ROOT/'macros/ConfigureNative2DBenchmark.java').read_text()
        verified = _verified_java_escapers(text)
        # Use the real inventory writer and actual assignment source, not a toy gate.
        for bad in ('error=e.toString();', 'error=quote(e.toString())+e.toString();'):
            changed = text.replace('error=quote(e.toString());', bad)
            m = next(re.finditer(r'error\\":(?:\\")?', changed))
            self.assertFalse(_safe_error_callsite(changed, m, verified))
        changed = text.replace('error=quote(e.toString());', 'error=quote(e.toString());error=e.toString();')
        m = next(re.finditer(r'error\\":(?:\\")?', changed))
        self.assertFalse(_safe_error_callsite(changed, m, verified))
        changed = text.replace('+error+', '+error+e.toString()+')
        m = next(re.finditer(r'error\\":(?:\\")?', changed))
        self.assertFalse(_safe_error_callsite(changed, m, verified))
        # Merely containing a valid helper never validates an unrelated raw callsite.
        changed = text.replace('+quote(e.toString())+', '+e.toString()+')
        self.assertTrue(any(not _safe_error_callsite(changed, m, verified)
                            for m in re.finditer(r'error\\":(?:\\")?', changed)))
        # Independent review's actual direct-call defect: a valid helper followed
        # by a raw exception string must never inherit the helper's validation.
        for raw in ('e.toString()', '"raw\\n"'):
            changed = text.replace('+quote(e.toString())+', '+quote(e.toString())+'+raw+'+')
            self.assertTrue(any(not _safe_error_callsite(changed, m, verified)
                                for m in re.finditer(r'error\\":(?:\\")?', changed)))
        quality = (ROOT/'macros/QualityReadonly.java').read_text()
        qverified = _verified_java_escapers(quality)
        changed = quality.replace('.append(q(e.toString()))', '.append(q(e.toString())+e.toString())')
        self.assertTrue(any(not _safe_error_callsite(changed, m, qverified)
                            for m in re.finditer(r'error\\":(?:\\")?', changed)))
        bad_helper = text.replace('if(c<32)', 'if(c<2)')
        self.assertNotIn('quote', _verified_java_escapers(bad_helper))

    def test_actual_q_statement_end_and_null_branch_are_safe_but_raw_mutations_are_not(self):
        for filename in ['ProbeNativeResidualSpatial.java','ProbeNativeResidualSpatialNoBatchSave.java']:
            text=(ROOT/'macros'/filename).read_text()
            verified=_verified_java_escapers(text)
            self.assertIn('q',verified)
            matches=lambda source:list(re.finditer(r'error\\":(?:\\")?',source))
            self.assertTrue(all(_safe_error_callsite(text,m,verified) for m in matches(text)))
            for old,new in [('q(restoreError.toString())','restoreError.toString()'),
                            ('q(restoreError.toString())','q(restoreError.toString())+restoreError.toString()'),
                            ('q(deleteError))','deleteError)'),
                            ('q(deleteError))','q(deleteError)+deleteError)'),
                            ('append(q(restoreError.toString()));','append(q(restoreError.toString())).append(restoreError.toString());')]:
                changed=text.replace(old,new)
                self.assertNotEqual(text,changed)
                self.assertTrue(any(not _safe_error_callsite(changed,m,verified) for m in matches(changed)))

    def test_every_error_writing_macro_covers_all_control_characters(self):
        r"""每个写 error 字段的宏都必须有一个**能被真跑验证**的转义器。

        2026-10-14（C1）：原判据是语法正则（源码里要出现 `<0x20|32` **且** 单反斜杠的 `\u%04x` 字面），
        于是 (a) 用嵌套类里转义器的宏、(b) 源码写作双反斜杠（Java 字面量要求）的宏都被误判为不合格。
        判据改为行为：该宏可达的转义器里至少有一个能对 C0 全部、引号、反斜杠正确往返。
        """
        offenders = []
        for path, text in self.error_writing_macros():
            if not _verified_java_escapers(text):
                offenders.append(path.name)
        self.assertEqual([], offenders,
                         'these macros can write a control character that destroys the record')

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'Java runtime unavailable')
    def test_frozen_export_quote_executes_all_controls(self):
        # Execute the actual frozen helper, not a Python reimplementation. Decimal
        # 32 and hexadecimal 0x20 are the same boundary; all C0 bytes must roundtrip.
        text = (ROOT/'macros/ExportExternalFlowResults.java').read_text()
        helper = next(line.strip() for line in text.splitlines()
                      if line.strip().startswith('private static String quote(String s)'))
        with tempfile.TemporaryDirectory() as d:
            source = Path(d)/'EscaperProbe.java'
            source.write_text('public class EscaperProbe { '+helper+
                ' public static void main(String[] a){StringBuilder b=new StringBuilder();'
                'for(int i=0;i<32;i++)b.append((char)i);b.append((char)34).append((char)92);'
                'System.out.print(quote(b.toString()));}}')
            subprocess.run(['javac', str(source)], check=True, capture_output=True)
            out = subprocess.run(['java', '-cp', d, 'EscaperProbe'], check=True,
                                 capture_output=True, text=True).stdout
            self.assertEqual(json.loads(out), ''.join(chr(i) for i in range(32))+'"'+chr(92))

    def test_the_round6_style_raw_newline_is_rejected_by_the_criterion(self):
        # The criterion must reject the actual defect, not just accept today's files: a writer that
        # only escapes backslashes and quotes - the pre-fix shape - fails it.
        pre_fix = 'json.append(error.replace("\\\\", "\\\\\\\\").replace("\\"", "\\\\\\""))'
        self.assertNotIn('0x20', pre_fix)
        self.assertNotIn('\\u%04x', pre_fix)


class TheFailedCapRefusalListIsDerivedNotHandWritten(unittest.TestCase):
    """2026-10-14 failed-cap independent post-review, finding 1 (§2-O9).

    The review ran `scan_run` on `finite-te-gmsh-cap-20261014a`/actual-cap-attempt1 and found the
    hand-written central refusal list named only 3 records while the gate refuses 4: the one record
    that CLAIMS ok (NODE-SELECTION-AUDIT.json) was missing from the hand list. A hand-written list of
    what the gate refuses drifts exactly like every other hand-written list in this project, so the
    correction file is DERIVED from the gate and this test recomputes it live: if the gate's refusal
    set and the recorded set ever disagree, this fails instead of a reader trusting the file.
    """

    RUN = ROOT / ('test-evidence/finite-trailing-edge-policy-20261014/gmsh-fallback-final-preparation'
                  '/actual-cap-attempt1')
    CORRECTION = ROOT / ('test-evidence/finite-trailing-edge-policy-20261014/gmsh-fallback-final-preparation'
                         '/CENTRAL-REFUSAL-LIST-CORRECTION.json')

    def test_the_recorded_refusal_set_equals_the_live_gate_output(self):
        recorded = json.loads(self.CORRECTION.read_text(encoding='utf-8'))
        live = scan_run(self.RUN)
        live_set = {row['name']: row['citation_state'] for row in live['records']}
        self.assertEqual(live_set, {name: row['citation_state']
                                    for name, row in recorded['records'].items()})
        # scan_run 会按传入路径回显，故比较解析后的绝对路径而不是字面量。
        self.assertEqual({Path(item['record']).resolve() for item in live['violations']},
                         {Path(item['record']).resolve() for item in recorded['violations']})

    def test_the_ok_claiming_record_is_in_the_refusal_set(self):
        recorded = json.loads(self.CORRECTION.read_text(encoding='utf-8'))
        row = recorded['records']['NODE-SELECTION-AUDIT.json']
        self.assertTrue(row['claims_ok'])
        self.assertEqual(NOT_CITABLE_FAILED_RUN, row['citation_state'])
        self.assertFalse(row['citable'])
        # 该失败阶段里唯一自称 ok 的记录正是最容易被误引用的那一条：它必须被拒，且被列名。
        self.assertIn('NODE-SELECTION-AUDIT.json',
                      [Path(item['record']).name for item in recorded['violations']])

    def test_the_failed_cap_is_not_split_into_a_successful_stage(self):
        live = scan_run(self.RUN)
        self.assertTrue(live['violations'])
        self.assertTrue(all(not row['citable'] for row in live['records']),
                        'no record from a FAILED stage may be citable')
        # 对照：更早的只读映射 stage 仍然可引用——证明判据不是对所有记录一律拒绝。
        control = ROOT / ('test-evidence/finite-trailing-edge-policy-20261014/gmsh-fallback-final-preparation'
                          '/ACTUAL-FINITE-TE-READONLY-MAPPING.json')
        self.assertEqual(CITABLE, citation_status(control)['state'])


if __name__ == '__main__':
    unittest.main()
