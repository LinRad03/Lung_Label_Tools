"""Add four disease review queues from the frozen 535-image training manifest.

Existing 100 reader tasks are preserved. The original report field is copied
verbatim into server-private files after an exact five-row CT association.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

import nibabel as nib
import numpy as np

from annotator.db import connect
from scripts.prepare_real_smoke import pseudonym

ROOT = Path(__file__).resolve().parents[5]
GATE = ROOT / "audit_out/11_anatomy_location_jepa/v1/DEV_PILOT_SOURCE_GATE_v2.json"
TABLE = ROOT / "Data/Manifests/LocalCohort/train_lobe_level.csv"
PRIVATE = ROOT / "audit_out/12_ct_annotation_tool/private_annotation_pilot"
QUEUE_SPECS = {
    "bronchiectasis": ("DEV_BRONCHIECTASIS_435_EXTRA_V1", "new"),
    "lymphadenopathy": ("DEV_LYMPHADENOPATHY_435_EXTRA_V1", "new"),
    "pericardial_effusion": ("DEV_PERICARDIAL_EFFUSION_535_V1", "all"),
    "pulmonary_bulla": ("DEV_PULMONARY_BULLA_535_V1", "all"),
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def main():
    gate = json.loads(GATE.read_text())
    manifest = Path(gate["train_manifest_path"])
    if sha(manifest) != gate["train_manifest_sha256"]:
        raise RuntimeError("FROZEN_SOURCE_CHANGED")
    with manifest.open(newline="") as f:
        train = list(csv.DictReader(f))
    if len(train) != 535 or len({r["patient_group_id"] for r in train}) != 535:
        raise RuntimeError("TRAIN_GROUP_COUNT_CHANGED")
    dbfile = PRIVATE / "annotations.sqlite3"
    old_manifest = json.loads((PRIVATE / "approved_annotation_manifest_v3.json").read_text())
    if len(old_manifest) != 100:
        raise RuntimeError("OLD_MANIFEST_CHANGED")
    old_by_sha = {r["image_sha256"]: r for r in old_manifest}
    if len(old_by_sha) != 100:
        raise RuntimeError("OLD_CASE_DUPLICATE")
    source_sha = {r["ct_sha256"] for r in train}
    if not set(old_by_sha) <= source_sha:
        raise RuntimeError("OLD_CASE_NOT_IN_FROZEN_TRAIN")
    if len(source_sha) != 535:
        raise RuntimeError("FROZEN_CT_DUPLICATE")
    if (PRIVATE / "approved_annotation_manifest_v4.json").exists():
        raise FileExistsError("PRIORITY_QUEUE_ALREADY_INSTALLED")
    with connect(dbfile) as db:
        existing = db.execute("SELECT case_id,image_sha256,patient_group FROM cases").fetchall()
        if len(existing) != 100 or {r["image_sha256"] for r in existing} != set(old_by_sha):
            raise RuntimeError("DATABASE_CASES_CHANGED")
        if db.execute("SELECT COUNT(*) FROM tasks WHERE lease_until>?", (time.time(),)).fetchone()[0]:
            raise RuntimeError("ACTIVE_READER_LEASE")
        if db.execute("SELECT COUNT(*) FROM queues WHERE queue_id IN (?,?,?,?)",
                      tuple(q for q, _ in QUEUE_SPECS.values())).fetchone()[0]:
            raise RuntimeError("QUEUE_ID_OCCUPIED")
        if not db.execute("SELECT 1 FROM users WHERE username='1' AND role='reader1'").fetchone():
            raise RuntimeError("READER_MISSING")
    # Exact source path join; basename is only an index hint, never identity proof.
    wanted = defaultdict(list)
    for row in train:
        wanted[Path(row["ct_path"]).name].append(row)
    joined = defaultdict(list)
    with TABLE.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            for target in wanted.get(Path(row["ct_path"]).name, ()):
                if os.path.samefile(row["ct_path"], target["ct_path"]):
                    joined[target["ct_sha256"]].append(row)
    if set(joined) != source_sha:
        raise RuntimeError("REPORT_JOIN_INCOMPLETE")
    reports = {}
    for h, rows in joined.items():
        texts = {r["full_report_text"] for r in rows}
        if len(rows) != 5 or len(texts) != 1 or not next(iter(texts)).strip():
            raise RuntimeError("REPORT_NOT_UNIQUE")
        reports[h] = next(iter(texts))
    private_reports = PRIVATE / "original_reports_v1"
    private_reports.mkdir(mode=0o700, exist_ok=True)
    os.chmod(private_reports, 0o700)
    secret = (PRIVATE / "identity_hmac.key").read_bytes()
    new_rows = []
    rejected_masks = 0
    for idx, row in enumerate(sorted((r for r in train if r["ct_sha256"] not in old_by_sha),
                                      key=lambda r: r["ct_sha256"]), 1):
        ct = Path(row["ct_path"]).resolve(strict=True)
        mask = Path(row["mask_path"]).resolve(strict=True) if row["mask_path"] else None
        if mask:
            image, pseudo = nib.load(str(ct)), nib.load(str(mask))
            if image.shape[:3] != pseudo.shape[:3] or not np.allclose(image.affine, pseudo.affine, atol=1e-3):
                mask = None
                rejected_masks += 1
        new_rows.append({"case_id": f"DEV535_CT_{idx:04d}", "project": "DEV_ANNOTATION_PILOT",
                         "cohort": "dev_train_535_extra_v1", "role": "development",
                         "patient_group": pseudonym(secret, row["patient_group_id"], "PG_"),
                         "examination_group": pseudonym(secret, row["candidate_key"], "EX_"),
                         "ct_path": str(ct), "mask_path": str(mask) if mask else None,
                         "deidentified_report_path": str(private_reports / (f"DEV535_CT_{idx:04d}.txt")),
                         "linkage_status": "verified", "source_version": "dev_train_535_original_report_v1",
                         "image_sha256": row["ct_sha256"]})
    if len(new_rows) != 435:
        raise RuntimeError("NEW_CASE_COUNT_CHANGED")
    old_rows = []
    for row in old_manifest:
        old_rows.append({**row, "deidentified_report_path": str(private_reports / (row["case_id"] + ".txt")),
                         "source_version": "dev_train_535_original_report_v1"})
    all_rows = old_rows + new_rows
    if len({r["patient_group"] for r in all_rows}) != 535:
        raise RuntimeError("PATIENT_GROUP_DUPLICATE")
    backup = PRIVATE / "annotations_pre_priority_535_v1.sqlite3"
    with sqlite3.connect(dbfile) as src, sqlite3.connect(backup) as dest:
        src.backup(dest)
    os.chmod(backup, 0o600)
    # Case report files never leave the controlled directory.
    for row in all_rows:
        path = Path(row["deidentified_report_path"])
        with path.open("x", encoding="utf-8") as f:
            f.write(reports[row["image_sha256"]])
        os.chmod(path, 0o600)
    manifest4 = PRIVATE / "approved_annotation_manifest_v4.json"
    tmp = manifest4.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, manifest4)
    now = time.time()
    digests = {}
    with connect(dbfile) as db:
        for row in old_rows:
            updated = db.execute("UPDATE cases SET report_path=?,source_version=? WHERE case_id=? AND image_sha256=?",
                                 (row["deidentified_report_path"], row["source_version"], row["case_id"], row["image_sha256"]))
            if updated.rowcount != 1:
                raise RuntimeError("OLD_CASE_UPDATE_FAILED")
        db.executemany("INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       [(r["case_id"],r["project"],r["cohort"],r["role"],r["patient_group"],
                         r["examination_group"],r["ct_path"],r["deidentified_report_path"],r["mask_path"],
                         r["linkage_status"],r["image_sha256"],r["source_version"]) for r in new_rows])
        for disease, (qid, selector) in QUEUE_SPECS.items():
            items = new_rows if selector == "new" else all_rows
            ids = sorted(r["case_id"] for r in items)
            digest = hashlib.sha256(json.dumps({"queue":qid,"disease":disease,"cases":ids,
                                               "source_sha256":gate["train_manifest_sha256"],
                                               "reader":"1"}, sort_keys=True).encode()).hexdigest()
            db.execute("INSERT INTO queues VALUES (?,?,?,?,?,?,?,?,?)",
                       (qid,"DEV_ANNOTATION_PILOT","dev_train_535_union_v1",disease,"assisted",
                        20261103,len(ids),digest,now))
            db.executemany("INSERT INTO tasks(queue_id,case_id,reader,stage) VALUES (?,?,?,'A')",
                           [(qid,cid,"1") for cid in ids])
            db.execute("INSERT INTO audit_events(event,actor,queue_id,at,detail_code) VALUES (?,?,?,?,?)",
                       ("QUEUE_FROZEN","local_maintenance",qid,now,"FROZEN_TRAIN_535"))
            digests[qid] = digest
        db.execute("INSERT INTO audit_events(event,actor,at,detail_code) VALUES (?,?,?,?)",
                   ("REPORT_SOURCE_UPDATED","local_maintenance",now,"ORIGINAL_REPORT_LOCAL_ONLY"))
    receipt = {"status":"INSTALLED", "unique_training_patients":535, "new_cases":435,
               "existing_cases_preserved":100,"original_report_cases":535,
               "mask_geometry_rejected":rejected_masks,"new887_cases":0,
               "queue_patient_counts":{qid:(435 if selector=="new" else 535)
                                       for qid,selector in QUEUE_SPECS.values()},
               "existing_100_task_queues_preserved":True,
               "source_manifest_sha256":gate["train_manifest_sha256"],
               "report_table_sha256":sha(TABLE),"manifest_sha256":sha(manifest4),
               "database_backup_sha256":sha(backup),"queue_hashes":digests}
    (PRIVATE.parent / "PRIORITY_REVIEW_535_RECEIPT.json").write_text(json.dumps(receipt,indent=2)+"\n")
    print(json.dumps({k:receipt[k] for k in ("status","unique_training_patients","new_cases",
                                          "original_report_cases","mask_geometry_rejected",
                                          "new887_cases","queue_patient_counts")},ensure_ascii=False))


if __name__ == "__main__":
    main()
