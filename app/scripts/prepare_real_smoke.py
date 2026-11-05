"""One real, approved development CT for technical reading checks only.

Output stays in a controlled server directory. No clinical annotation is submitted.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

import nibabel as nib
import numpy as np
from fastapi.testclient import TestClient

from annotator.app import create_app, hash_password, import_manifest
from annotator.db import connect


def pseudonym(secret: bytes, value: str, prefix: str) -> str:
    return prefix + hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()[:24]


def choose_technical_ct(rows: list[dict[str, str]]) -> dict[str, str]:
    """Select by file size/affine only, never by labels or model outcome."""
    candidates = []
    for row in rows:
        ct, mask = Path(row["ct_path"]), Path(row["mask_path"])
        if not ct.is_file() or not mask.is_file() or ct.stat().st_size >= 80_000_000:
            continue
        image, pseudo = nib.load(str(ct)), nib.load(str(mask))
        if image.shape[:3] == pseudo.shape[:3] and np.allclose(image.affine, pseudo.affine, atol=1e-3):
            candidates.append((ct.stat().st_size, row))
    if not candidates:
        raise RuntimeError("NO_TECHNICAL_CT_WITH_ALIGNED_MASK")
    return min(candidates, key=lambda x: x[0])[1]


def main() -> None:
    gate = Path(os.environ["ANNOTATOR_SOURCE_GATE"])
    private = Path(os.environ["ANNOTATOR_REAL_SMOKE_DIR"]).resolve()
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(private, 0o700)
    g = json.loads(gate.read_text())
    manifest = Path(g["train_manifest_path"])
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != g["train_manifest_sha256"]:
        raise RuntimeError("TRAIN_MANIFEST_HASH_MISMATCH")
    with manifest.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    selected = choose_technical_ct(rows)
    secret_file = private / "identity_hmac.key"
    if not secret_file.exists():
        secret_file.write_bytes(secrets.token_bytes(32))
        os.chmod(secret_file, 0o600)
    secret = secret_file.read_bytes()
    ct, mask = Path(selected["ct_path"]).resolve(), Path(selected["mask_path"]).resolve()
    record = {
        "case_id": "TECH_REAL_001", "project": "TECH_ONLY", "cohort": "real_development_technical",
        "role": "development", "patient_group": pseudonym(secret, selected["patient_group_id"], "PG_"),
        "examination_group": pseudonym(secret, selected["candidate_key"], "EX_"),
        "ct_path": str(ct), "mask_path": str(mask), "deidentified_report_path": None,
        "linkage_status": "verified", "source_version": "dev_pilot_v2", "image_sha256": selected["ct_sha256"],
    }
    controlled_manifest = private / "technical_manifest.json"
    if controlled_manifest.exists():
        old = json.loads(controlled_manifest.read_text())
        if old != [record]:
            raise RuntimeError("TECHNICAL_MANIFEST_ALREADY_FROZEN")
    else:
        controlled_manifest.write_text(json.dumps([record], indent=2))
        os.chmod(controlled_manifest, 0o600)
    db = private / "technical_annotations.sqlite3"
    app = create_app(db, (ct.parent, mask.parent))
    with connect(db) as cx:
        existing = cx.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    if not existing:
        counts = import_manifest(db, controlled_manifest, (ct.parent, mask.parent))
        password_file = private / "technical_passwords.json"
        if password_file.exists():
            raise RuntimeError("PASSWORD_FILE_ALREADY_EXISTS")
        accounts = {"tech_admin": secrets.token_urlsafe(24), "tech_reader": secrets.token_urlsafe(24)}
        with connect(db) as cx:
            for name, role in (("tech_admin", "admin"), ("tech_reader", "reader1")):
                cx.execute("INSERT INTO users VALUES (?,?,?)", (name, role, hash_password(accounts[name])))
        password_file.write_text(json.dumps(accounts))
        os.chmod(password_file, 0o600)
        client = TestClient(app)
        assert client.post("/api/login", json={"username": "tech_admin", "password": accounts["tech_admin"]}).status_code == 200
        response = client.post("/api/queues/freeze", json={"queue_id": "REAL_INPUT_TECH_ONLY", "project": "TECH_ONLY",
            "cohort": "real_development_technical", "disease": "pulmonary_nodule", "mode": "assisted",
            "seed": 20261101, "patient_budget": 1, "readers": ["tech_reader"]})
        if response.status_code != 200:
            raise RuntimeError("TECHNICAL_QUEUE_FAILED")
    else:
        counts = {"imported": existing}
    print(json.dumps({"source_manifest_verified": True, "selected_ct_count": counts["imported"],
                      "mask_geometry_aligned": True, "report_display_enabled": False,
                      "technical_only": True, "ct_compressed_mb": round(ct.stat().st_size / 1_000_000, 1)}))


if __name__ == "__main__":
    main()
