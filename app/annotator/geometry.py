"""RAS millimetre coordinate transforms for annotations and model blocks."""
from __future__ import annotations

import math
from typing import Any

import numpy as np


def voxel_to_ras(affine: list[list[float]], ijk: list[float]) -> list[float]:
    a = np.asarray(affine, dtype=float)
    if a.shape != (4, 4) or abs(np.linalg.det(a[:3, :3])) < 1e-10:
        raise ValueError("INVALID_AFFINE")
    return (a @ np.r_[ijk, 1.0])[:3].tolist()


def ras_to_voxel(affine: list[list[float]], ras: list[float]) -> list[float]:
    a = np.asarray(affine, dtype=float)
    if a.shape != (4, 4) or abs(np.linalg.det(a[:3, :3])) < 1e-10:
        raise ValueError("INVALID_AFFINE")
    return (np.linalg.inv(a) @ np.r_[ras, 1.0])[:3].tolist()


def model_voxel(ras: list[float], model: dict[str, Any]) -> list[float]:
    """Map original RAS to model voxel via the actual model affine, then crop/pad.

    The caller must supply a versioned model input affine and crop/pad offsets.
    No claimed JEPA block is produced when this metadata is absent.
    """
    ijk = np.asarray(ras_to_voxel(model["resampled_affine_ras"], ras))
    crop = np.asarray(model.get("crop_start_ijk", [0, 0, 0]), dtype=float)
    pad = np.asarray(model.get("pad_before_ijk", [0, 0, 0]), dtype=float)
    return (ijk - crop + pad).tolist()


def point_block(ras: list[float], model: dict[str, Any]) -> dict[str, Any]:
    voxel = model_voxel(ras, model)
    shape = model["input_shape_ijk"]
    block = model["block_shape_ijk"]
    inside = all(0 <= voxel[d] < shape[d] for d in range(3))
    return {
        "inside": inside,
        "model_voxel_ijk": voxel,
        "block_ijk": [math.floor(voxel[d] / block[d]) for d in range(3)] if inside else None,
        "model_version": model["version"],
    }


def box_block_fractions(lo_ras: list[float], hi_ras: list[float], model: dict[str, Any]) -> list[dict[str, Any]]:
    """Axis-aligned RAS box intersection with input blocks; point is never a mask."""
    # All eight corners are needed when the model affine is oblique.
    corners = np.asarray([model_voxel([x, y, z], model)
                          for x in (lo_ras[0], hi_ras[0])
                          for y in (lo_ras[1], hi_ras[1])
                          for z in (lo_ras[2], hi_ras[2])])
    lo, hi = corners.min(axis=0), corners.max(axis=0)
    # A doctor may draw a 2D box on a single CT slice. Give that plane one
    # model voxel of thickness for block intersection; this is a spatial
    # preview, never a claim of 3D lesion volume or complete segmentation.
    for axis in range(3):
        if hi[axis] - lo[axis] < 1.0:
            center = (lo[axis] + hi[axis]) / 2.0
            lo[axis], hi[axis] = center - 0.5, center + 0.5
    shape = model["input_shape_ijk"]
    bs = model["block_shape_ijk"]
    out = []
    ranges = [range(max(0, math.floor(lo[d] / bs[d])),
                    min(math.ceil(shape[d] / bs[d]), math.ceil(hi[d] / bs[d]))) for d in range(3)]
    for i in ranges[0]:
        for j in ranges[1]:
            for k in ranges[2]:
                b0 = np.array([i * bs[0], j * bs[1], k * bs[2]], float)
                b1 = np.minimum(b0 + bs, shape)
                overlap = np.maximum(0, np.minimum(hi, b1) - np.maximum(lo, b0))
                vol = float(np.prod(overlap))
                if vol > 0:
                    out.append({"block_ijk": [i, j, k], "fraction": vol / float(np.prod(b1 - b0))})
    return out
