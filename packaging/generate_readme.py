"""Generate the public README from the tested release contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = """# Lung Label Tools

Local research annotation tool for chest CT. It serves NIfTI volumes, linked reports and optional lobe pseudo masks through a browser at `127.0.0.1:8767` by default. It stores point/box marks in original CT RAS coordinates and shows 4³ JEPA model blocks alongside finer 16³/32³ **annotation** subgrids. No patient data or trained model weights are included in the release.

## Downloads

Tagged releases build three native artifacts:

| Platform | Artifact | Status |
| --- | --- | --- |
| Linux x86-64 | `LungLabelTools-linux-x86_64.tar.gz` | Built and smoke-tested on Linux |
| Windows x86-64 | `LungLabelTools-windows-x86_64.exe` | Built and smoke-tested by Windows CI |
| macOS Apple Silicon | `LungLabelTools-macos-arm64.dmg` | Built and smoke-tested by macOS CI |

Each artifact has a SHA256 sidecar. Windows and macOS binaries are unsigned; local security prompts may require user approval. A release is published only after all three native CI tests pass. The GitHub Actions run and its artifact checksums are the evidence of platform builds.

## Quick start

Linux: extract the tarball and run `LungLabelTools/LungLabelTools --demo`. Windows: run the EXE with `--demo`. macOS: open the application from the DMG. Demo mode creates **synthetic** cases only and opens a localhost browser; it never imports local CT data by itself.

For approved real data, choose a private database path and approved NIfTI root. Use the executable for all steps:

```text
LungLabelTools --db /private/annotations.sqlite3 --data-root /approved/ct --import-manifest /approved/manifest.json
LungLabelTools --db /private/annotations.sqlite3 --data-root /approved/ct --add-user reader1 --role reader1
LungLabelTools --db /private/annotations.sqlite3 --data-root /approved/ct --port 8767
```

On Windows, use Windows paths and `LungLabelTools-windows-x86_64.exe`; on macOS, use the executable inside the app bundle. The import manifest must contain reliable CT–report–mask links and patient groups. The server checks paths against the approved root, keeps the database outside the package and binds to localhost. A new queue is created in the admin interface after importing cases. Do not publish the local server to an unauthenticated network.

`--diagnose` prints the detected OS, architecture and visible NVIDIA devices. It reports CUDA visibility separately from acceleration. The packaged server currently does **not** use CUDA for image computation. The 3D CT viewer uses browser WebGL2 when available; actual hardware acceleration depends on the browser/display driver. On systems where Chrome selects software WebGL, 3D remains functional but slower. The UI reports the actual graphics backend. No GPU is needed for 2D annotation.

## Source build and checks

Use Python 3.10 and install `requirements-runtime.txt` plus `requirements-build.txt` in an isolated environment. Run `python packaging/generate_readme.py`, `python -m pytest -q app/tests/test_jepa_mapping.py`, `python packaging/build_release.py`, and `python packaging/smoke_release.py`. The build runs on the host OS; a Linux build cannot create a validated Windows or macOS binary. `.github/workflows/release.yml` builds and tests all three platforms on a `v*` tag and publishes a release only when every build succeeds.

The source in `app/` comes from a patient-free, hash-checked package. The annotation DB, CTs, reports, masks, case mappings, predictions and browser profile are deliberately excluded. Development pilot integration scripts under `app/scripts/` require project-specific controlled files and are not part of the generic quick start.

## Research limits

The five-lobe overlay is a nnUNet pseudo label when supplied. The 16³/32³ grids are annotation previews over the frozen 64³ JEPA input; they are not newly trained model tokens. No medical diagnosis, independent lesion detection, segmentation accuracy or clinical suitability is claimed. Preserve the original CT coordinates and versioned annotation records for downstream training.
"""


if __name__ == "__main__":
    (ROOT / "README.md").write_text(README)
    print("README_GENERATED")
