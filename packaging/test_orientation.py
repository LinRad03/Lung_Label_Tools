"""Synthetic RAS marker test for the CPU viewer's radiological display."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import nibabel as nib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lung-label-orientation-") as tmp:
        path = Path(tmp) / "right_marker.nii.gz"
        image = np.zeros((64, 64, 32), dtype=np.int16)
        image[56, 32, 16] = 1000  # positive RAS X: patient's right
        nib.save(nib.Nifti1Image(image, np.eye(4)), path)
        subprocess.run(["node", str(ROOT / "app/frontend/test_fallback.mjs"), str(path)],
                       cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
