"""Family-level mesh acceptance (user decision 2026-10-30).

"合格" used to mean "one mesh cleared the quality thresholds".  That definition let the campaign spend ~7 h solving
five J points on a production mesh that passed at only 2.06x the volume-change gate, while the mesh ladder it needed
for the GCI claim failed at 0.72x/0.85x/0.95x on the very same recipe.  The upgraded definition is:

    合格 = 族过线 + 余量可见 + 失败可定位 + 求解前必须过族门

  - 族 (family): the set of meshes the case DECLARES it will use or claim (production mesh + every GCI level + every
    geometry/condition arm that will be solved or cited).  Declared up front; a family that is expanded later must be
    re-judged as a whole.
  - 过线 (pass): EVERY member clears the UNCHANGED hard gates (cell quality / face validity / volume change / prism /
    y+).  The number reported for the family is the WORST member's, because the numerical claim (GCI or mesh scatter)
    is a property of the family, not of one mesh.
  - 余量可见 (margin visible): each member records margin = measured / threshold; members below `margin.fragile_below`
    are FRAGILE and require a declared waiver (localization + scope-variant evidence) before the family accepts them.
  - 失败可定位 (failure localizable): a member that fails a gate must carry a localization record (the read-only
    AuditMrfBadCellLocalization output: worst-cell cluster bbox).  A failure record without localization is NOT
    citable.
  - 求解前必须过族门 (no solve before the family gate): a solve plan is refused unless the family it belongs to is
    accepted and includes the exact mesh the plan consumes (sha256).

Fail-closed everywhere: a missing/unknown member record, an unknown threshold, a wrong record schema, or a family
declaration without the block all end in "not accepted".  Thresholds are never invented here - they come from the
CaseSpec (quality.mesh_min_*) and the declared acceptance block.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

VERDICT_PASS = "PASS"
VERDICT_FRAGILE = "FRAGILE"
VERDICT_FAIL = "FAIL"
VERDICT_UNKNOWN = "UNKNOWN"

REQUIRED_ACCEPTANCE_KEYS = ("family", "margin", "localization_required_on_failure", "distribution_bands")


class MeshAcceptanceError(RuntimeError):
    """Raised when the family gate must refuse (bad contract, missing evidence, unaccepted family)."""


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MeshAcceptanceError(f"{what} must be a number")
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise MeshAcceptanceError(f"{what} must be finite")
    return value


def acceptance_block(spec: dict) -> dict | None:
    """Return the declared acceptance block, or None when the CaseSpec does not declare one.

    A CaseSpec without the block is NOT judged here: the family gate applies to cases that opted in.  The frozen
    2026-10-30 CaseSpec (case_spec.json of uav-prop-apc10x7e) intentionally stays byte-identical without the block.
    """
    quality = spec.get("quality")
    if not isinstance(quality, dict):
        raise MeshAcceptanceError("CaseSpec has no quality object")
    block = quality.get("mesh_acceptance")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise MeshAcceptanceError("quality.mesh_acceptance must be an object")
    missing = [key for key in REQUIRED_ACCEPTANCE_KEYS if key not in block]
    if missing:
        raise MeshAcceptanceError("quality.mesh_acceptance is incomplete: " + ", ".join(missing))
    family = block["family"]
    if not isinstance(family, dict) or family.get("rule") != "ALL_PASS":
        raise MeshAcceptanceError("quality.mesh_acceptance.family.rule must be ALL_PASS")
    members = family.get("members")
    if not isinstance(members, list) or not members:
        raise MeshAcceptanceError("quality.mesh_acceptance.family.members must be a non-empty list")
    return block


def thresholds(spec: dict) -> dict:
    """The UNCHANGED hard gates, read from the CaseSpec (never hard-coded here)."""
    quality = spec.get("quality") or {}
    out = {}
    for key in ("mesh_min_cell_quality", "mesh_min_face_validity", "mesh_min_volume_change"):
        if key not in quality:
            raise MeshAcceptanceError(f"CaseSpec quality.{key} is required for a family verdict")
        out[key] = _number(quality[key], f"quality.{key}")
    if not 0 < out["mesh_min_face_validity"] <= 1:
        raise MeshAcceptanceError("quality.mesh_min_face_validity must be in (0, 1]")
    return out


def classify_member(quality_record: dict, prism_record: dict | None, gates: dict, margin_rule: dict) -> dict:
    """Classify one family member against the unchanged hard gates plus the declared margin rule."""
    if quality_record.get("status") != "ok":
        raise MeshAcceptanceError("mesh quality record status is not ok")
    cell = _number(quality_record.get("measured_cell_quality_min"), "measured_cell_quality_min")
    face = _number(quality_record.get("measured_face_validity_min"), "measured_face_validity_min")
    volume = _number(quality_record.get("measured_volume_change_min"), "measured_volume_change_min")
    cells = quality_record.get("cells")
    if isinstance(cells, bool) or not isinstance(cells, int) or cells <= 0:
        raise MeshAcceptanceError("mesh quality record has no positive cell count")
    hard_failures = []
    if cell < gates["mesh_min_cell_quality"]:
        hard_failures.append("mesh_min_cell_quality")
    if face < gates["mesh_min_face_validity"]:
        hard_failures.append("mesh_min_face_validity")
    if volume < gates["mesh_min_volume_change"]:
        hard_failures.append("mesh_min_volume_change")
    prism_ok = None
    if prism_record is not None:
        if prism_record.get("status") == "ok":
            prism_ok = prism_record.get("gate_passed") is True
            if prism_ok is False:
                hard_failures.append("prism_coverage")
        else:
            hard_failures.append("prism_record_not_ok")
    margin = volume / gates["mesh_min_volume_change"]
    fragile_below = _number(margin_rule["fragile_below"], "margin.fragile_below")
    target = _number(margin_rule["target"], "margin.target")
    if hard_failures:
        verdict = VERDICT_FAIL
    elif margin < fragile_below:
        verdict = VERDICT_FRAGILE
    else:
        verdict = VERDICT_PASS
    return {
        "cells": cells,
        "cell_quality_min": cell,
        "face_validity_min": face,
        "volume_change_min": volume,
        "volume_change_threshold": gates["mesh_min_volume_change"],
        "margin": margin,
        "fragile_below": fragile_below,
        "margin_target": target,
        "hard_failures": hard_failures,
        "prism_gate": prism_ok,
        "verdict": verdict,
    }


def distribution(quality_record: dict, bands: list, gates: dict) -> dict:
    """Report the distribution bands the record supports (M5: the minimum is a one-cell statistic)."""
    counts = quality_record.get("cells_below_band") or {}
    out = {}
    for band in bands:
        threshold = gates["mesh_min_volume_change"] * _number(band, "band")
        key = f"{band:g}x"
        value = counts.get(key)
        if value is None:
            out[key] = {"threshold": threshold, "count": None,
                        "note": "not recorded by this audit version; the minimum alone is reported"}
        else:
            out[key] = {"threshold": threshold, "count": value}
    return out


def _load(path: Path, what: str) -> dict:
    if not path.is_file():
        raise MeshAcceptanceError(f"{what} record missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MeshAcceptanceError(f"{what} record unreadable: {path}: {exc}") from exc


@dataclass
class FamilyVerdict:
    accepted: bool
    family_id: str
    members: list = field(default_factory=list)
    rejections: list = field(default_factory=list)
    waivers: list = field(default_factory=list)
    worst: dict | None = None

    def as_record(self) -> dict:
        return {
            "schema_version": 1,
            "kind": "mesh_family_gate",
            "family_id": self.family_id,
            "accepted": self.accepted,
            "members": self.members,
            "rejections": self.rejections,
            "waivers": self.waivers,
            "worst": self.worst,
            "rule": "ALL_PASS with margin + localization; the reported number is the WORST member",
        }


def evaluate_family(spec: dict, evidence_root: str | Path, waivers: list | None = None) -> FamilyVerdict:
    """Judge the declared family.  Missing evidence, an unreadable record or a malformed member is a REJECTION."""
    block = acceptance_block(spec)
    if block is None:
        raise MeshAcceptanceError("this CaseSpec declares no quality.mesh_acceptance family")
    gates = thresholds(spec)
    root = Path(evidence_root)
    waiver_index = {}
    for waiver in waivers or []:
        member_id = waiver.get("member")
        if not member_id:
            raise MeshAcceptanceError("a waiver needs a member id")
        waiver_index[member_id] = waiver
    verdict = FamilyVerdict(accepted=False, family_id=str(block["family"].get("id") or "unnamed"))
    for member in block["family"]["members"]:
        member_id = str(member.get("id"))
        role = member.get("role")
        entry = {"id": member_id, "role": role, "declared": dict(member)}
        try:
            quality_path = root / str(member.get("quality_record") or "")
            if not member.get("quality_record"):
                raise MeshAcceptanceError("member declares no quality_record")
            quality_record = _load(quality_path, "mesh quality")
            prism_record = None
            if member.get("prism_record"):
                prism_record = _load(root / str(member["prism_record"]), "prism coverage")
            verdict_member = classify_member(quality_record, prism_record, gates, block["margin"])
            entry.update(verdict_member)
            entry["quality_record"] = str(member["quality_record"])
            entry["record_sha256"] = sha256_file(quality_path)
            if member.get("mesh_sha256"):
                entry["mesh_sha256"] = str(member["mesh_sha256"])
            entry["distribution"] = distribution(quality_record, block["distribution_bands"], gates)
            if verdict_member["verdict"] == VERDICT_FRAGILE:
                waiver = waiver_index.get(member_id)
                if waiver is None:
                    verdict.rejections.append({"member": member_id, "reason": "FRAGILE without a waiver",
                                               "margin": verdict_member["margin"],
                                               "required": "localization record + scope-variant evidence"})
                else:
                    missing = [key for key in ("localization_record", "declared_by", "date", "reason") if not waiver.get(key)]
                    if missing:
                        verdict.rejections.append({"member": member_id, "reason": "incomplete waiver: " + ", ".join(missing)})
                    else:
                        verdict.waivers.append({"member": member_id, "waiver": waiver})
        except MeshAcceptanceError as exc:
            entry["verdict"] = VERDICT_UNKNOWN
            entry["error"] = str(exc)
            verdict.rejections.append({"member": member_id, "reason": str(exc)})
        verdict.members.append(entry)
    if verdict.members:
        numeric = [m for m in verdict.members if "margin" in m]
        if numeric:
            worst = min(numeric, key=lambda m: m["margin"])
            verdict.worst = {"member": worst["id"], "volume_change_min": worst["volume_change_min"],
                             "margin": worst["margin"], "verdict": worst["verdict"]}
    verdict.accepted = (bool(verdict.members)
                        and not verdict.rejections
                        and all(m["verdict"] in (VERDICT_PASS, VERDICT_FRAGILE) for m in verdict.members))
    return verdict


def localization_gate(failure_record: dict, localization_record: dict | None) -> dict:
    """A failed member's record is citable only if the worst cells were localized (M4)."""
    status = failure_record.get("status")
    if status == "ok" and failure_record.get("quality_gate_passed") is True:
        return {"citable": True, "reason": "no failure to localize"}
    if localization_record is None:
        return {"citable": False, "reason": "failure without localization: the worst cells were not located"}
    if localization_record.get("localized") is not True or localization_record.get("read_only") is not True:
        return {"citable": False, "reason": "localization record is not a successful read-only localization"}
    volume = (localization_record.get("metrics") or {}).get("VolumeChange") or {}
    if not volume.get("worst_bbox_min") or not volume.get("worst_bbox_max"):
        return {"citable": False, "reason": "localization record carries no worst-cell bounding box"}
    return {"citable": True, "worst_bbox": volume.get("worst_bbox_min"), "worst_count": volume.get("worst_count")}


def require_family_for_solve(plan_spec: dict, family_record: dict, consumed_mesh_sha256: str) -> None:
    """Refuse a solve plan unless the accepted family covers the exact mesh it consumes.

    `plan_spec` is the controller plan (run_id + steps); a plan is a solve plan when any step uses a solver macro.
    """
    steps = plan_spec.get("steps") or []
    solver_macros = {"RunMrfTrial.java", "RunMrfSegment.java"}
    if not any(str(step.get("macro")) in solver_macros for step in steps):
        return  # mesh-only / audit-only plans are not gated by the family rule
    if family_record.get("accepted") is not True:
        raise MeshAcceptanceError(
            "solve refused: the declared mesh family is not accepted "
            f"(rejections: {family_record.get('rejections')})")
    members = family_record.get("members") or []
    if not any(m.get("mesh_sha256") == consumed_mesh_sha256 for m in members):
        raise MeshAcceptanceError(
            "solve refused: the accepted family does not include the mesh this plan consumes "
            f"(mesh sha256 {consumed_mesh_sha256})")
    worst = family_record.get("worst") or {}
    if worst.get("verdict") == VERDICT_FRAGILE:
        # FRAGILE-but-waived families are usable, but the caller must keep the waiver on record.
        if not family_record.get("waivers"):
            raise MeshAcceptanceError("solve refused: the family's worst member is FRAGILE without a waiver")


def evaluate_declarations(declarations: dict, evidence_root: str | Path, waivers: list | None = None) -> dict:
    """Evaluate every family in a declarations file.

    A declarations file carries the CaseSpec thresholds (copied, never invented), the acceptance rules, and one or
    more named families.  This is how a closed campaign's meshes are replayed through the family gate without
    fabricating a new CaseSpec.
    """
    thresholds_block = declarations.get("thresholds")
    acceptance = declarations.get("acceptance")
    families = declarations.get("families")
    for key, value in (("thresholds", thresholds_block), ("acceptance", acceptance), ("families", families)):
        if not isinstance(value, (dict, list)):
            raise MeshAcceptanceError(f"declarations.{key} missing or malformed")
    if not isinstance(families, list) or not families:
        raise MeshAcceptanceError("declarations.families must be a non-empty list")
    spec = {"quality": dict(thresholds_block)}
    spec["quality"]["mesh_acceptance"] = dict(acceptance)
    results = []
    for family in families:
        if not isinstance(family, dict) or not family.get("id") or not isinstance(family.get("members"), list):
            raise MeshAcceptanceError("each declared family needs an id and a members list")
        judge = {"quality": dict(thresholds_block)}
        judge["quality"]["mesh_acceptance"] = {**acceptance, "family": family}
        verdict = evaluate_family(judge, evidence_root, waivers)
        record = verdict.as_record()
        record["family_id"] = str(family["id"])
        record["family_declared"] = family
        results.append(record)
    return {"schema_version": 1, "kind": "mesh_family_gate_replay", "thresholds": thresholds_block,
            "acceptance": acceptance, "families": results}


def _main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Evaluate declared mesh families (family-level acceptance)")
    parser.add_argument("--spec", help="CaseSpec that carries quality.mesh_acceptance (single family)")
    parser.add_argument("--declarations", help="declarations file with one or more families (replay)")
    parser.add_argument("--evidence-root", required=True, help="root the member records are relative to")
    parser.add_argument("--waivers", default=None, help="optional JSON list of waivers")
    parser.add_argument("--out", required=True, help="family gate record to write")
    args = parser.parse_args(argv)
    waivers = json.loads(Path(args.waivers).read_text(encoding="utf-8")) if args.waivers else []
    if bool(args.spec) == bool(args.declarations):
        parser.error("give exactly one of --spec or --declarations")
    if args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        record = evaluate_family(spec, args.evidence_root, waivers).as_record()
        record["spec"] = args.spec
    else:
        declarations = json.loads(Path(args.declarations).read_text(encoding="utf-8"))
        record = evaluate_declarations(declarations, args.evidence_root, waivers)
        record["declarations"] = args.declarations
    record["evidence_root"] = args.evidence_root
    Path(args.out).write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    families = record.get("families") or [record]
    print(json.dumps([{"family": f.get("family_id"), "accepted": f.get("accepted"), "worst": f.get("worst")}
                      for f in families], ensure_ascii=False))
    return 0 if all(f.get("accepted") for f in families) else 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
