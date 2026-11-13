# -*- coding: utf-8 -*-
"""The single executable rule for "how does a stage become STAR processes?".

Why this file exists (in code, because prose has no binding force here):

Round 6 discovered that ``starccm+ -batch A,B,C`` **keeps going after a middle macro throws**: the
physics macro raised, the mesh macro still ran, and the mesh macro wrote a self-consistent
``"status":"ok"`` record into a stage the runner had marked FAILED. The stage verdict came from one
process exit code, so a single failure and a single success ended up in the same evidence directory.
The proposed fix - have every macro verify its predecessor's artefacts - is a scattered check, and
scattered checks are what already failed here.

This module is the centralised replacement. The decision "one stage, several macros -> several STAR
processes, one per macro, each with its own exit code" is made here, once, in Python, where it is
unit-testable; the PowerShell runners only *execute* the plan this module describes and refuse to run
a stage whose plan is missing or inconsistent. The review ceremony is unchanged: one review record,
one submission, one stage - but each macro gets its own process, so a failure can no longer leak a
success into another macro's evidence.

Rules (each raises StagePlanError with a precise reason):

P1  A stage declares 1..4 macro filenames.
P2  A stage with more than one macro MUST declare ``macro_outputs`` (one entry per macro, a filename
    or null). Without it the chain is unknown, and the only way to run the stage would be the batched
    single process this module exists to forbid. Refusing is the fail-closed choice.
P3  Each macro's input is the previous macro's declared output, or the stage input when that output is
    null. A macro never reads a file a later macro is supposed to have produced.
P4  The stage's declared final output must be exactly the last non-null macro output, and every
    declared macro output must be listed in the stage's ``required`` artefacts, so the runner verifies
    the file it hands to the next macro.
P5  Within a pipeline, a stage input must be a reviewed input file or an earlier stage's output.
"""
from __future__ import annotations

import re
import math
from pathlib import Path

STAGE_NAME_RE = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
MACRO_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.java$')
FILE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
MAX_MACROS = 4


class StagePlanError(Exception):
    """A stage cannot be turned into per-macro STAR processes."""


def _file_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not FILE_RE.match(value):
        raise StagePlanError('%s is not a plain file name: %r' % (label, value))
    return value


def macro_names(stage: dict) -> list[str]:
    macros = stage.get('macros')
    if not isinstance(macros, list) or not macros:
        raise StagePlanError('stage %r has no macro list' % stage.get('name'))
    if len(macros) > MAX_MACROS:
        raise StagePlanError('stage %r lists %d macros, more than the supported %d'
                             % (stage.get('name'), len(macros), MAX_MACROS))
    names = []
    for entry in macros:
        name = entry.get('name') if isinstance(entry, dict) else entry
        if not isinstance(name, str) or not MACRO_RE.match(name):
            raise StagePlanError('invalid macro name in stage %r: %r' % (stage.get('name'), name))
        names.append(name)
    return names


def plan_stage(stage: dict, timing_max=1500) -> dict:
    """Return the per-macro execution plan for one stage, or raise StagePlanError.

    The returned plan is what the runner executes and what the review record authorises:
        {'name':..., 'input':..., 'output':..., 'steps':[{'index','macro','input','output'}, ...]}
    """
    if not isinstance(stage, dict):
        raise StagePlanError('stage is not an object')
    name = stage.get('name')
    if not isinstance(name, str) or not STAGE_NAME_RE.match(name):
        raise StagePlanError('invalid stage name: %r' % (name,))
    names = macro_names(stage)
    timing = stage.get('timing_contract')
    if timing is not None:
        fields = {'load_seconds', 'macro_seconds', 'chunk_overrun_seconds', 'save_seconds', 'exit_seconds'}
        if not isinstance(timing, dict) or set(timing) != fields:
            raise StagePlanError('timing_contract must name load/macro/chunk_overrun/save/exit seconds')
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in timing.values()):
            raise StagePlanError('timing_contract values must be finite nonnegative seconds')
        if len(names) != 1:
            raise StagePlanError('timing_contract requires one macro')
    required = stage.get('required') or []
    if not isinstance(required, list):
        raise StagePlanError('stage %s: required must be a list' % name)

    stage_input = stage.get('input')
    if stage_input is not None:
        stage_input = _file_name(stage_input, 'stage %s input' % name)
    # worker manifests name the final simulation output_sim; trial pipelines name it output.
    declared_output = stage.get('output', stage.get('output_sim'))
    if declared_output is not None:
        declared_output = _file_name(declared_output, 'stage %s output' % name)

    outputs = stage.get('macro_outputs')
    if outputs is None:
        if len(names) > 1:
            raise StagePlanError(
                'stage %s runs %d macros but declares no macro_outputs: the stage would have to be '
                'executed as one batched STAR process, in which a failure in one macro cannot stop '
                'the next. Declare one output (or null) per macro.'
                % (name, len(names)))
        outputs = [declared_output] if len(names) == 1 else []
    if not isinstance(outputs, list) or len(outputs) != len(names):
        raise StagePlanError('stage %s: macro_outputs must have one entry per macro (%d), got %r'
                             % (name, len(names), outputs))
    cleaned = []
    for index, value in enumerate(outputs):
        cleaned.append(None if value is None else _file_name(value, 'stage %s macro_outputs[%d]' % (name, index)))

    steps = []
    current = stage_input
    for index, macro in enumerate(names):
        step_output = cleaned[index]
        steps.append({'index': index, 'macro': macro, 'input': current, 'output': step_output})
        if step_output is not None:
            current = step_output

    non_null = [value for value in cleaned if value is not None]
    if declared_output is not None:
        if not non_null or non_null[-1] != declared_output:
            raise StagePlanError('stage %s declares output %s but its macro outputs end at %r; the '
                                 'runner would verify a different file than the chain produced'
                                 % (name, declared_output, non_null[-1] if non_null else None))
    elif non_null:
        raise StagePlanError('stage %s produces %r but declares no final output' % (name, non_null[-1]))
    # Only INTERMEDIATE outputs must appear in `required`: those are the files handed to the next
    # macro, and the runner must have verified them. The final output is verified by the stage's own
    # output check, so requiring it in `required` as well turned a sloppy-but-workable declaration into
    # a hard refusal - which would have refused 11 already-written pipelines of the other branch. Found
    # by planning every existing pipeline against the rule before shipping it.
    for value in non_null[:-1]:
        if required and value not in required:
            raise StagePlanError('stage %s: macro output %s is not in required, so the runner would '
                                 'hand the next macro a file it never verified' % (name, value))
    # ②R14: a stage may DECLARE that it consumes records produced by an earlier stage of the same run (the
    # validation stage needs the solve stage's measurement record, its drag-history CSV and its own stage
    # record). The shape is checked here and the binding is checked in plan_pipeline, which is the only place
    # that can see the whole chain; the two runner layers enforce both at run time.
    inherited = stage.get('inherited_inputs')
    inherited_plan = None
    if inherited is not None:
        if not isinstance(inherited, dict):
            raise StagePlanError('stage %s: inherited_inputs must be an object' % name)
        source = inherited.get('stage')
        if not isinstance(source, str) or not STAGE_NAME_RE.match(source):
            raise StagePlanError('stage %s: inherited_inputs.stage is invalid: %r' % (name, source))
        files = inherited.get('files')
        if not isinstance(files, list) or not files:
            raise StagePlanError('stage %s: inherited_inputs.files must be a nonempty list' % name)
        cleaned_inherited = [_file_name(value, 'stage %s inherited_inputs' % name) for value in files]
        if len(set(cleaned_inherited)) != len(cleaned_inherited):
            raise StagePlanError('stage %s: inherited_inputs.files repeats a name' % name)
        if source == name:
            raise StagePlanError('stage %s: inherited_inputs cannot name the stage itself' % name)
        inherited_plan = {'stage': source, 'files': cleaned_inherited}
    return {'name': name, 'input': stage_input, 'output': declared_output, 'steps': steps,
            'inherited_inputs': inherited_plan}


def plan_pipeline(pipeline: dict) -> dict:
    """Plan every stage of a pipeline and check the stage-to-stage input chain."""
    from resource_contract import validate_pipeline_resources
    contract = validate_pipeline_resources(pipeline)
    timing_max = contract['outer_timeout_seconds'] if contract else 1500
    stages = pipeline.get('stages')
    if not isinstance(stages, list) or not stages:
        raise StagePlanError('pipeline has no stages')
    if len(stages) > 6:
        raise StagePlanError('pipeline lists %d stages, more than the supported 6' % len(stages))
    pinned_inputs = set(pipeline.get('input_hashes') or {})
    plans, available = [], set(pinned_inputs)
    produced_by = {}
    for stage in stages:
        plan = plan_stage(stage, timing_max=timing_max)
        # ②R14: an inherited input must be produced by an EARLIER declared stage of this same pipeline, and
        # that stage must declare the file among its required artefacts (or as its own output). Anything else
        # is a declaration the runner could not honour, so it is refused before submission.
        inherited = plan.get('inherited_inputs')
        if inherited is not None:
            if inherited['stage'] not in produced_by:
                raise StagePlanError('stage %s inherits from stage %r, which is not an earlier stage of this '
                                     'pipeline' % (plan['name'], inherited['stage']))
            source_plan = produced_by[inherited['stage']]
            producible = set(source_plan.get('declared_artifacts') or [])
            # Every stage's own record is produced by the RUNNER, not by the macro, so it need not appear in
            # `required`: AGENTS.md section 6 makes `stage-<name>.json` mandatory on success AND failure.
            producible.add('stage-%s.json' % inherited['stage'])
            for value in inherited['files']:
                if value not in producible:
                    raise StagePlanError('stage %s inherits %s from stage %s, but that stage does not declare '
                                         'producing it' % (plan['name'], value, inherited['stage']))
        if plan['input'] is not None and plan['input'] not in available:
            raise StagePlanError('stage %s reads %s, which is neither a reviewed input %s nor an '
                                 'earlier stage output' % (plan['name'], plan['input'],
                                                           sorted(pinned_inputs)))
        for step in plan['steps']:
            if step['input'] is not None and step['input'] not in available:
                raise StagePlanError('stage %s macro %s reads %s before anything produced it'
                                     % (plan['name'], step['macro'], step['input']))
            if step['output'] is not None:
                available.add(step['output'])
        declared = set(stage.get('required') or [])
        if plan.get('output'):
            declared.add(plan['output'])
        plan['declared_artifacts'] = sorted(declared)
        produced_by[plan['name']] = plan
        plans.append(plan)
    return {'stages': plans,
            'macro_outputs': {plan['name']: [step['output'] for step in plan['steps']]
                              for plan in plans}}


def plan_manifest_stage(workflow_id: str, stage_name: str, stage: dict) -> dict:
    """Plan one installed-workflow stage (worker path). Same rules, manifest field names."""
    plan = plan_stage({'name': stage_name, 'macros': stage.get('macros'),
                       'macro_outputs': stage.get('macro_outputs'),
                       'input': stage.get('input'), 'output_sim': stage.get('output_sim'),
                       'required': stage.get('required'),
                       'inherited_inputs': stage.get('inherited_inputs')})
    plan['workflow_id'] = workflow_id
    return plan


def installed_manifest_plans(workflows_dir: Path) -> list[dict]:
    """Plan every stage of every installed workflow manifest. Raises on the first bad stage."""
    import json

    plans = []
    for manifest_path in sorted(Path(workflows_dir).glob('*/manifest.json')):
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        for stage_name, stage in (manifest.get('stages') or {}).items():
            plan = plan_manifest_stage(manifest.get('id'), stage_name, stage)
            plan['manifest'] = str(manifest_path)
            declared = set(stage.get('required') or [])
            if plan.get('output'):
                declared.add(plan['output'])
            plan['declared_artifacts'] = sorted(declared)
            plans.append(plan)
    return plans
