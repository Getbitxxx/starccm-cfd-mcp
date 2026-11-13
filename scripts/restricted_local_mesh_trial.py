"""Controller for fixed scoped local-mesh and physical-inventory trials; not a production MCP API."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from backend import Backend, ps_quote

ROOT = r'C:\CodexCFDRestricted'
SOURCES = {
    'physics': ('AuditPhysicalReadiness.java', 'trial_physical_readiness.ps1',
                'ddf102ed31450f9a71c6793a0b6acabe886f1325901d40a7090ec8f2e9bbf9be'),
    'configure': ('ConfigureAuditedLocalMesh.java', 'trial_local_mesh.ps1',
                  '79196e6298c12fcf11861c1c4f92d38f1c57d37cebc296b975c883df8aa45c10'),
    'readback': ('AuditLocalMeshConfiguration.java', 'trial_local_mesh_readback.ps1',
                 '8bf99dc5634635123cc8a00ec5b28c3c4ed38a5dbbbd37a154aa8fbcb77c04ab'),
    'initialize': ('AuditLocalMeshConfiguration.java', 'trial_initialize_local_mesh.ps1',
                   '8bf99dc5634635123cc8a00ec5b28c3c4ed38a5dbbbd37a154aa8fbcb77c04ab'),
    'generate': ('AuditLocalMeshConfiguration.java', 'trial_generate_local_mesh.ps1',
                 '8bf99dc5634635123cc8a00ec5b28c3c4ed38a5dbbbd37a154aa8fbcb77c04ab'),
    'rename': ('RenameLocalMeshFixture.java', 'trial_rename_local_mesh.ps1',
               '4b08e509104a08558d852572670a85fea4869c0ebaad9df162ff8e77931e1d7d'),
    'nearwall': ('AuditNativePrismCoverage.java', 'trial_native_prism_coverage.ps1',
                 '305b263cc86c14976b1dcde42f052ca782a148e0639ab29a969fc7c5b66f52cb'),
    'distance': ('AuditBadCellSurfaceDistances.java', 'trial_surface_distances.ps1',
                 '5b15a4c79897f41abec06fdf5ac365881b261cc962def268df4de6030edfa6a2'),
    'bounds': ('AuditBadMeshCellBounds.java', 'trial_bad_mesh_bounds.ps1',
               '64e59a00ade8ddacd1f67fa0d00acc7da311064eb153c4400025ddc202c29f5d'),
}

def start_trial(b: Backend, trial_id: str, mode: str, source: str,
                source_sha256: str, properties: Path, evidence: Path) -> dict:
    if not re.fullmatch(r'[a-z0-9-]{8,64}', trial_id):
        raise ValueError('Invalid trial id')
    if mode not in SOURCES or not re.fullmatch(r'[a-f0-9]{64}', source_sha256):
        raise ValueError('Invalid mode/hash')
    library = b.remote_root + '\\library\\'
    if not (re.fullmatch(re.escape(library) + r'[a-f0-9]{64}\.sim', source) or
            re.fullmatch(re.escape(ROOT) + r'\\[a-z0-9-]{8,64}\\local_mesh_(configured|renamed|generated|initialized)\.sim', source)):
        raise ValueError('Source must be a registered immutable input or scoped configured output')
    if source.startswith(library) and source != library + source_sha256 + '.sim':
        raise ValueError('Content-addressed input filename does not match requested hash')
    macro, runner, digest = SOURCES[mode]
    path = Path('macros') / macro
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('Source differs from explicitly reviewed hash; compile/review before changing registry')
    if mode == 'physics' and hashlib.sha256((Path('scripts/security')/runner).read_bytes()).hexdigest() != '300330247016cbd05ac690b05586dd1a1c8a12060ff8019842e6f6a43023998a':
        raise ValueError('Physical-inventory runner differs from reviewed hash')
    extra = {}
    if mode == 'generate':
        extra = {'GenerateAuditedLocalMesh.java': 'e6678da01bda0c1a93c5e15b7033c104d048fd15a63ca7e26114e3e2d5b20425',
                 'AuditMrfVolumeMeshQuality.java': '12395d8b4a523ebd8f19dea9034123f1d4ece215b7e1b99c7d6d66cf97856bd4'}
        for name, expected in extra.items():
            if hashlib.sha256((Path('macros')/name).read_bytes()).hexdigest() != expected:
                raise ValueError('Extra trial macro differs from reviewed hash')
    if mode == 'initialize':
        proof=json.loads((evidence/'clearance-repair-summary.json').read_text())
        if (source_sha256 != '811587d6ff26936cd8aa10e55505877ea354852bf02db31d746fe82351fce803'
                or proof['trial']['output_sha256'] != source_sha256
                or proof['preinit_quality']['passed'] is not True):
            raise ValueError('Initializer requires this assessed fixed mesh')
        extra={'InitializeAuditedLocalMesh.java':'e368c481fda46e5fac3be0b6347b666cf1bf01c3dc8d8e80621489894e793deb',
               'AuditMrfVolumeMeshQuality.java':'12395d8b4a523ebd8f19dea9034123f1d4ece215b7e1b99c7d6d66cf97856bd4',
               'AuditAutomationPreflight.java':'ea3087f27b792db2ffa65bdcb645f8a3c72d4d72a028ef6a3d3b95a034f9e2d9'}
        for name,expected in extra.items():
            if hashlib.sha256((Path('macros')/name).read_bytes()).hexdigest()!=expected:
                raise ValueError('Initialization macro differs from reviewed hash')
    if mode == 'rename':
        extra = {'AuditAutomationPreflight.java':'ea3087f27b792db2ffa65bdcb645f8a3c72d4d72a028ef6a3d3b95a034f9e2d9'}
        for name, expected in extra.items():
            if hashlib.sha256((Path('macros')/name).read_bytes()).hexdigest() != expected:
                raise ValueError('Preflight differs from reviewed hash')
    job = ROOT + '\\' + trial_id
    properties = properties.resolve(strict=True)
    controls_hash = hashlib.sha256(properties.read_bytes()).hexdigest()
    # Inspect all jobs before replacing shared control files or rotating the account password.
    check = b.ps("""[pscustomobject]@{
        tasks=@(Get-ScheduledTask | Where-Object {$_.TaskName -like 'CodexCFD*' -and $_.State -eq 'Running'} | Select-Object -ExpandProperty TaskName);
        star=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^star(ccm|[-.])'} | Select-Object -ExpandProperty ProcessId)
    } | ConvertTo-Json""")
    if check['tasks'] or check['star']:
        raise ValueError('Windows CFD execution slot is occupied')
    # Never reuse a directory containing output: failed trials retain their evidence.
    b.ps(f"""if(Test-Path -LiteralPath {ps_quote(job)}){{throw 'Trial id already exists'}}
    if((Get-FileHash -LiteralPath {ps_quote(source)} -Algorithm SHA256).Hash.ToLowerInvariant() -ne {ps_quote(source_sha256)}){{throw 'Source hash mismatch'}}
    New-Item -ItemType Directory -Path {ps_quote(job)} | Out-Null
    Copy-Item -LiteralPath {ps_quote(source)} -Destination {ps_quote(job+'\\input.sim')}
    """)
    for f in Path('scripts/security').glob('*.ps1'):
        b.scp(f, ROOT + '\\' + f.name)
    b.ps(f"""foreach($name in @({ps_quote(runner)},'provision_restricted_worker.ps1')){{
    $tokens=$null;$errors=$null
    [Management.Automation.Language.Parser]::ParseFile((Join-Path {ps_quote(ROOT)} $name),[ref]$tokens,[ref]$errors) | Out-Null
    if($errors.Count -gt 0){{throw ($errors.Message -join '; ')}}
    }}""")
    b.scp(path, ROOT + '\\' + macro)
    for name in extra:
        b.scp(Path('macros')/name, ROOT+'\\'+name)
    b.scp(properties, job + '\\local_mesh.properties')
    c = json.loads(Path('test-evidence/restricted-worker-20261001/local-mesh-trial-config.json').read_text())
    c.update(job_dir=job, probe_script=ROOT+'\\'+runner, input_sim=job+'\\input.sim',
             source_sha256=source_sha256, audit_macro=ROOT+'\\'+macro,
             macro_sha256=digest, properties_sha256=controls_hash)
    if mode == 'generate':
        c.update(generate_macro=ROOT+'\\GenerateAuditedLocalMesh.java',
                 generate_sha256=extra['GenerateAuditedLocalMesh.java'],
                 quality_macro=ROOT+'\\AuditMrfVolumeMeshQuality.java',
                 quality_sha256=extra['AuditMrfVolumeMeshQuality.java'],
                 logical_cores=8, memory_gb=32, timeout_seconds=900)
    if mode == 'initialize':
        gate=evidence/'clearance-quality-gate.json'
        b.scp(gate,job+'\\preinit-quality-gate.json')
        c.update(generate_macro=ROOT+'\\InitializeAuditedLocalMesh.java',
                 generate_sha256=extra['InitializeAuditedLocalMesh.java'],
                 quality_macro=ROOT+'\\AuditMrfVolumeMeshQuality.java',
                 quality_sha256=extra['AuditMrfVolumeMeshQuality.java'],
                 preflight_macro=ROOT+'\\AuditAutomationPreflight.java',
                 preflight_sha256=extra['AuditAutomationPreflight.java'],
                 preinit_quality_gate=job+'\\preinit-quality-gate.json',
                 preinit_quality_gate_sha256=hashlib.sha256(gate.read_bytes()).hexdigest(),
                 logical_cores=8,memory_gb=32,timeout_seconds=300)
    if mode == 'rename':
        c.update(quality_macro=ROOT+'\\AuditAutomationPreflight.java',
                 quality_sha256=extra['AuditAutomationPreflight.java'])
    evidence.mkdir(parents=True, exist_ok=True)
    config = evidence / (trial_id+'-config.json')
    config.write_text(json.dumps(c, ensure_ascii=False, indent=2))
    remote_config = ROOT + '\\' + trial_id + '-config.json'
    b.scp(config, remote_config)
    result = b.ps('& '+ps_quote(ROOT+'\\provision_restricted_worker.ps1')+
                  ' -ConfigPath '+ps_quote(remote_config))
    return {'trial_id': trial_id, 'mode': mode, 'macro_sha256': digest,
            'source_sha256': source_sha256, 'properties_sha256': controls_hash,
            'job_dir': job, 'provision': result, 'production_executable': False}

def poll_trial(b: Backend, trial_id: str, evidence: Path) -> dict:
    if not re.fullmatch(r'[a-z0-9-]{8,64}', trial_id):
        raise ValueError('Invalid trial id')
    job = ROOT + '\\' + trial_id
    r = b.ps(f"""$p=Join-Path {ps_quote(job)} 'case-probe.json'
    if(Test-Path -LiteralPath $p){{Get-Content -LiteralPath $p -Raw -Encoding UTF8}}else{{
    [pscustomobject]@{{state='PENDING_RESULT';task_state=(Get-ScheduledTask -TaskName CodexCFD-RestrictedProbe).State.ToString();last_result=(Get-ScheduledTaskInfo -TaskName CodexCFD-RestrictedProbe).LastTaskResult}} | ConvertTo-Json}}
    """)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence/(trial_id+'-result.json')).write_text(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get('state') in {'FAILED','CONFIGURED_PENDING_READBACK','LOCAL_MESH_READBACK_SUCCEEDED','GENERATED_PENDING_QUALITY_GATE','FIXTURE_RENAMED','BAD_MESH_BOUNDS_SUCCEEDED','SURFACE_DISTANCES_SUCCEEDED','INITIALIZED_PENDING_QUALITY_GATE','PRISM_COVERAGE_SUCCEEDED','PHYSICAL_READINESS_SUCCEEDED','ROTOR_GEOMETRY_IMPORTED'}:
        files = b.ps(f'@(Get-ChildItem -LiteralPath {ps_quote(job)} -File | Where-Object {{$_.Extension -in ".json",".log",".properties",".tsv",".csv"}} | Select-Object -ExpandProperty Name) | ConvertTo-Json')
        if isinstance(files, str):
            files = [files]
        for name in files or []:
            d = evidence / trial_id; d.mkdir(exist_ok=True)
            b.scp(d/name, job+'\\'+name, download=True)
    return r


IMPORT_MACRO_SHA = '5f99677e37028cab3edb54ac3c82c00ea1263c5a1ce6c09e079ff9d588feba8b'
IMPORT_RUNNER_SHA = '98ca9e6aee2541fc92cd0b24cb774f6ed970ba39a29e27f6fce8e69c05ef6b15'
IMPORT_READER_SHA = 'ea3087f27b792db2ffa65bdcb645f8a3c72d4d72a028ef6a3d3b95a034f9e2d9'


def start_geometry_import_trial(b: Backend, trial_id: str, stl: Path,
                                evidence: Path, review: dict) -> dict:
    """Fixed restricted importer candidate. Review approval is a prerequisite, not compilation."""
    if not re.fullmatch(r'[a-z0-9-]{8,64}', trial_id):
        raise ValueError('Invalid trial id')
    if (review.get('approved') is not True or review.get('independent') is not True
            or review.get('macro_sha256') != IMPORT_MACRO_SHA
            or review.get('runner_sha256') != IMPORT_RUNNER_SHA):
        raise ValueError('Independent scoped import review required')
    for path, expected in [(Path('macros/ImportAuditedRotorSurface.java'), IMPORT_MACRO_SHA),
                           (Path('scripts/security/trial_rotor_geometry_import.ps1'), IMPORT_RUNNER_SHA),
                           (Path('macros/AuditAutomationPreflight.java'), IMPORT_READER_SHA)]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Fixed reviewed import source changed')
    proof=json.loads((evidence/'verified-surface-qa.json').read_text())
    selected=json.loads((evidence/'selected-geometry.json').read_text())
    digest=hashlib.sha256(stl.read_bytes()).hexdigest()
    if (proof['topology_and_volume_passed'] is not True or proof['sha256'] != digest
            or selected['stl_sha256'] != digest):
        raise ValueError('Import input lacks matching geometry consistency proof')
    check=b.ps("""[pscustomobject]@{
      tasks=@(Get-ScheduledTask | Where-Object {$_.TaskName -like 'CodexCFD*' -and $_.State -eq 'Running'} | Select-Object -ExpandProperty TaskName);
      star=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^star(ccm|[-.])'} | Select-Object -ExpandProperty ProcessId)
    } | ConvertTo-Json""")
    if check['tasks'] or check['star']:
        raise ValueError('Windows CFD execution slot is occupied')
    job=ROOT+'\\'+trial_id
    b.ps('if(Test-Path -LiteralPath '+ps_quote(job)+"){throw 'Trial id exists'}; New-Item -ItemType Directory -Path "+ps_quote(job)+' | Out-Null')
    for local in [Path('scripts/security/trial_rotor_geometry_import.ps1'),
                  Path('scripts/security/provision_restricted_worker.ps1'),
                  Path('macros/ImportAuditedRotorSurface.java'),Path('macros/AuditAutomationPreflight.java')]:
        b.scp(local,ROOT+'\\'+local.name)
    b.scp(stl,job+r'\rotor_mm.stl')
    c=json.loads(Path('test-evidence/restricted-worker-20261001/local-mesh-trial-config.json').read_text())
    c.update(job_dir=job,probe_script=ROOT+r'\trial_rotor_geometry_import.ps1',
             audit_macro=ROOT+r'\ImportAuditedRotorSurface.java',macro_sha256=IMPORT_MACRO_SHA,
             preflight_macro=ROOT+r'\AuditAutomationPreflight.java',preflight_sha256=IMPORT_READER_SHA,
             stl_sha256=digest,timeout_seconds=660)
    config=evidence/(trial_id+'-config.json');config.write_text(json.dumps(c,indent=2))
    remote_config=ROOT+'\\'+trial_id+'-config.json';b.scp(config,remote_config)
    b.ps("$tokens=$null;$errors=$null;[Management.Automation.Language.Parser]::ParseFile("+
         ps_quote(c['probe_script'])+",[ref]$tokens,[ref]$errors) | Out-Null; if($errors.Count){throw ($errors.Message -join '; ')}")
    result=b.ps('& '+ps_quote(ROOT+r'\provision_restricted_worker.ps1')+' -ConfigPath '+ps_quote(remote_config))
    return dict(trial_id=trial_id,job_dir=job,macro_sha256=IMPORT_MACRO_SHA,
                runner_sha256=IMPORT_RUNNER_SHA,stl_sha256=digest,provision=result,
                production_executable=False)
