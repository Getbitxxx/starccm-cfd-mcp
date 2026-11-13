from __future__ import annotations
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from backend import Backend
from casespec import validate_case_spec, installed_operations
from knowledge import search_knowledge as search_index
import registered_workflow_mcp as registered

mcp=FastMCP('starccm-cfd', instructions='Discover installed STAR-CCM+ workflow packs, audit existing .sim cases, and run allowlisted isolated jobs over SSH. Successful execution does not prove CFD convergence or physical validity.')

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_registered_workflows() -> list:
    """Discover reviewed restricted registrations and exact source/CaseSpec hashes; not arbitrary-model capability."""
    return registered.list_registered_workflows()

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def submit_registered_workflow(workflow_id: str, source_sim: str, case_spec_path: str, request_id: str) -> dict:
    """Start the existing reviewed Windows trial and immediately return a durable run_id, without waiting for STAR completion. Exact registered input bytes only; reuse request_id on uncertain network retry. Uses original budget and execution-slot gates."""
    return registered.submit_registered_workflow(workflow_id, source_sim, case_spec_path, request_id)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_registered_workflow_run(run_id: str) -> dict:
    """One existing controller poll for a persistent registered trial. Execution, delivery and engineering are separate; never waits for the solve loop."""
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
    return Backend().health()

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

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def validate_macro(source_path: str) -> dict:
    """Upload and compile a candidate Java macro against installed STAR 2502 API, record review flags and SHA256. Execution stays disabled until an isolated trial worker is verified."""
    return Backend().validate_macro(source_path)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def validate_plan(case_spec: dict, inspect_job_id: str | None=None, case: str | None=None, plan_operations: list[str] | None=None) -> dict:
    """Validate SI CaseSpec against a completed core/preflight job, physical input completeness, and AVAILABLE operations (resolved against the live manifests and the files on disk, never by catalog id alone). Pass plan_operations to judge the executability of the plan that would ACTUALLY be submitted: coverage is then decided by that plan and its one run mechanism, never by the union of every mechanism's capabilities. Reports implementation / verified-on-this-case / general-acceptance / engineering separately, and names why each unserved operation is unserved. Does not launch a simulation."""
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

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_run(run_id: str, case: str | None=None) -> dict:
    """Return persistent pipeline state, completed stages, artifacts and scheduler state, with every step attributed to the operation it came from. Process state, artifact delivery and engineering validity are separate fields; engineering_verified is always false here."""
    return Backend().get_run(run_id,case)

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
    return Backend().fetch_run_results(run_id,include_sim)

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
    return Backend().report_run(run_id,evidence,case_label,include_sim)

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
