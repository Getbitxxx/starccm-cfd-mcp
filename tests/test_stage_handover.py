# -*- coding: utf-8 -*-
"""②R14: the stage-file handover and the runner's input/output hash accounting.

Two halves, both about REAL defects measured in round 14 (round14-formal-layout-finding.json):

  * the validation stage received only the staged `.sim`, so the independent audit could not judge and
    reported NOT_CONVERGED_UNMEASURED. The workflow manifest now DECLARES the solve stage's three records
    as inherited inputs, and the two runner layers hand them over inside the same run, bound to the source
    and its sha256, refusing before STAR starts when anything is missing, stale or unsuccessful;
  * `worker/run_job.ps1` recorded each macro's INPUT after it had switched `$current` to the OUTPUT, so a
    macro that produced a file recorded that file as its own input, and the record carried no hashes at all.

`StructuralPins` runs anywhere. `HostBehaviour` drives the REAL extracted blocks on the REAL Windows host
(it needs STARCCM_CONFIG); it creates a throwaway probe directory on the host, runs the four measured cases
(normal handover, missing file, tampered bytes, failed source stage) plus the macro-record statements, and
removes the directory again. It launches no STAR process.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

PIPELINE = ROOT / 'worker' / 'run_pipeline.ps1'
JOB = ROOT / 'worker' / 'run_job.ps1'
MANIFEST = ROOT / 'workflows' / 'static-external-flow-2502' / 'manifest.json'

PIPELINE_BLOCK_START = '# ②R14: STAGE FILE HANDOVER. A stage may DECLARE'
PIPELINE_BLOCK_END = '        $stageReq=[pscustomobject]@{'
JOB_BLOCK_START = '# ②R14: STAGE FILE HANDOVER, verified by THIS layer'
JOB_BLOCK_END = '    $state.runtime_digest_verified=$true'
INPUT_CAPTURE = "$macroInputPath=$current"
RECORD_BUILD = '$macroRecords+=[pscustomobject]@{index=$index;macro=$macro;ran=$true;status=\'ok\''


def block(text: str, start: str, end: str) -> str:
    """The real statements between two anchors, verbatim. A missing anchor is a hard failure."""
    begin = text.index(start)
    finish = text.index(end, begin)
    return text[begin:finish]


def statement_position(text: str, statement: str) -> int:
    """Where a STATEMENT begins - a line whose code starts with it, not a mention inside a comment."""
    for line in text.splitlines():
        if line.strip().startswith(statement) and not line.strip().startswith('#'):
            return text.index(line)
    raise AssertionError('no statement line starts with %r' % statement)


class StructuralPins(unittest.TestCase):
    def setUp(self):
        self.pipeline = PIPELINE.read_text(encoding='utf-8')
        self.job = JOB.read_text(encoding='utf-8')

    def test_the_handover_blocks_are_present_and_executable_looking(self):
        pipeline_block = block(self.pipeline, PIPELINE_BLOCK_START, PIPELINE_BLOCK_END)
        job_block = block(self.job, JOB_BLOCK_START, JOB_BLOCK_END)
        for needle in ('inherited_inputs names the stage', 'did not succeed', 'Inherited input missing',
                       'source_sha256=(Get-FileHash'):
            self.assertIn(needle, pipeline_block, needle)
        for needle in ('Inherited input hash mismatch', 'Inherited input copy hash mismatch',
                       'source_sha256', 'staged_sha256'):
            self.assertIn(needle, job_block, needle)

    def test_run_job_verifies_the_handover_before_the_first_star_process(self):
        verify_at = self.job.index(JOB_BLOCK_START)
        launch_at = self.job.index('Start-Process -FilePath')
        self.assertLess(verify_at, launch_at,
                        'the handover refusal must happen before any STAR process is started')

    def test_the_pipeline_requires_an_earlier_successful_step_of_the_same_run(self):
        pipeline_block = block(self.pipeline, PIPELINE_BLOCK_START, PIPELINE_BLOCK_END)
        self.assertIn('$i -lt $stepIndex', pipeline_block,
                      'the source stage must be searched among the EARLIER steps of this run')
        self.assertIn("$fromState.state -ne 'SUCCEEDED'", pipeline_block,
                      'a source stage that did not succeed must refuse the stage')

    def test_the_macro_input_identity_is_captured_before_the_output_switch(self):
        # The measured bug: input was read after `$current=$outPath`, so the output was recorded as input.
        capture_at = statement_position(self.job, INPUT_CAPTURE)
        switch_at = statement_position(self.job, '$current=$outPath')
        self.assertLess(capture_at, switch_at,
                        'the input identity must be captured before $current switches to the output')
        record_at = statement_position(self.job, RECORD_BUILD)
        self.assertGreater(record_at, switch_at)
        self.assertIn('input=$macroInputName;input_sha256=$macroInputSha', self.job)
        self.assertIn('output=$outName;output_sha256=$outSha', self.job)

    def test_the_failure_and_not_run_paths_keep_honest_identities(self):
        # Review round 1 of this round found these unpinned: a FAILED macro must keep its REAL input
        # identity (it did run) while a macro a failure prevented from running must record nulls - never
        # the previous macro's output.
        lines = self.job.splitlines()
        failure_at = next(index for index, line in enumerate(lines) if "status='failed'" in line)
        failure_record = '\n'.join(lines[failure_at:failure_at + 3])
        self.assertIn('input=$macroInputName', failure_record)
        self.assertIn('input_sha256=$macroInputSha', failure_record)
        self.assertIn('output=$null', failure_record)
        not_run_at = next(index for index, line in enumerate(lines) if "status='not_run'" in line)
        not_run = '\n'.join(lines[not_run_at:not_run_at + 3])
        for needle in ('input=$null', 'input_sha256=$null', 'output=$null', 'output_sha256=$null'):
            self.assertIn(needle, not_run, 'a macro that never ran must not inherit an identity: ' + needle)

    def test_only_what_the_source_stage_produces_may_be_inherited(self):
        pipeline_block = block(self.pipeline, PIPELINE_BLOCK_START, PIPELINE_BLOCK_END)
        self.assertIn('$producible=@{', pipeline_block)
        self.assertIn('is not produced by the source stage', pipeline_block)
        self.assertIn("$fromStage.required", pipeline_block)
        self.assertIn("$fromStage.output_sim", pipeline_block)

    def test_the_runner_refuses_an_inherited_name_that_would_overwrite_its_own_files(self):
        self.assertIn("Inherited input would overwrite this stage's own file", self.job)
        self.assertIn("$reservedNames=@('input.sim','case_spec.json'", self.job)

    def test_the_stage_record_carries_the_hashes_the_audit_reads(self):
        for needle in ("$stageRecord.input='input.sim'", '$stageRecord.input_sha256=$sourceHash',
                       '$stageRecord.output_sha256=(Get-FileHash'):
            self.assertIn(needle, self.job, needle)

    def test_the_manifest_declares_exactly_the_measured_three_files(self):
        declared = json.loads(MANIFEST.read_text(encoding='utf-8'))['stages']['validation']['inherited_inputs']
        self.assertEqual('solve', declared['stage'])
        self.assertEqual(['external_flow_solve.json', 'external_flow_solve_history.csv', 'stage-solve.json'],
                         declared['files'])


@unittest.skipUnless(os.environ.get('STARCCM_CONFIG'), 'needs the Windows host (STARCCM_CONFIG)')
class HostBehaviour(unittest.TestCase):
    """Runs the REAL extracted blocks on the host. No STAR process is started.

    The script is a template with @@placeholders@@ rather than an f-string, so every PowerShell brace stays
    literal and the extracted statements are inserted verbatim.
    """

    RUN_ID = 'r14-handover-probe'

    TEMPLATE = r"""
$ErrorActionPreference='Stop'
$probeRoot=@@PROBE_ROOT@@
if(Test-Path -LiteralPath $probeRoot){Remove-Item -LiteralPath $probeRoot -Recurse -Force}
$sourceJob=Join-Path $probeRoot 'stages\run_external_flow_solve'
$jobDir=Join-Path $probeRoot 'stages\audit_external_flow_convergence'
New-Item -ItemType Directory -Path $sourceJob,$jobDir -Force | Out-Null
$solveRecord='{"schema_version":1,"stage":"solve","state":"ok","input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","output_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}'
$solveJson='{"schema_version":1,"status":"ok","stop_reason":"iteration_cap","finite_state":true,"measured_drag_x_n_final":0.4467,"measured_mass_imbalance_final":1e-12}'
$history="iteration,drag$([char]10)1,0.44$([char]10)2,0.4467$([char]10)"
Set-Content -LiteralPath (Join-Path $sourceJob 'stage-solve.json') -Value $solveRecord -Encoding UTF8
Set-Content -LiteralPath (Join-Path $sourceJob 'external_flow_solve.json') -Value $solveJson -Encoding UTF8
Set-Content -LiteralPath (Join-Path $sourceJob 'external_flow_solve_history.csv') -Value $history -Encoding UTF8
Set-Content -LiteralPath (Join-Path $sourceJob 'state.json') -Value '{"state":"SUCCEEDED"}' -Encoding UTF8
Set-Content -LiteralPath (Join-Path $jobDir 'input.sim') -Value 'solution bytes' -Encoding UTF8
$root=[IO.Path]::GetFullPath(@@REMOTE_ROOT@@).TrimEnd([char]92)+[char]92
# the real block reads the source step's manifest from $PSScriptRoot\workflows\<workflow_id>\manifest.json,
# so the probe builds that directory and points $PSScriptRoot at it (assignment verified on the host).
$runtime=Join-Path $probeRoot 'runtime'
New-Item -ItemType Directory -Path (Join-Path $runtime 'workflows\static-external-flow-2502') -Force | Out-Null
$manifestJson=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@@MANIFEST_B64@@'))
Set-Content -LiteralPath (Join-Path $runtime 'workflows\static-external-flow-2502\manifest.json') -Value $manifestJson -Encoding UTF8
$PSScriptRoot=$runtime
$req=@{steps=@( @{key='run_external_flow_solve';workflow_id='static-external-flow-2502';stage='solve'},
                 @{key='audit_external_flow_convergence';workflow_id='static-external-flow-2502';stage='validation'} )}
$stepIndex=1
$item=$req.steps[1]
$stage=New-Object psobject -Property @{inherited_inputs=(New-Object psobject -Property @{
    stage='solve';files=@('external_flow_solve.json','external_flow_solve_history.csv','stage-solve.json')})}
$RunDir=$probeRoot
$results=@{}

# ---- case 1: normal handover through the REAL pipeline block, then the REAL run_job block
$inheritedInputs=@()
@@PIPELINE_BLOCK@@
$results.pipeline_resolved=@($inheritedInputs | ForEach-Object { $_.name })
$results.pipeline_hashes_are_64_hex=@($inheritedInputs | Where-Object { $_.source_sha256 -match '^[0-9a-f]{64}$' }).Count
$JobDir=$jobDir
$stageRecord=@{stage='validation';state='RUNNING'}   # the real script uses a hashtable here
$request=New-Object psobject -Property @{inherited_inputs=$inheritedInputs}
$inheritedRecords=@()
@@JOB_BLOCK@@
$results.staged=@(Get-ChildItem -LiteralPath $JobDir -File | ForEach-Object { $_.Name } | Where-Object { $_ -like 'external_flow*' -or $_ -eq 'stage-solve.json' })
$results.staged_verified=@($inheritedRecords | ForEach-Object { [bool]$_.verified })

# ---- case 2: a missing inherited file must be refused before anything runs
Remove-Item -LiteralPath (Join-Path $sourceJob 'external_flow_solve_history.csv') -Force
try { $inheritedInputs=@(); @@PIPELINE_BLOCK@@; $results.missing='NO THROW' }
catch { $results.missing=''+$_.Exception.Message }
Set-Content -LiteralPath (Join-Path $sourceJob 'external_flow_solve_history.csv') -Value $history -Encoding UTF8

# ---- case 3: a failed source stage must be refused
Set-Content -LiteralPath (Join-Path $sourceJob 'state.json') -Value '{"state":"FAILED"}' -Encoding UTF8
try { $inheritedInputs=@(); @@PIPELINE_BLOCK@@; $results.failed_source='NO THROW' }
catch { $results.failed_source=''+$_.Exception.Message }
Set-Content -LiteralPath (Join-Path $sourceJob 'state.json') -Value '{"state":"SUCCEEDED"}' -Encoding UTF8

# ---- case 4: tampered bytes must be refused by the run_job block
$inheritedInputs=@()
@@PIPELINE_BLOCK@@
Add-Content -LiteralPath (Join-Path $sourceJob 'external_flow_solve_history.csv') -Value '999,9.9'
$JobDir=$jobDir
$request=New-Object psobject -Property @{inherited_inputs=$inheritedInputs}
try { $inheritedRecords=@(); @@JOB_BLOCK@@; $results.tampered='NO THROW' }
catch { $results.tampered=''+$_.Exception.Message }

# ---- case 6: a name the source stage does NOT produce must be refused (the clobber shape)
try {
  $stage=New-Object psobject -Property @{inherited_inputs=(New-Object psobject -Property @{
      stage='solve';files=@('input.sim')})}
  $inheritedInputs=@(); @@PIPELINE_BLOCK@@; $results.not_produced='NO THROW'
} catch { $results.not_produced=''+$_.Exception.Message }
$stage=New-Object psobject -Property @{inherited_inputs=(New-Object psobject -Property @{
    stage='solve';files=@('external_flow_solve.json','external_flow_solve_history.csv','stage-solve.json')})}

# ---- case 7: the run_job collision guard refuses an inherited name equal to its own input
try {
  $request=New-Object psobject -Property @{inherited_inputs=@(
      (New-Object psobject -Property @{name='input.sim';source_stage='run_external_flow_solve'
          ;source_path=(Join-Path $sourceJob 'external_flow_solve.json')
          ;source_sha256=(Get-FileHash -LiteralPath (Join-Path $sourceJob 'external_flow_solve.json') -Algorithm SHA256).Hash.ToLowerInvariant()}))}
  $inheritedRecords=@(); @@JOB_BLOCK@@; $results.collision='NO THROW'
} catch { $results.collision=''+$_.Exception.Message }
$request=New-Object psobject -Property @{inherited_inputs=@()}

# ---- case 5: the macro record statements carry the REAL input, not the output
$current=Join-Path $jobDir 'input.sim'
$index=0
$macro='RunExternalFlowSolve.java'
$outName='external_flow_solved.sim'
$outPath=Join-Path $jobDir $outName
Set-Content -LiteralPath $outPath -Value 'solved bytes' -Encoding UTF8
$proc=New-Object psobject -Property @{ExitCode=0}
$macroInputName=$null;$macroInputSha=$null;$outSha=$null;$macroRecords=@()
@@CAPTURE@@
$outSha=(Get-FileHash -LiteralPath $outPath -Algorithm SHA256).Hash.ToLowerInvariant()
$current=$outPath
$macroRecords+=[pscustomobject]@{index=$index;macro=$macro;ran=$true;status='ok'
    exit_code=$proc.ExitCode;input=$macroInputName;input_sha256=$macroInputSha
    output=$outName;output_sha256=$outSha;log='l';error=$null}
$results.macro_input=$macroRecords[0].input
$results.macro_output=$macroRecords[0].output
$results.macro_input_sha=$macroRecords[0].input_sha256
$results.macro_output_sha=$macroRecords[0].output_sha256
$results.input_is_not_the_output=[bool]($results.macro_input -ne $results.macro_output)

Remove-Item -LiteralPath $probeRoot -Recurse -Force
[pscustomobject]@{results=$results;probe_root_removed=!(Test-Path -LiteralPath $probeRoot)} | ConvertTo-Json -Depth 8
"""

    def build_probe(self) -> str:
        import base64
        from backend import ps_quote
        pipeline_block = block(PIPELINE.read_text(encoding='utf-8'), PIPELINE_BLOCK_START, PIPELINE_BLOCK_END)
        job_block = block(JOB.read_text(encoding='utf-8'), JOB_BLOCK_START, JOB_BLOCK_END)
        # the capture is a three-line block: the path, then the name, then the hash
        lines = JOB.read_text(encoding='utf-8').splitlines()
        first = next(index for index, line in enumerate(lines) if line.strip().startswith(INPUT_CAPTURE))
        capture_lines = []
        for line in lines[first:first + 4]:
            if not line.strip().startswith('$macroInput'):
                break
            capture_lines.append(line.strip())
        capture = '\n'.join(capture_lines)
        remote = self.backend.remote_root
        return (self.TEMPLATE
                .replace('@@PROBE_ROOT@@', ps_quote(remote + '\\runs\\' + self.RUN_ID))
                .replace('@@REMOTE_ROOT@@', ps_quote(remote))
                .replace('@@PIPELINE_BLOCK@@', pipeline_block)
                .replace('@@JOB_BLOCK@@', job_block)
                .replace('@@MANIFEST_B64@@', base64.b64encode(
                    (ROOT / 'workflows' / 'static-external-flow-2502' / 'manifest.json').read_bytes()).decode())
                .replace('@@CAPTURE@@', capture))

    def setUp(self):
        from backend import Backend
        self.backend = Backend()

    def test_the_four_measured_cases(self):
        out = self.backend.ps(self.build_probe())
        results = out['results']
        self.assertEqual(['external_flow_solve.json', 'external_flow_solve_history.csv', 'stage-solve.json'],
                         results['pipeline_resolved'])
        self.assertEqual(3, results['pipeline_hashes_are_64_hex'])
        self.assertIn('external_flow_solve_history.csv', results['staged'])
        self.assertIn('stage-solve.json', results['staged'])
        self.assertEqual([True, True, True], results['staged_verified'])
        self.assertIn('Inherited input missing', results['missing'])
        self.assertIn('did not succeed', results['failed_source'])
        self.assertIn('hash mismatch', results['tampered'])
        self.assertIn('is not produced by the source stage', results['not_produced'])
        self.assertIn('would overwrite', results['collision'])
        self.assertEqual('input.sim', results['macro_input'])
        self.assertEqual('external_flow_solved.sim', results['macro_output'])
        self.assertTrue(results['input_is_not_the_output'])
        self.assertRegex(results['macro_input_sha'], r'^[0-9a-f]{64}$')
        self.assertRegex(results['macro_output_sha'], r'^[0-9a-f]{64}$')
        self.assertNotEqual(results['macro_input_sha'], results['macro_output_sha'])
        self.assertTrue(out['probe_root_removed'], 'the probe directory must not be left behind')


if __name__ == '__main__':
    unittest.main(verbosity=2)
