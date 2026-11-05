"""Freeze a real development CT annotation queue from an accepted source manifest.

Raw report text is deliberately unavailable until a de-identified, checked mapping exists.
All path-bearing outputs remain in the controlled server directory.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import secrets
from pathlib import Path

import nibabel as nib
import numpy as np
from fastapi.testclient import TestClient

from annotator.app import create_app, hash_password, import_manifest
from annotator.db import connect
from scripts.prepare_real_smoke import pseudonym


def choose_groups(rows: list[dict[str, str]], seed: int, n: int) -> list[dict[str, str]]:
    if len({r["patient_group_id"] for r in rows}) != len(rows):
        raise ValueError("TRAIN_SOURCE_GROUP_NOT_UNIQUE")
    return sorted(rows, key=lambda r: hashlib.sha256(f"{seed}:{r['patient_group_id']}".encode()).hexdigest())[:n]


def main() -> None:
    gate = Path(os.environ["ANNOTATOR_SOURCE_GATE"])
    private = Path(os.environ["ANNOTATOR_PILOT_DIR"]).resolve()
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(private, 0o700)
    g = json.loads(gate.read_text())
    source = Path(g["train_manifest_path"])
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_sha != g["train_manifest_sha256"]:
        raise RuntimeError("SOURCE_MANIFEST_HASH_MISMATCH")
    with source.open(newline="") as stream:
        all_rows = list(csv.DictReader(stream))
    seed, budget = 20261029, 100
    selected = choose_groups(all_rows, seed, budget)
    if len(selected) != budget:
        raise RuntimeError("PILOT_SUPPORT_INSUFFICIENT")
    secret_file = private / "identity_hmac.key"
    if not secret_file.exists():
        secret_file.write_bytes(secrets.token_bytes(32))
        os.chmod(secret_file, 0o600)
    secret = secret_file.read_bytes()
    rows, invalid_masks, missing_masks = [], 0, 0
    for i, row in enumerate(selected, 1):
        ct = Path(row["ct_path"]).resolve(strict=True)
        mask = Path(row["mask_path"]).resolve(strict=True) if row["mask_path"] else None
        if mask:
            image, pseudo = nib.load(str(ct)), nib.load(str(mask))
            if image.shape[:3] != pseudo.shape[:3] or not np.allclose(image.affine, pseudo.affine, atol=1e-3):
                invalid_masks += 1
                mask = None
        else:
            missing_masks += 1
        rows.append({"case_id": f"DEV_CT_{i:04d}", "project": "DEV_ANNOTATION_PILOT",
                     "cohort": "dev_train_100", "role": "development",
                     "patient_group": pseudonym(secret, row["patient_group_id"], "PG_"),
                     "examination_group": pseudonym(secret, row["candidate_key"], "EX_"),
                     "ct_path": str(ct), "mask_path": str(mask) if mask else None,
                     "deidentified_report_path": None, "linkage_status": "verified",
                     "source_version": "dev_pilot_train_v2", "image_sha256": row["ct_sha256"]})
    manifest = private / "approved_annotation_manifest_v1.json"
    if manifest.exists():
        if json.loads(manifest.read_text()) != rows:
            raise RuntimeError("PILOT_MANIFEST_ALREADY_FROZEN")
    else:
        manifest.write_text(json.dumps(rows, indent=2))
        os.chmod(manifest, 0o600)
    roots = tuple({Path(r["ct_path"]).parent for r in rows} | {Path(r["mask_path"]).parent for r in rows if r["mask_path"]})
    db = private / "annotations.sqlite3"
    app = create_app(db, roots)
    with connect(db) as cx:
        existing = cx.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    if not existing:
        counts = import_manifest(db, manifest, roots)
        accounts = {name: secrets.token_urlsafe(24) for name in ("pilot_admin", "pilot_reader1", "pilot_reader2")}
        with connect(db) as cx:
            for name, role in (("pilot_admin", "admin"), ("pilot_reader1", "reader1"), ("pilot_reader2", "reader2")):
                cx.execute("INSERT INTO users VALUES (?,?,?)", (name, role, hash_password(accounts[name])))
        credential_file = private / "pilot_accounts.json"
        credential_file.write_text(json.dumps(accounts))
        os.chmod(credential_file, 0o600)
        client = TestClient(app)
        assert client.post("/api/login", json={"username": "pilot_admin", "password": accounts["pilot_admin"]}).status_code == 200
        request = {"queue_id": "DEV_PULMONARY_NODULE_V1", "project": "DEV_ANNOTATION_PILOT",
                   "cohort": "dev_train_100", "disease": "pulmonary_nodule", "mode": "assisted",
                   "seed": seed, "patient_budget": budget, "readers": ["pilot_reader1", "pilot_reader2"]}
        preview = client.post("/api/queues/preview", json=request)
        if preview.status_code != 200 or preview.json()["eligible_patients"] != budget:
            raise RuntimeError("QUEUE_PREVIEW_FAILED")
        frozen = client.post("/api/queues/freeze", json=request)
        if frozen.status_code != 200:
            raise RuntimeError("QUEUE_FREEZE_FAILED")
        queue_hash = frozen.json()["hash"]
    else:
        counts = {"imported": existing, "with_mask": sum(bool(r["mask_path"]) for r in rows), "with_report": 0}
        with connect(db) as cx:
            q = cx.execute("SELECT frozen_hash FROM queues WHERE queue_id='DEV_PULMONARY_NODULE_V1'").fetchone()
            queue_hash = q["frozen_hash"] if q else None
    result = {"status": "REAL_DEVELOPMENT_ANNOTATION_PILOT", "source_train_manifest_sha256": source_sha,
              "annotation_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
              "queue_hash": queue_hash, "images": counts["imported"], "patient_groups": budget,
              "masks_available": counts["with_mask"], "mask_geometry_rejected": invalid_masks,
              "masks_missing": missing_masks, "deidentified_reports_available": 0,
              "new887_cross_system_independence": "UNVERIFIED", "test_cases_imported": 0}
    (private / "aggregate_status.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in {"source_train_manifest_sha256", "annotation_manifest_sha256", "queue_hash"}}))


if __name__ == "__main__":
    main()
