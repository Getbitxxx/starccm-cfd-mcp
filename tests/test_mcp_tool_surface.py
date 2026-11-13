# -*- coding: utf-8 -*-
"""Ratchet on the MCP tool surface: names, read/write intent, description budget, probe size.

AGENTS.md 2-O9: an audit finding becomes an executable test in the same round, or the round is not
done.  The 2026-11-03 review of this MCP found four things that no test could have caught, because
``tests/`` contained no reference to ``server_v2`` / ``list_tools`` / ``readOnlyHint`` at all:

  (a) two tools were advertised ``readOnlyHint=true`` while writing to disk -- ``get_run``
      (``quality_contract.remote_snapshot`` caches remote bytes under
      ``work/quality-contract/readbacks``) and ``get_registered_workflow_run``
      (``registered_workflow_mcp_v2`` writes ``<run_id>-poll.log`` after invoking the poll CLI).
      This is not cosmetic: ``readOnlyHint`` is what decides whether the ``UNPUBLISHED_CODE`` gate
      applies to a call (``mcp_business_worker.business_call``), so a writing tool marked read-only
      writes while the gate is skipped -- the same shape as "recorded but never compared";
  (b) ``connection_health`` returned the whole workflow catalog: measured 24061 bytes, of which
      23570 (98%) duplicated ``list_workflows`` and pushed the six fields that matter out of sight;
  (c) ``connection_health.deployment_error`` was a bare English string although
      ``mcp_runtime_release.ERROR_ACTIONS`` exists precisely to give every failure a code and an
      action;
  (d) the surface drifted by accretion: 33 tools, of which 19 belong to three parallel
      submit/poll/fetch families (job / run / registered-workflow) with interchangable verbs.

The surface is parsed from source with ``ast`` instead of imported, so this file runs under the
plain ``python3`` used by the rest of the suite (``server_v2`` needs the ``mcp`` package).

Run: python3 -m unittest tests.test_mcp_tool_surface -v
"""
import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'scripts' / 'server_v2.py'

# Pinned 2026-11-03. Changing any entry is a deliberate contract change: update this file in the
# same commit and say why, otherwise the pin is worthless.
TOOLS = (
    'prepare_static_external_flow', 'assemble_registered_workflow_studies',
    'prepare_registered_workflow_candidate', 'register_reviewed_workflow_candidate',
    'list_registered_workflows', 'submit_registered_workflow', 'get_registered_workflow_run',
    'fetch_registered_workflow_results', 'list_workflows', 'connection_health', 'upload_case',
    'register_case', 'inspect_case', 'classify_case_parts', 'prepare_local_mesh_plan',
    'search_knowledge', 'diff_case_specs', 'check_mesh_recipe', 'validate_macro', 'validate_plan',
    'list_operations', 'resolve_operations', 'submit_run', 'get_run', 'cancel_run', 'resume_run',
    'fetch_run_results', 'submit_job', 'report_run', 'get_job', 'list_jobs', 'cancel_job',
    'fetch_results',
)

# Only tools that provably touch no filesystem state. Anything whose wrapper reaches
# remote_snapshot / a poll CLI / a log write belongs on the other side, whatever it is called.
READ_ONLY = frozenset({
    'check_mesh_recipe', 'classify_case_parts', 'connection_health', 'diff_case_specs', 'get_job',
    'list_jobs', 'list_operations', 'list_registered_workflows', 'list_workflows',
    'prepare_local_mesh_plan', 'resolve_operations', 'search_knowledge', 'validate_plan',
})

# The two (a) defects, kept as an explicit regression guard rather than a comment: if either is
# ever annotated read-only again the failure names the file and line that writes.
KNOWN_WRITERS = {
    'get_run': 'scripts/quality_contract.py: remote_snapshot caches under work/quality-contract/readbacks',
    'get_registered_workflow_run': 'scripts/registered_workflow_mcp_v2.py: writes <run_id>-poll.log',
}

# Verb -> number of tools carrying it. A new tool that lands on an existing verb must edit this
# pin first; that is the cheapest possible brake on a fourth submit/poll/fetch family.
VERB_BUDGET = {'submit': 3, 'get': 3, 'cancel': 2, 'fetch': 3, 'list': 4, 'prepare': 3,
               'register': 2, 'assemble': 1, 'validate': 2, 'resolve': 1, 'check': 1, 'diff': 1,
               'inspect': 1, 'upload': 1, 'classify': 1, 'search': 1, 'connection': 1, 'report': 1,
               'resume': 1}

# Docstring word ceiling per tool (current value as measured 2026-11-03). Growth fails; shrinking
# is always allowed. 1040 words total for 33 tools is already a lot of prose for a caller to parse.
DESCRIPTION_WORDS = {
    'prepare_static_external_flow': 25, 'assemble_registered_workflow_studies': 18,
    'prepare_registered_workflow_candidate': 23, 'register_reviewed_workflow_candidate': 18,
    'list_registered_workflows': 11, 'submit_registered_workflow': 34,
    'get_registered_workflow_run': 38, 'fetch_registered_workflow_results': 32,
    'list_workflows': 13, 'connection_health': 14, 'upload_case': 18, 'register_case': 20,
    'inspect_case': 34, 'classify_case_parts': 34, 'prepare_local_mesh_plan': 26,
    'search_knowledge': 18, 'diff_case_specs': 54, 'check_mesh_recipe': 52, 'validate_macro': 27,
    'validate_plan': 85, 'list_operations': 26, 'resolve_operations': 50, 'submit_run': 110,
    'get_run': 51, 'cancel_run': 12, 'resume_run': 10, 'fetch_run_results': 11, 'submit_job': 54,
    'report_run': 47, 'get_job': 17, 'list_jobs': 12, 'cancel_job': 26, 'fetch_results': 20,
}

HEALTH_BUDGET_BYTES = 3000


def surface():
    """Tool name -> (read_only, docstring word count), parsed from the decorators."""
    tree = ast.parse(SERVER.read_text(encoding='utf-8'))
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and getattr(decorator.func, 'attr', None) == 'tool'):
                continue
            read_only = False
            for keyword in decorator.keywords:
                if keyword.arg == 'annotations' and isinstance(keyword.value, ast.Call):
                    for annotation in keyword.value.keywords:
                        if annotation.arg == 'readOnlyHint':
                            read_only = ast.literal_eval(annotation.value)
            found[node.name] = (read_only, len((ast.get_docstring(node) or '').split()))
    return found


class ToolSurface(unittest.TestCase):
    def setUp(self):
        self.tools = surface()

    def test_every_tool_has_an_explicit_annotation(self):
        # A missing annotations= argument silently defaults to readOnlyHint=False, which is safe
        # for writes but leaves the surface unreadable for a planner. Pin explicitness instead.
        source = SERVER.read_text(encoding='utf-8')
        self.assertEqual(source.count('@mcp.tool(annotations=ToolAnnotations('), len(self.tools))

    def test_names_are_pinned(self):
        self.assertEqual(tuple(sorted(self.tools)), tuple(sorted(TOOLS)))

    def test_read_only_set_is_pinned(self):
        actual = frozenset(name for name, (read_only, _) in self.tools.items() if read_only)
        self.assertEqual(sorted(actual), sorted(READ_ONLY))

    def test_known_writing_tools_are_not_advertised_read_only(self):
        for name, where in KNOWN_WRITERS.items():
            with self.subTest(tool=name):
                self.assertFalse(self.tools[name][0],
                                 name + ' writes (' + where + ') and must not carry readOnlyHint=true')

    def test_descriptions_do_not_grow(self):
        for name, (_, words) in self.tools.items():
            with self.subTest(tool=name):
                self.assertLessEqual(words, DESCRIPTION_WORDS[name],
                                     name + ' docstring grew past its pinned word count')
        self.assertLessEqual(sum(w for _, w in self.tools.values()), sum(DESCRIPTION_WORDS.values()))

    def test_verb_budget(self):
        counts = {}
        for name in self.tools:
            counts[name.split('_')[0]] = counts.get(name.split('_')[0], 0) + 1
        self.assertEqual(counts, {verb: n for verb, n in VERB_BUDGET.items() if n})


class HealthProbe(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import backend
        import formal_worker
        if not hasattr(formal_worker, 'health_payload'):
            self.skipTest('health_payload is not in the current formal_worker.py: the active 2026-10-23 '
                          'deployment approval pins its support hash to c3c9bc99, so the probe slim-down '
                          'cannot be published again until a new deployment approval exists. The tool '
                          'surface half of this file still runs.')
        self.formal_worker = formal_worker
        self.catalog = backend.workflow_catalog()
        self.assertEqual(len(self.catalog), 4, 'workflow catalog changed; re-validate the probe')

    def workload(self):
        return [{'id': key, 'title': value.get('title'), 'input': value.get('input'),
                 'stages': value['stages']} for key, value in self.catalog.items()]

    def test_probe_stays_small_and_omits_stage_detail(self):
        payload = self.formal_worker.health_payload(
            {'host': 'HOST', 'star_version': 'STAR', 'runtime_verified': True,
             'worker_deployed': True, 'runtime_digest': 'd' * 64}, self.workload())
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertLess(len(encoded), HEALTH_BUDGET_BYTES,
                        'health probe grew: stage detail belongs to list_workflows, not health')
        self.assertNotIn('macros', encoded)
        self.assertEqual(sum(item['stage_count'] for item in payload['workflows']), 51)
        self.assertEqual(sorted(payload['workflows'][0]),
                         ['id', 'input', 'stage_count', 'title'])

    def test_deployment_error_carries_code_and_action(self):
        payload = self.formal_worker.health_payload(
            {'deployment_error': 'Deployment approval differs: runtime_digest'}, [])
        self.assertEqual(payload['deployment_error_code'], 'DEPLOYMENT_REVIEW_MISMATCH')
        self.assertTrue(payload['deployment_user_action'].strip())

    def test_unknown_error_falls_back_to_conservative_action(self):
        payload = self.formal_worker.health_payload({'deployment_error': 'something new broke'}, [])
        self.assertEqual(payload['deployment_error_code'], 'UNCLASSIFIED_ERROR')
        self.assertTrue(payload['deployment_user_action'].strip())

    def test_healthy_probe_has_no_error_fields(self):
        payload = self.formal_worker.health_payload({'worker_deployed': True}, [])
        self.assertNotIn('deployment_error_code', payload)
        self.assertNotIn('deployment_user_action', payload)


if __name__ == '__main__':
    unittest.main()
