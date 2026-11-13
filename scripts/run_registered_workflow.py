"""Select a registered, reviewed workflow and delegate to the existing entry.

This adapter does no STAR execution, polling, gate evaluation or transfer itself.
Inputs must match the registered reviewed bytes; a changed input requires a new
registration/review, rather than silently inheriting approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import shutil
import re

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def prepare(workflow, source, spec, evidence, trial_id):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', workflow):
        raise ValueError('Invalid registered workflow id')
    directory = ROOT / 'registered-workflows' / workflow
    if directory.parent != ROOT / 'registered-workflows':
        raise ValueError('Invalid registered workflow id')
    manifest_path = directory / 'manifest.json'
    manifest = load(manifest_path)
    if manifest['id'] != workflow or manifest['mechanism'] != 'restricted_trial':
        raise ValueError('Registration does not name the existing execution mechanism')
    pipeline_path = ROOT / manifest['pipeline']
    pipeline = load(pipeline_path)
    cap = manifest['star_seconds_cap']
    deadlines = [stage['timeout_seconds'] for stage in pipeline['stages']]
    if (not isinstance(cap, int) or not 0 < cap <= 600 or
            any(not isinstance(value, int) or value < 30 for value in deadlines) or
            sum(deadlines) > cap):
        raise ValueError('Registered stage deadlines exceed the authorized STAR cap')
    if (manifest.get('presentation_entry') not in manifest.get('adapter_files', []) or
            not (ROOT / manifest['presentation_entry']).is_file()):
        raise ValueError('Presentation entry is missing or outside reviewed adapter files')
    supplied = {'source_input.sim': Path(source).resolve(), 'case_spec.json': Path(spec).resolve()}
    for name, path in supplied.items():
        if digest(path) != pipeline['input_hashes'][name]:
            raise ValueError('Submitted input differs from reviewed registration: ' + name)
    # The existing runner requires pinned basenames. Stage isolated copies;
    # never rename/overwrite the submitted source or reuse a different byte set.
    inputs_path = Path(evidence).resolve() / 'submitted-inputs.json'
    inputs_path.parent.mkdir(parents=True, exist_ok=True)
    review = load(inputs_path.parent / 'pipeline-review.json')
    if review.get('approved') is not True or review.get('independent') is not True:
        raise ValueError('Independent review required for this submission')
    # Extra adapter/presentation bytes are bound alongside the existing five-fold
    # gate; the controller still independently checks all its normal pins.
    for relative in manifest['adapter_files'] + ['registered-workflows/' + workflow + '/manifest.json']:
        if review.get('registration_hashes', {}).get(relative) != digest(ROOT / relative):
            raise ValueError('Registered adapter hash differs: ' + relative)
    staged = inputs_path.parent / 'registered-inputs'
    staged.mkdir(exist_ok=True)
    for name, source_path in supplied.items():
        target = staged / name
        if target.exists():
            if digest(target) != pipeline['input_hashes'][name]:
                raise ValueError('Isolated input already exists with different bytes: ' + name)
        else:
            shutil.copyfile(source_path, target)
        if digest(target) != pipeline['input_hashes'][name]:
            raise ValueError('Isolated input copy hash mismatch: ' + name)
    inputs = {'inputs': [str(staged / name) for name in supplied]}
    inputs_path.write_text(json.dumps(inputs, ensure_ascii=False, indent=2) + '\n')
    command = [sys.executable, str(ROOT / 'scripts/run_static_external_flow_e2e.py'),
               '--pipeline', str(pipeline_path), '--inputs', str(inputs_path),
               '--trial-id', trial_id, '--evidence', str(inputs_path.parent),
               '--timeout', str(manifest['poll_timeout_seconds'])]
    return manifest, command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workflow', required=True)
    parser.add_argument('--source-sim', type=Path, required=True)
    parser.add_argument('--case-spec', type=Path, required=True)
    parser.add_argument('--trial-id', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    manifest, command = prepare(args.workflow, args.source_sim, args.case_spec,
                                args.evidence, args.trial_id)
    print(json.dumps({'workflow': args.workflow, 'delegated_command': command,
                      'engineering_validated': False}, ensure_ascii=False), flush=True)
    if args.prepare_only:
        return 0
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        return result.returncode
    presentation = [sys.executable, str(ROOT / manifest['presentation_entry']),
                    '--run-dir', str(args.evidence.resolve() / args.trial_id)]
    return subprocess.run(presentation, cwd=ROOT).returncode


if __name__ == '__main__':
    raise SystemExit(main())
