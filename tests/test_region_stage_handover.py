"""The region stage must inherit the domain build record it actually reads.

formal-pat18-full-chain-20261019c failed in BuildExternalFlowRegion because the
upstream build stage succeeded but its external_domain_build.json stayed in the
build stage directory. The stage declared no inherited_inputs, so
worker/run_pipeline.ps1 had nothing to hand over. These tests bind the
declaration to the macro that needs it and to the source stage that produces it.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from stage_plan import StagePlanError, plan_pipeline, plan_stage  # noqa: E402

STATIC = json.loads((ROOT / 'workflows/static-external-flow-2502/manifest.json').read_text())
EXTERNAL = json.loads((ROOT / 'workflows/external-domain-2502/manifest.json').read_text())


def region_stage(**overrides):
    stage = dict(STATIC['stages']['region-physics-mesh'], name='region-physics-mesh')
    stage.update(overrides)
    return stage


class RegionHandoverDeclaration(unittest.TestCase):
    def test_region_stage_declares_the_domain_build_record(self):
        inherited = STATIC['stages']['region-physics-mesh']['inherited_inputs']
        self.assertEqual(inherited['stage'], 'build')
        self.assertEqual(inherited['files'], ['external_domain_build.json'])

    def test_source_stage_really_produces_the_inherited_file(self):
        build = EXTERNAL['stages']['build']
        self.assertIn('external_domain_build.json', build['required'])
        self.assertEqual(build['output_sim'], 'external_domain_built.sim')

    def test_the_region_macro_reads_exactly_that_file(self):
        macros = STATIC['stages']['region-physics-mesh']['macros']
        self.assertEqual(macros[0], 'BuildExternalFlowRegion.java')
        source = (ROOT / 'macros/BuildExternalFlowRegion.java').read_text()
        self.assertIn('new File(dir, "external_domain_build.json")', source)

    def test_declaration_is_valid_to_the_stage_planner(self):
        plan = plan_stage(region_stage())
        self.assertEqual(plan['inherited_inputs'],
                         {'stage': 'build', 'files': ['external_domain_build.json']})

    def test_malformed_declarations_are_refused(self):
        for mutation in ({'stage': 'region-physics-mesh', 'files': ['external_domain_build.json']},
                         {'stage': 'build', 'files': []},
                         {'stage': 'build', 'files': ['../external_domain_build.json']}):
            with self.assertRaises(StagePlanError):
                plan_stage(region_stage(inherited_inputs=mutation))


class PipelineCounterexamples(unittest.TestCase):
    def pipeline(self, inherited_files):
        build = {'name': 'build', 'macros': ['BuildExternalDomain.java'],
                 'required': ['external_domain_build.json', 'external_domain_built.sim'],
                 'output': 'external_domain_built.sim'}
        region = {'name': 'region-physics-mesh',
                  'macros': ['BuildExternalFlowRegion.java', 'ConfigureExternalFlowPhysics.java',
                             'BuildExternalFlowMesh.java'],
                  'macro_outputs': ['external_flow_region.sim', 'external_flow_physics.sim',
                                    'external_flow_meshed.sim'],
                  'required': ['external_flow_region.sim', 'external_flow_physics.sim',
                               'external_flow_meshed.sim'],
                  'input': 'external_domain_built.sim',
                  'output': 'external_flow_meshed.sim',
                  'inherited_inputs': {'stage': 'build', 'files': inherited_files}}
        return {'stages': [build, region]}

    def test_the_declared_file_is_accepted(self):
        plan_pipeline(self.pipeline(['external_domain_build.json']))

    def test_a_file_the_source_does_not_produce_is_refused(self):
        with self.assertRaisesRegex(StagePlanError, 'does not declare producing it'):
            plan_pipeline(self.pipeline(['case_spec.json']))

    def test_an_unknown_or_later_source_stage_is_refused(self):
        pipeline = self.pipeline(['external_domain_build.json'])
        pipeline['stages'][1]['inherited_inputs']['stage'] = 'solve'
        with self.assertRaisesRegex(StagePlanError, 'not an earlier stage'):
            plan_pipeline(pipeline)


if __name__ == '__main__':
    unittest.main(verbosity=2)
