from __future__ import annotations

import hashlib
import csv
import io
import json
import sqlite3

import nibabel as nib
import numpy as np
import pytest
from fastapi.testclient import TestClient

from annotator.app import create_app, hash_password, import_manifest
from annotator.db import connect
from annotator.geometry import box_block_fractions, point_block, ras_to_voxel, voxel_to_ras
from annotator.report_consistency import compare


def test_report_consistency_conservative_rules():
    assert compare("双肺散在结节。", "pulmonary_nodule", "positive")["agreement"] == "agree"
    assert compare("未见肺结节。", "pulmonary_nodule", "positive")["agreement"] == "disagree"
    assert compare("未见肺结节。", "pulmonary_nodule", "negative")["agreement"] == "agree"
    assert compare("右肺纹理增多。", "pulmonary_nodule", "positive")["reason"] == "positive_not_mentioned"
    assert compare("右肺纹理增多。", "pulmonary_nodule", "negative")["agreement"] == "uncertain"
    assert compare("疑似肺结节。", "pulmonary_nodule", "positive")["agreement"] == "uncertain"
    assert compare("既往肺结节已消退。", "pulmonary_nodule", "positive")["agreement"] == "uncertain"
    conclusion = compare("检查所见：双肺结节。诊断意见：未见肺结节。", "pulmonary_nodule", "positive")
    assert conclusion["scope"] == "conclusion" and conclusion["agreement"] == "disagree"


@pytest.fixture
def setup_app(tmp_path):
    data = tmp_path / "approved"
    data.mkdir()
    img_path = data / "synthetic.nii.gz"
    affine = np.array([[0, -1.4, 0, 10], [0.7, 0, 0, -20], [0, 0, 2.5, 30], [0, 0, 0, 1.]])
    nib.save(nib.Nifti1Image(np.zeros((16, 20, 12), dtype=np.int16), affine), img_path)
    report = data / "synthetic_deidentified.txt"
    report.write_text("Synthetic finding in right lung.")
    mask_path = data / "synthetic_mask.nii.gz"
    nib.save(nib.Nifti1Image(np.ones((16, 20, 12), dtype=np.uint8), affine), mask_path)
    manifest = data / "manifest.json"
    manifest.write_text(json.dumps([
        dict(case_id="research001", project="demo", cohort="dev", role="development",
             patient_group="synthetic_patient_1", examination_group="synthetic_exam_1",
             ct_path=str(img_path), deidentified_report_path=str(report), mask_path=str(mask_path),
             linkage_status="verified", source_version="synthetic_v1"),
        dict(case_id="research002", project="demo", cohort="test", role="test",
             patient_group="synthetic_patient_2", examination_group="synthetic_exam_2",
             ct_path=str(img_path), deidentified_report_path=str(report), mask_path=None,
             linkage_status="verified", source_version="synthetic_v1"),
    ]))
    db = tmp_path / "db.sqlite3"
    app = create_app(db, (data,))
    assert import_manifest(db, manifest, (data,))["imported"] == 2
    with connect(db) as cx:
        for name, role in [("admin", "admin"), ("r1", "reader1"), ("r2", "reader2"), ("adj", "adjudicator")]:
            cx.execute("INSERT INTO users VALUES (?,?,?)", (name, role, hash_password("synthetic-password")))
    return db, TestClient(app)


def login(client, username):
    assert client.post("/api/login", json={"username": username, "password": "synthetic-password"}).status_code == 200


def test_password_free_one_use_link(setup_app, monkeypatch):
    db, _ = setup_app
    monkeypatch.setenv("ANNOTATOR_LINK_ONLY", "1")
    app = create_app(db, (db.parent / "approved",))
    token = "synthetic-access-link-token-" + "x" * 48
    with connect(db) as cx:
        cx.execute("INSERT INTO access_links VALUES (?,?,?,NULL)",
                   (hashlib.sha256(token.encode()).hexdigest(), "r1", 4102444800.0))
    with TestClient(app) as client:
        assert client.post("/api/login", json={"username": "r1", "password": "synthetic-password"}).status_code == 403
        assert client.get("/api/access-mode").json()["access_link_required"] is True
        first = client.get(f"/access/{token}", follow_redirects=False)
        assert first.status_code == 303 and first.headers["location"] == "/"
        assert client.get("/api/me").json()["username"] == "r1"
        assert client.get(f"/access/{token}", follow_redirects=False).status_code == 404


def test_local_direct_reader_cannot_admin_or_export(setup_app, monkeypatch):
    db, _ = setup_app
    monkeypatch.setenv("ANNOTATOR_DIRECT_USER", "r1")
    app = create_app(db, (db.parent / "approved",))
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        assert client.get("/api/me").json() == {"username": "r1", "role": "reader1"}
        assert client.post("/api/login", json={"username": "admin", "password": "synthetic-password"}).status_code == 403
        assert client.get("/api/export/anything/training").status_code == 403
        assert client.post("/api/queues/preview", json={}).status_code in {403, 422}
    with TestClient(app, client=("198.51.100.10", 12345)) as remote:
        assert remote.get("/api/me").status_code == 403


def freeze(client, queue_id, cohort, mode):
    login(client, "admin")
    res = client.post("/api/queues/freeze", json=dict(queue_id=queue_id, project="demo", cohort=cohort,
        disease="pulmonary_nodule", mode=mode, seed=42, patient_budget=10, readers=["r1", "r2"]))
    assert res.status_code == 200, res.json()
    assert len(res.json()["hash"]) == 64


def test_blinding_two_readers_and_export_gate(setup_app):
    db, client = setup_app
    freeze(client, "blindq", "test", "blind")
    login(client, "r1")
    first = client.get("/api/queues/blindq/next").json()
    tid = first["task_id"]
    assert first["stage"] == "A"
    assert "report_available" not in first and "mask_available" not in first
    assert client.get(f"/api/tasks/{tid}/report").status_code == 403
    assert client.post(f"/api/tasks/{tid}/report-consistency", json={"state": "positive"}).status_code == 403
    assert client.get(f"/api/tasks/{tid}/mask").status_code == 403
    assert client.get(f"/api/tasks/{tid}/image-meta").status_code == 200
    assert client.get(f"/api/tasks/{tid}/volume").status_code == 200
    draft = {"state": "positive", "read_complete": True, "lesions": [{"id": "l1", "kind": "point", "ras_mm": [1, 2, 3]}]}
    assert client.put(f"/api/tasks/{tid}/draft", json={"version": 0, "payload": draft}).status_code == 200
    assert client.put(f"/api/tasks/{tid}/draft", json={"version": 0, "payload": draft}).status_code == 409
    login(client, "r2")
    assert client.get(f"/api/tasks/{tid}").status_code == 404
    own = client.get("/api/queues/blindq/next").json()
    assert own["draft"] == {}
    login(client, "r1")
    assert client.post(f"/api/tasks/{tid}/submit", json={"version": 1, "payload": draft}).status_code == 200
    followup = client.get("/api/queues/blindq/next").json()
    assert followup["stage"] == "B"
    assert client.get(f"/api/tasks/{followup['task_id']}/report").status_code == 200
    login(client, "admin")
    assert client.get("/api/export/blindq/training").status_code == 403


def test_state_required_skip_disabled_and_historical_skip_can_be_reviewed(setup_app):
    db_path, client = setup_app
    freeze(client, "devq", "dev", "assisted")
    login(client, "r1")
    first = client.get("/api/queues/devq/next").json()
    assert first["report_available"] is True
    missing = {"state": None, "read_complete": True, "lesions": []}
    assert client.post(f"/api/tasks/{first['task_id']}/submit", json={"version": 0, "payload": missing}).status_code == 400
    bad = {"state": "negative", "read_complete": False, "lesions": []}
    assert client.post(f"/api/tasks/{first['task_id']}/submit", json={"version": 0, "payload": bad}).status_code == 400
    drawn_negative = {"state": "negative", "read_complete": True,
                      "lesions": [{"id": "synthetic_drawn", "kind": "point", "ras_mm": [-4, -14.4, 45]}]}
    assert client.post(f"/api/tasks/{first['task_id']}/submit", json={"version": 0, "payload": drawn_negative}).status_code == 400
    assert client.post(f"/api/tasks/{first['task_id']}/skip", json={"reason": "insufficient image quality"}).status_code == 410
    login(client, "admin")
    assert client.get("/api/export/devq/training").json()["records"] == []
    from annotator.db import connect
    with connect(db_path) as db:
        db.execute("UPDATE tasks SET status='skipped' WHERE task_id=?", (first["task_id"],))
    login(client, "r1")
    assert len(client.get("/api/queues/devq/review-list?kind=skipped").json()["tasks"]) == 1
    historical = client.get(f"/api/tasks/{first['task_id']}").json()
    assert historical["status"] == "skipped"
    complete = {"state": "uncertain", "read_complete": True, "lesions": []}
    assert client.post(f"/api/tasks/{first['task_id']}/submit", json={"version": 0, "payload": complete}).status_code == 200
    login(client, "admin")
    assert client.get("/api/export/devq/training").json()["records"][0]["state"] == "uncertain"


def test_submitted_development_label_exports_with_geometry(setup_app):
    _, client = setup_app
    freeze(client, "devq", "dev", "assisted")
    login(client, "r1")
    task = client.get("/api/queues/devq/next").json()
    suggestion = client.post(f"/api/tasks/{task['task_id']}/report-consistency",
                             json={"state": "positive"})
    assert suggestion.status_code == 200
    assert suggestion.json()["reason"] == "positive_not_mentioned"
    label = {"state": "positive", "read_complete": True, "report_agreement": "disagree",
             "lesions": [{"id": "synthetic_lesion", "kind": "point", "ras_mm": [-4, -14.4, 45]}]}
    assert client.post(f"/api/tasks/{task['task_id']}/submit",
                       json={"version": 0, "payload": label}).status_code == 200
    assert len(client.get("/api/queues/devq/review-list?kind=discordant").json()["tasks"]) == 1
    review_all = client.get("/api/queues/devq/review-list?kind=all").json()
    assert review_all["counts"]["discordant"] == 1
    assert review_all["tasks"][0]["review_category"] == "discordant"
    login(client, "admin")
    result = client.get("/api/export/devq/training").json()
    assert len(result["records"]) == 1
    assert result["records"][0]["state"] == "positive"
    assert result["records"][0]["lesions"][0]["geometry_ras"]["ras_mm"] == [-4, -14.4, 45]
    assert result["records"][0]["lesions"][0]["model_mapping"]["model_version"]
    assert result["records"][0]["annotation_version"] == 1
    block_csv = client.get("/api/export/devq/jepa-blocks.csv")
    assert block_csv.status_code == 200
    rows = list(csv.DictReader(io.StringIO(block_csv.text)))
    assert any(row["lesion_id"] == "synthetic_lesion" and row["view"] == "global" for row in rows)
    fine = client.post(f"/api/tasks/{task['task_id']}/jepa-preview",
                       json={"ras_mm": [-4, -14.4, 45], "grid_size": 32})
    assert fine.status_code == 200
    assert fine.json()["grid_role"] == "annotation_subgrid_only"
    assert fine.json()["views"][0]["block_shape_ijk"] == [2, 2, 2]


def test_auto_discordance_review_and_reader_override(setup_app):
    _, client = setup_app
    freeze(client, "devq", "dev", "assisted")
    login(client, "r1")
    task = client.get("/api/queues/devq/next").json()
    tid = task["task_id"]
    label = {"state": "positive", "read_complete": True, "lesions": []}
    assert client.post(f"/api/tasks/{tid}/submit", json={"version": 0, "payload": label}).status_code == 200
    review = client.get("/api/queues/devq/review-list?kind=discordant").json()["tasks"]
    assert len(review) == 1 and review[0]["agreement_source"] == "auto_v1"
    revised = {**label, "report_agreement": "agree", "report_agreement_source": "reader"}
    assert client.post(f"/api/tasks/{tid}/revise", json={"version": 1, "payload": revised,
        "reason": "Synthetic review correction"}).status_code == 200
    assert client.get("/api/queues/devq/review-list?kind=discordant").json()["tasks"] == []
    assert client.get("/api/queues/devq/review-list?kind=agree").json()["counts"]["agree"] == 1


def test_more_than_one_hundred_box_marks_can_submit(setup_app):
    _, client = setup_app
    freeze(client, "devq", "dev", "assisted")
    login(client, "r1")
    task = client.get("/api/queues/devq/next").json()
    marks = [{"id": f"synthetic_box_{i}", "kind": "box", "ras_mm": [-4, -14, 45],
              "ras_hi_mm": [-3, -13, 45]} for i in range(101)]
    payload = {"state": "positive", "read_complete": True, "lesions": marks}
    saved = client.put(f"/api/tasks/{task['task_id']}/draft", json={"version": 0, "payload": payload})
    assert saved.status_code == 200
    submitted = client.post(f"/api/tasks/{task['task_id']}/submit",
                            json={"version": saved.json()["version"], "payload": payload})
    assert submitted.status_code == 200


def test_submitted_label_can_be_revised_without_losing_original(setup_app):
    db_path, client = setup_app
    freeze(client, "devq", "dev", "assisted")
    login(client, "r1")
    task = client.get("/api/queues/devq/next").json()
    original = {"state": "positive", "read_complete": True,
                "lesions": [{"id": "lesion_1", "kind": "box", "ras_mm": [0.0, -15.0, 40.0],
                             "ras_hi_mm": [5.0, -10.0, 40.0]}]}
    assert client.post(f"/api/tasks/{task['task_id']}/submit", json={"version": 0, "payload": original}).status_code == 200
    preview = client.post(f"/api/tasks/{task['task_id']}/jepa-preview-batch",
                          json={"lesions": [{"ras_mm": [0, -15, 40], "ras_hi_mm": [5, -10, 40]}]})
    assert preview.status_code == 200
    assert preview.json()["previews"][0]["views"][0]["mapping"]
    changed = json.loads(json.dumps(original))
    changed["lesions"][0]["jepa_blocks"] = {"model_version": "report_anatomy_tutor_dev_v1_global_lobe_64_grid4",
                                            "views": {"global": [[1, 1, 1]]}}
    revised = client.post(f"/api/tasks/{task['task_id']}/revise",
                          json={"version": 1, "payload": changed, "reason": "adjusted block selection"})
    assert revised.status_code == 200 and revised.json()["version"] == 2
    assert client.post(f"/api/tasks/{task['task_id']}/revise",
                       json={"version": 1, "payload": changed, "reason": "stale copy"}).status_code == 409
    from annotator.db import connect
    with connect(db_path) as db:
        versions = db.execute("SELECT payload,action FROM annotation_versions WHERE task_id=? ORDER BY version",
                              (task["task_id"],)).fetchall()
    assert len(versions) == 2
    assert versions[0]["action"] == "submit" and "jepa_blocks" not in versions[0]["payload"]
    assert versions[1]["action"] == "revision"
    login(client, "admin")
    exported = client.get("/api/export/devq/training").json()["records"][0]
    assert exported["annotation_version"] == 2
    assert exported["lesions"][0]["selected_blocks"]["views"]["global"] == [[1, 1, 1]]


def test_bulla_lobe_csv_requires_confirmed_ct_location(setup_app):
    _,client=setup_app
    login(client,"admin")
    result=client.post("/api/queues/freeze",json=dict(queue_id="bullaq",project="demo",cohort="dev",
        disease="pulmonary_bulla",mode="assisted",seed=42,patient_budget=10,readers=["r1","r2"]))
    assert result.status_code==200
    login(client,"r1")
    task=client.get("/api/queues/bullaq/next").json()
    payload={"state":"positive","read_complete":True,"lesions":[{"id":"lesion_1","kind":"point",
             "ras_mm":[0.0,-15.0,40.0]}]}
    assert client.post(f"/api/tasks/{task['task_id']}/submit",json={"version":0,"payload":payload}).status_code==200
    assert client.get("/api/export/bullaq/bulla-lobes.csv").status_code==403
    login(client,"admin")
    text=client.get("/api/export/bullaq/bulla-lobes.csv").text
    assert "pending_review" in text and "False" in text
    login(client,"r1")
    payload["lesions"][0].update(bulla_lobes=["RUL"],bulla_location_confirmed=True)
    assert client.post(f"/api/tasks/{task['task_id']}/revise",json={"version":1,"payload":payload,
                       "reason":"confirmed CT lobe"}).status_code==200
    login(client,"admin")
    text=client.get("/api/export/bullaq/bulla-lobes.csv").text
    assert "RUL,confirmed,human_ct_review,False,True" in text


def test_affine_oblique_roundtrip_and_blocks():
    aff = [[0.7, -0.2, 0, 7], [0.2, 0.7, 0, -8], [0, 0, 2.5, 3], [0, 0, 0, 1]]
    ijk = [4.2, 6.7, 2]
    ras = voxel_to_ras(aff, ijk)
    assert np.allclose(ras_to_voxel(aff, ras), ijk, atol=1e-9)
    model = {"version": "synthetic_v1", "resampled_affine_ras": aff, "input_shape_ijk": [32, 32, 16],
             "block_shape_ijk": [8, 8, 8], "crop_start_ijk": [0, 0, 0], "pad_before_ijk": [0, 0, 0]}
    assert point_block(ras, model)["block_ijk"] == [0, 0, 0]
    assert len(box_block_fractions(voxel_to_ras(aff, [7, 7, 1]), voxel_to_ras(aff, [10, 10, 2]), model)) >= 2


def test_adjudication_after_two_submissions(setup_app):
    _, client = setup_app
    freeze(client, "blindq", "test", "blind")
    for reader in ("r1", "r2"):
        login(client, reader)
        task = client.get("/api/queues/blindq/next").json()
        result = client.post(f"/api/tasks/{task['task_id']}/submit", json={"version": 0,
            "payload": {"state": "negative", "read_complete": True, "lesions": []}})
        assert result.status_code == 200
    login(client, "admin")
    assert client.post("/api/queues/blindq/prepare-adjudication", json={"adjudicator": "adj"}).json()["created"] == 1
    login(client, "adj")
    task = client.get("/api/queues/blindq/next").json()
    assert task["stage"] == "adjudication"
    assert len(client.get(f"/api/tasks/{task['task_id']}/adjudication").json()["independent_annotations"]) == 2
    assert client.get(f"/api/tasks/{task['task_id']}/report").status_code == 403


def test_queue_preview_and_restart_resume(setup_app, tmp_path):
    db, client = setup_app
    login(client, "admin")
    request = dict(queue_id="devq", project="demo", cohort="dev", disease="pulmonary_nodule",
                   mode="assisted", seed=42, patient_budget=1, readers=["r1", "r2"])
    preview = client.post("/api/queues/preview", json=request)
    assert preview.status_code == 200
    assert preview.json()["eligible_patients"] == 1
    assert preview.json()["selection_uses_report_label"] is False
    assert client.post("/api/queues/freeze", json=request).status_code == 200
    login(client, "r1")
    task = client.get("/api/queues/devq/next").json()
    payload = {"state": "positive", "read_complete": True, "lesions": []}
    assert client.put(f"/api/tasks/{task['task_id']}/draft", json={"version": 0, "payload": payload}).status_code == 200
    fresh = TestClient(create_app(db, (tmp_path / "approved",)))
    login(fresh, "r1")
    resumed = fresh.get("/api/queues/devq/next").json()
    assert resumed["task_id"] == task["task_id"]
    assert resumed["version"] == 1 and resumed["draft"]["state"] == "positive"


def test_mask_and_model_grid_are_stage_and_config_bound(setup_app, tmp_path):
    db, client = setup_app
    freeze(client, "devq", "dev", "assisted")
    login(client, "r1")
    task = client.get("/api/queues/devq/next").json()
    assert task["mask_available"] is True
    assert client.get(f"/api/tasks/{task['task_id']}/mask").status_code == 200
    assert client.get("/api/model-grid").json() == {"enabled": False}
    assert client.post(f"/api/tasks/{task['task_id']}/map-point", json={"ras_mm": [0, 0, 0]}).status_code == 404
    model_file = tmp_path / "model.json"
    model_file.write_text(json.dumps({"version": "synthetic_v1", "resampled_affine_ras": np.eye(4).tolist(),
        "input_shape_ijk": [32, 32, 32], "block_shape_ijk": [8, 8, 8]}))
    configured = TestClient(create_app(db, (tmp_path / "approved",), model_config=model_file))
    login(configured, "r1")
    assert configured.get("/api/model-grid").json()["version"] == "synthetic_v1"
    mapped = configured.post(f"/api/tasks/{task['task_id']}/map-point", json={"ras_mm": [9, 9, 9]}).json()
    assert mapped["block_ijk"] == [1, 1, 1]


def test_misaligned_pseudo_mask_is_rejected(setup_app, tmp_path):
    _, _client = setup_app
    data = tmp_path / "approved"
    wrong = data / "wrong_mask.nii.gz"
    nib.save(nib.Nifti1Image(np.zeros((9, 9, 9), dtype=np.uint8), np.eye(4)), wrong)
    row = json.loads((data / "manifest.json").read_text())[0]
    row["mask_path"] = str(wrong)
    candidate = data / "candidate.json"
    candidate.write_text(json.dumps([row]))
    other_db = tmp_path / "other.sqlite3"
    create_app(other_db, (data,))
    with pytest.raises(ValueError, match="MASK_GEOMETRY_MISMATCH"):
        import_manifest(other_db, candidate, (data,))
    with connect(other_db) as cx:
        assert cx.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
