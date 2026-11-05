"""Derive fine annotation subgrids from current saved RAS marks; never edit labels."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from annotator.jepa_mapping import ANNOTATION_GRID_VERSION, preview_many


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private", type=Path, required=True)
    args = parser.parse_args()
    private = args.private.resolve()
    manifest = next((private / f"approved_annotation_manifest_v{i}.json" for i in (4, 3, 2, 1)
                     if (private / f"approved_annotation_manifest_v{i}.json").exists()))
    approved = {str(Path(row["ct_path"]).resolve()) for row in json.loads(manifest.read_text())}
    db_path = private / "annotations.sqlite3"
    output = private / "annotation_subgrid_v2_current.jsonl"
    if output.exists():
        raise RuntimeError("OUTPUT_EXISTS_CREATE_NEW_VERSION")
    temporary = output.with_suffix(".jsonl.partial")
    if temporary.exists():
        raise RuntimeError("PARTIAL_OUTPUT_EXISTS")
    counts = {"tasks_with_marks": 0, "lesions": 0, "unapproved_ct": 0, "failed_tasks": 0,
              "grid16_records": 0, "grid32_records": 0}
    with sqlite3.connect(db_path) as db, temporary.open("x") as stream:
        rows = db.execute("SELECT t.task_id,t.case_id,t.version,t.draft_json,c.ct_path,c.mask_path "
                          "FROM tasks t JOIN cases c ON c.case_id=t.case_id ORDER BY t.task_id")
        for task_id, case_id, version, draft_json, ct_path, mask_path in rows:
            lesions = json.loads(draft_json).get("lesions", [])
            if not lesions:
                continue
            ct = Path(ct_path).resolve()
            if str(ct) not in approved:
                counts["unapproved_ct"] += 1
                continue
            geometries = [(lesion["ras_mm"], lesion.get("ras_hi_mm")) for lesion in lesions]
            try:
                previews = {n: preview_many(ct, Path(mask_path).resolve() if mask_path else None,
                                            geometries, grid_size=n) for n in (16, 32)}
            except (OSError, ValueError, KeyError):
                counts["failed_tasks"] += 1
                continue
            for i, lesion in enumerate(lesions):
                item = {"task_id": task_id, "case_id": case_id, "annotation_version": version,
                        "lesion_id": lesion["id"], "geometry_sha256": hashlib.sha256(
                            json.dumps(geometries[i], sort_keys=True).encode()).hexdigest(),
                        "grid16": previews[16][i], "grid32": previews[32][i]}
                stream.write(json.dumps(item, separators=(",", ":")) + "\n")
                counts["lesions"] += 1
                counts["grid16_records"] += 1
                counts["grid32_records"] += 1
            counts["tasks_with_marks"] += 1
    temporary.replace(output)
    receipt = {"version": ANNOTATION_GRID_VERSION, "scope": "current_saved_annotations_only",
               "model_token_grid_unchanged": "4x4x4", "database_sha256": sha(db_path),
               "approved_manifest_sha256": sha(manifest), "output_sha256": sha(output), **counts}
    receipt_path = private / "annotation_subgrid_v2_receipt.json"
    if receipt_path.exists():
        raise RuntimeError("RECEIPT_EXISTS_CREATE_NEW_VERSION")
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k not in {"database_sha256", "approved_manifest_sha256"}}))


if __name__ == "__main__":
    main()
