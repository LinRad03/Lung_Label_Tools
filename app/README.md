# Lung-Vol-JEPA CT research annotator

Version 1.0 is an isolated Linux browser application. It uses a local FastAPI/SQLite server and [NiiVue](https://niivue.com/docs/) 0.69.0 for NIfTI multiplanar reading. It is a research annotation tool, not a clinical diagnostic device.

## What works

- A manifest links each research ID to one CT, patient group, examination group, optional **de-identified** report and optional pseudo-mask. No path or filename matching is performed.
- Admins preview aggregate queue eligibility, then freeze a disease-specific queue by patient group and seed. Two readers receive independent tasks. Skips retain their reason and never become negatives.
- Development mode may show a de-identified report and an optional pseudo-mask overlay. The importer requires CT and mask shape/affine agreement before showing it. Blind test stage A serves the CT alone. The server returns neither report nor mask in stage A. Stage A submission is immutable; stage B is a separate report review. An adjudicator may receive tasks only after both independent A submissions.
- NiiVue shows axial, coronal and sagittal slices with orientation labels and CT window presets. Point and box annotations store original-image RAS millimetres. State and location are separate fields. Drafts are versioned; conflicting saves fail rather than overwrite.
- Training export rejects blind/test queues on the server. A versioned model transform can map points or boxes to model blocks and locate the current point in the UI. Without it the UI and export make no JEPA-block claim.

## Linux setup

Use Python 3.10 or later and Node 20. From this directory:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
cd frontend && npm ci && npm run build && cd ..
```

The Python file pins direct dependencies; the npm lockfile pins the full frontend dependency tree. The installed `NiiVue` license is in `third_party/NiiVue-LICENSE`.

Create a controlled runtime directory and accounts. The manifest and database must remain on the server. `ANNOTATOR_DATA_ROOTS` is a colon-separated allowlist of directories containing approved files.

```bash
export ANNOTATOR_DB=/controlled/annotator/annotations.sqlite3
export ANNOTATOR_DATA_ROOTS=/controlled/approved_ct:/controlled/deidentified_reports
PYTHONPATH=. .venv/bin/python -m annotator.cli --db "$ANNOTATOR_DB" add-user admin admin
PYTHONPATH=. .venv/bin/python -m annotator.cli --db "$ANNOTATOR_DB" add-user reader1 reader1
PYTHONPATH=. .venv/bin/python -m annotator.cli --db "$ANNOTATOR_DB" add-user reader2 reader2
PYTHONPATH=. .venv/bin/python -m annotator.cli --db "$ANNOTATOR_DB" add-user arbiter adjudicator
PYTHONPATH=. .venv/bin/python -m annotator.cli --db "$ANNOTATOR_DB" import-manifest /controlled/annotator/approved_manifest.json --data-root /controlled/approved_ct --data-root /controlled/deidentified_reports
bash scripts/run_background.sh
```

Open `http://127.0.0.1:8765` on the server or through an approved SSH tunnel. The default service binds only to localhost and disables HTTP access logs. To stop this tool only: `bash scripts/stop_local.sh`. To resume, rerun `bash scripts/run_background.sh`; SQLite drafts and queues persist. Do not expose the service directly to the public Internet. Terminate TLS and enforce site authentication at an approved internal proxy if remote access is required.

## Explicit manifest

Each JSON array row uses `case_id`, `project`, `cohort`, `role` (`development`, `test`, `review`), `patient_group`, `examination_group`, `ct_path`, `deidentified_report_path`, `mask_path`, `linkage_status` (`verified` or an unresolved code), and `source_version`. `image_sha256` may be supplied and must match. Group IDs must be verified by the data custodian; filename-derived hashes do not establish patient identity. A case with ambiguous linkage is unavailable to the queue. The importer rejects a patient group assigned to both development and test roles within the same project, but cross-system identity still requires upstream governance.

`case_id` must be a research-only identifier. Raw reports must never be listed under `deidentified_report_path`. The importer checks the explicit path allowlist and file type, not whether free text has been properly de-identified. That review is required before real-data import. The browser does not receive filesystem paths or patient-group keys.

For NIfTI, image metadata is read from its affine. RAS world coordinates are authoritative. Model mapping requires a versioned JSON containing `version`, `resampled_affine_ras`, `input_shape_ijk`, `block_shape_ijk`, `crop_start_ijk`, and `pad_before_ijk`. Set `ANNOTATOR_MODEL_CONFIG` only after checking the exact JEPA input pipeline and source-to-model transform. A point maps to a block; it does not create a complete lesion mask. Box-to-block fractions are geometric overlap approximations.

## Synthetic demonstration and tests

```bash
PYTHONPATH=. .venv/bin/python scripts/make_synthetic_demo.py
ANNOTATOR_DB=runtime/synthetic_demo/annotations.sqlite3 \
ANNOTATOR_DATA_ROOTS=runtime/synthetic_demo/data bash scripts/start_local.sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_app.py
```

The synthetic demo credentials are `demo_reader1` or `demo_reader2` with `synthetic-only-password`. They are for the synthetic localhost demo only. `tests/browser_smoke.py` drives an existing headless Chrome on CDP port 9223 and checks CT loading, point annotation, draft save, blind-stage submission and automatic next task. Never deploy the demo database with real data.

## Controlled development annotation pilot

A development pilot was created from the frozen training manifest and later extended to 535 distinct patient groups and CTs. The original 100-case nodule queue remains intact. The current service is on `http://127.0.0.1:8767`; its database and exact-linked original reports remain in the controlled pilot directory. The browser shows research case IDs only. For these development-assisted queues, the report panel displays the original linked report text in this localhost-only deployment. Other queued diseases have independent tasks on the same CTs; a missing report finding is not a negative label.

The image panel has a bottom zoom slider (100–800%), synchronized with pinch/Ctrl-scroll zoom in both CPU and WebGL viewers. The last zoom ratio is remembered for the same reader and applied to the next patient and after page reload. The top review progress bar counts submitted tasks for the selected queue; skipped tasks are shown separately and do not count as completed reviews. The report panel highlights terms for the selected disease without changing the original report text or inferring a diagnosis from a mention. A negated or uncertain mention remains in its original sentence. Submitted development annotations export their original RAS geometry and a versioned JEPA target-block preview; a planar box receives one model voxel of thickness solely for block intersection. This is not a three-dimensional lesion mask. The controlled database and report files are never included in the source package.

For the development bulla queue, the last queue and task number are remembered in the local browser for resuming work; no CT or report is stored there. The bottom-right buttons and queue slider browse previous and next patients; the current 535-case queue has one CT per patient group. A submitted case opens read-only, and a deliberate revision creates a new annotation version while retaining the original. The JEPA panel automatically shows blocks intersecting each point or planar box. The reader can toggle blocks, expand or contract a connected selection, and view the selected blocks as a translucent 3D mesh. The 3D button uses NiiVue WebGL2 volume rendering with a translucent CT and optional five-lobe nnUNet overlay. CPU slice viewing remains available when WebGL2 is absent. Hardware acceleration depends on the browser graphics context; the browser launcher now tries hardware WebGL by default, and `ANNOTATOR_SOFTWARE_GL=1` selects SwiftShader.

Each bulla lesion has a separate lobe confirmation control. nnUNet supplies a suggestion, which remains unconfirmed until the reader selects and confirms one or more lobes and submits the case or a revision. The administrator-only `GET /api/export/DEV_PULMONARY_BULLA_535_V1/bulla-lobes.csv` route exports a controlled CSV. A local controlled snapshot can be created with `scripts/export_bulla_locations.py --db <controlled-db> --queue DEV_PULMONARY_BULLA_535_V1 --out <controlled-output.csv>`. Rows marked `pending_review` are excluded from training; confirmed rows carry `label_source=human_ct_review`. The CSV stays on the server and is not in the patient-free ZIP.

The viewer uses NiiVue WebGL when a context is available and a CPU canvas fallback for axis-aligned CTs otherwise. Both paths open each patient in the axial view by default. Clicking 3D while in CPU compatibility mode checks WebGL2, saves any ordinary draft, switches to the WebGL viewer and restores the same patient in 3D. If the browser does not provide WebGL2, use the project Chrome 3D launcher; coronal, sagittal and multiplanar 2D views remain available. Select **框选 · 拖动** to draw a live cyan box that remains after release, or **点选 · 单击** for a persistent green cross. Trackpad pinch or Ctrl+scroll zooms; ordinary two-finger scrolling changes slices. **上一例**, **下一例**, and the queue case selector support rapid review. Previously submitted tasks open read-only until the reader starts a versioned revision; historical skipped tasks are editable. Saved marks reappear on their matching slices, and **定位** returns to the saved location. Original CT RAS coordinates remain authoritative.

For an assisted development task with a matching nnUNet pseudo-mask, **显示五肺叶伪标签** overlays five distinct lobe colors. The lesion list automatically reports which pseudo-mask lobes intersect a point or planar box; this hint remains separate from the doctor's entered region. Historical mask labels 6–10 are excluded from lobe names. The software WebGL browser and CPU fallback both passed real-CT technical checks. The headless test used software WebGL, so hardware GPU acceleration depends on the reader's browser and graphics setup.

The `DEV_COPD_CT_REVIEW_535_V1` queue gives reader `1` 535 development CTs already in the pilot; no new patient was imported. Exact-linked reports contain zero literal COPD/慢阻肺 mentions and 41 emphysema mentions. These are search counts, not COPD diagnoses. The task is a review of COPD-related CT findings; clinical COPD diagnosis requires separate clinical and lung-function evidence.

The source manifest hash is checked before queue construction. The pilot selects one CT per patient group with a fixed seed and never samples from NEW887. Cross-system patient independence from NEW887 remains unverified, so this development queue is for training-side annotation and software assessment only. It is not an independent test reference.

For a fresh authorized server, set `ANNOTATOR_SOURCE_GATE` to the accepted source-gate JSON and `ANNOTATOR_PILOT_DIR` to a private directory. Then run `PYTHONPATH=. .venv/bin/python -m scripts.prepare_annotation_pilot` once and `PYTHONPATH=. .venv/bin/python -m scripts.run_annotation_pilot` to serve it. To attach report cues, set `ANNOTATOR_REPORT_TABLE` to the approved local report table and run `PYTHONPATH=. .venv/bin/python -m scripts.attach_report_cues`; restart the pilot service afterward. The preparation script is idempotent for the frozen manifest and refuses a changed one. Direct reader and one-use link authentication remain available in the generic application as alternate deployment modes.

Technical acceptance includes synthetic geometry and lobe mapping tests. Disposable headless Chrome sessions loaded real development CTs and reports, placed points and boxes, checked the visible pseudo-mask colors, case navigation, zoom, JEPA preview and read-only review of a submitted task. No QA label was submitted. Clinician usability and annotation quality remain unassessed.

## Current limits

- NIfTI is implemented. DICOM series selection and partial-volume streaming are not yet implemented. Report disease keywords are highlighted, and checked JEPA blocks can be edited in the browser.
- There is no doctor usability validation yet. The real-data check validates loading, coordinates, masks and visible marks, not clinical correctness. The displayed lobe map is a pseudo-label, not an expert segmentation.
- The app has password login, short-lived server sessions and localhost binding. Institutional deployment needs the site's authentication, TLS, backup, retention and audit policy.
- A full independent test reference still requires real blinded readers and adjudication; synthetic test results are software acceptance evidence only.

## Review and block records (v1.18)

The report panel is left of the CT on desktop. A disease state must be selected before submission; a negative state additionally requires the completed-read checkbox. The skip control and skip API are disabled. Previously skipped tasks remain in the queue history and can be opened for a new annotation. Previously submitted tasks show **修改已提交标注（保留历史）** above the disease state controls; revisions retain the original version. The report comparison field is an explicit reader judgement (agree, disagree or uncertain), shown only when the linked report is available. Clicking the top review progress bar opens the skipped and reader-recorded report-disagreement lists; selecting an entry reopens that case. Historical annotations without a comparison response are not inferred to agree or disagree.

For new lung parenchymal marks, a loaded five-lobe nnUNet pseudo-mask is checked before accepting the mark. A mark entirely outside all five pseudo-lobes is removed from active lesions and its RAS geometry is retained in the controlled draft as `rejected_lesions`. Unsubmitted drafts are checked again when the mask loads. This rule does not apply to pleural, mediastinal or pericardial tasks, or to cases without a usable mask. Previously submitted versions are not silently rewritten; opening a revision applies the same check to the editable copy. A pseudo-mask boundary is a technical filter, not expert lesion validation.

Administrators can obtain one row per selected JEPA block from `GET /api/export/{queue_id}/jepa-blocks.csv`. The CSV is a controlled patient-level training record, includes the frozen model mapping version and manual/automatic selection source, and stays on the server. It is not included in the source ZIP.

## Automatic comparison and right-click pseudo-mask (v1.19)

An accepted new mark sets the editable disease state to positive. A mark rejected for lying outside a loaded lobe pseudo-mask does not set a positive state. On assisted tasks with an available report, the server compares the selected disease state with versioned, conservative report mention rules. Where a conclusion heading exists, that section is compared; otherwise the whole report is compared. A positive annotation with no disease mention in the compared section is suggested as discordant. Explicit matching or conflicting states are suggested as concordant or discordant; uncertain, historical, or contradictory wording produces an uncertain suggestion. An unmentioned disease is never converted into an explicit report negative. The reader can change the suggestion with the report comparison controls; a manual choice is preserved until the disease state changes. Submitted versions remain unchanged until a reader explicitly revises them. The progress panel includes automatic discordance for older submitted annotations without a recorded comparison.

Right-click in the CT canvas toggles the five-lobe pseudo-mask overlay where one is available. The browser menu is suppressed only over the image canvas. These report rules and pseudo-labels support review and are not clinically validated reference labels.

## Submission and reading controls (v1.20)

An accepted drawing marks the editable task positive and the CT reading complete. A negative decision requires checking the completed-read box. The report comparison offers only **一致** and **不一致**; when the report text is ambiguous, neither is preselected and the reader may choose. The navigation crosshair appears briefly after a left click and then clears; saved lesion marks remain visible. Draft saves and submission are serialized, so a pending autosave cannot submit a stale task version. Up to 1000 marks can be saved per task; existing drafts and submitted versions are retained. A synthetic browser test submits during a delayed autosave, while the real-CT browser check does not write a clinical annotation.

## Review, layout and 3D controls (v1.21)

The review bar shows a submitted percentage. Clicking it opens counts and case links for report agreement, disagreement and historical skips; an undetermined count remains separate when report comparison cannot establish either state. The report occupies the upper half of the left panel and the current case's marked locations occupy the lower half. A vertical slice control sits at the image's right edge. The point, box-start and box-end command buttons are hidden; drawing on the image and JEPA block preview remain available. In 3D, left drag rotates through NiiVue and right vertical drag changes volume scale. Right drag also zooms planar views; a right click without drag retains the lobe-overlay toggle.

The progress title is a plain clickable element. Its former label association with the progress bar could dispatch a second click and immediately close the panel; the browser acceptance now uses a physical mouse click on the title.

## Two-button image pan (v1.22)

Hold left and right mouse buttons together and drag to pan the CT in planar and 3D views. The gesture cancels any pending point or box drawing and does not toggle the lobe overlay. Left drag alone keeps the normal drawing or 3D rotation action; right drag alone keeps zoom, and a stationary right click keeps the lobe toggle. A read-only real-CT browser check verified planar and 3D pan without changing existing annotations. The project Chrome launcher is executable and can be run with `ANNOTATOR_PILOT_DIR` set to the controlled pilot directory.

Real-CT read-only browser checks passed for the report/annotation split, review navigation, 3D CT+lobe+block rendering, left-drag rotation and right-drag zoom. No QA annotation was submitted. The tested Chrome session reported software WebGL even with hardware and ANGLE GL flags; availability of NVIDIA GPUs for model computation does not establish browser hardware rendering. The current 3D view works through WebGL2, with this acceleration limit disclosed in the UI backend label.

## Fine annotation subgrids (v1.23)

The trained JEPA mapping remains a 4×4×4 grid over a 64³ input, with 16 input voxels per model block. The viewer can now show 16³ or 32³ annotation subgrids calculated from saved original-CT point and box coordinates. These subgrids provide a finer review overlay, not newly trained JEPA tokens or additional CT resolution. Existing editable 4³ model-block selections and original marks remain unchanged. A controlled recomputation processed 60 currently saved tasks and 487 marks at both fine resolutions, with zero unapproved CTs or failed mappings; the patient-level output remains under the private annotation directory. A real-CT read-only Chrome check switched through 16³, 32³ and 4³ successfully. The 3D mesh for fine grids is capped at 128 selected cells to avoid excessive geometry work; the planar grid remains available for larger selections. Hardware browser rendering is still unavailable on the tested Chrome display path, which reports software WebGL.

## Clinical left-right display (v1.23.1)

Axial and coronal views now use radiological display convention: the patient's right side (positive RAS X) appears on the viewer's left. Both NiiVue WebGL and the CPU fallback apply this presentation rule, including coordinate picking and annotation overlays. Sagittal orientation, superior-inferior display, original NIfTI affine and saved RAS annotations are unchanged. A synthetic orientation test and a real-CT read-only browser pointer test passed. The browser test confirmed that moving left to right in the axial view decreases RAS X; no clinical mark was submitted or rewritten.
