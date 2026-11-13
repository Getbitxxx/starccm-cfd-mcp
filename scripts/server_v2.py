from __future__ import annotations
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from backend import Backend
from casespec import validate_case_spec, installed_operations
from knowledge import search_knowledge as search_index
import registered_workflow_mcp_v2 as registered
from run_registered_workflow_v2 import prepare_candidate, publish_candidate, prepare_static_external_flow as prepare_static
from run_registered_workflow import ROOT, digest
import run_sensitivity_study

_LOADED_FILES = ('scripts/server_v2.py', 'scripts/registered_workflow_mcp_v2.py',
                 'scripts/run_registered_workflow_v2.py', 'scripts/run_sensitivity_study.py',
                 'scripts/registered_workflow_mcp.py')
_LOADED_HASHES = {name: digest(ROOT / name) for name in _LOADED_FILES}

mcp=FastMCP('starccm-cfd', instructions='Discover installed STAR-CCM+ workflow packs, audit existing .sim cases, and run allowlisted isolated jobs over SSH. Successful execution does not prove CFD convergence or physical validity.')

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def prepare_static_external_flow(source_sim: str, case_spec: dict, workflow_id: str) -> dict:
    """Prepare supported audited static source, provenance contract, CaseSpec and mesh/domain level candidates. Resolves evidence internally; never starts STAR or approves computation. Unknown source returns NEEDS_TRUSTED_SOURCE_AUDIT."""
    return prepare_static(source_sim, case_spec, workflow_id)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def assemble_registered_workflow_studies(workflow_id: str, run_id: str | None=None) -> dict:
    """Call the shared mesh/domain evidence assembler for a prepared registration. No submission or STAR; missing levels stay NOT_ESTABLISHED."""
    return registered.assemble_registered_workflow_studies(workflow_id, run_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def prepare_registered_workflow_candidate(source_sim: str, case_spec_path: str, workflow_id: str, geometry_record: str, geometry_contract: str, evidence: str) -> dict:
    """Generate a hash-bound PENDING_REVIEW candidate from new inputs and explicit CaseSpec. Does not approve, submit, or run STAR; complete engineering-plan gaps remain visible."""
    return prepare_candidate(source_sim, case_spec_path, workflow_id, geometry_record, geometry_contract, evidence)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def register_reviewed_workflow_candidate(workflow_id: str) -> dict:
    """Register only a current-byte independently reviewed candidate; old input approval never authorizes new bytes. Does not run STAR."""
    return publish_candidate(workflow_id)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_registered_workflows() -> list:
    """Discover reviewed restricted registrations and exact source/CaseSpec hashes; not arbitrary-model capability."""
    return registered.list_registered_workflows()

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def submit_registered_workflow(workflow_id: str, source_sim: str, case_spec_path: str, request_id: str) -> dict:
    """Start the existing reviewed Windows trial and immediately return a durable run_id, without waiting for STAR completion. Exact registered input bytes only; reuse request_id on uncertain network retry. Uses original budget and execution-slot gates."""
    return registered.submit_registered_workflow(workflow_id, source_sim, case_spec_path, request_id)

# NOT read-only: the poll persists <run_id>-result.json and writes
# <run_id>-poll.log under the trial's mcp-handles directory
# (registered_workflow_mcp_v2.py, the write_text after subprocess.run).
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def get_registered_workflow_run(run_id: str) -> dict:
    """One existing controller poll for a persistent registered trial. Execution, delivery and engineering are separate; never waits for the solve loop. Persists the poll verdict and its log under the trial evidence, and never changes the trial itself."""
    return registered.get_registered_workflow_run(run_id)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def fetch_registered_workflow_results(run_id: str) -> dict:
    """Retrieve a queried terminal registered trial with existing hash-verified transfer and Chinese reporter. Never starts or waits for STAR. Returns .sim, fields/CSV/curves and HTML on successful delivery; execution is not engineering validation."""
    return registered.fetch_registered_workflow_results(run_id)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_workflows() -> list:
    """List packaged workflows, their input scope and stages. No host connection is required."""
    return Backend().list_workflows()

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def connection_health() -> dict:
    """Check the Windows host, STAR version and deployed worker; does not load a simulation."""
    result = Backend().health()
    current = {name: digest(ROOT / name) for name in _LOADED_FILES}
    result['desktop_runtime'] = {'version': 'static-external-flow-rc1',
        'loaded_sha256': _LOADED_HASHES, 'disk_sha256': current,
        'loaded_matches_disk': current == _LOADED_HASHES,
        'entry_tools': ['prepare_static_external_flow', 'validate_plan',
                        'assemble_registered_workflow_studies']}
    return result

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def upload_case(local_path: str) -> dict:
    """Upload a local .sim to the configured Windows library, verify SHA256, and return case_ref. Original file is preserved."""
    return Backend().upload_case(local_path)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def register_case(local_path: str | None=None, windows_path: str | None=None) -> dict:
    """Create a SHA-256-checked immutable library copy from a Mac .sim or an allowed Windows .sim. Does not modify the original."""
    return Backend().register_case(local_path, windows_path)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def inspect_case(case_ref: str, request_id: str, wait_seconds: int=90, review_ref: str | None=None) -> dict:
    """Run generic STAR-CCM+ 2502 structural audit. Returns structured geometry/region/physics evidence when ready; otherwise returns a durable job_id for polling. A matching independent review record is required before the stage starts. Original case is untouched."""
    return Backend().inspect_case(case_ref, request_id, wait_seconds, review_ref)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def classify_case_parts(case_ref: str, inspect_job_id: str, geometry: dict | None=None,
                        model_length_unit: str | None=None) -> dict:
    """Screen part roles from a successful core/preflight and matching input SHA-256. Boundary ownership can reject fluid domains selected as solids; names alone never establish role. Does not modify a simulation or prove solid identity."""
    return Backend().classify_case_parts(case_ref, inspect_job_id, geometry, model_length_unit)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def prepare_local_mesh_plan(case_ref: str, inspect_job_id: str, controls: dict) -> dict:
    """Check complete explicit surface roles, paired interfaces and SI mesh controls against a hash-matched preflight. Does not modify STAR, generate a mesh, or authorize candidate execution."""
    return Backend().prepare_local_mesh_plan(case_ref, inspect_job_id, controls)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def search_knowledge(query: str, star_version: str='2502', limit: int=8) -> list:
    """Search curated official titles and installed tutorial references. Entries marked title_and_link_only are not verified code or article text."""
    return search_index(query, star_version, limit)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def diff_case_specs(baseline: str, candidate: str, declaration: str | None=None) -> dict:
    """Compare two CaseSpec JSON files and split the changed keys into declared and undeclared ones. `declaration` optionally points at the run config that carries derivation.overrides. Reports single_variable only when exactly one physical key changed and no key was added or removed. Read-only: no STAR, no network, no writes; it never judges a quality gate."""
    from case_spec_diff import diff_specs
    return diff_specs(ROOT, baseline, candidate, declaration)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def check_mesh_recipe(case_spec: str) -> dict:
    """Arithmetic plausibility of one mesh recipe before spending mesh time: implied prism growth, last prism layer against the adjacent core cell, and local-refinement size against the body target and minimum. Arithmetic only; a plausible recipe is not an accepted mesh and no gate is evaluated. Read-only: no STAR, no network, no writes."""
    from case_spec_diff import check_recipe_file
    return check_recipe_file(ROOT, case_spec)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def validate_macro(source_path: str) -> dict:
    """Upload and compile a candidate Java macro against installed STAR 2502 API, record review flags and SHA256. Execution stays disabled until an isolated trial worker is verified."""
    return Backend().validate_macro(source_path)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def validate_plan(case_spec: dict, inspect_job_id: str | None=None, case: str | None=None, plan_operations: list[str] | None=None, workflow_id: str | None=None) -> dict:
    """Validate SI CaseSpec against a completed core/preflight job, physical input completeness, and AVAILABLE operations (resolved against the live manifests and the files on disk, never by catalog id alone). Pass plan_operations to judge the executability of the plan that would ACTUALLY be submitted: coverage is then decided by that plan and its one run mechanism, never by the union of every mechanism's capabilities. Reports implementation / verified-on-this-case / general-acceptance / engineering separately, and names why each unserved operation is unserved. Does not launch a simulation."""
    if workflow_id is not None:
        return registered.validate_registered_workflow_plan(workflow_id, case_spec, plan_operations)
    return Backend().validate_plan(case_spec,inspect_job_id,case,plan_operations)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_operations() -> list:
    """List the operations DECLARED in the registry with their scope, evidence and mechanism. A declared operation is not necessarily available: resolve_operations checks the real call path."""
    return installed_operations()

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def resolve_operations(operation_ids: list[str] | None=None, case: str | None=None) -> dict:
    """Resolve operations to real call paths, parameters, inputs/outputs, scope and evidence. Reports implementation, verification on the current case, general acceptance and engineering as four SEPARATE levels, and returns the step object the formal path accepts or the reviewed command for operations the restricted-trial entry owns. Read-only; it never submits anything."""
    return Backend().resolve_operations(operation_ids, case)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def submit_run(case_ref: str, request_id: str, steps: list[dict] | None=None, timeout_seconds: int=172800, case_spec: dict | None=None, inspect_job_id: str | None=None, review_ref: str | None=None, operations: list[str] | None=None, parameters: dict | None=None) -> dict:
    """Submit a persistent ordered graph of installed stages, given either as explicit steps or as catalog operations that resolve to real stages (resolve_operations). An offline operation with a verified formal-path invocation and a declared plan_role may be named as an explicit PRE or POST step of the plan; a declaration alone is never enough. `parameters` maps an operation id to the stage parameters that stage DECLARES; undeclared or process-critical names are refused. A matching independent review record is required before any stage starts, and the worker re-verifies each runner layer's own hash. With a CaseSpec the SELECTED plan must pass the full plan gate. Execution completion never means engineering verification."""
    return Backend().submit_run(case_ref,request_id,steps,timeout_seconds,case_spec,inspect_job_id,review_ref,operations,parameters)

# NOT read-only: quality_contract.attach_run_quality -> remote_snapshot caches the
# remote bytes under work/quality-contract/readbacks/<run_id>/<digest>. readOnlyHint
# also decides whether the UNPUBLISHED_CODE gate applies to a call, so advertising a
# writing tool as read-only would let unpublished code write while the gate is skipped.
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def get_run(run_id: str, case: str | None=None) -> dict:
    """Return persistent pipeline state, completed stages, artifacts and scheduler state, with every step attributed to the operation it came from. Process state, artifact delivery and engineering validity are separate fields; engineering_verified is always false here. Writes only an immutable local readback cache of the remote bytes; it never changes the run."""
    from quality_contract import attach_run_quality
    backend=Backend()
    return attach_run_quality(backend.get_run(run_id,case),backend,run_id)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def cancel_run(run_id: str) -> dict:
    """Request cancellation of the current run and its owned STAR process tree."""
    return Backend().cancel_run(run_id)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def resume_run(run_id: str) -> dict:
    """Resume a waiting or timed-out run from verified per-stage checkpoints."""
    return Backend().resume_run(run_id)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def fetch_run_results(run_id: str, include_sim: bool=False) -> dict:
    """Download a terminal run's reports and optional .sim checkpoints to Mac."""
    from quality_contract import attach_run_quality, remote_snapshot
    backend=Backend()
    from result_storage_policy import require_results_capacity
    storage=require_results_capacity(backend.config['local_results'],additional_roots=[ROOT/'work/quality-contract/readbacks'])
    result=backend.fetch_run_results(run_id,include_sim)
    result=dict(result,result_storage=storage)
    return attach_run_quality(result,backend,run_id,directory=remote_snapshot(backend,run_id))

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def submit_job(stage: str, case_ref: str, request_id: str, timeout_seconds: int=600,
               workflow_id: str | None=None, case_spec: dict | None=None,
               inspect_job_id: str | None=None, review_ref: str | None=None,
               parameters: dict | None=None) -> dict:
    """Submit an allowlisted workflow stage as an isolated background job. A matching independent review record (approved, independent, and pinning the pipeline/controller/runner/macro/input hashes) is required before the stage starts; the worker re-verifies those hashes. External-domain construction also requires a CaseSpec and successful core preflight. Reuse request_id on network retry; successful execution is not CFD validation."""
    return Backend().submit_job(stage,case_ref,request_id,timeout_seconds,workflow_id,case_spec,inspect_job_id,review_ref,parameters)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def report_run(run_id: str, evidence: str | None=None, case_label: str | None=None, include_sim: bool=True) -> dict:
    """The formal path's report step: retrieve the run, re-hash every artifact against the sha256 the Windows runner recorded, then call the shared Chinese reporter. Returns process, engineering and delivery as three SEPARATE conclusions; engineering is never claimed by execution. A failed run still produces an honest report."""
    from quality_contract import remote_snapshot
    from formal_run_report import report_run as generate
    from pathlib import Path
    backend=Backend()
    from result_storage_policy import require_results_capacity
    storage=require_results_capacity(backend.config['local_results'],additional_roots=[ROOT/'work/quality-contract/readbacks'])
    source=remote_snapshot(backend,run_id,include_sim=include_sim)
    destination=Path(evidence).expanduser().resolve() if evidence else Path(backend.config['local_results']).expanduser().resolve()/'quality-reports'/run_id/source.name
    destination.mkdir(parents=True,exist_ok=True)
    result=generate(run_id,destination,run_dir=source,case_label=case_label,include_sim=include_sim)
    return dict(result,result_storage=storage)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_job(job_id: str) -> dict:
    """Return durable job state, current progress, limited log tail and artifact references. SUCCEEDED is execution success only."""
    return Backend().get_job(job_id)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_jobs() -> list:
    """List the 30 most recently updated job states from this worker root."""
    return Backend().list_jobs()

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def cancel_job(job_id: str) -> dict:
    """Request cancellation of this isolated job. Stops only its owned process tree, preserving the input case and unrelated STAR sessions. Unsaved iteration progress may be lost."""
    return Backend().cancel_job(job_id)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def fetch_results(job_id: str, include_sim: bool=False) -> dict:
    """Download terminal-job evidence to the configured Mac result directory. Optionally include .sim output files. Never interprets success as physical validation."""
    return Backend().fetch_results(job_id,include_sim)

if __name__=='__main__':
    mcp.run(transport='stdio')
