"""Generate synthetic-only demonstration data and queues; never reads patient data."""
from __future__ import annotations

import json
import os
from pathlib import Path

import nibabel as nib
import numpy as np
from fastapi.testclient import TestClient

from annotator.app import create_app, hash_password, import_manifest
from annotator.db import connect


def main() -> None:
    root = Path(os.environ.get("ANNOTATOR_DEMO_ROOT", Path(__file__).resolve().parents[1] / "runtime" / "synthetic_demo")).resolve()
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, role in enumerate(("development", "development", "test", "test"), 1):
        x, y, z = np.mgrid[:64, :64, :48]
        image = np.full((64, 64, 48), -1000, np.int16)
        inside = ((x - 32) / 24) ** 2 + ((y - 32) / 22) ** 2 + ((z - 24) / 19) ** 2 < 1
        image[inside] = -700
        image[(x-24-i)**2+(y-29)**2+(z-22)**2 < 6**2] = 50
        affine = np.array([[0.8, 0, 0, -26], [0, 0.8, 0, -26], [0, 0, 2.0, -48], [0, 0, 0, 1]])
        ct = data / f"synthetic_{i}.nii.gz"
        nib.save(nib.Nifti1Image(image, affine), ct)
        mask_path = None
        if role == "development":
            mask_path = data / f"synthetic_{i}_pseudo_mask.nii.gz"
            nib.save(nib.Nifti1Image(inside.astype(np.uint8), affine), mask_path)
        report = data / f"synthetic_{i}_deidentified.txt"
        report.write_text("SYNTHETIC ONLY. Example report: small opacity in the right lung.\n")
        rows.append(dict(case_id=f"SYNTHETIC_{i:03d}", project="demo", cohort="dev" if role=="development" else "blind_test",
                         role=role, patient_group=f"SYNTHETIC_PATIENT_{i:03d}", examination_group=f"SYNTHETIC_EXAM_{i:03d}",
                         ct_path=str(ct), deidentified_report_path=str(report), mask_path=str(mask_path) if mask_path else None,
                         linkage_status="verified", source_version="synthetic_v1"))
    manifest = data / "demo_manifest.json"
    manifest.write_text(json.dumps(rows, indent=2))
    db = root / "annotations.sqlite3"
    app = create_app(db, (data,))
    if not db.exists():
        raise RuntimeError("DB_NOT_CREATED")
    with connect(db) as cx:
        count = cx.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    if count == 0:
        print(json.dumps(import_manifest(db, manifest, (data,)), sort_keys=True))
        with connect(db) as cx:
            for username, role in (("demo_admin", "admin"), ("demo_reader1", "reader1"),
                                   ("demo_reader2", "reader2"), ("demo_adjudicator", "adjudicator")):
                cx.execute("INSERT INTO users VALUES (?,?,?)", (username, role, hash_password("synthetic-only-password")))
        client = TestClient(app)
        client.post("/api/login", json={"username": "demo_admin", "password": "synthetic-only-password"})
        for queue_id, cohort, mode in (("demo_dev", "dev", "assisted"), ("demo_blind", "blind_test", "blind")):
            res = client.post("/api/queues/freeze", json=dict(queue_id=queue_id, project="demo", cohort=cohort,
                disease="pulmonary_nodule", mode=mode, seed=20261029, patient_budget=2,
                readers=["demo_reader1", "demo_reader2"]))
            if res.status_code != 200:
                raise RuntimeError("QUEUE_CREATION_FAILED")
    print("SYNTHETIC_DEMO_READY")


if __name__ == "__main__":
    main()
