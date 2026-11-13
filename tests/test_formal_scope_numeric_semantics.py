"""Semantic scope and the complete local submission predicate; no SSH or STAR."""
import copy
import base64
import json
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests import test_formal_catalog as fixtures
import production_catalog as pc
import backend


class NumericMeaning(unittest.TestCase):
    def test_equal_finite_numbers_and_nested_objects_have_one_digest(self):
        for left,right in [(0,0.0),(1,1.0),(0,-0.0),(-2,-2.0),
            ({'b':[0.0,{'x':1.0}],'a':-0.0},{'a':0,'b':[0,{'x':1}]})]:
            with self.subTest(left=left,right=right):
                self.assertTrue(pc.semantic_equal(left,right))
                self.assertEqual(pc.canonical_digest(left,'semantic-v1'),pc.canonical_digest(right,'semantic-v1'))

    def test_real_changes_types_missing_null_and_order_are_distinct(self):
        for left,right in [(0,1),(0.1,0.10000000000000002),(True,1),(False,0),('0',0),
            ({},{'x':None}),([1,2],[2,1]),(9007199254740993,float(9007199254740993)),
            ({'x':True},{'x':1}),(['1'],[1])]:
            with self.subTest(left=left,right=right):
                self.assertFalse(pc.semantic_equal(left,right))
                self.assertNotEqual(pc.canonical_digest(left,'semantic-v1'),pc.canonical_digest(right,'semantic-v1'))

    def test_non_json_non_finite_and_unknown_algorithms_are_rejected(self):
        for value in [float('nan'),float('inf'),-float('inf'),{'x':[float('nan')]},(1,),{1:'x'}]:
            with self.subTest(value=value), self.assertRaises(ValueError):pc.canonical_digest(value,'semantic-v1')
        for algorithm in [None,'semantic-v2','SEMANTIC-V1','']:
            with self.assertRaises(ValueError):pc.canonical_digest({},algorithm)

    def test_legacy_digest_is_unchanged_and_explicit(self):
        self.assertNotEqual(pc.canonical_digest(0),pc.canonical_digest(0.0))
        self.assertEqual(pc.canonical_digest({'x':0}),pc.canonical_digest({'x':0},'legacy'))

    def test_equal_numeric_parameters_launch_identical_environment_bytes(self):
        declared={'VALUE':{}}
        for left,right in [(1,1.0),(0,-0.0),(2**60,float(2**60))]:
            self.assertEqual(pc.formal_parameter_environment({'VALUE':left},declared),
                             pc.formal_parameter_environment({'VALUE':right},declared))
        self.assertEqual({'VALUE':'1'},pc.formal_parameter_environment({'VALUE':1.0},declared))
        self.assertEqual({'VALUE':'1.0'},pc.formal_parameter_environment({'VALUE':'1.0'},declared))
        with self.assertRaises(ValueError):pc.formal_parameter_environment({'VALUE':True},declared)


class ScopeMeaning(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FormalCatalogTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root;self.entry=self.fixture.entry
        self.spec=dict(self.fixture.spec,values={'origin':[0.0,-0.0,1.0],'enabled':True,'optional':None})
        self.manifest=pc.read_json(self.root/'workflows/synthetic/manifest.json')
        stage=self.manifest['stages']['one']
        self.names=['one','two','three','four','five','six']
        self.manifest['stages']={name:copy.deepcopy(stage) for name in self.names}
        fixtures.write(self.root/'workflows/synthetic/manifest.json',json.dumps(self.manifest))
        self.steps=[{'key':n,'workflow_id':'synthetic','stage':n,'parameters':{'TEST_VALUE':'1'}} for n in self.names]
        formal=self.entry['formal'];formal.update(case_spec_digest_algorithm='semantic-v1',
            case_spec_digest=pc.canonical_digest(self.spec,'semantic-v1'),
            pipeline_hashes={'synthetic':pc.sha256(self.root/'workflows/synthetic/manifest.json')},
            steps=pc.normalize_formal_steps(self.steps,self.root,'semantic-v1'),plan_operations=self.names,
            **pc.formal_runtime_pins(self.root))
        fixtures.write(self.root/'spec.json',json.dumps(self.spec));self.fixture.sign()

    def materialize(self,spec=None,steps=None):
        return pc.materialize_formal(self.entry,self.spec['case_ref'],self.steps if steps is None else steps,
            self.spec if spec is None else spec,self.root/'output',self.root)

    def test_actual_materializer_and_loader_accept_numeric_representation_changes(self):
        transported=copy.deepcopy(self.spec);transported['values']['origin']=[0,0,1]
        path=self.materialize(transported)
        self.assertEqual('semantic-v1',pc.read_json(path)['case_spec_digest_algorithm'])
        pc.validate_formal_submission(str(path),self.spec['case_ref'],self.steps,transported,self.names,self.root)
        self.fixture.load(path)

    def test_case_changes_types_missing_and_nonfinite_are_rejected(self):
        for value in [{'origin':[0,0,2],'enabled':True,'optional':None},
                      {'origin':[0,0,1],'enabled':1,'optional':None},
                      {'origin':['0',0,1],'enabled':True,'optional':None},
                      {'origin':[0,0,1],'enabled':True},
                      {'origin':[float('nan'),0,1],'enabled':True,'optional':None},
                      {'origin':[float('inf'),0,1],'enabled':True,'optional':None}]:
            with self.subTest(value=value),self.assertRaises(ValueError):self.materialize(dict(self.spec,values=value))

    def test_step_order_plan_and_typed_parameters_cannot_drift(self):
        path=self.materialize()
        variants=[list(reversed(self.steps))]
        for value in [1,1.0,True,'0',0,float('inf')]:
            steps=copy.deepcopy(self.steps);steps[0]['parameters']['TEST_VALUE']=value;variants.append(steps)
        for steps in variants:
            with self.assertRaises(ValueError):
                pc.validate_formal_submission(str(path),self.spec['case_ref'],steps,self.spec,self.names,self.root)
        for ops in [list(reversed(self.names)),self.names[:-1],self.names+[True],None]:
            with self.assertRaises(ValueError):
                pc.validate_formal_submission(str(path),self.spec['case_ref'],self.steps,self.spec,ops,self.root)

    def test_numeric_parameters_are_semantic_but_strings_and_bools_are_not(self):
        for step in self.entry['formal']['steps']:step['parameters']['TEST_VALUE']=1
        self.fixture.sign()
        steps=copy.deepcopy(self.steps)
        for step in steps:step['parameters']['TEST_VALUE']=1.0
        path=self.materialize(steps=steps)
        pc.validate_formal_submission(str(path),self.spec['case_ref'],steps,self.spec,self.names,self.root)
        for value in ['1',True,0.1,0.10000000000000002]:
            bad=copy.deepcopy(steps);bad[0]['parameters']['TEST_VALUE']=value
            with self.assertRaises(ValueError):
                pc.validate_formal_submission(str(path),self.spec['case_ref'],bad,self.spec,self.names,self.root)

    def test_real_input_manifest_macro_support_and_algorithm_drift_is_rejected(self):
        for name in ['input.sim','workflows/synthetic/manifest.json','macros/One.java','scripts/casespec.py']:
            target=self.root/name;before=target.read_bytes();target.write_bytes(before+b' drift')
            with self.subTest(name=name),self.assertRaises(ValueError):self.materialize()
            target.write_bytes(before)
        for algorithm in [None,'semantic-v2','legacy']:
            before=self.entry['formal']['case_spec_digest_algorithm']
            self.entry['formal']['case_spec_digest_algorithm']=algorithm;self.fixture.sign()
            with self.assertRaises(ValueError):self.materialize()
            self.entry['formal']['case_spec_digest_algorithm']=before;self.fixture.sign()

    def test_algorithm_field_is_bound_and_cannot_be_dropped_from_review(self):
        path=self.materialize();original=pc.read_json(path)
        for value in [None,'legacy','semantic-v2']:
            changed=dict(original,case_spec_digest_algorithm=value);fixtures.write(path,json.dumps(changed))
            with self.assertRaises(ValueError):pc.verify_formal_review_record(changed,self.root)
        changed=dict(original);changed.pop('case_spec_digest_algorithm')
        with self.assertRaises(ValueError):pc.verify_formal_review_record(changed,self.root)

    def test_backend_checks_original_parameter_types_before_env_conversion(self):
        path=self.materialize();client=object.__new__(backend.Backend)
        registry={'operations':{n:{'implementation':'RESOLVED','mechanism':'workflow_stage',
            'call_path':{'workflow_id':'synthetic','stage':n}} for n in self.names}}
        with patch.object(backend,'PLUGIN',self.root), \
             patch.object(backend,'workflow_catalog',return_value={'synthetic':self.manifest}), \
             patch.object(backend,'stage_parameter_declarations',return_value={'TEST_VALUE':{}}), \
             patch.object(backend,'load_review_record',return_value={'macro_hashes':{'One.java':'0'*64}}), \
             patch('casespec.resolve_operations',return_value=registry), \
             patch.object(client,'validate_plan',side_effect=RuntimeError('SCOPE_ACCEPTED_NO_REMOTE')) as plan, \
             patch.object(client,'ps') as remote:
            good=copy.deepcopy(self.spec);good['values']['origin']=[0,0,1]
            with self.assertRaisesRegex(RuntimeError,'SCOPE_ACCEPTED_NO_REMOTE'):
                client.submit_run(self.spec['case_ref'],'semantic-positive-001',steps=self.steps,
                    case_spec=good,review_ref=str(path))
            plan.reset_mock();bad=copy.deepcopy(self.steps);bad[0]['parameters']['TEST_VALUE']=1
            with self.assertRaisesRegex(ValueError,'approved scope'):
                client.submit_run(self.spec['case_ref'],'semantic-negative-001',steps=bad,
                    case_spec=good,review_ref=str(path))
            plan.assert_not_called();remote.assert_not_called()

    def test_actual_backend_request_has_same_numeric_env_in_steps_and_operations_modes(self):
        for step in self.entry['formal']['steps']:step['parameters']['TEST_VALUE']=1
        self.fixture.sign();path=self.materialize(steps=self.entry['formal']['steps'])
        client=object.__new__(backend.Backend)
        client.remote_root=r'C:\Formal';client.config={'star_launcher':r'C:\STAR\starccm.bat'}
        registry={'operations':{n:{'implementation':'RESOLVED','mechanism':'workflow_stage',
            'call_path':{'workflow_id':'synthetic','stage':n}} for n in self.names}}
        reviewed={'macro_hashes':{'One.java':pc.sha256(self.root/'macros/One.java')},
                  'resource_contract_sha256':pc.sha256(self.root/'scripts/resource_contract.py'),
                  'sha256':pc.sha256(path),'path':str(path),'runner_sha256':'a'*64,
                  'pipeline_runner_sha256':'b'*64,'input_sha256':self.entry['formal']['input_sha256']}
        captured=[]
        def remote(script,**kwargs):
            for value in re.findall(r"FromBase64String\('([A-Za-z0-9+/=]+)'\)",script):
                payload=json.loads(base64.b64decode(value))
                if 'steps' in payload:captured.append(payload)
            return {'state':'PENDING','run_id':'synthetic-local-only'}
        with patch.object(backend,'PLUGIN',self.root), \
             patch.object(backend,'workflow_catalog',return_value={'synthetic':self.manifest}), \
             patch.object(backend,'stage_parameter_declarations',return_value={'TEST_VALUE':{}}), \
             patch.object(backend,'load_review_record',return_value=reviewed), \
             patch('casespec.resolve_operations',return_value=registry), \
             patch('casespec.validate_case_spec',return_value=SimpleNamespace(digest='c'*64)), \
             patch('casespec.required_operations',return_value=self.names), \
             patch('formal_worker.request_pins',return_value={'runtime_digest':'d'*64,'expected_worker_sid':'S-1-5-21-1-2-3-1001'}), \
             patch.object(client,'runtime_digest',return_value='d'*64), \
             patch.object(client,'validate_plan',return_value={'valid':True,'executable':True,'required_operations':self.names}), \
             patch.object(client,'ps',side_effect=remote) as ps:
            steps=copy.deepcopy(self.steps)
            for step in steps:step['parameters']['TEST_VALUE']=1.0
            client.submit_run(self.spec['case_ref'],'semantic-local-steps-001',steps=steps,
                case_spec=self.spec,review_ref=str(path))
            client.submit_run(self.spec['case_ref'],'semantic-local-operations-001',operations=self.names,
                parameters={n:{'TEST_VALUE':1} for n in self.names},case_spec=self.spec,review_ref=str(path))
            self.assertEqual(2,len(captured))
            self.assertEqual([s['env'] for s in captured[0]['steps']],[s['env'] for s in captured[1]['steps']])
            self.assertTrue(all(s['env']=={'TEST_VALUE':'1'} for s in captured[0]['steps']))
            ps.reset_mock();bad=copy.deepcopy(steps)
            for step in bad:step['env']=step.pop('parameters')
            with self.assertRaisesRegex(ValueError,'runner-internal'):
                client.submit_run(self.spec['case_ref'],'semantic-local-env-only-001',steps=bad,
                    case_spec=self.spec,review_ref=str(path))
            ps.assert_not_called()
            with self.assertRaisesRegex(ValueError,'runner-internal'):self.materialize(steps=bad)
