from scripts.package_safe import allowed, scan, package


def test_allowlist_rejects_patient_artifacts():
    for name in ("runtime/case.nii.gz", "runtime/report.txt", "runtime/annotations.sqlite3",
                 "frontend/dist/assets/../../../private.csv", "/tmp/raw.dcm"):
        assert not allowed(name)
    assert allowed("annotator/app.py")


def test_content_scan_rejects_synthetic_identifier():
    try:
        scan("README.md", b"fake identifier " + b"1234567890" + b"12345678")
    except ValueError as exc:
        assert str(exc) == "SENSITIVE_PATTERN"
    else:
        raise AssertionError("scanner missed synthetic identifier")


def test_package_refuses_to_overwrite(tmp_path):
    destination = tmp_path / "existing.zip"
    destination.write_bytes(b"synthetic-existing-package")
    try:
        package(destination)
    except ValueError as exc:
        assert str(exc) == "PACKAGE_OUTPUT_ALREADY_EXISTS"
    else:
        raise AssertionError("package overwrote an existing delivery")
    assert destination.read_bytes() == b"synthetic-existing-package"
