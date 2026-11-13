"""SSH transport and persistent Windows jobs. No arbitrary command/macro tool is exposed."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

PLUGIN = Path(__file__).resolve().parents[1]
RUNTIME_MANIFEST = 'runtime-manifest.txt'


def runtime_source_files(root: Path = PLUGIN) -> list:
    """The declared Windows-runtime file set, read from `<root>/runtime-manifest.txt`.

    Declared, not globbed: see `Backend.runtime_files` for why a glob made "put a file in a directory"
    equal to "change the production runtime". Blank lines and `#` comments are skipped; duplicates and
    missing files are refused here rather than surfacing later as a deployment mismatch.
    """
    manifest = root / RUNTIME_MANIFEST
    if not manifest.is_file():
        raise ValueError('%s is missing; the runtime set must be declared, not inferred' % RUNTIME_MANIFEST)
    declared = [line.strip() for line in manifest.read_text(encoding='utf-8').splitlines()
                if line.strip() and not line.strip().startswith('#')]
    if not declared:
        raise ValueError('%s declares no files; refusing to deploy an empty runtime' % RUNTIME_MANIFEST)
    duplicates = sorted({p for p in declared if declared.count(p) > 1})
    if duplicates:
        raise ValueError('%s lists a path twice: %s' % (RUNTIME_MANIFEST, ', '.join(duplicates)))
    files = [root / rel for rel in declared]
    missing = [rel for rel, path in zip(declared, files) if not path.is_file()]
    if missing:
        raise ValueError('%s declares missing files: %s' % (RUNTIME_MANIFEST, ', '.join(missing)))
    # A declaration must not be able to name anything outside the four runtime categories. Without
    # this, `../outside.java` or `macros/adhoc/exp.java` would be accepted and hashed into the digest:
    # the drift test would notice, but nothing would prevent it, and the point of the manifest is to be
    # a closed list rather than a suggestion. Checked on the RESOLVED path so `..` cannot escape.
    base = root.resolve()
    escaped = []
    for rel, path in zip(declared, files):
        resolved = path.resolve()
        if not resolved.is_relative_to(base):
            escaped.append(rel)
            continue
        inside = resolved.relative_to(base).as_posix()
        if not (inside == 'scripts/resource_contract.py'
                or inside.startswith('macros/') and inside.count('/') == 1
                or inside.startswith('worker/')
                or inside.startswith('workflows/')):
            escaped.append(rel)
    if escaped:
        raise ValueError('%s declares paths outside the runtime categories '
                         '(macros/*, worker/*, workflows/*, scripts/resource_contract.py): %s'
                         % (RUNTIME_MANIFEST, ', '.join(escaped)))
    return files
TERMINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'REJECTED_BUSY'}
WORKFLOW_ID = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')
STAGE_ID = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
FILE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
POSTCHECKS = {'artifacts_v1', 'preflight_v1', 'smoke_100_v1', 'external_domain_v1', 'cylindrical_domain_v1', 'rotor360_v1', 'boundary_values_v1', 'mrf_interfaces_v1', 'mrf_mesh_generated_v1', 'mrf_split_mesh_generated_v1'}

def workflow_catalog(root: Path = PLUGIN / 'workflows') -> dict:
    """Load packaged, allowlisted workflows; fail closed on malformed manifests."""
    catalog = {}
    for path in sorted(root.glob('*/manifest.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        workflow_id = data.get('id')
        if (data.get('schema_version') != 1 or not isinstance(workflow_id, str)
                or not WORKFLOW_ID.fullmatch(workflow_id) or path.parent.name != workflow_id
                or workflow_id in catalog):
            raise ValueError(f'Invalid workflow manifest: {path}')
        stages = data.get('stages')
        if not isinstance(stages, dict) or not stages:
            raise ValueError(f'Workflow has no stages: {workflow_id}')
        for stage_id, stage in stages.items():
            if not STAGE_ID.fullmatch(stage_id) or not isinstance(stage, dict):
                raise ValueError(f'Invalid stage in {workflow_id}')
            for key in ('macros', 'markers', 'required'):
                values = stage.get(key)
                if not isinstance(values, list) or not values or not all(isinstance(x, str) and x for x in values):
                    raise ValueError(f'Invalid {key} in {workflow_id}/{stage_id}')
            if any(not FILE_NAME.fullmatch(x) or not x.endswith('.java') or not (PLUGIN/'macros'/x).is_file() for x in stage['macros']):
                raise ValueError(f'Missing or invalid macro in {workflow_id}/{stage_id}')
            if any(not FILE_NAME.fullmatch(x) for x in stage['required']):
                raise ValueError(f'Invalid required artifact in {workflow_id}/{stage_id}')
            if stage.get('postcheck') not in POSTCHECKS:
                raise ValueError(f'Unsupported postcheck in {workflow_id}/{stage_id}')
            output_sim = stage.get('output_sim')
            if output_sim is not None and (not isinstance(output_sim, str) or
                    not FILE_NAME.fullmatch(output_sim) or not output_sim.endswith('.sim') or
                    output_sim not in stage['required']):
                raise ValueError(f'Invalid output_sim in {workflow_id}/{stage_id}')
        catalog[workflow_id] = data
    if not catalog:
        raise ValueError('No packaged workflows found')
    return catalog

def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"

def valid_id(value: str) -> str:
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{7,63}', value):
        raise ValueError('request_id/job_id must be 8-64 lowercase letters, digits or hyphens')
    return value

def valid_ref(value: str, require_sim: bool = False) -> str:
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or not re.fullmatch(r'[A-Za-z0-9_./-]+', value):
        raise ValueError('Use a relative case/artifact reference returned by this server')
    if str(p) != value or p.parts[0] not in {'jobs', 'library', 'runs'}:
        raise ValueError('Reference must be under jobs/, library/, or runs/')
    if require_sim and p.suffix.lower() != '.sim':
        raise ValueError('Expected a .sim reference')
    return value


# ---------------------------------------------------------------------------
# Stage parameters (R11). A manifest stage may declare the environment its macros read; the formal path
# can now deliver those values. Two rules make it fail closed rather than a way to reconfigure a worker:
#   * only DECLARED names are accepted, so a request cannot invent a parameter for a stage that has none;
#   * process-critical names are refused outright, so a parameter can never redirect the interpreter, the
#     loader, the temp directory or the profile the worker runs under.
# The runner re-validates and applies them only to its own child processes.
PARAMETER_NAME = re.compile(r'[A-Z][A-Z0-9_]{0,63}\Z')
PARAMETER_VALUE = re.compile(r'[\x20-\x7e]{0,255}\Z')
DENIED_PARAMETER_NAMES = frozenset({
    'PATH', 'PATHEXT', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'USERPROFILE',
    'HOMEPATH', 'HOMEDRIVE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMDATA',
    'PSMODULEPATH', 'COMPUTERNAME', 'USERNAME', 'USERDOMAIN', 'LOGONSERVER', 'NUMBER_OF_PROCESSORS',
    'PROCESSOR_ARCHITECTURE', 'PSExecutionPolicyPreference', 'PYTHONPATH', 'PYTHONHOME', 'JAVA_HOME',
})


def validate_stage_parameters(parameters, declared: dict | None = None) -> dict:
    """Normalize a stage's parameters to {NAME: string} or refuse.

    `declared` is the stage manifest's own parameter map ({name: {...}}); when it is supplied, a name the
    stage did not declare is refused. When it is None the caller has no declaration to check against and
    any well-formed name is accepted (only used where the stage itself is the reviewed artifact).
    """
    if parameters is None:
        return {}
    if not isinstance(parameters, dict):
        raise ValueError('parameters must be an object of NAME -> value')
    if len(parameters) > 64:
        raise ValueError('Too many stage parameters')
    normalized = {}
    for name, value in parameters.items():
        if not isinstance(name, str) or not PARAMETER_NAME.fullmatch(name):
            raise ValueError('Invalid parameter name: %r' % (name,))
        if name.upper() in DENIED_PARAMETER_NAMES:
            raise ValueError('Refusing a process-critical parameter name: ' + name)
        if declared is not None and name not in declared:
            raise ValueError('Stage does not declare the parameter %s; declared: %s'
                             % (name, ', '.join(sorted(declared)) or 'none'))
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError('Parameter %s must be a string or a finite number' % name)
        text = str(value)
        if not PARAMETER_VALUE.fullmatch(text):
            raise ValueError('Parameter %s must be printable ASCII and at most 255 characters' % name)
        normalized[name] = text
    return normalized


def stage_parameter_declarations(workflow_id: str, stage: str) -> dict:
    """The environment names a manifest stage declares, from the live manifest."""
    spec = workflow_catalog()[workflow_id]['stages'][stage]
    declared = (spec.get('parameters') or {}).get('env') or []
    return {item['name']: item for item in declared if isinstance(item, dict) and isinstance(item.get('name'), str)}


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def build_runtime_manifest(files, root: Path = PLUGIN) -> dict:
    """Describe the exact Windows layout while preserving the established full digest algorithm."""
    entries = []
    digest = hashlib.sha256()
    targets = set()
    for path in sorted(files):
        source = path.relative_to(root).as_posix()
        target = source.removeprefix('worker/') if source.startswith('worker/') else source
        if target.casefold() in targets:
            raise ValueError('Duplicate Windows runtime path: ' + target)
        targets.add(target.casefold())
        data = path.read_bytes()
        digest.update(source.encode('utf-8')); digest.update(data)
        entries.append({'source': source, 'path': target, 'sha256': hashlib.sha256(data).hexdigest()})
    return {'schema_version': 1, 'runtime_digest': digest.hexdigest(), 'files': entries}

def runtime_manifest_bytes(manifest: dict) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode('utf-8')

HEX64 = re.compile(r'[a-f0-9]{64}\Z')

def case_ref_digest(case_ref: str) -> str:
    """The content address of an immutable library input. The file name IS the digest."""
    ref = valid_ref(case_ref, require_sim=True)
    match = re.fullmatch(r'library/([a-f0-9]{64})\.sim', ref)
    if match is None:
        raise ValueError('case_ref must be a content-addressed library/<sha256>.sim reference')
    return match.group(1)

# Which review-record field pins which runner. The two layers are DIFFERENT FILES and each one verifies
# its OWN bytes on Windows: worker/run_pipeline.ps1 hashes $PSCommandPath against `pipeline_runner_sha256`,
# and the worker/run_job.ps1 it launches hashes $PSCommandPath against `runner_sha256`.
#
# The defect this fixes (R11, located by the R12 review of the formal path): submit_run pinned
# run_pipeline.ps1's sha256 into the request field `runner_sha256`; run_pipeline.ps1 forwarded that value
# verbatim into every stage request; and run_job.ps1 compared it with the hash of its own file. The two
# scripts have different bytes, so EVERY formal stage was refused before STAR started. The fix is not to
# delete a comparison and not to substitute a live, unreviewed hash: it is to bind each layer to its own
# reviewed hash. Both are compared on macOS against the record and again on Windows against the real file.
RUNNER_REVIEW_FIELDS = {'run_job.ps1': 'runner_sha256',
                        'run_pipeline.ps1': 'pipeline_runner_sha256'}


def load_review_record(review_ref: str, workflow_id: str, stage: str, case_ref: str,
                       runner_names, macro_names: list[str]) -> dict:
    """Executable form of the project rule that every STAR trial needs a matching review record.

    A stage may only start when an approved, independent review record still agrees with the
    pipeline manifest, the controller, every runner LAYER that will execute it, every macro the stage
    runs, and the input's content address. Nothing here is documentary: any drift raises and the job
    never starts.

    `runner_names` is the layer list this call path really uses - 'run_job.ps1' for submit_job, and
    both 'run_pipeline.ps1' and 'run_job.ps1' for submit_run. Each entry is pinned by its own field
    (RUNNER_REVIEW_FIELDS); a record that pins only one layer cannot authorise a path that runs two.
    """
    if isinstance(runner_names, str):
        runner_names = [runner_names]
    if not isinstance(review_ref, str) or not review_ref.strip():
        raise ValueError('A review record is required before any STAR stage may start: '
                         'pass review_ref (see review/README.md)')
    path = Path(review_ref).expanduser()
    if not path.is_absolute():
        path = PLUGIN / path
    if not path.is_file():
        raise ValueError('Review record not found: ' + str(path))
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('approved') is not True or data.get('independent') is not True:
        raise ValueError('Review record is not an approved, independent review: ' + str(path))
    if data.get('workflow_id') not in (None, workflow_id) or data.get('stage') not in (None, stage):
        raise ValueError('Review record is for a different workflow/stage: ' + str(path))
    actual = {'controller_sha256': sha256(PLUGIN / 'scripts' / 'backend.py'),
              'pipeline_sha256': sha256(PLUGIN / 'workflows' / workflow_id / 'manifest.json')}
    # One field per runner layer, each verified against the real file. An unknown layer name is a hard
    # error rather than a silently unchecked runner: a layer nobody pinned must not execute.
    for name in runner_names:
        field = RUNNER_REVIEW_FIELDS.get(name)
        if field is None:
            raise ValueError('Unknown runner layer: %r (see backend.RUNNER_REVIEW_FIELDS)' % (name,))
        digest = sha256(PLUGIN / 'worker' / name)
        pinned = data.get(field)
        if not isinstance(pinned, str) or HEX64.fullmatch(pinned) is None:
            raise ValueError('Review record lacks a usable ' + field)
        if pinned != digest:
            raise ValueError('Reviewed %s differs from the current artifact' % field)
        actual[field] = digest
    digest = actual['controller_sha256']
    pinned = data.get('controller_sha256')
    if not isinstance(pinned, str) or HEX64.fullmatch(pinned) is None:
        raise ValueError('Review record lacks a usable controller_sha256')
    if pinned != digest:
        raise ValueError('Reviewed controller_sha256 differs from the current artifact')
    runtime = build_runtime_manifest(Backend.runtime_files(None))
    runtime_pins = {'runtime_digest': runtime['runtime_digest'],
                    'runtime_manifest_sha256': hashlib.sha256(runtime_manifest_bytes(runtime)).hexdigest(),
                    'runtime_guard_sha256': sha256(PLUGIN/'worker/runtime_guard.ps1'),
                    'pipeline_runner_sha256': sha256(PLUGIN/'worker/run_pipeline.ps1')}
    for field, value in runtime_pins.items():
        if data.get(field) != value:
            raise ValueError('Review must pin current ' + field)
    actual.update(runtime_pins)
    # A PLAN may span more than one workflow (the scenario-1 chain needs static-external-flow-2502 AND
    # external-domain-2502 for its domain stage). One scalar `pipeline_sha256` cannot equal two different
    # manifests, so such a plan could never be authorised at all - measured in R12 with an otherwise perfect
    # review record: submit_run refused with "Reviewed pipeline_sha256 differs", pointing at a gate problem
    # rather than at the plan. `pipeline_hashes` pins one manifest per workflow of the plan, and the check
    # stays per-workflow and fail-closed: the entry for THIS stage's workflow must exist and must match.
    # The scalar field keeps working for single-workflow records, so nothing already signed breaks.
    pinned_pipelines = data.get('pipeline_hashes')
    if pinned_pipelines is not None:
        if not isinstance(pinned_pipelines, dict) or not pinned_pipelines:
            raise ValueError('Review record has an unusable pipeline_hashes (expected workflow_id -> sha256)')
        recorded = pinned_pipelines.get(workflow_id)
        if not isinstance(recorded, str) or HEX64.fullmatch(recorded) is None:
            raise ValueError('Review record does not pin pipeline_hashes[%s]' % workflow_id)
        if recorded != actual['pipeline_sha256']:
            raise ValueError('Reviewed pipeline_hashes[%s] differs from the current artifact' % workflow_id)
    else:
        pinned = data.get('pipeline_sha256')
        if not isinstance(pinned, str) or HEX64.fullmatch(pinned) is None:
            raise ValueError('Review record lacks a usable pipeline_sha256')
        if pinned != actual['pipeline_sha256']:
            raise ValueError('Reviewed pipeline_sha256 differs from the current artifact')
    pinned_macros = data.get('macro_hashes')
    if not isinstance(pinned_macros, dict) or not pinned_macros:
        raise ValueError('Review record lacks macro_hashes')
    macro_hashes = {}
    for name in macro_names:
        digest = sha256(PLUGIN / 'macros' / name)
        if pinned_macros.get(name) != digest:
            raise ValueError('Reviewed macro hash differs or is absent: ' + name)
        macro_hashes[name] = digest
    pinned_input = data.get('input_hashes')
    if not isinstance(pinned_input, dict) or pinned_input.get('input.sim') != case_ref_digest(case_ref):
        raise ValueError('Reviewed input hash differs from case_ref: ' + case_ref)
    from production_catalog import verify_formal_review_record
    verify_formal_review_record(data, PLUGIN)
    return {'path': str(path.relative_to(PLUGIN)) if path.is_relative_to(PLUGIN) else str(path),
            'sha256': sha256(path), 'macro_hashes': macro_hashes,
            'runner_sha256': actual.get('runner_sha256'),
            'pipeline_runner_sha256': actual.get('pipeline_runner_sha256'),
            'input_sha256': case_ref_digest(case_ref),
            'resource_contract_sha256': data.get('resource_contract_sha256'), **runtime_pins}

def check_resource_review(review):
    if review.get('resource_contract_sha256') != sha256(PLUGIN / 'scripts' / 'resource_contract.py'):
        raise ValueError('Review must pin current resource_contract_sha256')


class Backend:
    def __init__(self, config_file: str | None = None):
        self.config_path = Path(config_file or os.environ.get('STARCCM_CONFIG', PLUGIN / 'connection.json')).resolve()
        self.config = json.loads(self.config_path.read_text())
        self.remote_root = self.config['remote_root'].rstrip('\\/')
        if not re.fullmatch(r'[A-Za-z]:[\\/][A-Za-z0-9_./\\-]+', self.remote_root):
            raise ValueError('v0.1 remote_root must be an absolute Windows path without spaces')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', self.config['host']) or not re.fullmatch(r'[A-Za-z0-9_.-]+', self.config['user']):
            raise ValueError('Invalid SSH host/user')
        self.target = self.config['user'] + '@' + self.config['host']
        self.options = ['-i', str(Path(self.config['identity_file']).expanduser().resolve()),
                        '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
                        '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10']

    def remote_path(self, ref: str) -> str:
        return self.remote_root + '\\' + valid_ref(ref).replace('/', '\\')

    def ps(self, script: str, timeout: int = 40):
        preamble = "$ErrorActionPreference='Stop'\n$ProgressPreference='SilentlyContinue'\n[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding\n"
        body = preamble + script
        if len(body.encode('utf-8')) > 3000:
            # Large stdin payloads can leave Windows OpenSSH/PowerShell waiting for EOF.
            # Transfer an exact UTF-8 script and invoke it by a short, bounded command.
            remote_dir = self.remote_root + '\\transport'
            self.ps('New-Item -ItemType Directory -Path '+ps_quote(remote_dir)+' -Force | Out-Null')
            remote_script = remote_dir + '\\' + uuid.uuid4().hex + '.ps1'
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8-sig', suffix='.ps1', delete=False) as stream:
                stream.write(body)
                local_script = Path(stream.name)
            try:
                self.scp(local_script, remote_script)
                result = subprocess.run(['ssh', *self.options, self.target,
                                         'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File '+remote_script],
                                        capture_output=True, timeout=timeout)
            finally:
                local_script.unlink(missing_ok=True)
                try:
                    self.ps('Remove-Item -LiteralPath '+ps_quote(remote_script)+' -Force -ErrorAction SilentlyContinue')
                except Exception:
                    pass
            output = result.stdout.decode('utf-8', errors='replace').strip().lstrip('\ufeff')
            if result.returncode:
                raise RuntimeError('Windows command failed: '+result.stderr.decode('utf-8', errors='replace')[-4000:])
            return json.loads(output) if output else None
        # Pass small scripts directly. Windows OpenSSH can keep stdin open for the
        # bootstrap reader, which makes otherwise short PowerShell calls hang.
        encoded = base64.b64encode(body.encode('utf-16le')).decode()
        result = subprocess.run(['ssh', *self.options, self.target,
                                 'powershell.exe -NoProfile -NonInteractive -EncodedCommand '+encoded],
                                capture_output=True, timeout=timeout)
        output = result.stdout.decode('utf-8', errors='replace').strip().lstrip('\ufeff')
        if result.returncode:
            raise RuntimeError('Windows command failed: '+result.stderr.decode('utf-8', errors='replace')[-4000:])
        return json.loads(output) if output else None

    def scp(self, local: Path, remote: str, download: bool = False):
        remote_arg = self.target+':'+remote.replace('\\', '/')
        args = [remote_arg, str(local)] if download else [str(local), remote_arg]
        # Timeout raised from 240 s after a 1.4 GB transient checkpoint upload was truncated mid-transfer,
        # leaving a job directory whose only content was a short copy of the input and no solver log. Every
        # earlier input was 261-718 MB and fitted; the measured download rate is about 10 MB/s, so 1.4 GB needs
        # at least 140 s and any stall exceeded the old bound. Truncation is caught by the launcher's remote
        # hash verification, so this raises the ceiling rather than weakening a check.
        r = subprocess.run(['scp', *self.options, *args], capture_output=True, timeout=1800)
        if r.returncode:
            raise RuntimeError('SCP failed: '+r.stderr.decode('utf-8', errors='replace')[-2000:])

    def runtime_files(self):
        """The Windows runtime is exactly what `runtime-manifest.txt` declares.

        It used to be a glob (`macros/*.java`, `worker/*.ps1`, `workflows/*/manifest.json`,
        `scripts/resource_contract.py`). That made "drop a file in a directory" equal to "change the
        production runtime": on 2026-11-03 twenty-four experiment macros landed in `macros/`, the
        digest moved from 3f5aeec8 to 6e3f0a79, the deployment approval stopped matching, and the formal
        path refused every submission - with no deployment decision by anyone.

        Reading a declared list inverts that: adding a macro to `macros/` no longer changes the
        runtime, and adding an entry to the manifest is a visible claim that must be accompanied by a
        deployment review. `tests/test_runtime_manifest.py` keeps the declaration and the directory in
        agreement, so an unlisted file is a red test rather than a mystery outage.
        """
        workflow_catalog()
        return sorted(runtime_source_files(PLUGIN))

    def runtime_manifest(self):
        return build_runtime_manifest(self.runtime_files())

    def runtime_digest(self):
        return self.runtime_manifest()['runtime_digest']

    def deploy(self):
        from formal_worker import deploy
        return deploy(self)

    def health(self):
        from formal_worker import health
        return health(self)

    def list_workflows(self):
        return [{'id': key, 'title': value.get('title'), 'description': value.get('description'),
                 'input': value.get('input'), 'validated_on': value.get('validated_on'),
                 'stages': [{'id': stage, 'description': spec.get('description'),
                             'macros': list(spec.get('macros') or []),
                             'macro_outputs': list(spec.get('macro_outputs')) if spec.get('macro_outputs') else None,
                             'output_sim': spec.get('output_sim'),
                             'required': list(spec.get('required') or []),
                             'parameters': spec.get('parameters')}
                            for stage, spec in value['stages'].items()]}
                for key, value in workflow_catalog().items()]

    def upload_case(self, local_path: str):
        path = Path(local_path).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != '.sim':
            raise ValueError('Upload requires an existing local .sim file')
        digest = sha256(path)
        ref = f'library/{digest}.sim'
        remote = self.remote_path(ref)
        exists = self.ps(f"if(Test-Path -LiteralPath {ps_quote(remote)}){{(Get-FileHash -LiteralPath {ps_quote(remote)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json}}else{{'missing' | ConvertTo-Json}}")
        if exists != digest:
            # Unique temporary name avoids partially overwriting a library artifact.
            import uuid
            temp = remote+'.'+uuid.uuid4().hex+'.upload'
            self.scp(path, temp)
            self.ps(f"$h=(Get-FileHash -LiteralPath {ps_quote(temp)} -Algorithm SHA256).Hash.ToLowerInvariant();if($h -ne {ps_quote(digest)}){{throw 'Upload hash mismatch'}};Move-Item -LiteralPath {ps_quote(temp)} -Destination {ps_quote(remote)} -Force")
        return {'case_ref': ref, 'sha256': digest, 'bytes': path.stat().st_size}

    def register_case(self, local_path: str | None = None, windows_path: str | None = None):
        """Register an immutable .sim copy from Mac or an allowed Windows directory."""
        if (local_path is None) == (windows_path is None):
            raise ValueError('Provide exactly one of local_path or windows_path')
        if local_path is not None:
            return self.upload_case(local_path)
        if not windows_path.lower().endswith('.sim') or not re.fullmatch(r'[A-Za-z]:[\\/][^*?"<>|\r\n]+', windows_path):
            raise ValueError('Expected an absolute Windows .sim path')
        roots = self.config.get('windows_case_roots', [])
        if not roots:
            raise ValueError('No Windows case roots configured')
        roots64 = base64.b64encode(json.dumps(roots).encode()).decode()
        library_path = self.remote_root+'\\library'
        return self.ps(f"""
$source=(Resolve-Path -LiteralPath {ps_quote(windows_path)}).Path
$allowed=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({ps_quote(roots64)})) | ConvertFrom-Json
$ok=$false
foreach($item in $allowed){{
 $root=[IO.Path]::GetFullPath($item).TrimEnd([char]92)+[char]92
 if($source.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){{$ok=$true;break}}
}}
if(!$ok){{throw 'Source is outside configured Windows case roots'}}
if([IO.Path]::GetExtension($source) -ine '.sim'){{throw 'Only .sim files are accepted'}}
$hash=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
$library={ps_quote(library_path)}
New-Item -ItemType Directory -Path $library -Force | Out-Null
$destination=Join-Path $library ($hash+'.sim')
if(!(Test-Path -LiteralPath $destination)){{
 $temp=$destination+'.'+[guid]::NewGuid().ToString('N')+'.copy'
 Copy-Item -LiteralPath $source -Destination $temp
 if((Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash){{Remove-Item -LiteralPath $temp;throw 'Copy hash mismatch'}}
 Move-Item -LiteralPath $temp -Destination $destination
}}
if((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash){{throw 'Library hash mismatch'}}
[pscustomobject]@{{case_ref='library/'+$hash+'.sim';sha256=$hash;bytes=(Get-Item -LiteralPath $destination).Length}} | ConvertTo-Json
""", timeout=120)

    def inspect_case(self, case_ref: str, request_id: str, wait_seconds: int = 90,
                     review_ref: str | None = None):
        """Run the generic audit; return structured evidence or a durable pending ID."""
        if not isinstance(wait_seconds, int) or not 0 <= wait_seconds <= 120:
            raise ValueError('wait_seconds must be 0-120')
        self.submit_job('preflight', case_ref, request_id, 300, 'core', review_ref=review_ref)
        deadline = time.monotonic() + wait_seconds
        while True:
            job = self.get_job(request_id)
            state = job['status']['state']
            if state in TERMINAL:
                if state != 'SUCCEEDED':
                    return {'job_id': request_id, 'state': state, 'error': job['status'].get('error')}
                output = self.remote_path('jobs/'+request_id+'/automation_preflight.json')
                audit = self.ps(f'Get-Content -LiteralPath {ps_quote(output)} -Raw -Encoding UTF8')
                return {'job_id': request_id, 'state': state, 'case_ref': case_ref, 'audit': audit,
                        'source_sha256': job['status'].get('source_sha256')}
            if time.monotonic() >= deadline:
                return {'job_id': request_id, 'state': state, 'case_ref': case_ref}
            time.sleep(min(3, max(0, deadline - time.monotonic())))

    def _trusted_preflight(self, case_ref: str, inspect_job_id: str):
        job = self.get_job(valid_id(inspect_job_id))
        status = job['status']
        if (status['state'] != 'SUCCEEDED' or status.get('workflow_id') != 'core'
                or status.get('stage') != 'preflight'):
            raise ValueError('inspect_job_id must refer to a successful core preflight')
        source = self.remote_path(valid_ref(case_ref, require_sim=True))
        current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
        if not re.fullmatch(r'[a-f0-9]{64}', str(current_hash)) or current_hash != status.get('source_sha256'):
            raise ValueError('CaseSpec case_ref does not match the inspected input hash')
        output = self.remote_path('jobs/'+inspect_job_id+'/automation_preflight.json')
        return self.ps(f'Get-Content -LiteralPath {ps_quote(output)} -Raw -Encoding UTF8'), status

    def classify_case_parts(self, case_ref: str, inspect_job_id: str,
                            geometry: dict | None = None, model_length_unit: str | None = None):
        from part_classification import classify_parts
        audit, status = self._trusted_preflight(case_ref, inspect_job_id)
        result = classify_parts(audit, geometry, model_length_unit)
        result.update(case_ref=case_ref, inspect_job_id=inspect_job_id,
                      source_sha256=status['source_sha256'])
        return result

    def prepare_local_mesh_plan(self, case_ref: str, inspect_job_id: str, controls: dict):
        """Read-only planning. A valid configuration never grants macro execution."""
        from local_mesh_plan import prepare_local_mesh_plan
        audit, status = self._trusted_preflight(case_ref, inspect_job_id)
        result = prepare_local_mesh_plan(audit, controls)
        result.update(case_ref=case_ref, inspect_job_id=inspect_job_id,
                      source_sha256=status['source_sha256'])
        return result

    def validate_plan(self, case_spec: dict, inspect_job_id: str | None = None, case: str | None = None,
                      plan_operations: list[str] | None = None):
        """Plan validation. Availability comes from REAL call paths, never from a catalog id.

        `installed_operations` is deliberately NOT passed: casespec.validate_case_spec then resolves every
        entry against the live workflow catalog and the files on disk, refuses to let a sample-specific
        capability satisfy a generic requirement, and reports implementation / verified-on-this-case /
        general-acceptance / engineering as four separate levels.

        `plan_operations` is the execution plan that would be submitted. When it is given, the
        executability conclusion is judged on THAT plan (one run mechanism, pre/post steps included only
        where the mechanism's code really invokes them); when it is not, a mechanism counts only if a
        single operation of it covers everything. The union of two mechanisms' capabilities is never an
        executable path. Read-only: nothing is launched.
        """
        from casespec import validate_case_spec
        from part_classification import classify_parts
        audit = None
        if inspect_job_id is not None:
            case_ref = case_spec.get('case_ref', '') if isinstance(case_spec, dict) else ''
            audit, _ = self._trusted_preflight(case_ref, inspect_job_id)
        if case is None and isinstance(case_spec, dict):
            case = case_spec.get('case') if isinstance(case_spec.get('case'), str) else None
        result = validate_case_spec(case_spec, None, audit, case=case,
                                    plan_operations=plan_operations).as_dict()
        result['capability_note'] = ('implementation / verified_on_current_case / general_acceptance / '
                                     'engineering are separate levels and are never collapsed into '
                                     '`executable`; engineering is never claimed by wiring an operation')
        result['inspect_job_id'] = inspect_job_id
        if audit is not None and isinstance(case_spec, dict):
            try:
                classification = classify_parts(audit, case_spec.get('geometry'), case_spec.get('model_length_unit'))
                result['part_classification'] = classification
                result['errors'].extend(classification['errors'])
            except ValueError as exc:
                result['errors'].append(str(exc))
            if result['errors']:
                result['valid'] = result['executable'] = False
        return result

    def resolve_operations(self, operation_ids: list[str] | None = None, case: str | None = None):
        """Resolve declared operations to real call paths, parameters and (where possible) a run graph.

        Read-only. Returns, per operation: the mechanism, the call path, the parameters it needs, its
        inputs/outputs/scope, its evidence, and the four capability levels kept apart. For operations that
        the FORMAL path can submit it also returns the exact step object `submit_run` would accept;
        operations that belong to the restricted-trial mechanism return their ready-to-run command instead,
        because that reviewed entry - not this path - is their executor.
        """
        from casespec import resolve_operations
        registry = resolve_operations(operation_ids, case=case)
        plan, refused = [], []
        # The order a formal run wants for the operations it was asked about: pre steps, then the STAR
        # stages, then the post steps. It is what makes the reproducible command derivable instead of
        # hand-assembled, and it is the same list validate_plan judges coverage on.
        ordered = {'pre': [], 'stage': [], 'post': []}
        for op_id, entry in sorted((registry.get('operations') or {}).items()):
            step = None
            if entry.get('mechanism') == 'workflow_stage' and entry.get('implementation') == 'RESOLVED':
                path = entry['call_path']
                step = {'key': op_id, 'workflow_id': path['workflow_id'], 'stage': path['stage'],
                        'parameters': {item['name']: item.get('verified_value')
                                       for item in ((entry.get('parameters') or {}).get('env') or [])
                                       if isinstance(item, dict) and item.get('name')
                                       and item.get('verified_value') is not None},
                        'required_artifacts': path['required'], 'output_sim': path['output_sim']}
                plan.append(step)
                ordered['stage'].append(op_id)
            elif (entry.get('mechanism') == 'offline_tool'
                  and entry.get('implementation') == 'RESOLVED'
                  and any(item.get('mechanism') == 'workflow_stage'
                          for item in (entry.get('invoked_by') or []))):
                ordered[entry.get('plan_role') or 'stage'].append(op_id)
            entry = dict(entry)
            entry['submit_step'] = step
            entry['run_command'] = (entry.get('call_path') or {}).get('command')
            registry['operations'][op_id] = entry
            if entry.get('implementation') != 'RESOLVED':
                refused.append({'id': op_id, 'reason': entry.get('reason')})
        registry['formal_steps'] = plan
        registry['formal_plan'] = ordered['pre'] + ordered['stage'] + ordered['post']
        registry['formal_plan_roles'] = {**{op: 'pre' for op in ordered['pre']},
                                        **{op: 'stage' for op in ordered['stage']},
                                        **{op: 'post' for op in ordered['post']}}
        registry['refused'] = refused
        registry['_note'] = ('submit_step is what the formal path (submit_run) accepts; run_command is the '
                             'reviewed restricted-trial entry for the operations that path owns. A step is '
                             'offered only when the mechanism resolves against the live manifests. '
                             'formal_plan is the same set in the order a formal run submits it: pre steps, '
                             'stages, post steps.')
        return registry

    def submit_run(self, case_ref: str, request_id: str, steps: list[dict] | None = None,
                   timeout_seconds: int = 172800, case_spec: dict | None = None,
                   inspect_job_id: str | None = None, review_ref: str | None = None,
                   operations: list[str] | None = None, parameters: dict | None = None):
        """Schedule an immutable installed-stage graph; full CFD requires a valid executable CaseSpec.

        The graph may be given as explicit `steps` (unchanged) or as `operations` - catalog ids that are
        RESOLVED against the live manifests. Operation mode refuses anything the formal path cannot really
        run (unknown id, an unresolved call path, or a mechanism whose executor is the restricted-trial
        entry) instead of silently dropping it. `parameters` maps an operation id to the stage parameters
        that operation declares; undeclared or process-critical names are refused.
        """
        from casespec import validate_case_spec, resolve_operations, required_operations
        # The plan that is actually submitted. It is what validate_plan judges coverage against, so an
        # offline pre/post step counts only when THIS path really performs it (see below) - never through
        # an `applies_to` declaration.
        plan_operations: list[str] = []
        pre_operations: list[str] = []
        post_operations: list[str] = []
        if operations is not None:
            if steps:
                raise ValueError('Provide either steps or operations, not both')
            registry = resolve_operations(operations)
            steps = []
            for op_id in operations:
                entry = (registry.get('operations') or {}).get(op_id)
                if entry is None:
                    raise ValueError('Unknown operation: %s; call list_operations' % op_id)
                if entry.get('implementation') != 'RESOLVED':
                    raise ValueError('Operation %s does not resolve to a call path: %s'
                                     % (op_id, entry.get('reason')))
                mechanism = entry.get('mechanism')
                if mechanism == 'workflow_stage':
                    path = entry['call_path']
                    steps.append({'key': op_id, 'workflow_id': path['workflow_id'],
                                  'stage': path['stage'],
                                  'parameters': (parameters or {}).get(op_id, {})})
                    plan_operations.append(op_id)
                    continue
                if mechanism == 'offline_tool':
                    # Allowed as an explicit pre/post step, but ONLY with a binding this path really
                    # executes: `invoked_by` is checked against the file and symbol on disk by the
                    # resolver, and the role decides what actually has to happen for the plan to be true.
                    bindings = [item for item in (entry.get('invoked_by') or [])
                                if item.get('mechanism') == 'workflow_stage']
                    if not bindings:
                        raise ValueError(
                            'Operation %s is an offline tool with no verified formal-path invocation '
                            '(%s); a declaration is not an invocation' % (op_id, entry.get('coverage_note')
                                                                          or 'no invoked_by binding'))
                    role = (entry.get('plan_role') or '').strip()
                    if role == 'pre':
                        pre_operations.append(op_id)
                    elif role == 'post':
                        post_operations.append(op_id)
                    else:
                        raise ValueError('Operation %s declares no plan_role, so the formal path cannot '
                                         'say whether it must run before or after the graph' % op_id)
                    plan_operations.append(op_id)
                    continue
                raise ValueError(
                    'Operation %s is served by the %s mechanism, not by this path. Use its reviewed '
                    'entry instead: %s' % (op_id, mechanism,
                                           (entry.get('call_path') or {}).get('command')
                                           or (entry.get('call_path') or {}).get('tool')))
            for op_id, values in ((parameters or {}).items() if isinstance(parameters, dict) else []):
                if op_id not in operations:
                    raise ValueError('parameters names an operation that was not requested: %s' % op_id)
        if not steps:
            raise ValueError('The formal path needs at least one installed stage; the pre/post steps of a '
                             'plan are not an execution graph')
        valid_ref(case_ref, require_sim=True)
        valid_id(request_id)
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 0:
            raise ValueError('timeout_seconds must be a nonnegative integer observation')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
            raise ValueError('steps must contain 1-32 installed stages')
        catalog = workflow_catalog()
        keys = set()
        normalized = []
        for item in steps:
            if not isinstance(item, dict):
                raise ValueError('Each step must be an object')
            if 'env' in item:
                raise ValueError('Submitted steps use parameters; env is runner-internal')
            key, workflow_id, stage = (item.get('key'), item.get('workflow_id'), item.get('stage'))
            if not all(isinstance(value, str) for value in (key, workflow_id, stage)):
                raise ValueError('Each step needs key, workflow_id and stage')
            if not STAGE_ID.fullmatch(key) or key in keys:
                raise ValueError('Step keys must be unique, safe identifiers')
            if workflow_id not in catalog or stage not in catalog[workflow_id]['stages']:
                raise ValueError('Unknown installed workflow/stage')
            stage_timeout = item.get('timeout_seconds', min(timeout_seconds, 900))
            if isinstance(stage_timeout, bool) or not isinstance(stage_timeout, int) or stage_timeout < 0:
                raise ValueError('Invalid stage timeout')
            keys.add(key)
            macros = list(catalog[workflow_id]['stages'][stage]['macros'])
            file_hashes = load_review_record(review_ref, workflow_id, stage, case_ref,
                                             ['run_pipeline.ps1', 'run_job.ps1'], macros)['macro_hashes']
            declared = stage_parameter_declarations(workflow_id, stage)
            env = validate_stage_parameters(item.get('parameters'), declared)
            normalized.append({'key': key, 'workflow_id': workflow_id, 'stage': stage,
                               'timeout_seconds': stage_timeout, 'macro_hashes': file_hashes,
                               'env': env})
        # Steps mode does not name catalog ids, so the selected plan is whatever those (workflow_id, stage)
        # pairs resolve to. Plan coverage is still judged on the real graph, not on the catalog at large.
        if operations is None:
            registry_for_plan = resolve_operations()
            for step in normalized:
                for op_id, op_entry in sorted((registry_for_plan.get('operations') or {}).items()):
                    path = op_entry.get('call_path') or {}
                    if (op_entry.get('implementation') == 'RESOLVED'
                            and op_entry.get('mechanism') == 'workflow_stage'
                            and path.get('workflow_id') == step['workflow_id']
                            and path.get('stage') == step['stage']):
                        plan_operations.append(op_id)
                        break
        from production_catalog import validate_formal_submission
        # Review the caller's typed parameters before the runner's env string conversion.
        scope_algorithm = validate_formal_submission(review_ref, case_ref, steps, case_spec, plan_operations, PLUGIN)
        if scope_algorithm == 'semantic-v1':
            from production_catalog import formal_parameter_environment
            for step, item in zip(normalized, steps):
                step['env'] = formal_parameter_environment(item.get('parameters', {}),
                    stage_parameter_declarations(step['workflow_id'], step['stage']))
        pre_results: dict[str, dict] = {}
        if case_spec is not None:
            assessment = self.validate_plan(case_spec, inspect_job_id, plan_operations=plan_operations)
            if not assessment['valid'] or not assessment['executable']:
                raise ValueError('CaseSpec is incomplete or the SELECTED plan is not executable '
                                 '(plan_blocker: %s); inspect validate_plan with the same plan_operations'
                                 % assessment.get('plan_blocker'))
            if case_spec.get('case_ref') != case_ref:
                raise ValueError('CaseSpec and run case_ref differ')
            registry = resolve_operations()
            # What THIS plan provides is what the gate just resolved as servable, so a member it could not
            # serve is exactly what plan_unresolved_operations names. Counting only the staged steps made a
            # COMPLETE plan look partial: measured in ②R13 by dry-running this very method, where the full
            # 11-operation plan was refused with "Run graph omits required CaseSpec operations:
            # classify_parts, domain_study, export_evidence, mesh_study, report" immediately after
            # validate_plan had called that same plan executable. The offline pre/post members are not
            # declarations: each one is executed by this method (see the pre/post blocks below).
            provided = set(plan_operations) - set(assessment.get('plan_unresolved_operations') or [])
            for step in normalized:
                for op_entry in (registry.get('operations') or {}).values():
                    path = op_entry.get('call_path') or {}
                    if (op_entry.get('implementation') == 'RESOLVED'
                            and path.get('workflow_id') == step['workflow_id']
                            and path.get('stage') == step['stage']):
                        provided.update(op_entry.get('provides') or [])
            omitted = sorted(set(assessment['required_operations']) - provided)
            if omitted:
                raise ValueError('Run graph omits required CaseSpec operations: ' + ', '.join(omitted)
                                 + ' (each selected stage contributes only the capabilities its operation '
                                   'declares in operations/catalog.json)')
        # A pre step is not a declaration: the plan named it, so it is really run here, and the plan gate
        # above was judged with it included. Without a completed preflight the classification cannot be
        # performed at all, and the plan is refused rather than counted.
        if 'classify_parts' in pre_operations:
            if not inspect_job_id:
                raise ValueError('The plan includes classify_parts, but no inspect_job_id was given: the '
                                 'formal path must really run the classification, not declare it')
            classification = self.classify_case_parts(case_ref, inspect_job_id,
                                                      (case_spec or {}).get('geometry'),
                                                      (case_spec or {}).get('model_length_unit'))
            if classification.get('errors'):
                raise ValueError('part classification refused the CaseSpec: '
                                 + '; '.join(str(item) for item in classification['errors']))
            pre_results['classify_parts'] = {
                'roles_digest': hashlib.sha256(json.dumps(classification.get('roles') or {},
                                                          sort_keys=True).encode()).hexdigest(),
                'inspect_job_id': inspect_job_id, 'source_sha256': classification.get('source_sha256')}
        requested_cores = 64
        memory_gb = case_spec.get('limits', {}).get('memory_gb', 64) if case_spec else 64
        from resource_contract import make_contract
        resource_contract = make_contract({'memory_gb': memory_gb}, case_spec.get('resource_contract') if case_spec else None)
        requested_cores = resource_contract['logical_cores']
        memory_gb = resource_contract['memory_gb']
        review = load_review_record(review_ref, normalized[0]['workflow_id'], normalized[0]['stage'],
                                    case_ref, ['run_pipeline.ps1', 'run_job.ps1'],
                                    list(catalog[normalized[0]['workflow_id']]['stages'][normalized[0]['stage']]['macros']))
        check_resource_review(review)
        # `steps` keeps macro_hashes and env per step: worker/run_pipeline.ps1 reads both from each item,
        # and hashing the whole structure into request_digest is what makes a parameter change a DIFFERENT
        # request instead of a silently reused one.
        logical = {'case_ref': case_ref, 'steps': normalized, 'timeout_seconds': timeout_seconds,
                   'runtime_digest': self.runtime_digest(),
                   'case_spec_digest': validate_case_spec(case_spec).digest if case_spec else None,
                   'inspect_job_id': inspect_job_id, 'logical_cores': requested_cores,
                   'memory_gb': memory_gb, 'resource_contract': resource_contract,
                   'resource_contract_sha256': review['resource_contract_sha256'], 'review_sha256': review['sha256'],
                   # The plan's offline steps travel INSIDE the digest: a run either performs them or its
                   # request digest differs. `pre` steps have already run (their result digest is here);
                   # `post` steps are what report_run performs after the graph finishes.
                   'plan_operations': list(plan_operations),
                   'pre_operations': pre_results,
                   'post_operations': sorted(post_operations)}
        from formal_worker import request_pins
        logical.update(request_pins(self))
        request_digest = hashlib.sha256(json.dumps(logical, sort_keys=True).encode()).hexdigest()
        run_dir = self.remote_root + '\\runs\\' + request_id
        source = self.remote_path(case_ref)
        runtime = self.remote_root + '\\runtime\\' + logical['runtime_digest'][:16]
        pipeline_path = runtime+'\\run_pipeline.ps1'
        source_root = self.remote_root+'\\'
        pipeline_argument = runtime+'\\run_pipeline.ps1'
        req = dict(logical, run_id=request_id, request_digest=request_digest, case_spec=case_spec,
                   source_path=source, remote_root=self.remote_root,
                   star_launcher=self.config['star_launcher'],
                   # Each runner LAYER is pinned by its own reviewed hash. run_pipeline.ps1 verifies
                   # `pipeline_runner_sha256` against its own bytes and forwards `runner_sha256` (the job
                   # runner's reviewed hash) into every stage request, where run_job.ps1 verifies it
                   # against its own bytes. Passing one hash for both layers is what broke the formal
                   # path: the two files differ, so the stage layer refused every run.
                   runner_sha256=review['runner_sha256'],
                   pipeline_runner_sha256=review['pipeline_runner_sha256'],
                   input_sha256=review['input_sha256'],
                   review_sha256=review['sha256'], review_ref=review['path'])
        payload = base64.b64encode(json.dumps(req).encode()).decode()
        from formal_worker import guarded_submission
        return self.ps(guarded_submission(self, f"""
$run={ps_quote(run_dir)}
$requestFile=Join-Path $run 'request.json'
if(Test-Path -LiteralPath $requestFile){{
 $old=Get-Content -LiteralPath $requestFile -Raw -Encoding UTF8 | ConvertFrom-Json
 if($old.request_digest -ne {ps_quote(request_digest)}){{throw 'request_id already used for different parameters'}}
 [pscustomobject]@{{run_id={ps_quote(request_id)};reused=$true;state='EXISTING'}} | ConvertTo-Json
 exit 0
}}
if(!(Test-Path -LiteralPath {ps_quote(pipeline_path)})){{throw 'Deploy this runtime before submission'}}
$source=(Resolve-Path -LiteralPath {ps_quote(source)}).Path
$root={ps_quote(source_root)}
if(!$source.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){{throw 'Source outside worker root'}}
New-Item -ItemType Directory -Path $run -ErrorAction Stop | Out-Null
$request=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({ps_quote(payload)})) | ConvertFrom-Json
$request | Add-Member -NotePropertyName source_sha256 -NotePropertyValue (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
$request | Add-Member -NotePropertyName submitted_at_utc -NotePropertyValue ([DateTime]::UtcNow.ToString('o'))
$request | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $requestFile -Encoding UTF8
@{{state='PENDING';run_id={ps_quote(request_id)};request_digest={ps_quote(request_digest)};engineering_verified=$false}} | ConvertTo-Json | Set-Content (Join-Path $run 'state.json') -Encoding UTF8
try{{
 $args='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+{ps_quote(pipeline_argument)}+'" -RunDir "'+$run+'" -ExpectedRequestSha256 "'+(Get-FileHash -LiteralPath $requestFile -Algorithm SHA256).Hash.ToLowerInvariant()+'"'
 $action=New-ScheduledTaskAction -Execute "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Argument $args
 $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
 Register-FormalTask -TaskName {ps_quote('CodexCFD-Run-'+request_id)} -Action $action -Settings $settings -ExpectedSid $request.expected_worker_sid
 Start-ScheduledTask -TaskName {ps_quote('CodexCFD-Run-'+request_id)}
 [pscustomobject]@{{run_id={ps_quote(request_id)};reused=$false;state='PENDING'}} | ConvertTo-Json
}}catch{{
 @{{state='FAILED';run_id={ps_quote(request_id)};error=$_.Exception.Message}} | ConvertTo-Json | Set-Content (Join-Path $run 'state.json') -Encoding UTF8
 throw
}}
"""), timeout=120)

    def get_run(self, run_id: str, case: str | None = None):
        valid_id(run_id)
        result = self._get_run_status(run_id)
        # Attribute every step to the operation it came from and keep the three conclusions separate:
        # process (what ran), delivery (what came back), engineering (never claimed here).
        from casespec import resolve_operations
        try:
            registry = resolve_operations(case=case).get('operations') or {}
        except Exception:                                               # noqa: BLE001 - never block a read
            registry = {}
        steps = ((result.get('status') or {}).get('steps') or []) if isinstance(result, dict) else []
        if not steps and isinstance(result, dict):
            completed = (result.get('status') or {}).get('completed') or []
            steps = [{'key': key} for key in completed]
        attributed = []
        for step in steps:
            key = step.get('key') if isinstance(step, dict) else step
            entry = registry.get(key) if isinstance(key, str) else None
            attributed.append({'key': key,
                               'operation': key if entry else None,
                               'workflow_id': (entry or {}).get('call_path', {}).get('workflow_id'),
                               'stage': (entry or {}).get('call_path', {}).get('stage'),
                               'mechanism': (entry or {}).get('mechanism'),
                               'general_acceptance': (entry or {}).get('general_acceptance')})
        if isinstance(result, dict):
            result['operations'] = attributed
            result['engineering_verified'] = False
            status = result.get('status') or {}
            # The plan the run was submitted with, and whether its offline steps have really happened for
            # THIS run: the report entry leaves `report.state.json` in the run directory after it has
            # retrieved and hash-verified the artifacts. A declared post step is never reported as done
            # on the strength of the declaration alone.
            report_recorded = any(str(item.get('ref') or '').endswith('report.state.json')
                                  for item in (result.get('artifacts') or []))
            result['plan'] = {
                'plan_operations': status.get('plan_operations') or [],
                'pre_operations': status.get('pre_operations') or {},
                'post_operations': status.get('post_operations') or [],
                'post_operations_performed': ({'export_evidence': True, 'report': True}
                                              if report_recorded else {}),
                'report_state_recorded': report_recorded,
                'note': ('a pre step is recorded with the digest of what it actually returned; a post step '
                         'is performed by report_run, which leaves report.state.json in the run directory')}
            result['engineering_note'] = ('Execution state is not engineering validity: the CaseSpec '
                                          'thresholds are evaluated by the validation stage and by '
                                          'scripts/air3b_convergence_gate.py, never by this endpoint.')
            result['delivery'] = {'artifacts_listed': len(result.get('artifacts') or []),
                                  'note': ('list of artifacts present in the run directory; whether each one '
                                           'was hash-verified is recorded per stage, and fetch_run_results '
                                           'is what compares transferred bytes against the runner record')}
        return result

    def _get_run_status(self, run_id: str):
        run = self.remote_root + '\\runs\\' + run_id
        return self.ps(f"""
$run={ps_quote(run)}
$state=Get-Content -LiteralPath (Join-Path $run 'state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$artifacts=@(Get-ChildItem -LiteralPath $run -Recurse -File | Where-Object {{$_.Extension -in '.json','.txt','.csv','.log','.sim' -and $_.Name -notin 'request.json','state.tmp.json','input.sim'}} | ForEach-Object {{[pscustomobject]@{{ref='runs/{run_id}/'+$_.FullName.Substring($run.Length+1).Replace([char]92,[char]47);bytes=$_.Length}}}})
$task=Get-ScheduledTask -TaskName {ps_quote('CodexCFD-Run-'+run_id)} -ErrorAction SilentlyContinue
$taskState=if($task){{$task.State.ToString()}}else{{'NOT_REGISTERED'}}
$age=if($state.updated_at){{([DateTime]::UtcNow-[DateTime]::Parse($state.updated_at)).TotalSeconds}}else{{0}}
if($state.state -in 'STARTING','RUNNING' -and $taskState -ne 'Running' -and $age -gt 15){{$state.state='INTERRUPTED';$state.reason='Scheduled task is no longer running; resume from the last verified checkpoint'}}
[pscustomobject]@{{status=$state;artifacts=$artifacts;scheduler_state=$taskState}} | ConvertTo-Json -Depth 12
""")

    def cancel_run(self, run_id: str):
        state = self.get_run(run_id)['status']['state']
        if state in {'EXECUTION_COMPLETED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'RESOURCE_STOP', 'REJECTED_BUSY'}:
            return {'run_id': run_id, 'state': state, 'cancel_requested': False}
        path = self.remote_root+'\\runs\\'+valid_id(run_id)+'\\cancel.request'
        self.ps(f"Set-Content -LiteralPath {ps_quote(path)} -Value 'cancel' -Encoding ASCII")
        return {'run_id': run_id, 'cancel_requested': True}

    def resume_run(self, run_id: str):
        current = self.get_run(run_id)['status']['state']
        if current not in {'INTERRUPTED'}:
            raise ValueError('Resume only an explicitly interrupted run with verified successful checkpoints')
        task = 'CodexCFD-Run-'+valid_id(run_id)
        run_dir = self.remote_root+'\\runs\\'+run_id
        request_path = run_dir+'\\request.json'
        state_path = run_dir+'\\state.json'
        return self.ps(f"""
$request=Get-Content -LiteralPath {ps_quote(request_path)} -Raw -Encoding UTF8 | ConvertFrom-Json
Start-ScheduledTask -TaskName {ps_quote(task)}
[pscustomobject]@{{run_id={ps_quote(run_id)};state='RESUME_REQUESTED'}} | ConvertTo-Json
""")

    def fetch_run_results(self, run_id: str, include_sim: bool = False):
        run = self.get_run(run_id)
        if run['status']['state'] not in {'EXECUTION_COMPLETED','WAITING_AGENT','FAILED','CANCELLED','TIMED_OUT','INTERRUPTED','RESOURCE_STOP','REJECTED_BUSY'}:
            raise ValueError('Run is still active')
        destination = Path(self.config['local_results']).expanduser().resolve() / 'runs' / valid_id(run_id)
        destination.mkdir(parents=True, exist_ok=True)
        files = []
        for item in run['artifacts']:
            ref = valid_ref(item['ref'])
            relative = PurePosixPath(ref).relative_to('runs/'+run_id)
            if relative.suffix.lower() == '.sim' and not include_sim:
                continue
            if item['bytes'] > (2_000_000_000 if include_sim else 50_000_000):
                raise ValueError('Artifact exceeds download bound')
            local = destination / str(relative)
            local.parent.mkdir(parents=True, exist_ok=True)
            self.scp(local, self.remote_path(ref), download=True)
            files.append(str(local))
        return {'run_id': run_id, 'state': run['status']['state'], 'directory': str(destination),
                'files': files, 'engineering_verified': False}

    def report_run(self, run_id: str, evidence: str | None = None, case_label: str | None = None,
                   include_sim: bool = True, strict: bool = False, run_dir: str | None = None):
        """The formal run's report step: retrieve, verify against the runner's own hashes, report.

        This is what closes the R12 gap that `report` existed only on the restricted-trial entry. It adds
        no criterion of its own: `scripts/formal_run_report.py` adapts the run directory into the evidence
        layout and calls the SAME `scripts/generate_run_report.py`, which keeps using
        `scripts/run_record_gate.py` as the single citation judge. Process, engineering and delivery come
        back as three separate conclusions, and a failed run still gets an honest report.

        It also records, in the RUN's own directory, that the plan's post steps really ran
        (`report.state.json` with the verdict, the report sha256 and the verified-artifact count), so a
        declared post step is never counted as done on the strength of the declaration.
        """
        valid_id(run_id)
        from formal_run_report import report_run as _report_run
        destination = (Path(evidence).expanduser().resolve() if evidence
                       else Path(self.config.get('local_results', '.')).expanduser().resolve() / 'reports')
        destination.mkdir(parents=True, exist_ok=True)
        return _report_run(run_id, destination,
                           run_dir=Path(run_dir).expanduser().resolve() if run_dir else None,
                           case_label=case_label, strict=strict, include_sim=include_sim, backend=self)

    def record_report_state(self, run_id: str, payload: dict):
        """Write the report step's own record into the RUN directory, not only into local evidence.

        Without this, "the formal path produces the report" would rest on the caller's word. With it,
        `get_run` can show whether THIS run's post steps really happened.
        """
        valid_id(run_id)
        path = self.remote_root + '\\runs\\' + run_id + '\\report.state.json'
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(body.encode('utf-8')) > 20000:
            raise ValueError('report state record is too large')
        return self.ps(f"""
$path={ps_quote(path)}
Set-Content -LiteralPath $path -Value {ps_quote(body)} -Encoding UTF8
[pscustomobject]@{{run_id={ps_quote(run_id)};report_state_written=$true;path=$path}} | ConvertTo-Json
""")

    def validate_macro(self, source_path: str):
        """Compile a candidate against installed STAR 2502 API; never execute generated code."""
        source = Path(source_path).expanduser().resolve(strict=True)
        if source.suffix != '.java' or not source.is_file() or source.stat().st_size > 256_000:
            raise ValueError('Expected a .java file of at most 256 KB')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', source.stem):
            raise ValueError('Java class filename must be a simple identifier')
        code = source.read_text(encoding='utf-8')
        if not re.search(r'public\s+class\s+'+re.escape(source.stem)+r'\s+extends\s+StarMacro\b', code):
            raise ValueError('Source must define a matching public StarMacro subclass')
        if re.search(r'(?m)^\s*package\s+', code):
            raise ValueError('Candidate macros may not define a Java package')
        flags = []
        for label, pattern in {
            'process_execution': r'\b(?:Runtime\.getRuntime|ProcessBuilder|System\.exit)\b',
            'network_access': r'\b(?:java\.net|java\.nio\.channels\.SocketChannel)\b',
            'reflection': r'\b(?:java\.lang\.reflect|Class\.forName)\b',
            'native_access': r'\b(?:System\.loadLibrary|Runtime\.loadLibrary)\b',
            'file_deletion': r'\b(?:Files\.delete|\.delete\s*\()'
        }.items():
            if re.search(pattern, code):
                flags.append(label)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        candidate_dir = self.remote_root+'\\candidates\\'+digest
        remote_source = candidate_dir+'\\'+source.name
        self.ps(f'New-Item -ItemType Directory -Path {ps_quote(candidate_dir)} -Force | Out-Null')
        self.scp(source, remote_source)
        javac = self.config.get('javac_path')
        if not isinstance(javac, str) or not re.fullmatch(r'[A-Za-z]:[\\/][^*?"<>|\r\n]+javac\.exe', javac, re.I):
            raise ValueError('Configure an absolute javac_path for this STAR installation')
        star_root = str(Path(self.config['star_launcher'].replace('\\','/')).parent.parent).replace('/','\\')
        result = self.ps(f"""
$source={ps_quote(remote_source)}
$hash=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
if($hash -ne {ps_quote(digest)}){{throw 'Candidate upload hash mismatch'}}
$directory={ps_quote(candidate_dir)}
$classes=Join-Path $directory 'classes'
New-Item -ItemType Directory -Path $classes -Force | Out-Null
$jars=@(Get-ChildItem -LiteralPath {ps_quote(star_root)} -Filter '*.jar' -File -Recurse | ForEach-Object {{$_.FullName.Replace([char]92,[char]47)}})
$cp=$jars -join ';'
$argsFile=Join-Path $directory 'javac.args'
$argsText='-proc:none' + [Environment]::NewLine + '-encoding UTF-8' + [Environment]::NewLine + '-cp' + [Environment]::NewLine + '"'+$cp+'"' + [Environment]::NewLine + '-d' + [Environment]::NewLine + '"'+$classes.Replace([char]92,[char]47)+'"' + [Environment]::NewLine + '"'+$source.Replace([char]92,[char]47)+'"'
[IO.File]::WriteAllText($argsFile,$argsText,(New-Object System.Text.UTF8Encoding($false)))
$stdout=Join-Path $directory 'javac.stdout.txt'
$stderr=Join-Path $directory 'javac.stderr.txt'
$process=Start-Process -FilePath {ps_quote(javac)} -ArgumentList ('@'+$argsFile) -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$messages=((Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue) + [Environment]::NewLine + (Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue))
[pscustomobject]@{{compiled=($process.ExitCode -eq 0);compiler_exit_code=$process.ExitCode;diagnostics=$messages.Substring(0,[Math]::Min(10000,$messages.Length));class_count=@(Get-ChildItem -LiteralPath $classes -Filter '*.class' -File -Recurse).Count;api_jar_count=$jars.Count}} | ConvertTo-Json
""", timeout=120)
        result.update({'sha256': digest, 'candidate_ref': 'candidates/'+digest+'/'+source.name,
                       'static_review_flags': flags, 'trial_state': 'NOT_RUN',
                       'executable': False,
                       'reason': 'Generated code requires a separately verified restricted worker and isolated case trial before execution'})
        return result

    def submit_job(self, stage: str, case_ref: str, request_id: str, timeout_seconds: int = 600,
                   workflow_id: str | None = None, case_spec: dict | None = None,
                   inspect_job_id: str | None = None, review_ref: str | None = None,
                   parameters: dict | None = None):
        # Preserve the v0.2 calls while requiring explicit workflow selection for new packs.
        if workflow_id is None:
            workflow_id = 'core' if stage == 'preflight' else 'propeller-periodic-2502'
        catalog = workflow_catalog()
        if workflow_id not in catalog or stage not in catalog[workflow_id]['stages']:
            raise ValueError('Unsupported workflow/stage; call list_workflows')
        declared_parameters = stage_parameter_declarations(workflow_id, stage)
        env = validate_stage_parameters(parameters, declared_parameters)
        valid_id(request_id); valid_ref(case_ref, require_sim=True)
        from production_catalog import validate_formal_submission
        scope_algorithm = validate_formal_submission(review_ref, case_ref,
            [{'workflow_id': workflow_id, 'stage': stage, 'parameters': parameters if parameters is not None else {}}],
            case_spec, [], PLUGIN)
        if scope_algorithm == 'semantic-v1':
            from production_catalog import formal_parameter_environment
            env = formal_parameter_environment(parameters if parameters is not None else {}, declared_parameters)
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 0:
            raise ValueError('timeout_seconds must be a nonnegative integer observation')
        if workflow_id == 'external-domain-2502' and stage in {'build', 'build-cylinder'}:
            if case_spec is None:
                raise ValueError('External-domain construction requires an explicit CaseSpec')
            from casespec import validate_case_spec
            assessment = validate_case_spec(case_spec)
            if not assessment.valid or case_spec.get('case_ref') != case_ref:
                raise ValueError('External-domain CaseSpec is incomplete or references a different input')
            if inspect_job_id is None:
                raise ValueError('External-domain construction requires a successful core inspect_job_id')
            inspect_job_id = valid_id(inspect_job_id)
            inspected = self.get_job(inspect_job_id)
            if (inspected['status'].get('state') != 'SUCCEEDED' or
                    inspected['status'].get('workflow_id') != 'core'):
                raise ValueError('inspect_job_id must refer to a successful core preflight')
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if current_hash != inspected['status'].get('source_sha256'):
                raise ValueError('CaseSpec case_ref does not match the inspected input hash')
            audit_path = self.remote_path('jobs/'+inspect_job_id+'/automation_preflight.json')
            audit = self.ps(f'Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8')
            if not isinstance(audit, dict) or audit.get('status') not in {'ok', 'partial'}:
                raise ValueError('Trusted geometry audit is unavailable')
            parts = audit.get('geometryParts')
            if not isinstance(parts, list):
                raise ValueError('Trusted geometry audit lacks geometryParts')
            part_index = {(part.get('qualifiedName') or part.get('name')): part
                          for part in parts if isinstance(part, dict)}
            geometry = case_spec.get('geometry', {})
            selected = list(geometry.get('body_parts', [])) + list(geometry.get('rotor_parts', []))
            scale = {'m': 1.0, 'mm': 0.001, 'cm': 0.01, 'in': 0.0254}[case_spec['model_length_unit']]
            outer = case_spec['domain']['outer_bounds_m']
            domain = case_spec['domain']
            if stage == 'build' and domain.get('shape', 'box') != 'box':
                raise ValueError('Box-domain stage requires domain.shape=box')
            if stage == 'build-cylinder':
                import math
                if domain.get('shape') != 'cylinder':
                    raise ValueError('Cylinder-domain stage requires domain.shape=cylinder')
                radius = domain.get('cylinder_radius_m')
                zmin = domain.get('cylinder_z_min_m')
                zmax = domain.get('cylinder_z_max_m')
                center = domain.get('cylinder_center_xy_m')
                values = [radius, zmin, zmax] + (center if isinstance(center, list) else [])
                if (not isinstance(center, list) or len(center) != 2 or len(values) != 5 or
                        any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values) or
                        radius <= 0 or zmin >= zmax):
                    raise ValueError('Cylinder dimensions must be finite and nondegenerate SI values')
                expected_outer = [center[0]-radius, center[1]-radius, zmin,
                                  center[0]+radius, center[1]+radius, zmax]
                if any(abs(float(outer[i])-expected_outer[i]) > 1e-9 for i in range(6)):
                    raise ValueError('domain.outer_bounds_m must equal the cylinder bounding box')
            for name in set(selected):
                part = part_index.get(name)
                if part is None:
                    raise ValueError(f'Geometry selector is absent from trusted audit: {name}')
                if part.get('isClosed') is not True or part.get('isManifold') is not True:
                    raise ValueError(f'Geometry part is open, non-manifold, or unverified: {name}')
                bounds = part.get('boundingBoxRaw')
                if (not isinstance(bounds, list) or len(bounds) != 6 or
                        any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in bounds)):
                    raise ValueError(f'Geometry part has no valid measured bounds: {name}')
                bounds_m = [float(v)*scale for v in bounds]
                if any(bounds_m[i] < outer[i] or bounds_m[i+3] > outer[i+3] for i in range(3)):
                    raise ValueError(f'domain.outer_bounds_m does not contain audited geometry: {name}')
                if stage == 'build-cylinder':
                    xfar = max(abs(bounds_m[0]-center[0]), abs(bounds_m[3]-center[0]))
                    yfar = max(abs(bounds_m[1]-center[1]), abs(bounds_m[4]-center[1]))
                    if math.hypot(xfar, yfar) >= radius:
                        raise ValueError(f'Cylinder radius does not conservatively contain audited geometry: {name}')
        if workflow_id == 'external-domain-2502' and stage == 'expand-rotor-360':
            if inspect_job_id is None:
                raise ValueError('Full-360 geometry expansion requires a successful core inspect_job_id')
            inspect_job_id = valid_id(inspect_job_id)
            inspected = self.get_job(inspect_job_id)
            if (inspected['status'].get('state') != 'SUCCEEDED' or
                    inspected['status'].get('workflow_id') != 'core'):
                raise ValueError('inspect_job_id must refer to a successful core preflight')
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if current_hash != inspected['status'].get('source_sha256'):
                raise ValueError('Full-360 case reference does not match the inspected input hash')
            audit_path = self.remote_path('jobs/'+inspect_job_id+'/automation_preflight.json')
            audit = self.ps(f'Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8')
            if not isinstance(audit, dict) or audit.get('status') not in {'ok', 'partial'}:
                raise ValueError('Trusted geometry audit is unavailable')
            part_index = {(part.get('qualifiedName') or part.get('name')): part
                          for part in audit.get('geometryParts', []) if isinstance(part, dict)}
            rotor = part_index.get('PROPELLER')
            if rotor is None or rotor.get('isClosed') is not True or rotor.get('isManifold') is not True:
                raise ValueError('Full-360 conversion requires a closed, manifold audited PROPELLER sector')
        if workflow_id == 'external-domain-2502' and stage == 'create-full-region':
            if inspect_job_id is None:
                raise ValueError('Full-flow region creation requires a successful core inspect_job_id')
            inspect_job_id = valid_id(inspect_job_id)
            inspected = self.get_job(inspect_job_id)
            if (inspected['status'].get('state') != 'SUCCEEDED' or
                    inspected['status'].get('workflow_id') != 'core'):
                raise ValueError('inspect_job_id must refer to a successful core preflight')
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if current_hash != inspected['status'].get('source_sha256'):
                raise ValueError('Region input does not match the inspected source hash')
            audit_path = self.remote_path('jobs/'+inspect_job_id+'/automation_preflight.json')
            audit = self.ps(f'Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8')
            parts = audit.get('geometryParts') if isinstance(audit, dict) else None
            if not isinstance(parts, list) or audit.get('status') not in {'ok', 'partial'}:
                raise ValueError('Trusted full-flow geometry audit is unavailable')
            domain_part = next((part for part in parts if isinstance(part, dict) and
                                part.get('name') == 'AUTO_FREE_AIR_DOMAIN'), None)
            surfaces = domain_part.get('partSurfaces', []) if domain_part else []
            outer = [surface for surface in surfaces if isinstance(surface, dict) and
                     surface.get('name', '').startswith('AUTO_FREE_AIR_CYLINDER.')]
            if (not domain_part or domain_part.get('isClosed') is not True or
                    domain_part.get('isManifold') is not True or len(surfaces) < 9 or len(outer) != 3):
                raise ValueError('Expected a closed full-flow Boolean part with three split outer surfaces')
        if workflow_id == 'external-domain-2502' and stage == 'map-full-flow-outer-boundaries':
            if case_spec is None or case_spec.get('domain', {}).get('shape') != 'cylinder':
                raise ValueError('Outer-boundary mapping requires the explicit cylinder CaseSpec')
            from casespec import validate_case_spec
            if not validate_case_spec(case_spec).valid:
                raise ValueError('Outer-boundary mapping requires a valid cylinder CaseSpec')
            if inspect_job_id is None:
                raise ValueError('Outer-boundary mapping requires a successful core inspect_job_id')
            inspect_job_id = valid_id(inspect_job_id)
            inspected = self.get_job(inspect_job_id)
            if (inspected['status'].get('state') != 'SUCCEEDED' or
                    inspected['status'].get('workflow_id') != 'core'):
                raise ValueError('inspect_job_id must refer to a successful core preflight')
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if current_hash != inspected['status'].get('source_sha256'):
                raise ValueError('Boundary input does not match the inspected source hash')
            audit_path = self.remote_path('jobs/'+inspect_job_id+'/automation_preflight.json')
            audit = self.ps(f'Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8')
            regions = audit.get('regions') if isinstance(audit, dict) else None
            if not isinstance(regions, list) or audit.get('status') != 'ok':
                raise ValueError('Trusted full-flow region audit is unavailable')
            region = next((r for r in regions if isinstance(r, dict) and
                           r.get('name') == 'AUTO_FULL360_FLOW'), None)
            boundaries = region.get('boundaries', []) if region else []
            outer = [b for b in boundaries if isinstance(b, dict) and
                     b.get('name', '').startswith('AUTO_FREE_AIR_CYLINDER.')]
            if (len(boundaries) != 9 or len(outer) != 3 or
                    any(b.get('boundaryTypeClassSimpleName') != 'WallBoundary' for b in boundaries)):
                raise ValueError('Expected nine new default wall boundaries with three split cylinder surfaces')
        if workflow_id == 'external-domain-2502' and stage == 'remove-legacy-sector-regions':
            if inspect_job_id is None:
                raise ValueError('Sector cleanup requires a successful core inspect_job_id')
            inspect_job_id = valid_id(inspect_job_id)
            inspected = self.get_job(inspect_job_id)
            if (inspected['status'].get('state') != 'SUCCEEDED' or
                    inspected['status'].get('workflow_id') != 'core'):
                raise ValueError('inspect_job_id must refer to a successful core preflight')
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if current_hash != inspected['status'].get('source_sha256'):
                raise ValueError('Sector cleanup input does not match the inspected source hash')
            audit_path = self.remote_path('jobs/'+inspect_job_id+'/automation_preflight.json')
            audit = self.ps(f'Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8')
            regions = audit.get('regions') if isinstance(audit, dict) else None
            interfaces = audit.get('interfaces') if isinstance(audit, dict) else None
            if (not isinstance(audit, dict) or audit.get('status') != 'ok' or not isinstance(regions, list) or
                    not isinstance(interfaces, list)):
                raise ValueError('Trusted boundary-mapped full-domain audit is unavailable')
            indexed = {r.get('name'): r for r in regions if isinstance(r, dict)}
            if set(indexed) != {'FLUID', 'PROPELLER', 'AUTO_FULL360_FLOW'}:
                raise ValueError('Unexpected region set before sector cleanup')
            boundaries = indexed['AUTO_FULL360_FLOW'].get('boundaries', [])
            types = {b.get('name'): b.get('boundaryTypeClassSimpleName')
                     for b in boundaries if isinstance(b, dict)}
            required = {'AUTO_INLET': 'InletBoundary', 'AUTO_OUTLET': 'PressureBoundary',
                        'AUTO_RADIAL_FARFIELD': 'SymmetryBoundary'}
            if (len(boundaries) != 9 or any(types.get(k) != v for k, v in required.items()) or
                    list(types.values()).count('WallBoundary') != 6 or
                    {i.get('name') for i in interfaces if isinstance(i, dict)} !=
                    {'FLUID/FLUID', 'FLUID/PROPELLER', 'PROPELLER/PROPELLER'}):
                raise ValueError('Full-flow boundaries or legacy interfaces differ from audited layout')
        if workflow_id == 'external-domain-2502' and stage == 'configure-normal-inlet-outlet':
            from casespec import validate_case_spec
            import math
            if (case_spec is None or not validate_case_spec(case_spec).valid or
                    case_spec.get('case_ref') != case_ref or case_spec.get('domain', {}).get('shape') != 'cylinder'):
                raise ValueError('Normal inlet/outlet stage requires a valid CaseSpec for this cylinder input')
            flow = case_spec.get('flow', {})
            velocity = flow.get('velocity_m_s', [])
            speed = math.sqrt(sum(v*v for v in velocity))
            if (speed <= 0 or abs(velocity[0]) > 1e-9*speed or
                    abs(velocity[1]) > 1e-9*speed or velocity[2] >= 0 or
                    'turbulence_intensity' not in flow or 'turbulent_viscosity_ratio' not in flow):
                raise ValueError('Explicit -Z inlet speed and turbulence values are required')
            roles = case_spec.get('boundary_roles')
            if (not isinstance(roles, dict) or
                    any(not isinstance(roles.get(key), str) or not roles[key]
                        for key in ('region', 'inlet', 'outlet')) or
                    len({roles[key] for key in ('inlet', 'outlet')}) != 2):
                raise ValueError('CaseSpec must select distinct flow region, inlet and outlet')
            if inspect_job_id is None:
                raise ValueError('Boundary values require a successful core inspect_job_id')
            inspect_job_id = valid_id(inspect_job_id)
            inspected = self.get_job(inspect_job_id)
            if (inspected['status'].get('state') != 'SUCCEEDED' or
                    inspected['status'].get('workflow_id') != 'core'):
                raise ValueError('inspect_job_id must refer to a successful core preflight')
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if current_hash != inspected['status'].get('source_sha256'):
                raise ValueError('Boundary-value input does not match the inspected source hash')
            audit_path = self.remote_path('jobs/'+inspect_job_id+'/automation_preflight.json')
            audit = self.ps(f'Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8')
            regions = audit.get('regions') if isinstance(audit, dict) else None
            interfaces = audit.get('interfaces') if isinstance(audit, dict) else None
            if (not isinstance(audit, dict) or audit.get('status') != 'ok' or
                    not isinstance(regions, list) or len(regions) != 1 or
                    not isinstance(interfaces, list) or interfaces or
                    regions[0].get('name') != roles['region'] or
                    not regions[0].get('physicsContinuum')):
                raise ValueError('Expected one audited fluid region without legacy interfaces')
            boundaries = regions[0].get('boundaries', [])
            types = {b.get('name'): b.get('boundaryTypeClassSimpleName')
                     for b in boundaries if isinstance(b, dict)}
            if (types.get(roles['inlet']) != 'InletBoundary' or
                    types.get(roles['outlet']) != 'PressureBoundary'):
                raise ValueError('Selected inlet/outlet boundary types differ from the audit')
        if workflow_id == 'external-domain-2502' and stage == 'build-mrf-fluid-volumes':
            from casespec import validate_case_spec
            import math
            if (case_spec is None or not validate_case_spec(case_spec).valid or
                    case_spec.get('case_ref') != case_ref or
                    case_spec.get('domain', {}).get('shape') != 'cylinder' or
                    case_spec.get('rotation_zone', {}).get('shape') != 'cylinder'):
                raise ValueError('MRF volume stage requires a valid explicit cylinder and rotation_zone CaseSpec')
            if inspect_job_id is None:
                raise ValueError('MRF volume construction requires a successful core inspect_job_id')
            inspect_job_id = valid_id(inspect_job_id)
            inspected = self.get_job(inspect_job_id)
            if (inspected['status'].get('state') != 'SUCCEEDED' or
                    inspected['status'].get('workflow_id') != 'core'):
                raise ValueError('inspect_job_id must refer to a successful core preflight')
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if current_hash != inspected['status'].get('source_sha256'):
                raise ValueError('MRF input does not match the inspected source hash')
            audit_path = self.remote_path('jobs/'+inspect_job_id+'/automation_preflight.json')
            audit = self.ps(f'Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8')
            parts = audit.get('geometryParts') if isinstance(audit, dict) else None
            if (not isinstance(audit, dict) or audit.get('status') != 'ok' or not isinstance(parts, list)):
                raise ValueError('Trusted MRF geometry audit is unavailable')
            indexed = {p.get('name'): p for p in parts if isinstance(p, dict)}
            for name in ('AUTO_FREE_AIR_CYLINDER', 'AUTO_FREE_AIR_DOMAIN'):
                part = indexed.get(name)
                if not part or part.get('isClosed') is not True or part.get('isManifold') is not True:
                    raise ValueError(f'MRF source geometry is not closed and manifold: {name}')
            zone = case_spec['rotation_zone']
            center = zone['center_xy_m']
            radius = zone['radius_m']
            scale = {'m': 1.0, 'mm': 0.001, 'cm': 0.01, 'in': 0.0254}[case_spec['model_length_unit']]
            for name in case_spec['geometry']['rotor_parts']:
                part = indexed.get(name)
                if not part or part.get('isClosed') is not True or part.get('isManifold') is not True:
                    raise ValueError(f'MRF rotor solid is not closed and manifold: {name}')
                bounds = part.get('boundingBoxRaw')
                if (not isinstance(bounds, list) or len(bounds) != 6 or
                        any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                            for v in bounds)):
                    raise ValueError(f'MRF rotor solid lacks measured bounds: {name}')
                bounds = [v*scale for v in bounds]
                xfar = max(abs(bounds[0]-center[0]), abs(bounds[3]-center[0]))
                yfar = max(abs(bounds[1]-center[1]), abs(bounds[4]-center[1]))
                if (math.hypot(xfar, yfar) >= radius or bounds[2] <= zone['z_min_m'] or
                        bounds[5] >= zone['z_max_m']):
                    raise ValueError(f'MRF envelope does not conservatively contain rotor solid: {name}')
            obsolete_names = case_spec.get('legacy_mesh_operations_to_remove')
            mesh_audit_id = case_spec.get('mesh_operation_audit_job_id')
            if (not isinstance(obsolete_names, list) or not obsolete_names or
                    any(not isinstance(name, str) or not name for name in obsolete_names) or
                    len(set(obsolete_names)) != len(obsolete_names) or
                    not isinstance(mesh_audit_id, str)):
                raise ValueError('MRF volume stage requires explicit audited obsolete auto-mesh selectors')
            mesh_audit_id = valid_id(mesh_audit_id)
            mesh_audit_job = self.get_job(mesh_audit_id)
            if (mesh_audit_job['status'].get('state') != 'SUCCEEDED' or
                    mesh_audit_job['status'].get('workflow_id') != 'core' or
                    mesh_audit_job['status'].get('stage') != 'audit-boundary-physics' or
                    mesh_audit_job['status'].get('source_sha256') != current_hash):
                raise ValueError('Obsolete mesh operations lack a trusted audit of this source')
            mesh_audit_path = self.remote_path('jobs/'+mesh_audit_id+'/boundary_physics_audit.txt')
            mesh_audit_result = self.ps(f'[pscustomobject]@{{text=(Get-Content -LiteralPath {ps_quote(mesh_audit_path)} -Raw -Encoding UTF8)}} | ConvertTo-Json')
            mesh_audit_text = mesh_audit_result.get('text') if isinstance(mesh_audit_result, dict) else None
            if isinstance(mesh_audit_text, dict):
                mesh_audit_text = mesh_audit_text.get('value')
            if not isinstance(mesh_audit_text, str):
                raise ValueError('Trusted mesh-operation audit text is unavailable')
            mesh_operations = {}
            for line in mesh_audit_text.splitlines():
                fields = line.split('\t')
                if len(fields) == 3 and fields[0] == 'MESH_OPERATION':
                    mesh_operations[fields[1]] = fields[2]
            if any(mesh_operations.get(name) != 'star.meshing.AutoMeshOperation'
                   for name in obsolete_names):
                raise ValueError('Selected obsolete operation is not an audited AutoMeshOperation')
        if workflow_id == 'external-domain-2502' and stage == 'create-mrf-regions':
            if inspect_job_id is None:
                raise ValueError('MRF region creation requires a successful core inspect_job_id')
            inspect_job_id = valid_id(inspect_job_id)
            inspected = self.get_job(inspect_job_id)
            if (inspected['status'].get('state') != 'SUCCEEDED' or
                    inspected['status'].get('workflow_id') != 'core' or
                    inspected['status'].get('stage') != 'preflight'):
                raise ValueError('inspect_job_id must refer to a successful core preflight')
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if current_hash != inspected['status'].get('source_sha256'):
                raise ValueError('MRF region input does not match the inspected source hash')
            audit_path = self.remote_path('jobs/'+inspect_job_id+'/automation_preflight.json')
            audit = self.ps(f'Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8')
            parts = audit.get('geometryParts') if isinstance(audit, dict) else None
            regions = audit.get('regions') if isinstance(audit, dict) else None
            interfaces = audit.get('interfaces') if isinstance(audit, dict) else None
            if (not isinstance(audit, dict) or audit.get('status') != 'ok' or
                    not isinstance(parts, list) or not isinstance(regions, list) or
                    len(regions) != 1 or regions[0].get('name') != 'AUTO_FULL360_FLOW' or
                    not isinstance(interfaces, list) or interfaces):
                raise ValueError('Trusted pre-MRF region layout is unavailable')
            indexed = {p.get('name'): p for p in parts if isinstance(p, dict)}
            for name, count in (('AUTO_MRF_STATIONARY_FLUID', 6), ('AUTO_MRF_ROTATING_FLUID', 9)):
                part = indexed.get(name)
                if (not part or part.get('isClosed') is not True or
                        part.get('isManifold') is not True or
                        len(part.get('partSurfaces', [])) != count):
                    raise ValueError(f'Expected audited closed MRF part with {count} surfaces: {name}')
        if workflow_id == 'external-domain-2502' and stage == 'configure-mrf-interfaces':
            from casespec import validate_case_spec
            if (case_spec is None or not validate_case_spec(case_spec).valid or
                    case_spec.get('case_ref') != case_ref or
                    case_spec.get('scenario') != 'propeller_mrf'):
                raise ValueError('MRF interface stage requires a valid CaseSpec for this input')
            if inspect_job_id is None:
                raise ValueError('MRF interface stage requires a successful core inspect_job_id')
            inspect_job_id = valid_id(inspect_job_id)
            inspected = self.get_job(inspect_job_id)
            if (inspected['status'].get('state') != 'SUCCEEDED' or
                    inspected['status'].get('workflow_id') != 'core' or
                    inspected['status'].get('stage') != 'preflight'):
                raise ValueError('MRF interface stage requires a successful core preflight')
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if current_hash != inspected['status'].get('source_sha256'):
                raise ValueError('MRF interface input does not match the inspected source hash')
            audit_path = self.remote_path('jobs/'+inspect_job_id+'/automation_preflight.json')
            audit = self.ps(f'Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8')
            regions = audit.get('regions') if isinstance(audit, dict) else None
            interfaces = audit.get('interfaces') if isinstance(audit, dict) else None
            names = case_spec.get('mrf_regions')
            surface_map = case_spec.get('surface_map')
            map_keys = ('outer_inlet', 'outer_outlet', 'outer_side',
                        'inner_upstream', 'inner_downstream', 'inner_side')
            if (not isinstance(audit, dict) or audit.get('status') != 'ok' or
                    not isinstance(regions, list) or len(regions) != 2 or
                    not isinstance(interfaces, list) or interfaces or not isinstance(names, dict) or
                    not isinstance(surface_map, dict) or
                    any(not isinstance(surface_map.get(k), str) or not surface_map[k] for k in map_keys) or
                    len({surface_map[k] for k in map_keys}) != 6):
                raise ValueError('Trusted two-region MRF layout or surface selection is unavailable')
            indexed = {r.get('name'): r for r in regions if isinstance(r, dict)}
            if set(indexed) != {names.get('stationary'), names.get('rotating')}:
                raise ValueError('MRF Region names differ from the trusted audit')
            for name, count in ((names['stationary'], 6), (names['rotating'], 9)):
                region = indexed[name]
                boundaries = region.get('boundaries')
                types = {b.get('name'): b.get('boundaryTypeClassSimpleName')
                         for b in boundaries if isinstance(b, dict)} if isinstance(boundaries, list) else {}
                required_names = (map_keys if name == names['stationary'] else map_keys[3:])
                if (not region.get('physicsContinuum') or len(types) != count or
                        any(types.get(surface_map[k]) != 'WallBoundary' for k in required_names)):
                    raise ValueError('MRF boundaries differ from audited default-wall layout')
            map_id = valid_id(case_spec.get('surface_map_audit_job_id', ''))
            frame_id = valid_id(case_spec.get('reference_frame_audit_job_id', ''))
            for audit_id, expected_workflow, expected_stage in (
                    (map_id, 'external-domain-2502', 'audit-mrf-surface-map'),
                    (frame_id, 'core', 'audit-reference-frames')):
                record = self.get_job(audit_id)['status']
                if (record.get('state') != 'SUCCEEDED' or
                        record.get('workflow_id') != expected_workflow or
                        record.get('stage') != expected_stage or
                        record.get('source_sha256') != current_hash):
                    raise ValueError('MRF surface or frame audit does not match this source')
            map_path = self.remote_path('jobs/'+map_id+'/mrf_surface_map.json')
            map_audit = self.ps(f'Get-Content -LiteralPath {ps_quote(map_path)} -Raw -Encoding UTF8')
            if (not isinstance(map_audit, dict) or map_audit.get('status') != 'ok' or
                    any(map_audit.get(k) != surface_map[k] for k in map_keys)):
                raise ValueError('MRF surface map differs from the measured patch audit')
            frame_path = self.remote_path('jobs/'+frame_id+'/reference_frames_audit.txt')
            frame_audit = self.ps(f'(Get-Content -LiteralPath {ps_quote(frame_path)} -Raw -Encoding UTF8) | ConvertTo-Json -Compress')
            if isinstance(frame_audit, dict):
                frame_audit = frame_audit.get('value')
            if (not isinstance(frame_audit, str) or
                    not any(line.startswith('ROTATING\t'+case_spec.get('reference_frame_name', '')+'\t')
                            for line in frame_audit.splitlines())):
                raise ValueError('MRF rotating frame is absent from the trusted frame audit')
        if workflow_id == 'external-domain-2502' and stage == 'configure-mrf-mesh':
            from casespec import validate_case_spec
            if (case_spec is None or not validate_case_spec(case_spec).valid or
                    case_spec.get('case_ref') != case_ref or
                    case_spec.get('scenario') != 'propeller_mrf' or
                    not isinstance(case_spec.get('mesh'), dict)):
                raise ValueError('MRF mesh stage requires a valid explicit mesh CaseSpec')
            if inspect_job_id is None:
                raise ValueError('MRF mesh stage requires a successful core inspect_job_id')
            inspect_job_id = valid_id(inspect_job_id)
            inspected = self.get_job(inspect_job_id)['status']
            if (inspected.get('state') != 'SUCCEEDED' or
                    inspected.get('workflow_id') != 'core' or inspected.get('stage') != 'preflight'):
                raise ValueError('MRF mesh stage requires a successful core preflight')
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if current_hash != inspected.get('source_sha256'):
                raise ValueError('MRF mesh input does not match the inspected source hash')
            audit_path = self.remote_path('jobs/'+inspect_job_id+'/automation_preflight.json')
            audit = self.ps(f'Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8')
            regions = audit.get('regions') if isinstance(audit, dict) else None
            interfaces = audit.get('interfaces') if isinstance(audit, dict) else None
            parts = audit.get('geometryParts') if isinstance(audit, dict) else None
            names = case_spec.get('mrf_regions', {})
            expected_pair = {names.get('stationary'), names.get('rotating')}
            if (audit.get('status') != 'ok' or not isinstance(regions, list) or len(regions) != 2 or
                    {r.get('name') for r in regions if isinstance(r, dict)} != expected_pair or
                    not isinstance(interfaces, list) or len(interfaces) != 3 or
                    any(i.get('interfaceTypeClass') != 'star.common.InternalInterface' or
                        {i.get('region0'), i.get('region1')} != expected_pair for i in interfaces) or
                    not isinstance(parts, list)):
                raise ValueError('MRF mesh source lacks audited two-region three-interface layout')
            indexed = {p.get('name'): p for p in parts if isinstance(p, dict)}
            for name, count in (('AUTO_MRF_STATIONARY_FLUID', 6), ('AUTO_MRF_ROTATING_FLUID', 9)):
                part = indexed.get(name)
                if (not part or part.get('isClosed') is not True or part.get('isManifold') is not True or
                        len(part.get('partSurfaces', [])) != count):
                    raise ValueError(f'MRF mesh source lacks closed fluid part: {name}')
            bc_id = valid_id(case_spec.get('boundary_physics_audit_job_id', ''))
            bc_status = self.get_job(bc_id)['status']
            if (bc_status.get('state') != 'SUCCEEDED' or bc_status.get('workflow_id') != 'core' or
                    bc_status.get('stage') != 'audit-boundary-physics' or
                    bc_status.get('source_sha256') != current_hash):
                raise ValueError('MRF mesh source lacks matching boundary physics audit')
        if workflow_id == 'external-domain-2502' and stage in {'execute-mrf-diagnostic-mesh', 'split-mrf-mesh-operations'}:
            from casespec import validate_case_spec
            if (case_spec is None or not validate_case_spec(case_spec).valid or
                    case_spec.get('case_ref') != case_ref or
                    case_spec.get('scenario') != 'propeller_mrf' or
                    not isinstance(case_spec.get('mesh'), dict)):
                raise ValueError('MRF mesh execution requires a valid explicit mesh CaseSpec')
            audit_id = valid_id(case_spec.get('mesh_config_audit_job_id', ''))
            audit_status = self.get_job(audit_id)['status']
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if (audit_status.get('state') != 'SUCCEEDED' or
                    audit_status.get('workflow_id') != 'core' or
                    audit_status.get('stage') != 'audit-mrf-mesh-configuration' or
                    audit_status.get('source_sha256') != current_hash):
                raise ValueError('MRF mesh configuration audit does not match the input')
            audit_path = self.remote_path('jobs/'+audit_id+'/mrf_mesh_audit.txt')
            audit_text = self.ps(f'(Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8) | ConvertTo-Json -Compress')
            if isinstance(audit_text, dict):
                audit_text = audit_text.get('value')
            if not isinstance(audit_text, str):
                raise ValueError('Trusted MRF mesh configuration text is unavailable')
            rows = [line.split('\t') for line in audit_text.splitlines()]
            items = [row for row in rows if len(row) >= 2]
            inputs = {row[1] for row in items if row[0] == 'INPUT'}
            base_values = [row[1] for row in items if row[0] == 'BASE_SIZE_SI']
            prism_values = [row[1] for row in items if row[0] == 'PRISM_LAYERS']
            controls = {row[1]: (float(row[2]), float(row[3]))
                        for row in rows if len(row) == 4 and row[0] == 'CONTROL'}
            surfaces = {name: [row[2] for row in rows if len(row) == 3 and row[0] == 'SURFACE' and row[1] == name]
                        for name in controls}
            mesh = case_spec['mesh']
            try:
                base = float(base_values[0])
                prism = int(prism_values[0])
            except (IndexError, ValueError) as exc:
                raise ValueError('MRF mesh audit lacks valid base size or prism count') from exc
            if (inputs != {'AUTO_MRF_STATIONARY_FLUID', 'AUTO_MRF_ROTATING_FLUID'} or
                    len(base_values) != 1 or len(prism_values) != 1 or
                    abs(base-mesh['base_size_m']) > 1e-9 or prism != mesh['prism_layers'] or
                    set(controls) != {'AUTO_MRF_BLADE_SURFACES', 'AUTO_MRF_INTERFACE_SURFACES'} or
                    len(surfaces['AUTO_MRF_BLADE_SURFACES']) != 6 or
                    len(surfaces['AUTO_MRF_INTERFACE_SURFACES']) != 6 or
                    any(abs(controls[name][i] - expected) > 1e-7 for name, i, expected in (
                        ('AUTO_MRF_BLADE_SURFACES', 0, 100*mesh['blade_min_size_m']/base),
                        ('AUTO_MRF_BLADE_SURFACES', 1, 100*mesh['blade_target_size_m']/base),
                        ('AUTO_MRF_INTERFACE_SURFACES', 0, 100*mesh['interface_min_size_m']/base),
                        ('AUTO_MRF_INTERFACE_SURFACES', 1, 100*mesh['interface_target_size_m']/base)))):
                raise ValueError('MRF mesh operation differs from audited CaseSpec')
        if workflow_id == 'external-domain-2502' and stage in {'execute-mrf-split-diagnostic-mesh', 'adjust-mrf-rotating-prisms'}:
            from casespec import validate_case_spec
            if (case_spec is None or not validate_case_spec(case_spec).valid or
                    case_spec.get('case_ref') != case_ref or
                    case_spec.get('scenario') != 'propeller_mrf' or
                    not isinstance(case_spec.get('mesh'), dict)):
                raise ValueError('Split MRF mesh execution requires a valid explicit mesh CaseSpec')
            audit_id = valid_id(case_spec.get('split_mesh_audit_job_id', ''))
            audit_status = self.get_job(audit_id)['status']
            source_path = self.remote_path(case_ref)
            current_hash = self.ps(f'(Get-FileHash -LiteralPath {ps_quote(source_path)} -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
            if (audit_status.get('state') != 'SUCCEEDED' or audit_status.get('workflow_id') != 'core' or
                    audit_status.get('stage') != 'audit-mrf-split-mesh-configuration' or
                    audit_status.get('source_sha256') != current_hash):
                raise ValueError('Split MRF mesh audit does not match the input')
            audit_path = self.remote_path('jobs/'+audit_id+'/mrf_split_mesh_audit.txt')
            audit_text = self.ps(f'(Get-Content -LiteralPath {ps_quote(audit_path)} -Raw -Encoding UTF8) | ConvertTo-Json -Compress')
            if isinstance(audit_text, dict):
                audit_text = audit_text.get('value')
            if not isinstance(audit_text, str):
                raise ValueError('Trusted split MRF mesh audit text is unavailable')
            rows = [line.split('\t') for line in audit_text.splitlines()]
            fixed = 'AUTO_MRF_STATIONARY_MESH'
            moving = 'AUTO_MRF_ROTATING_MESH'
            mesh = case_spec['mesh']
            operations = {row[1]: (float(row[2]), int(row[3]))
                          for row in rows if len(row) == 4 and row[0] == 'OPERATION'}
            inputs = {row[1]: row[2] for row in rows if len(row) == 3 and row[0] == 'INPUT'}
            controls = {(row[1], row[2]): (float(row[3]), float(row[4]))
                        for row in rows if len(row) == 5 and row[0] == 'CONTROL'}
            surface_counts = {}
            for row in rows:
                if len(row) == 4 and row[0] == 'SURFACE':
                    key = (row[1], row[2])
                    surface_counts[key] = surface_counts.get(key, 0) + 1
            expected_control_keys = {(fixed, 'AUTO_MRF_STATIONARY_INTERFACE_SURFACES'),
                                     (moving, 'AUTO_MRF_BLADE_SURFACES'),
                                     (moving, 'AUTO_MRF_INTERFACE_SURFACES')}
            if (set(operations) != {fixed, moving} or
                    inputs != {fixed: 'AUTO_MRF_STATIONARY_FLUID', moving: 'AUTO_MRF_ROTATING_FLUID'} or
                    any(abs(size-mesh['base_size_m']) > 1e-9 or
                        layers != (mesh.get('rotating_prism_layers', mesh['prism_layers'])
                                   if name == moving and stage != 'adjust-mrf-rotating-prisms'
                                   else mesh['prism_layers'])
                        for name, (size, layers) in operations.items()) or
                    set(controls) != expected_control_keys or
                    surface_counts != {(fixed, 'AUTO_MRF_STATIONARY_INTERFACE_SURFACES'): 3,
                                       (moving, 'AUTO_MRF_BLADE_SURFACES'): 6,
                                       (moving, 'AUTO_MRF_INTERFACE_SURFACES'): 3}):
                raise ValueError('Split MRF mesh layout differs from audited CaseSpec')
            for key, (minimum, target) in controls.items():
                if key[1] == 'AUTO_MRF_BLADE_SURFACES':
                    expected = (100*mesh['blade_min_size_m']/mesh['base_size_m'],
                                100*mesh['blade_target_size_m']/mesh['base_size_m'])
                else:
                    expected = (100*mesh['interface_min_size_m']/mesh['base_size_m'],
                                100*mesh['interface_target_size_m']/mesh['base_size_m'])
                if abs(minimum-expected[0]) > 1e-7 or abs(target-expected[1]) > 1e-7:
                    raise ValueError('Split MRF mesh control sizes differ from CaseSpec')
            if stage == 'adjust-mrf-rotating-prisms':
                requested = mesh.get('rotating_prism_layers')
                if not isinstance(requested, int) or isinstance(requested, bool) or not 3 <= requested < mesh['prism_layers']:
                    raise ValueError('Rotating MRF prism repair requires an explicit lower layer count')
        review = load_review_record(review_ref, workflow_id, stage, case_ref, 'run_job.ps1',
                                    list(catalog[workflow_id]['stages'][stage]['macros']))
        check_resource_review(review)
        source = self.remote_path(case_ref)
        digest = self.runtime_digest()
        case_spec_digest = None
        if case_spec is not None:
            from casespec import validate_case_spec
            case_spec_digest = validate_case_spec(case_spec).digest
        logical = {'workflow_id': workflow_id, 'stage': stage, 'case_ref': case_ref,
                   'timeout_seconds': timeout_seconds, 'runtime_digest': digest,
                   'case_spec_digest': case_spec_digest, 'inspect_job_id': inspect_job_id,
                   'parameters': env, 'review_sha256': review['sha256']}
        from resource_contract import make_contract
        allocation = make_contract(case_spec.get('limits', {}) if case_spec else {}, case_spec.get('resource_contract') if case_spec else None)
        logical.update(resource_contract=allocation, resource_contract_sha256=review['resource_contract_sha256'], logical_cores=allocation['logical_cores'], memory_gb=allocation['memory_gb'])
        from formal_worker import request_pins
        logical.update(request_pins(self))
        request_digest = hashlib.sha256(json.dumps(logical, sort_keys=True).encode()).hexdigest()
        runtime = self.remote_root+'\\runtime\\'+digest[:16]
        worker_path = runtime+'\\run_job.ps1'
        source_root = self.remote_root+'\\'
        worker_argument = runtime+'\\run_job.ps1'
        jobdir = self.remote_path('jobs/'+request_id)
        req = dict(logical, job_id=request_id, request_digest=request_digest, case_spec=case_spec,
                   source_path=source, remote_root=self.remote_root, star_launcher=self.config['star_launcher'],
                   macro_hashes=review['macro_hashes'], runner_sha256=review['runner_sha256'],
                   input_sha256=review['input_sha256'], review_sha256=review['sha256'],
                   review_ref=review['path'], env=env)
        if workflow_id == 'external-domain-2502' and stage in {'execute-mrf-diagnostic-mesh', 'execute-mrf-split-diagnostic-mesh'}:
            req.update(min_free_memory_gb=16, min_free_disk_gb=50)
        req64 = base64.b64encode(json.dumps(req).encode()).decode()
        # Atomic directory creation makes request_id an idempotency key.
        from formal_worker import guarded_submission
        return self.ps(guarded_submission(self, f"""
$job={ps_quote(jobdir)}
$requestFile=Join-Path $job 'request.json'
if(Test-Path -LiteralPath $requestFile){{
 $old=Get-Content $requestFile -Raw | ConvertFrom-Json
 if($old.request_digest -ne {ps_quote(request_digest)}){{throw 'request_id already used for different parameters'}}
 [pscustomobject]@{{job_id={ps_quote(request_id)};reused=$true;state='EXISTING'}} | ConvertTo-Json
 exit 0
}}
if(!(Test-Path -LiteralPath {ps_quote(worker_path)})){{throw 'Deploy this runtime before submission'}}
$source=(Resolve-Path -LiteralPath {ps_quote(source)}).Path
$sourceRoot={ps_quote(source_root)}
if(!$source.StartsWith($sourceRoot,[StringComparison]::OrdinalIgnoreCase)){{throw 'Source outside configured root'}}
New-Item -ItemType Directory -Path $job -ErrorAction Stop | Out-Null
$request=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({ps_quote(req64)})) | ConvertFrom-Json
$request | Add-Member -NotePropertyName source_sha256 -NotePropertyValue (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
$request | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $requestFile -Encoding UTF8
@{{state='PENDING';job_id={ps_quote(request_id)};workflow_id={ps_quote(workflow_id)};stage={ps_quote(stage)};request_digest={ps_quote(request_digest)}}} | ConvertTo-Json | Set-Content (Join-Path $job 'state.json') -Encoding UTF8
try{{
 $args='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+{ps_quote(worker_argument)}+'" -JobDir "'+$job+'" -ExpectedRequestSha256 "'+(Get-FileHash -LiteralPath $requestFile -Algorithm SHA256).Hash.ToLowerInvariant()+'"'
 $action=New-ScheduledTaskAction -Execute "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Argument $args
 $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
 Register-FormalTask -TaskName {ps_quote('CodexCFD-'+request_id)} -Action $action -Settings $settings -ExpectedSid $request.expected_worker_sid
 Start-ScheduledTask -TaskName {ps_quote('CodexCFD-'+request_id)}
 [pscustomobject]@{{job_id={ps_quote(request_id)};reused=$false;state='PENDING'}} | ConvertTo-Json
}}catch{{
 @{{state='FAILED';job_id={ps_quote(request_id)};error=$_.Exception.Message}} | ConvertTo-Json | Set-Content (Join-Path $job 'state.json') -Encoding UTF8
 throw
}}
"""), timeout=120)

    def get_job(self, job_id: str):
        valid_id(job_id)
        job = self.remote_path('jobs/'+job_id)
        return self.ps(f"""
$job={ps_quote(job)}
$state=Get-Content -LiteralPath (Join-Path $job 'state.json') -Raw | ConvertFrom-Json
$files=@(Get-ChildItem -LiteralPath $job -File -Recurse | Where-Object {{$_.Extension -in '.sim','.csv','.json','.txt','.log','.stl' -and $_.Name -notin 'input.sim','request.json','state.tmp.json'}} | ForEach-Object {{[pscustomobject]@{{ref='jobs/{job_id}/'+$_.FullName.Substring($job.Length+1).Replace([char]92,[char]47);bytes=$_.Length}}}})
$task=Get-ScheduledTask -TaskName {ps_quote('CodexCFD-'+job_id)} -ErrorAction SilentlyContinue
$tail=''
if(Test-Path (Join-Path $job 'star.log')){{$tail=(Get-Content (Join-Path $job 'star.log') -Tail 6 | Out-String);if($tail.Length -gt 6000){{$tail=$tail.Substring($tail.Length-6000)}}}}
[pscustomobject]@{{status=$state;artifacts=$files;scheduler_state=if($null -ne $task){{$task.State.ToString()}}else{{'NOT_REGISTERED'}};log_tail=$tail}} | ConvertTo-Json -Depth 10
""")

    def list_jobs(self):
        jobs_glob = self.remote_root+'\\jobs\\*\\state.json'
        return self.ps(f"$files=@(Get-ChildItem -Path {ps_quote(jobs_glob)} -File -ErrorAction SilentlyContinue);$items=@($files | Sort-Object LastWriteTime -Descending | Select-Object -First 30 | ForEach-Object {{Get-Content $_.FullName -Raw | ConvertFrom-Json}});ConvertTo-Json -InputObject $items -Depth 8")

    def cancel_job(self, job_id: str):
        status = self.get_job(job_id)['status']
        if status['state'] in TERMINAL:
            return {'job_id': job_id, 'state': status['state'], 'cancel_requested': False}
        self.ps(f"Set-Content -LiteralPath {ps_quote(self.remote_path('jobs/'+job_id+'/cancel.request'))} -Value 'cancel' -Encoding ASCII")
        return {'job_id': job_id, 'cancel_requested': True, 'state': 'CANCEL_REQUESTED'}

    def fetch_results(self, job_id: str, include_sim: bool = False):
        job = self.get_job(job_id)
        if job['status']['state'] not in TERMINAL:
            raise ValueError('Fetch results after the job reaches a terminal state; use get_job for live progress')
        destination = Path(self.config['local_results']).expanduser().resolve()/valid_id(job_id)
        destination.mkdir(parents=True, exist_ok=True)
        downloaded = []
        for item in job['artifacts']:
            ref = valid_ref(item['ref'])
            relative = PurePosixPath(ref).relative_to('jobs/'+job_id)
            if relative.suffix == '.sim' and not include_sim:
                continue
            if item['bytes'] > (2_000_000_000 if include_sim else 50_000_000):
                raise ValueError('Artifact exceeds configured download bound')
            local = destination/str(relative)
            local.parent.mkdir(parents=True, exist_ok=True)
            self.scp(local, self.remote_path(ref), download=True)
            downloaded.append(str(local))
        return {'job_id': job_id, 'state': job['status']['state'], 'directory': str(destination), 'files': downloaded,
                'convergence_verified': False}
