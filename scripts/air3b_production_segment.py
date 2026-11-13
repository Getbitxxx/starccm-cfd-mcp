"""Derive one AIR-3B production segment pipeline from the reviewed template.

The independent reviewer authorized later segments under a standing protocol: each round must
show that only `input_hashes` and `AIR3B_SEGMENT_INDEX` changed relative to the reviewed
template, prove the checkpoint hash and size, carry the previous segment's verdict, include the
launcher config, and present a record whose `approved` is false until the reviewer answers.

This script produces exactly that: a derived pipeline, a machine-checked diff against the
template that refuses to proceed if anything else changed, a hashed record skeleton, and the
checkpoint facts. It never launches anything.

Usage:
    python scripts/air3b_production_segment.py --checkpoint <path> --index 2 \
        --expected-iteration <previous end_iteration> [--evidence <dir>]

`--expected-iteration` is mandatory because the reviewer made chain continuity a gate: each
segment must set AIR3B_EXPECTED_LOADED_ITERATION to the previous segment's end_iteration, and a
derivation that silently omits it would lose that gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "cases/air-three-blade/pipeline-mrf-production.json"
ALLOWED_DIFFERENCES = {"input_hashes", "AIR3B_SEGMENT_INDEX", "AIR3B_EXPECTED_LOADED_ITERATION",
                       "required", "require_contains", "note", "pipeline_id", "stages.0.input"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def diff_pipelines(template: dict, derived: dict) -> list[str]:
    """Returns the dotted paths that differ, so the caller can assert on the allowed set."""
    changes: list[str] = []

    def walk(a, b, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                walk(a.get(key, "<missing>"), b.get(key, "<missing>"),
                     f"{path}.{key}" if path else key)
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            # Recurse into equal-length lists so the report names the changed element rather than
            # the whole list, which would otherwise make every derivation look like it changed
            # the stage definition itself.
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}.{i}")
        elif a != b:
            changes.append(path)

    walk(template, derived, "")
    return changes


def build(checkpoint: Path, index: int, expected_iteration: int, evidence: Path | None) -> dict:
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    if not checkpoint.is_file():
        raise SystemExit(f"Checkpoint not found: {checkpoint}")
    digest = sha256(checkpoint)
    size = checkpoint.stat().st_size

    derived = json.loads(json.dumps(template))
    derived["pipeline_id"] = f"air3b-mrf-production-seg{index:02d}"
    derived["input_hashes"] = {checkpoint.name: digest}
    derived["env"]["AIR3B_SEGMENT_INDEX"] = str(index)
    # The stage must read the new checkpoint, not the template's input: repinning input_hashes
    # alone produced a pipeline whose first trial failed instantly with "Stage segment input
    # missing: mrf_pilot.sim". Caught before any solving, but it should never have been possible.
    derived["stages"][0]["input"] = checkpoint.name
    if expected_iteration < 0:
        raise SystemExit("Refusing to derive without --expected-iteration: the continuity gate "
                         "AIR3B_EXPECTED_LOADED_ITERATION would be silently inherited")
    derived["env"]["AIR3B_EXPECTED_LOADED_ITERATION"] = str(expected_iteration)
    derived["stages"][0]["required"] = [
        name.replace("_1_", f"_{index}_") for name in template["stages"][0]["required"]]
    # require_contains entries also name the history file. Updating only `required` left the
    # content check looking for air3b_segment_1_history.csv in a run that writes _2_, so the
    # second segment's trial reported FAILED ("Content requirement target missing") after a
    # completely successful 560-iteration solve. Both lists are rewritten now.
    derived["stages"][0]["require_contains"] = [
        entry.replace("air3b_segment_1_history.csv", f"air3b_segment_{index}_history.csv")
        for entry in template["stages"][0]["require_contains"]]
    if any("_1_history.csv" in entry for entry in derived["stages"][0]["require_contains"]):
        raise SystemExit("Refusing to derive: a history-file expectation was not rewritten")
    derived["note"] = (
        f"{template['note']} This is segment {index}, derived from the reviewed template with only "
        "the pinned checkpoint, the segment index, the expected loaded iteration and the history "
        f"file name changed. It continues from iteration {expected_iteration}.")

    changes = diff_pipelines(template, derived)
    allowed = all(
        any(change == item or change.endswith("." + item) or item in change
            for item in ALLOWED_DIFFERENCES)
        for change in changes)
    if not allowed:
        raise SystemExit("Refusing to derive: unexpected differences from the template: %s" % changes)

    out = REPO / f"cases/air-three-blade/pipeline-mrf-production-seg{index:02d}.json"
    out.write_text(json.dumps(derived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = {
        "subplan_id": "AIR-3B",
        "pipeline_id": derived["pipeline_id"],
        "state": "PENDING_REVIEW",
        "approved": False,
        "independent": False,
        "segment_index": index,
        "expected_loaded_iteration": expected_iteration,
        "template": str(TEMPLATE.relative_to(REPO)),
        "template_sha256": sha256(TEMPLATE),
        "pipeline_sha256": sha256(out),
        "macro_hashes": derived["macro_hashes"],
        "derived_differences": changes,
        "allowed_differences": sorted(ALLOWED_DIFFERENCES),
        "checkpoint": {
            "file": checkpoint.name,
            "bytes": size,
            "sha256": digest,
            "path": str(checkpoint),
        },
        "standing_protocol_items": {
            "pipeline_hash_and_unchanged_macro": True,
            "checkpoint_hash_and_size": True,
            "previous_segment_verdict": "PENDING: attach the previous segment's verdict JSON",
            "launcher_config": "PENDING: attach the generated trial config",
            "record_approved_false_until_answered": True,
        },
        "production_executable": False,
        "engineering_verified": False,
    }
    print(json.dumps({
        "pipeline": str(out.relative_to(REPO)),
        "pipeline_sha256": record["pipeline_sha256"],
        "checkpoint_sha256": digest,
        "checkpoint_bytes": size,
        "differences_from_template": changes,
    }, ensure_ascii=False, indent=2))
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / f"pipeline-review-seg{index:02d}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("record:", (evidence / f"pipeline-review-seg{index:02d}.json"))
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--index", required=True, type=int)
    parser.add_argument("--expected-iteration", required=True, type=int,
                        help="the previous segment's end_iteration, enforced by the macro")
    parser.add_argument("--evidence", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.index < 1:
        raise SystemExit("Segment index must be positive")
    build(args.checkpoint, args.index, args.expected_iteration, args.evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
