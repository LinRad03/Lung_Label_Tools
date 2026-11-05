"""Freeze a COPD-related CT review queue using the existing 535 development patients.

This does not assign COPD labels from report text or add any new patients.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from annotator.db import connect

ROOT = Path(__file__).resolve().parents[5]
PRIVATE = ROOT / "audit_out/12_ct_annotation_tool/private_annotation_pilot"
MANIFEST = PRIVATE / "approved_annotation_manifest_v4.json"
DB = PRIVATE / "annotations.sqlite3"
BACKUP = PRIVATE / "annotations_pre_copd_review_v1.sqlite3"
RECEIPT = ROOT / "audit_out/12_ct_annotation_tool/COPD_REVIEW_535_RECEIPT.json"
QUEUE_ID = "DEV_COPD_CT_REVIEW_535_V1"
COPD = re.compile(r"慢阻肺|慢性阻塞性肺(?:疾病|病)|chronic\s+obstructive\s+pulmonary\s+disease|\bCOPD\b", re.I)
EMPHYSEMA = re.compile(r"肺气肿|emphysema", re.I)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if BACKUP.exists() or RECEIPT.exists():
        raise FileExistsError("COPD_QUEUE_ALREADY_INSTALLED")
    cases = json.loads(MANIFEST.read_text())
    if len(cases) != 535 or len({c["patient_group"] for c in cases}) != 535:
        raise RuntimeError("FROZEN_SOURCE_COUNT_CHANGED")
    if any(c["role"] != "development" or c["linkage_status"] != "verified" for c in cases):
        raise RuntimeError("SOURCE_ROLE_OR_LINKAGE_INVALID")
    report_counts = {"explicit_copd_term": 0, "emphysema_mention": 0, "report_available": 0}
    for case in cases:
        report = Path(case["deidentified_report_path"])
        if not report.is_file():
            continue
        text = report.read_text(encoding="utf-8")
        if text.strip():
            report_counts["report_available"] += 1
            report_counts["explicit_copd_term"] += bool(COPD.search(text))
            report_counts["emphysema_mention"] += bool(EMPHYSEMA.search(text))
    with connect(DB) as db:
        db_cases = db.execute("SELECT case_id,patient_group,image_sha256,role FROM cases").fetchall()
        if len(db_cases) != 535 or {x["case_id"]: x["image_sha256"] for x in db_cases} != \
                {x["case_id"]: x["image_sha256"] for x in cases}:
            raise RuntimeError("LIVE_CASE_SET_MISMATCH")
        if any(x["role"] != "development" for x in db_cases):
            raise RuntimeError("TEST_CASE_IN_QUEUE")
        if db.execute("SELECT 1 FROM queues WHERE queue_id=?", (QUEUE_ID,)).fetchone():
            raise RuntimeError("QUEUE_ALREADY_PRESENT")
        if not db.execute("SELECT 1 FROM users WHERE username='1' AND role='reader1'").fetchone():
            raise RuntimeError("READER_NOT_AVAILABLE")
        prior_versions = db.execute("SELECT COUNT(*) FROM annotation_versions").fetchone()[0]
    with sqlite3.connect(DB) as source, sqlite3.connect(BACKUP) as target:
        source.backup(target)
    BACKUP.chmod(0o600)
    source_hash = sha(MANIFEST)
    ids = sorted(c["case_id"] for c in cases)
    frozen = hashlib.sha256(json.dumps({"queue": QUEUE_ID, "cases": ids, "reader": "1",
                                        "source_sha256": source_hash}, sort_keys=True).encode()).hexdigest()
    now = time.time()
    with connect(DB) as db:
        db.execute("INSERT INTO queues VALUES (?,?,?,?,?,?,?,?,?)",
                   (QUEUE_ID, "DEV_ANNOTATION_PILOT", "dev_train_535", "copd_review",
                    "assisted", 20261103, 535, frozen, now))
        db.executemany("INSERT INTO tasks(queue_id,case_id,reader,stage) VALUES (?,?,'1','A')",
                       [(QUEUE_ID, case_id) for case_id in ids])
        db.execute("INSERT INTO audit_events(event,actor,queue_id,at,detail_code) VALUES (?,?,?,?,?)",
                   ("QUEUE_FROZEN", "local_maintenance", QUEUE_ID, now, "EXISTING_535_PATIENTS"))
    with connect(DB) as db:
        task_count = db.execute("SELECT COUNT(*) FROM tasks WHERE queue_id=? AND reader='1'", (QUEUE_ID,)).fetchone()[0]
        after_versions = db.execute("SELECT COUNT(*) FROM annotation_versions").fetchone()[0]
    if task_count != 535 or after_versions != prior_versions:
        raise RuntimeError("QUEUE_POSTCHECK_FAILED")
    receipt = {"queue_id": QUEUE_ID, "unique_development_patients": 535,
               "image_tasks_for_reader_1": task_count, "new_patients": 0,
               "report_term_counts": report_counts,
               "note": "Report terms are search counts, not validated COPD diagnoses; CT review is not spirometric confirmation.",
               "source_manifest_sha256": source_hash, "queue_sha256": frozen,
               "backup_sha256": sha(BACKUP), "annotation_versions_preserved": after_versions,
               "new887_cases": 0}
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"queue_id": QUEUE_ID, "tasks": task_count, "new_patients": 0,
                      **report_counts, "annotation_versions_preserved": after_versions}))


if __name__ == "__main__":
    main()
