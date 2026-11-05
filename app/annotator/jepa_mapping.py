"""Preview the frozen development JEPA global/lobe target blocks in RAS mm.

Matches train_common_dev.view global extent, physical_views_v1.cube_grid,
in_lobe_center, and the 4x4x4 tutor target-block grid. No disease report is
used. This maps geometry only; a point is never a lesion segmentation.
"""
from __future__ import annotations

from pathlib import Path

import nibabel as nib
import numpy as np

from .geometry import box_block_fractions, point_block

VERSION = "report_anatomy_tutor_dev_v1_global_lobe_64_grid4"
ANNOTATION_GRID_VERSION = "source_coordinate_annotation_subgrid_v2"
SUPPORTED_GRID_SIZES = (4, 16, 32)


def _cube(center: np.ndarray, side: float) -> np.ndarray:
    spacing = side / 64.0
    a = np.eye(4, dtype=float)
    a[:3, :3] *= spacing
    a[:3, 3] = center - spacing * 63 / 2
    return a


def _inside_lobe_center(mask: np.ndarray, label: int) -> np.ndarray:
    points = np.argwhere(mask == label)
    if len(points) == 0:
        raise ValueError("MISSING_LOBE")
    midpoint = (points.min(0) + points.max(0)) / 2
    candidates = points[::max(1, len(points) // 4096)]
    distance = np.sum((candidates.astype(float) - midpoint) ** 2, axis=1)
    return candidates[int(np.argmin(distance))]


def _model(affine: np.ndarray, grid_size: int) -> dict:
    if grid_size not in SUPPORTED_GRID_SIZES:
        raise ValueError("UNSUPPORTED_GRID_SIZE")
    return {"version": VERSION if grid_size == 4 else f"{ANNOTATION_GRID_VERSION}_grid{grid_size}",
            "resampled_affine_ras": affine.tolist(), "input_shape_ijk": [64, 64, 64],
            "block_shape_ijk": [64 // grid_size] * 3}


def _case_views(ct_path: Path, mask_path: Path | None) -> list[tuple[str, np.ndarray]]:
    ct = nib.as_closest_canonical(nib.load(str(ct_path)))
    affine = ct.affine
    corners = np.array(np.meshgrid(*[(0, n - 1) for n in ct.shape[:3]], indexing="ij")).reshape(3, -1)
    world = affine[:3, :3] @ corners + affine[:3, 3:4]
    center = (world.min(1) + world.max(1)) / 2
    side = float(max(world.max(1) - world.min(1)) * 1.05)
    views = [("global", _cube(center, side))]
    if mask_path:
        mask_image = nib.as_closest_canonical(nib.load(str(mask_path)))
        if mask_image.shape[:3] != ct.shape[:3] or not np.allclose(mask_image.affine, affine, atol=1e-3):
            raise ValueError("MASK_GEOMETRY_MISMATCH")
        mask = np.asarray(mask_image.dataobj)
        for label, name in ((1, "LUL"), (2, "LLL"), (3, "RUL"), (4, "RML"), (5, "RLL")):
            if np.any(mask == label):
                voxel = _inside_lobe_center(mask, label)
                view_center = affine[:3, :3] @ voxel + affine[:3, 3]
                views.append((name, _cube(view_center, 96.0)))
    return views


def preview_many(ct_path: Path, mask_path: Path | None, geometries: list[tuple[list[float], list[float] | None]],
                 grid_size: int = 4) -> list[dict]:
    views = _case_views(ct_path, mask_path)
    results = []
    for ras_lo, ras_hi in geometries:
        out = []
        for name, view_affine in views:
            model = _model(view_affine, grid_size)
            mapped = (box_block_fractions(ras_lo, ras_hi, model) if ras_hi is not None
                      else point_block(ras_lo, model))
            out.append({"view": name, "mapping": mapped,
                        "input_affine_ras": view_affine.tolist(),
                        "input_shape_ijk": [64, 64, 64], "block_shape_ijk": [64 // grid_size] * 3})
        results.append({"model_version": _model(views[0][1], grid_size)["version"], "coordinate_system": "RAS", "views": out,
                        "grid_size": grid_size, "grid_role": "model_target_tokens" if grid_size == 4 else "annotation_subgrid_only",
                        "reference": "development JEPA target grid" if grid_size == 4 else "finer annotation preview; not trained JEPA tokens or lesion segmentation"})
    return results


def preview(ct_path: Path, mask_path: Path | None, ras_lo: list[float],
            ras_hi: list[float] | None = None, grid_size: int = 4) -> dict:
    return preview_many(ct_path, mask_path, [(ras_lo, ras_hi)], grid_size=grid_size)[0]
