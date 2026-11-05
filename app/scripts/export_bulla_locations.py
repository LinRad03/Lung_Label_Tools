"""Write a controlled, versioned lung-bulla lobe CSV; never package this file."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from pathlib import Path


def export(db_path: Path, queue_id: str, output: Path) -> dict[str, object]:
    if output.exists() or output.with_suffix(output.suffix + ".partial").exists():
        raise ValueError("OUTPUT_ALREADY_EXISTS")
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        q = db.execute("SELECT disease,mode,frozen_hash FROM queues WHERE queue_id=?", (queue_id,)).fetchone()
        if not q or q["disease"] != "pulmonary_bulla" or q["mode"] != "assisted":
            raise ValueError("BULLA_DEVELOPMENT_QUEUE_REQUIRED")
        rows = db.execute("SELECT t.case_id,t.version,t.draft_json,c.image_sha256,c.role FROM tasks t "
                          "JOIN cases c ON c.case_id=t.case_id WHERE t.queue_id=? AND t.status='submitted' AND t.stage='A'",
                          (queue_id,)).fetchall()
    if any(r["role"] != "development" for r in rows):
        raise ValueError("TEST_EXPORT_FORBIDDEN")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    count = eligible = 0
    with temporary.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("queue_hash", "case_id", "image_sha256", "annotation_version", "lesion_id", "disease",
                         "lobe", "location_status", "label_source", "report_viewed", "training_eligible"))
        for row in rows:
            annotation = json.loads(row["draft_json"])
            for lesion in annotation.get("lesions", []):
                confirmed = bool(lesion.get("bulla_location_confirmed"))
                for lobe in lesion.get("bulla_lobes", []) if confirmed else [""]:
                    useful = bool(confirmed and annotation.get("state") == "positive")
                    writer.writerow((q["frozen_hash"], row["case_id"], row["image_sha256"], row["version"],
                                     lesion["id"], "pulmonary_bulla", lobe,
                                     "confirmed" if confirmed else "pending_review",
                                     "human_ct_review" if confirmed else "none",
                                     bool(annotation.get("viewed_report")), useful))
                    count += 1
                    eligible += int(useful)
    temporary.replace(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {"rows": count, "training_eligible_rows": eligible, "sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--queue", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.db, args.queue, args.out)))
