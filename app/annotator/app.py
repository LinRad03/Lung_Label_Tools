"""Local research annotation API. Only synthetic fixtures belong in the Git tree."""
from __future__ import annotations

import hashlib
import hmac
import csv
import io
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Literal

import nibabel as nib
import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import connect, initialize
from .geometry import box_block_fractions, point_block
from .jepa_mapping import VERSION as JEPA_VIEW_VERSION, preview as jepa_preview, preview_many as jepa_preview_many
from .report_consistency import VERSION as REPORT_CONSISTENCY_VERSION, compare as compare_report

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ROLES = {"admin", "reader1", "reader2", "adjudicator"}
DISEASES = {
    "pulmonary_nodule": {"label": "肺结节", "tool": "point_box"},
    "atelectasis": {"label": "肺不张", "tool": "region"},
    "consolidation": {"label": "实变", "tool": "region"},
    "bronchiectasis": {"label": "支气管扩张", "tool": "multi_region"},
    "pleural_effusion": {"label": "胸腔积液", "tool": "side_region"},
    "lymphadenopathy": {"label": "淋巴结肿大", "tool": "point_box"},
    "pericardial_effusion": {"label": "心包积液", "tool": "region"},
    "pulmonary_bulla": {"label": "肺大泡／肺大疱", "tool": "multi_region"},
    "copd_review": {"label": "慢阻肺相关 CT 表现（需临床核验）", "tool": "multi_region"},
}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 320_000)
    return salt.hex() + ":" + digest.hex()


def check_password(password: str, stored: str) -> bool:
    salt, expected = stored.split(":")
    actual = hash_password(password, bytes.fromhex(salt))
    return hmac.compare_digest(actual, stored)


class Login(BaseModel):
    username: str
    password: str


class QueueRequest(BaseModel):
    queue_id: str
    project: str
    cohort: str
    disease: str
    mode: str
    seed: int = 20261029
    patient_budget: int = Field(ge=1, le=100000)
    readers: list[str]


class SaveRequest(BaseModel):
    version: int
    payload: dict[str, Any]


class SkipRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=200)


class AdjudicationRequest(BaseModel):
    adjudicator: str


class PointRequest(BaseModel):
    ras_mm: list[float] = Field(min_length=3, max_length=3)


class PreviewRequest(BaseModel):
    ras_mm: list[float] = Field(min_length=3, max_length=3)
    ras_hi_mm: list[float] | None = Field(default=None, min_length=3, max_length=3)
    grid_size: Literal[4, 16, 32] = 4


class BatchPreviewRequest(BaseModel):
    lesions: list[PreviewRequest] = Field(min_length=1, max_length=40)
    grid_size: Literal[4, 16, 32] = 4


class RevisionRequest(SaveRequest):
    reason: str = Field(min_length=2, max_length=200)


class ReportConsistencyRequest(BaseModel):
    state: str


def _allowed_path(raw: str | None, roots: tuple[Path, ...]) -> Path | None:
    if not raw:
        return None
    path = Path(raw).resolve(strict=True)
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError("PATH_OUTSIDE_ALLOWLIST")
    if not path.is_file():
        raise ValueError("NOT_A_FILE")
    return path


def _safe_id(value: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise ValueError("INVALID_ID")
    return value


def import_manifest(db_path: Path, manifest_path: Path, roots: tuple[Path, ...]) -> dict[str, int]:
    """Explicit manifest joins only; returns aggregate counts, never case keys."""
    rows = json.loads(manifest_path.read_text())
    if not isinstance(rows, list):
        raise ValueError("MANIFEST_NOT_LIST")
    validated = []
    for row in rows:
        cid = _safe_id(row["case_id"])
        role = row["role"]
        if role not in {"development", "test", "review"}:
            raise ValueError("INVALID_ROLE")
        ct = _allowed_path(row["ct_path"], roots)
        report = _allowed_path(row.get("deidentified_report_path"), roots)
        mask = _allowed_path(row.get("mask_path"), roots)
        if not str(ct).endswith((".nii", ".nii.gz")):
            raise ValueError("UNSUPPORTED_IMAGE")
        if report and report.suffix != ".txt":
            raise ValueError("REPORT_MUST_BE_DEIDENTIFIED_TEXT")
        if mask and not str(mask).endswith((".nii", ".nii.gz")):
            raise ValueError("UNSUPPORTED_MASK")
        if mask:
            ct_image, mask_image = nib.load(str(ct)), nib.load(str(mask))
            if ct_image.shape[:3] != mask_image.shape[:3] or not np.allclose(ct_image.affine, mask_image.affine, atol=1e-3):
                raise ValueError("MASK_GEOMETRY_MISMATCH")
        for key in ("project", "cohort", "patient_group", "examination_group", "source_version"):
            _safe_id(row[key])
        if row.get("linkage_status") not in {"verified", "report_ambiguous", "mask_ambiguous", "unresolved"}:
            raise ValueError("INVALID_LINKAGE")
        sha = hashlib.sha256(ct.read_bytes()).hexdigest()
        if row.get("image_sha256") and row["image_sha256"] != sha:
            raise ValueError("IMAGE_HASH_MISMATCH")
        validated.append((cid, row["project"], row["cohort"], role, row["patient_group"],
                          row["examination_group"], str(ct), str(report) if report else None,
                          str(mask) if mask else None, row["linkage_status"], sha, row["source_version"]))
    if len({r[0] for r in validated}) != len(validated):
        raise ValueError("DUPLICATE_CASE")
    with connect(db_path) as db:
        existing = db.execute("SELECT project,patient_group,role FROM cases").fetchall()
        group_roles = {}
        for r in validated:
            group_roles.setdefault((r[1], r[4]), set()).add(r[3])
        for r in existing:
            group_roles.setdefault((r["project"], r["patient_group"]), set()).add(r["role"])
        if any("development" in roles and "test" in roles for roles in group_roles.values()):
            raise ValueError("CROSS_ROLE_PATIENT_GROUP")
        db.executemany("INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", validated)
    return {"imported": len(validated), "with_report": sum(bool(r[7]) for r in validated),
            "with_mask": sum(bool(r[8]) for r in validated)}


def create_app(db_path: Path, data_roots: tuple[Path, ...], frontend_dist: Path | None = None,
               model_config: Path | None = None) -> FastAPI:
    initialize(db_path)
    roots = tuple(p.resolve() for p in data_roots)
    model = json.loads(model_config.read_text()) if model_config else None
    if model:
        if not model.get("version") or any(len(model.get(key, [])) != 3 for key in ("input_shape_ijk", "block_shape_ijk")):
            raise ValueError("INVALID_MODEL_GRID_CONFIG")
        if any(x <= 0 for x in model["input_shape_ijk"] + model["block_shape_ijk"]):
            raise ValueError("INVALID_MODEL_GRID_CONFIG")
    app = FastAPI(title="Lung-Vol-JEPA research annotation", docs_url=None, redoc_url=None)
    link_only = os.environ.get("ANNOTATOR_LINK_ONLY") == "1"
    direct_user = os.environ.get("ANNOTATOR_DIRECT_USER")
    if direct_user and not SAFE_ID.fullmatch(direct_user):
        raise ValueError("INVALID_DIRECT_USER")

    def user(request: Request):
        if direct_user:
            if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
                raise HTTPException(403, "LOCAL_ACCESS_ONLY")
            with connect(db_path) as db:
                direct = db.execute("SELECT username,role FROM users WHERE username=?", (direct_user,)).fetchone()
            if not direct or direct["role"] not in {"reader1", "reader2"}:
                raise HTTPException(503, "DIRECT_READER_UNAVAILABLE")
            return dict(direct)
        token = request.cookies.get("annotator_session", "")
        if not token:
            raise HTTPException(401, "LOGIN_REQUIRED")
        with connect(db_path) as db:
            row = db.execute("SELECT u.username,u.role FROM sessions s JOIN users u ON u.username=s.username "
                             "WHERE s.token_hash=? AND s.expires>?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not row:
            raise HTTPException(401, "LOGIN_REQUIRED")
        return dict(row)

    def admin(who=Depends(user)):
        if who["role"] != "admin":
            raise HTTPException(403, "ADMIN_REQUIRED")
        return who

    def task_access(db, task_id: int, who: dict[str, str]):
        row = db.execute("SELECT t.*,c.project,c.cohort,c.role AS case_role,c.ct_path,c.report_path,c.mask_path,"
                         "c.image_sha256,c.linkage_status,q.disease,q.mode FROM tasks t "
                         "JOIN cases c ON c.case_id=t.case_id JOIN queues q ON q.queue_id=t.queue_id "
                         "WHERE t.task_id=?", (task_id,)).fetchone()
        if not row or (row["reader"] != who["username"] and who["role"] != "admin"):
            raise HTTPException(404, "TASK_NOT_FOUND")
        return row

    def public_task(row):
        out = {"task_id": row["task_id"], "queue_id": row["queue_id"], "case_id": row["case_id"],
               "stage": row["stage"], "status": row["status"], "version": row["version"],
               "disease": row["disease"], "definition": DISEASES[row["disease"]],
               "draft": json.loads(row["draft_json"]), "linkage_status": row["linkage_status"]}
        if row["mode"] == "assisted" or row["stage"] == "B":
            out["report_available"] = bool(row["report_path"])
            out["mask_available"] = bool(row["mask_path"])
        return out

    @app.post("/api/login")
    def login(data: Login, response: Response):
        if link_only or direct_user:
            raise HTTPException(403, "ACCESS_LINK_REQUIRED")
        with connect(db_path) as db:
            row = db.execute("SELECT role,password_hash FROM users WHERE username=?", (data.username,)).fetchone()
            if not row or not check_password(data.password, row["password_hash"]):
                raise HTTPException(401, "INVALID_LOGIN")
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO sessions VALUES (?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), data.username, time.time() + 8 * 3600))
        response.set_cookie("annotator_session", token, httponly=True, samesite="strict", secure=False, max_age=8 * 3600)
        return {"username": data.username, "role": row["role"]}

    @app.get("/api/access-mode")
    def access_mode():
        return {"access_link_required": link_only, "direct_reader": bool(direct_user)}

    @app.get("/access/{token}")
    def exchange_access_link(token: str):
        if direct_user or not link_only or len(token) > 128 or len(token) < 40:
            raise HTTPException(404, "ACCESS_LINK_UNAVAILABLE")
        now = time.time()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with connect(db_path) as db:
            row = db.execute("SELECT username FROM access_links WHERE token_hash=? AND expires>? AND used_at IS NULL",
                             (token_hash, now)).fetchone()
            if not row:
                raise HTTPException(404, "ACCESS_LINK_UNAVAILABLE")
            consumed = db.execute("UPDATE access_links SET used_at=? WHERE token_hash=? AND used_at IS NULL", (now, token_hash))
            if consumed.rowcount != 1:
                raise HTTPException(404, "ACCESS_LINK_UNAVAILABLE")
            session = secrets.token_urlsafe(32)
            db.execute("INSERT INTO sessions VALUES (?,?,?)",
                       (hashlib.sha256(session.encode()).hexdigest(), row["username"], now + 7 * 86400))
        response = RedirectResponse("/", status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        response.set_cookie("annotator_session", session, httponly=True, samesite="strict", secure=False,
                            max_age=7 * 86400)
        return response

    @app.post("/api/logout")
    def logout(request: Request, response: Response, who=Depends(user)):
        token = request.cookies.get("annotator_session", "")
        with connect(db_path) as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
        response.delete_cookie("annotator_session")
        return {"ok": True}

    @app.get("/api/me")
    def me(who=Depends(user)):
        return who

    @app.get("/api/queues")
    def queues(who=Depends(user)):
        with connect(db_path) as db:
            rows = db.execute("SELECT DISTINCT q.queue_id,q.project,q.cohort,q.disease,q.mode,q.frozen_hash "
                              "FROM queues q JOIN tasks t ON t.queue_id=q.queue_id WHERE t.reader=? ORDER BY q.queue_id",
                              (who["username"],)).fetchall()
        return [dict(row) for row in rows]

    @app.post("/api/queues/preview")
    def preview_queue(data: QueueRequest, who=Depends(admin)):
        if data.disease not in DISEASES or data.mode not in {"assisted", "blind"}:
            raise HTTPException(400, "INVALID_QUEUE")
        with connect(db_path) as db:
            rows = db.execute("SELECT patient_group,role FROM cases WHERE project=? AND cohort=? AND linkage_status='verified'",
                              (data.project, data.cohort)).fetchall()
        roles = {r["role"] for r in rows}
        if (data.mode == "blind" and roles != {"test"}) or (data.mode == "assisted" and roles != {"development"}):
            raise HTTPException(400, "COHORT_ROLE_MISMATCH")
        return {"eligible_examinations": len(rows), "eligible_patients": len({r["patient_group"] for r in rows}),
                "patient_budget": data.patient_budget, "selection_uses_report_label": False}

    @app.post("/api/queues/freeze")
    def freeze_queue(data: QueueRequest, who=Depends(admin)):
        try:
            _safe_id(data.queue_id)
            _safe_id(data.project)
            _safe_id(data.cohort)
        except ValueError:
            raise HTTPException(400, "INVALID_ID")
        if data.disease not in DISEASES or data.mode not in {"assisted", "blind"}:
            raise HTTPException(400, "INVALID_QUEUE")
        if data.mode == "blind" and len(data.readers) < 2:
            raise HTTPException(400, "TWO_READERS_REQUIRED")
        if len(set(data.readers)) != len(data.readers):
            raise HTTPException(400, "DUPLICATE_READER")
        with connect(db_path) as db:
            users = db.execute("SELECT username,role FROM users WHERE username IN (%s)" % ",".join("?" * len(data.readers)), data.readers).fetchall()
            if len(users) != len(data.readers) or any(u["role"] not in {"reader1", "reader2"} for u in users):
                raise HTTPException(400, "INVALID_READER")
            cases = db.execute("SELECT case_id,patient_group,role FROM cases WHERE project=? AND cohort=? "
                               "AND linkage_status='verified'", (data.project, data.cohort)).fetchall()
            if not cases:
                raise HTTPException(400, "NO_VERIFIED_CASES")
            if data.mode == "blind" and any(c["role"] != "test" for c in cases):
                raise HTTPException(400, "BLIND_REQUIRES_TEST_ROLE")
            if data.mode == "assisted" and any(c["role"] != "development" for c in cases):
                raise HTTPException(400, "ASSISTED_REQUIRES_DEVELOPMENT_ROLE")
            groups = sorted({c["patient_group"] for c in cases}, key=lambda g: hashlib.sha256(f"{data.seed}:{g}".encode()).hexdigest())
            selected = set(groups[:data.patient_budget])
            items = sorted(c["case_id"] for c in cases if c["patient_group"] in selected)
            fingerprint = hashlib.sha256(json.dumps({"request": data.model_dump(), "cases": items}, sort_keys=True).encode()).hexdigest()
            db.execute("INSERT INTO queues VALUES (?,?,?,?,?,?,?,?,?)",
                       (data.queue_id, data.project, data.cohort, data.disease, data.mode, data.seed,
                        data.patient_budget, fingerprint, time.time()))
            db.executemany("INSERT INTO tasks(queue_id,case_id,reader,stage) VALUES (?,?,?,'A')",
                           [(data.queue_id, cid, reader) for cid in items for reader in data.readers])
            db.execute("INSERT INTO audit_events(event,actor,queue_id,at) VALUES (?,?,?,?)",
                       ("QUEUE_FROZEN", who["username"], data.queue_id, time.time()))
        return {"queue_id": data.queue_id, "patients": len(selected), "examinations": len(items), "hash": fingerprint}

    @app.get("/api/queues/{queue_id}/task-list")
    def task_list(queue_id: str, who=Depends(user)):
        with connect(db_path) as db:
            rows = db.execute("SELECT task_id,case_id,stage,status FROM tasks "
                              "WHERE queue_id=? AND reader=? ORDER BY task_id LIMIT 2000",
                              (queue_id, who["username"])).fetchall()
        return {"queue_id": queue_id, "tasks": [dict(row) for row in rows]}

    @app.get("/api/queues/{queue_id}/review-list")
    def review_list(queue_id: str, kind: str, who=Depends(user)):
        if kind not in {"skipped", "discordant", "agree", "all"}:
            raise HTTPException(400, "INVALID_REVIEW_FILTER")
        with connect(db_path) as db:
            rows = db.execute("SELECT t.task_id,t.case_id,t.stage,t.status,t.draft_json,"
                              "c.report_path,q.disease,q.mode FROM tasks t "
                              "JOIN cases c ON c.case_id=t.case_id "
                              "JOIN queues q ON q.queue_id=t.queue_id "
                              "WHERE t.queue_id=? AND t.reader=? ORDER BY t.task_id LIMIT 2000",
                              (queue_id, who["username"])).fetchall()
        tasks = []
        counts = {"agree": 0, "discordant": 0, "skipped": 0, "undetermined": 0}
        for row in rows:
            category = None
            if row["status"] == "skipped":
                category = "skipped"
            source = None
            if row["status"] == "submitted" and not (row["mode"] == "blind" and row["stage"] == "A"):
                draft = json.loads(row["draft_json"])
                agreement = draft.get("report_agreement")
                if agreement is not None:
                    source = draft.get("report_agreement_source") or "reader"
                elif row["report_path"] and draft.get("state") in {"positive", "negative", "uncertain"}:
                    try:
                        path = _allowed_path(row["report_path"], roots)
                        agreement = compare_report(path.read_text(), row["disease"], draft["state"])["agreement"]
                        source = "auto_v1"
                    except (OSError, ValueError, UnicodeError):
                        continue
                category = "agree" if agreement == "agree" else "discordant" if agreement == "disagree" else "undetermined"
            if category is None:
                continue
            counts[category] += 1
            if kind != "all" and category != kind:
                continue
            task = {key: row[key] for key in ("task_id", "case_id", "stage", "status")}
            task["review_category"] = category
            if source:
                task["agreement_source"] = source
            tasks.append(task)
        return {"kind": kind, "counts": counts, "tasks": tasks}

    @app.get("/api/queues/{queue_id}/next")
    def next_task(queue_id: str, who=Depends(user)):
        with connect(db_path) as db:
            now = time.time()
            row = db.execute("SELECT task_id FROM tasks WHERE queue_id=? AND reader=? AND "
                             "status IN ('pending','leased') "
                             "ORDER BY CASE stage WHEN 'A' THEN 0 ELSE 1 END, task_id LIMIT 1",
                             (queue_id, who["username"])).fetchone()
            if not row:
                return {"complete": True}
            db.execute("UPDATE tasks SET status='leased',lease_until=? WHERE task_id=?", (now + 1800, row["task_id"]))
            detail = task_access(db, row["task_id"], who)
            return public_task(detail)

    @app.get("/api/tasks/{task_id}")
    def task(task_id: int, who=Depends(user)):
        with connect(db_path) as db:
            return public_task(task_access(db, task_id, who))

    @app.post("/api/queues/{queue_id}/prepare-adjudication")
    def prepare_adjudication(queue_id: str, data: AdjudicationRequest, who=Depends(admin)):
        with connect(db_path) as db:
            adj = db.execute("SELECT role FROM users WHERE username=?", (data.adjudicator,)).fetchone()
            if not adj or adj["role"] != "adjudicator":
                raise HTTPException(400, "ADJUDICATOR_REQUIRED")
            q = db.execute("SELECT mode FROM queues WHERE queue_id=?", (queue_id,)).fetchone()
            if not q or q["mode"] != "blind":
                raise HTTPException(400, "BLIND_QUEUE_REQUIRED")
            rows = db.execute("SELECT case_id FROM tasks WHERE queue_id=? AND stage='A' "
                              "GROUP BY case_id HAVING COUNT(*)>=2 AND SUM(status='submitted')=COUNT(*)",
                              (queue_id,)).fetchall()
            count = 0
            for row in rows:
                result = db.execute("INSERT OR IGNORE INTO tasks(queue_id,case_id,reader,stage) VALUES (?,?,?,'adjudication')",
                                    (queue_id, row["case_id"], data.adjudicator))
                count += result.rowcount
        return {"created": count}

    @app.get("/api/tasks/{task_id}/adjudication")
    def adjudication(task_id: int, who=Depends(user)):
        with connect(db_path) as db:
            row = task_access(db, task_id, who)
            if row["stage"] != "adjudication" or who["role"] != "adjudicator":
                raise HTTPException(403, "ADJUDICATION_REQUIRED")
            independent = db.execute("SELECT reader,draft_json FROM tasks WHERE queue_id=? AND case_id=? "
                                     "AND stage='A' AND status='submitted' ORDER BY reader",
                                     (row["queue_id"], row["case_id"])).fetchall()
            if len(independent) < 2:
                raise HTTPException(409, "READERS_NOT_SUBMITTED")
        return {"independent_annotations": [{"reader": r["reader"], "annotation": json.loads(r["draft_json"])}
                                            for r in independent]}

    @app.get("/api/tasks/{task_id}/volume")
    def volume(task_id: int, who=Depends(user)):
        with connect(db_path) as db:
            row = task_access(db, task_id, who)
        path = _allowed_path(row["ct_path"], roots)
        return FileResponse(path, media_type="application/octet-stream", filename="research_ct.nii.gz" if str(path).endswith(".gz") else "research_ct.nii")

    @app.get("/api/tasks/{task_id}/image-meta")
    def image_meta(task_id: int, who=Depends(user)):
        with connect(db_path) as db:
            row = task_access(db, task_id, who)
        path = _allowed_path(row["ct_path"], roots)
        img = nib.load(str(path))
        aff = img.affine.tolist()
        return {"shape_ijk": list(img.shape[:3]), "affine_ras": aff, "coordinate_system": "RAS",
                "orientation": list(nib.aff2axcodes(img.affine)), "image_sha256": row["image_sha256"],
                "format": "nii.gz" if str(path).endswith(".gz") else "nii",
                "mask_format": ("nii.gz" if str(row["mask_path"]).endswith(".gz") else "nii")
                if row["mask_path"] and (row["mode"] == "assisted" or row["stage"] == "B") else None}

    @app.get("/api/model-grid")
    def model_grid(who=Depends(user)):
        if not model:
            return {"enabled": False}
        return {"enabled": True, "version": model["version"],
                "block_shape_ijk": model["block_shape_ijk"], "input_shape_ijk": model["input_shape_ijk"],
                "config_sha256": hashlib.sha256(model_config.read_bytes()).hexdigest()}

    @app.post("/api/tasks/{task_id}/map-point")
    def map_point(task_id: int, request: PointRequest, who=Depends(user)):
        with connect(db_path) as db:
            task_access(db, task_id, who)
        if not model:
            raise HTTPException(404, "MODEL_GRID_NOT_CONFIGURED")
        return point_block(request.ras_mm, model)

    @app.post("/api/tasks/{task_id}/jepa-preview")
    def preview_jepa(task_id: int, request: PreviewRequest, who=Depends(user)):
        with connect(db_path) as db:
            row = task_access(db, task_id, who)
        if row["mode"] == "blind" and row["stage"] == "A":
            raise HTTPException(403, "BLIND_STAGE")
        values = request.ras_mm + (request.ras_hi_mm or [])
        if not np.isfinite(values).all():
            raise HTTPException(400, "INVALID_COORDINATE")
        ct = _allowed_path(row["ct_path"], roots)
        mask = _allowed_path(row["mask_path"], roots)
        try:
            return jepa_preview(ct, mask, request.ras_mm, request.ras_hi_mm, grid_size=request.grid_size)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/tasks/{task_id}/jepa-preview-batch")
    def preview_jepa_batch(task_id: int, request: BatchPreviewRequest, who=Depends(user)):
        with connect(db_path) as db:
            row = task_access(db, task_id, who)
        if row["mode"] == "blind" and row["stage"] == "A":
            raise HTTPException(403, "BLIND_STAGE")
        geometries = [(lesion.ras_mm, lesion.ras_hi_mm) for lesion in request.lesions]
        if not all(np.isfinite(lo + (hi or [])).all() for lo, hi in geometries):
            raise HTTPException(400, "INVALID_COORDINATE")
        try:
            return {"previews": jepa_preview_many(_allowed_path(row["ct_path"], roots),
                _allowed_path(row["mask_path"], roots) if row["mask_path"] else None, geometries,
                grid_size=request.grid_size)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/tasks/{task_id}/report")
    def report(task_id: int, who=Depends(user)):
        with connect(db_path) as db:
            row = task_access(db, task_id, who)
        if row["mode"] == "blind" and row["stage"] != "B":
            raise HTTPException(403, "BLIND_STAGE")
        path = _allowed_path(row["report_path"], roots)
        return {"status": "available", "text": path.read_text()} if path else {"status": "missing"}

    @app.post("/api/tasks/{task_id}/report-consistency")
    def report_consistency(task_id: int, request: ReportConsistencyRequest, who=Depends(user)):
        with connect(db_path) as db:
            row = task_access(db, task_id, who)
        if row["mode"] == "blind" and row["stage"] != "B":
            raise HTTPException(403, "BLIND_STAGE")
        if request.state not in {"positive", "negative", "uncertain"}:
            raise HTTPException(400, "INVALID_STATE")
        path = _allowed_path(row["report_path"], roots)
        if not path:
            raise HTTPException(404, "REPORT_MISSING")
        return compare_report(path.read_text(), row["disease"], request.state)

    @app.get("/api/tasks/{task_id}/mask")
    def mask(task_id: int, who=Depends(user)):
        with connect(db_path) as db:
            row = task_access(db, task_id, who)
        if row["mode"] == "blind" and row["stage"] != "B":
            raise HTTPException(403, "BLIND_STAGE")
        path = _allowed_path(row["mask_path"], roots)
        if not path:
            raise HTTPException(404, "MASK_MISSING")
        return FileResponse(path, media_type="application/octet-stream", filename="pseudo_mask.nii.gz" if str(path).endswith(".gz") else "pseudo_mask.nii")

    def validate_payload(payload: dict[str, Any], submitted: bool):
        if payload.get("state") not in {None, "positive", "negative", "uncertain"}:
            raise HTTPException(400, "INVALID_STATE")
        if payload.get("report_agreement") not in {None, "agree", "disagree", "uncertain"}:
            raise HTTPException(400, "INVALID_REPORT_AGREEMENT")
        if payload.get("report_agreement_source") not in {None, "reader", "auto_v1"}:
            raise HTTPException(400, "INVALID_REPORT_AGREEMENT_SOURCE")
        if payload.get("report_consistency_version") not in {None, REPORT_CONSISTENCY_VERSION}:
            raise HTTPException(400, "INVALID_REPORT_CONSISTENCY_VERSION")
        if submitted and payload.get("state") is None:
            raise HTTPException(400, "STATE_REQUIRED")
        if submitted and payload.get("state") == "negative" and payload.get("read_complete") is not True:
            raise HTTPException(400, "NEGATIVE_REQUIRES_COMPLETE_READ")
        rejected = payload.get("rejected_lesions", [])
        if not isinstance(rejected, list) or len(rejected) > 1000:
            raise HTTPException(400, "INVALID_REJECTED_LESIONS")
        for removed in rejected:
            if (not isinstance(removed, dict) or removed.get("reason") != "outside_lobe_pseudomask" or
                removed.get("kind") not in {"point", "box"}):
                raise HTTPException(400, "INVALID_REJECTED_LESIONS")
            try:
                _safe_id(removed["id"])
            except (KeyError, ValueError):
                raise HTTPException(400, "INVALID_REJECTED_LESIONS")
            lo, hi = removed.get("ras_mm"), removed.get("ras_hi_mm", [])
            if not isinstance(lo, list) or not isinstance(hi, list) or len(lo) != 3 or (removed["kind"] == "box" and len(hi) != 3) or not all(isinstance(x, (int, float)) and np.isfinite(x) and abs(x) < 10000 for x in lo + hi):
                raise HTTPException(400, "INVALID_REJECTED_LESIONS")
        lesions = payload.get("lesions", [])
        if not isinstance(lesions, list) or len(lesions) > 1000:
            raise HTTPException(400, "INVALID_LESIONS")
        if submitted and lesions and payload.get("state") != "positive":
            raise HTTPException(400, "LESION_REQUIRES_POSITIVE_STATE")
        ids = []
        for lesion in lesions:
            try:
                ids.append(_safe_id(lesion["id"]))
            except (KeyError, ValueError):
                raise HTTPException(400, "INVALID_LESION_ID")
            if lesion.get("kind") not in {"point", "box"} or len(lesion.get("ras_mm", [])) != 3:
                raise HTTPException(400, "INVALID_GEOMETRY")
            if not all(isinstance(x, (float, int)) and abs(x) < 10000 for x in lesion["ras_mm"]):
                raise HTTPException(400, "INVALID_GEOMETRY")
            if lesion["kind"] == "box" and (len(lesion.get("ras_hi_mm", [])) != 3 or
                not all(isinstance(x, (float, int)) and abs(x) < 10000 for x in lesion["ras_hi_mm"])):
                raise HTTPException(400, "INVALID_GEOMETRY")
            selection = lesion.get("jepa_blocks")
            if selection is not None:
                if not isinstance(selection, dict) or selection.get("model_version") != JEPA_VIEW_VERSION:
                    raise HTTPException(400, "INVALID_BLOCK_SELECTION")
                views = selection.get("views")
                if not isinstance(views, dict) or len(views) > 6:
                    raise HTTPException(400, "INVALID_BLOCK_SELECTION")
                for name, blocks in views.items():
                    if name not in {"global", "LUL", "LLL", "RUL", "RML", "RLL"} or not isinstance(blocks, list) or len(blocks) > 64:
                        raise HTTPException(400, "INVALID_BLOCK_SELECTION")
                    if any(not isinstance(b, list) or len(b) != 3 or any(type(i) is not int or i < 0 or i > 3 for i in b) for b in blocks):
                        raise HTTPException(400, "INVALID_BLOCK_SELECTION")
            if "bulla_lobes" in lesion or "bulla_location_confirmed" in lesion:
                lobes=lesion.get("bulla_lobes", [])
                if not isinstance(lobes, list) or len(lobes)>5 or len(set(lobes))!=len(lobes) or any(l not in {"LUL","LLL","RUL","RML","RLL"} for l in lobes):
                    raise HTTPException(400, "INVALID_BULLA_LOCATION")
                if lesion.get("bulla_location_confirmed") is True and (not lobes or payload.get("state")!="positive"):
                    raise HTTPException(400, "INVALID_BULLA_LOCATION")
        if len(set(ids)) != len(ids):
            raise HTTPException(400, "DUPLICATE_LESION")

    def persist(task_id: int, data: SaveRequest, action: str, who):
        validate_payload(data.payload, action == "submit")
        with connect(db_path) as db:
            row = task_access(db, task_id, who)
            if data.payload.get("report_agreement") and (not row["report_path"] or
                (row["mode"] == "blind" and row["stage"] == "A")):
                raise HTTPException(400, "REPORT_AGREEMENT_UNAVAILABLE")
            if row["status"] not in {"pending", "leased", "skipped"}:
                raise HTTPException(409, "TASK_LOCKED")
            if row["version"] != data.version:
                raise HTTPException(409, "VERSION_CONFLICT")
            new_version = data.version + 1
            payload = json.dumps(data.payload, separators=(",", ":"))
            result = db.execute("UPDATE tasks SET draft_json=?,version=?,status=?,submitted_at=? "
                                "WHERE task_id=? AND version=?", (payload, new_version,
                                "submitted" if action == "submit" else "leased",
                                time.time() if action == "submit" else None, task_id, data.version))
            if result.rowcount != 1:
                raise HTTPException(409, "VERSION_CONFLICT")
            db.execute("INSERT INTO annotation_versions(task_id,version,stage,payload,action,saved_at) VALUES (?,?,?,?,?,?)",
                       (task_id, new_version, row["stage"], payload, action, time.time()))
            db.execute("INSERT INTO audit_events(event,actor,queue_id,task_id,at) VALUES (?,?,?,?,?)",
                       (action.upper(), who["username"], row["queue_id"], task_id, time.time()))
            if action == "submit" and row["stage"] == "A" and row["mode"] == "blind":
                db.execute("INSERT INTO tasks(queue_id,case_id,reader,stage) VALUES (?,?,?,'B')",
                           (row["queue_id"], row["case_id"], row["reader"]))
        return {"ok": True, "version": new_version, "status": action}

    @app.put("/api/tasks/{task_id}/draft")
    def save(task_id: int, data: SaveRequest, who=Depends(user)):
        return persist(task_id, data, "draft", who)

    @app.post("/api/tasks/{task_id}/submit")
    def submit(task_id: int, data: SaveRequest, who=Depends(user)):
        return persist(task_id, data, "submit", who)

    @app.post("/api/tasks/{task_id}/revise")
    def revise(task_id: int, data: RevisionRequest, who=Depends(user)):
        validate_payload(data.payload, True)
        with connect(db_path) as db:
            row = task_access(db, task_id, who)
            if data.payload.get("report_agreement") and (not row["report_path"] or
                (row["mode"] == "blind" and row["stage"] == "A")):
                raise HTTPException(400, "REPORT_AGREEMENT_UNAVAILABLE")
            if row["status"] != "submitted" or row["reader"] != who["username"]:
                raise HTTPException(409, "REVISION_NOT_ALLOWED")
            if row["version"] != data.version:
                raise HTTPException(409, "VERSION_CONFLICT")
            version = data.version + 1
            payload = json.dumps(data.payload, separators=(",", ":"))
            updated = db.execute("UPDATE tasks SET draft_json=?,version=?,submitted_at=? WHERE task_id=? AND version=?",
                (payload, version, time.time(), task_id, data.version))
            if updated.rowcount != 1:
                raise HTTPException(409, "VERSION_CONFLICT")
            db.execute("INSERT INTO annotation_versions(task_id,version,stage,payload,action,saved_at) VALUES (?,?,?,?,?,?)",
                (task_id, version, row["stage"], payload, "revision", time.time()))
            db.execute("INSERT INTO audit_events(event,actor,queue_id,task_id,at,detail_code) VALUES (?,?,?,?,?,?)",
                ("REVISION", who["username"], row["queue_id"], task_id, time.time(), data.reason[:80]))
        return {"ok": True, "version": version, "status": "submitted"}

    @app.post("/api/tasks/{task_id}/skip")
    def skip(task_id: int, data: SkipRequest, who=Depends(user)):
        raise HTTPException(410, "SKIP_DISABLED")

    @app.get("/api/queues/{queue_id}/summary")
    def summary(queue_id: str, who=Depends(user)):
        with connect(db_path) as db:
            rows = db.execute("SELECT stage,status,COUNT(*) AS n FROM tasks WHERE queue_id=? AND reader=? "
                              "GROUP BY stage,status", (queue_id, who["username"])).fetchall()
        return {"counts": [dict(r) for r in rows]}

    @app.get("/api/export/{queue_id}/training")
    def training_export(queue_id: str, who=Depends(admin)):
        with connect(db_path) as db:
            q = db.execute("SELECT * FROM queues WHERE queue_id=?", (queue_id,)).fetchone()
            if not q:
                raise HTTPException(404, "QUEUE_NOT_FOUND")
            roles = db.execute("SELECT DISTINCT c.role FROM tasks t JOIN cases c ON c.case_id=t.case_id WHERE t.queue_id=?",
                               (queue_id,)).fetchall()
            if q["mode"] != "assisted" or any(r["role"] != "development" for r in roles):
                raise HTTPException(403, "TEST_EXPORT_FORBIDDEN")
            rows = db.execute("SELECT t.case_id,t.stage,t.version,t.draft_json,c.image_sha256,c.ct_path,c.mask_path FROM tasks t JOIN cases c ON c.case_id=t.case_id "
                              "WHERE t.queue_id=? AND t.status='submitted' AND t.stage='A'", (queue_id,)).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["draft_json"])
            mapped = []
            lesions = payload.get("lesions", [])
            previews = jepa_preview_many(_allowed_path(row["ct_path"], roots),
                _allowed_path(row["mask_path"], roots) if row["mask_path"] else None,
                [(lesion["ras_mm"], lesion.get("ras_hi_mm") if lesion["kind"] == "box" else None) for lesion in lesions]) if lesions else []
            for lesion, mapping in zip(lesions, previews):
                mapped.append({"lesion_id": lesion["id"], "geometry_ras": lesion,
                               "model_mapping": mapping, "selected_blocks": lesion.get("jepa_blocks"),
                               "loss_mask": payload.get("state") == "positive"})
            result.append({"case_id": row["case_id"], "image_sha256": row["image_sha256"],
                           "disease": q["disease"], "state": payload.get("state"),
                           "annotation_version": row["version"],
                           "lesions": mapped, "region_negative_complete": bool(payload.get("region_exhaustive"))})
        return {"queue_hash": q["frozen_hash"], "coordinate_system": "RAS", "records": result}

    @app.get("/api/export/{queue_id}/jepa-blocks.csv")
    def jepa_blocks_csv(queue_id: str, who=Depends(admin)):
        snapshot = training_export(queue_id, who)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(("queue_hash", "case_id", "image_sha256", "annotation_version", "disease",
                         "lesion_id", "model_version", "view", "block_i", "block_j", "block_k",
                         "selection_source", "training_eligible"))
        for record in snapshot["records"]:
            for lesion in record["lesions"]:
                preview = lesion["model_mapping"]
                selected = lesion.get("selected_blocks")
                for view in preview["views"]:
                    name = view["view"]
                    override = (selected.get("views", {}).get(name) if isinstance(selected, dict) and
                                selected.get("model_version") == preview["model_version"] else None)
                    mapping = view["mapping"]
                    automatic = ([entry["block_ijk"] for entry in mapping] if isinstance(mapping, list) else
                                 [mapping["block_ijk"]] if mapping.get("inside") else [])
                    blocks = override if override is not None else automatic
                    for i, j, k in sorted({tuple(block) for block in blocks}):
                        writer.writerow((snapshot["queue_hash"], record["case_id"], record["image_sha256"],
                                         record["annotation_version"], record["disease"], lesion["lesion_id"],
                                         preview["model_version"], name, i, j, k,
                                         "manual" if override is not None else "automatic", bool(lesion["loss_mask"])))
        return Response(output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=jepa_blocks.csv"})

    @app.get("/api/export/{queue_id}/bulla-lobes.csv")
    def bulla_lobe_csv(queue_id: str, who=Depends(admin)):
        with connect(db_path) as db:
            q=db.execute("SELECT disease,mode,frozen_hash FROM queues WHERE queue_id=?",(queue_id,)).fetchone()
            if not q:raise HTTPException(404,"QUEUE_NOT_FOUND")
            if q["disease"]!="pulmonary_bulla" or q["mode"]!="assisted":
                raise HTTPException(403,"BULLA_DEVELOPMENT_QUEUE_REQUIRED")
            rows=db.execute("SELECT t.case_id,t.version,t.draft_json,c.image_sha256,c.role FROM tasks t "
                            "JOIN cases c ON c.case_id=t.case_id WHERE t.queue_id=? AND t.status='submitted' AND t.stage='A'",
                            (queue_id,)).fetchall()
        output=io.StringIO();writer=csv.writer(output)
        writer.writerow(("queue_hash","case_id","image_sha256","annotation_version","lesion_id","disease",
                         "lobe","location_status","label_source","report_viewed","training_eligible"))
        for row in rows:
            if row["role"]!="development":raise HTTPException(403,"TEST_EXPORT_FORBIDDEN")
            payload=json.loads(row["draft_json"])
            for lesion in payload.get("lesions", []):
                confirmed=bool(lesion.get("bulla_location_confirmed"))
                lobes=lesion.get("bulla_lobes", []) if confirmed else [""]
                for lobe in lobes:
                    writer.writerow((q["frozen_hash"],row["case_id"],row["image_sha256"],row["version"],
                                     lesion["id"],"pulmonary_bulla",lobe,"confirmed" if confirmed else "pending_review",
                                     "human_ct_review" if confirmed else "none",bool(payload.get("viewed_report")),
                                     bool(confirmed and payload.get("state")=="positive")))
        return Response(output.getvalue(),media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":"attachment; filename=bulla_lobe_locations.csv"})

    if frontend_dist and frontend_dist.exists():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    return app


def from_environment() -> FastAPI:
    db = Path(os.environ.get("ANNOTATOR_DB", "./runtime/annotations.sqlite3"))
    roots = tuple(Path(p) for p in os.environ.get("ANNOTATOR_DATA_ROOTS", "./runtime/data").split(os.pathsep))
    frontend = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    model_path = os.environ.get("ANNOTATOR_MODEL_CONFIG")
    return create_app(db, roots, frontend, Path(model_path) if model_path else None)


app = from_environment()
