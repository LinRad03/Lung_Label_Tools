"""Build an explicit source + compiled-UI ZIP; no runtime or patient files."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXACT = {
    "README.md", "requirements.lock.txt", "frontend/index.html", "frontend/src.js",
    "frontend/style.css", "frontend/fallback.js", "frontend/gpu_overlay.js",
    "frontend/lobe_mask.js", "frontend/report_highlight.js", "frontend/block_selection.js", "frontend/block_mesh.js", "frontend/test_fallback.mjs", "frontend/test_lobe_mask.mjs", "frontend/test_report_highlight.mjs", "frontend/test_block_selection.mjs", "frontend/test_block_mesh.mjs",
    "frontend/package.json", "frontend/package-lock.json",
    "third_party/NiiVue-LICENSE", "scripts/start_local.sh", "scripts/run_background.sh",
    "scripts/stop_local.sh", "scripts/make_synthetic_demo.py", "scripts/package_safe.py",
    "scripts/prepare_real_smoke.py", "scripts/run_real_smoke.py",
    "scripts/prepare_annotation_pilot.py", "scripts/run_annotation_pilot.py",
    "scripts/provision_pilot_links.py",
    "scripts/attach_report_cues.py", "scripts/open_webgl_chrome.sh",
    "scripts/install_priority_review_535.py",
    "scripts/add_copd_review_queue.py", "scripts/export_bulla_locations.py",
    "scripts/recompute_annotation_subgrids.py",
    "tests/test_app.py", "tests/test_package.py", "tests/test_real_smoke.py",
    "tests/browser_smoke.py", "tests/browser_admin_smoke.py", "tests/real_browser_smoke.py",
    "tests/live_browser_priority_smoke.py", "tests/browser_submit_race_smoke.py",
    "annotator/__init__.py", "annotator/app.py", "annotator/cli.py", "annotator/db.py",
    "annotator/geometry.py", "annotator/jepa_mapping.py", "annotator/report_consistency.py",
    "tests/test_jepa_mapping.py", "frontend/dist/index.html",
}
FORBIDDEN = (".nii", ".nii.gz", ".dcm", ".mha", ".npy", ".npz", ".pt", ".pth",
             ".ckpt", ".safetensors", ".pkl", ".sqlite3", ".db", ".log")
IDENTIFIER = re.compile(rb"\b(?:\d{17}[0-9Xx]|\d{15})\b")
SECRET = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:api[_-]?key|secret[_-]?key)\s*[:=]\s*['\"][^'\"]{8,}", re.I)
TRUSTED_COMPILED_BUNDLE_SHA256 = "a1ee076486d584e7b6b6666730c2f566f705290b0a53946f9f6c835d7da4d894"


def allowed(rel: str) -> bool:
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts or rel.lower().endswith(FORBIDDEN):
        return False
    return rel in EXACT or (rel.startswith("frontend/dist/assets/") and len(path.parts) == 4
                            and path.suffix in {".js", ".css", ".wasm"})


def scan(rel: str, content: bytes) -> None:
    if not allowed(rel):
        raise ValueError("NOT_IN_PACKAGE_ALLOWLIST")
    compiled_vendor_bundle = (rel.startswith("frontend/dist/assets/index-") and
                              hashlib.sha256(content).hexdigest() == TRUSTED_COMPILED_BUNDLE_SHA256)
    # NiiVue's reproducible minified bundle contains numeric constants that resemble IDs.
    # All authored source remains subject to the identifier check; this exact bundle hash is frozen.
    if (IDENTIFIER.search(content) and not compiled_vendor_bundle) or SECRET.search(content):
        raise ValueError("SENSITIVE_PATTERN")
    if b"/home/" + b"gn1/" in content or b"/Users/" + b"id/" in content:
        raise ValueError("SENSITIVE_ABSOLUTE_PATH")


def package(output: Path) -> dict[str, object]:
    if output.exists() or output.with_name(output.name + ".partial").exists():
        raise ValueError("PACKAGE_OUTPUT_ALREADY_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(EXACT | {p.relative_to(ROOT).as_posix() for p in (ROOT / "frontend/dist/assets").glob("*")})
    rows = []
    for rel in files:
        path = ROOT / rel
        if not path.is_file():
            raise ValueError("REQUIRED_BUILD_FILE_MISSING")
        content = path.read_bytes()
        scan(rel, content)
        rows.append((rel, len(content), hashlib.sha256(content).hexdigest(), content))
    manifest_io = io.StringIO()
    writer = csv.writer(manifest_io)
    writer.writerow(("relative_path", "bytes", "sha256"))
    writer.writerows((rel, size, digest) for rel, size, digest, _ in rows)
    manifest = manifest_io.getvalue().encode()
    tmp = output.with_name(output.name + ".partial")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel, _, _, content in rows:
            zf.writestr(rel, content)
        zf.writestr("MANIFEST.csv", manifest)
    with tempfile.TemporaryDirectory() as temporary:
        with zipfile.ZipFile(tmp) as zf:
            names = zf.namelist()
            if len(set(names)) != len(names) or any(n.startswith("/") or ".." in Path(n).parts for n in names):
                raise ValueError("UNSAFE_ZIP_PATH")
            zf.extractall(temporary)
        for rel, size, digest, _ in rows:
            actual = (Path(temporary) / rel).read_bytes()
            scan(rel, actual)
            if len(actual) != size or hashlib.sha256(actual).hexdigest() != digest:
                raise ValueError("POST_EXTRACT_HASH_MISMATCH")
    tmp.replace(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n")
    return {"files": len(rows) + 1, "bytes": output.stat().st_size, "sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(package(args.out))
