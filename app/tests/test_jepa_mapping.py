from pathlib import Path

import nibabel as nib
import numpy as np

from annotator.jepa_mapping import preview


def test_jepa_preview_has_global_and_lobe_views(tmp_path: Path):
    affine = np.diag([1.5, 1.5, 2.0, 1.0])
    ct = np.zeros((32, 32, 32), dtype=np.float32)
    mask = np.zeros((32, 32, 32), dtype=np.uint8)
    for i in range(5):
        mask[2 + i * 6:7 + i * 6, 5:25, 5:25] = i + 1
    ct_path, mask_path = tmp_path / "ct.nii.gz", tmp_path / "mask.nii.gz"
    nib.save(nib.Nifti1Image(ct, affine), ct_path)
    nib.save(nib.Nifti1Image(mask, affine), mask_path)
    center = (affine @ np.array([16, 16, 16, 1.0]))[:3].tolist()
    point = preview(ct_path, mask_path, center)
    assert len(point["views"]) == 6
    assert point["views"][0]["mapping"]["inside"]
    box = preview(ct_path, mask_path, [x - 3 for x in center], [x + 3 for x in center])
    assert len(box["views"][0]["mapping"]) >= 1


def test_jepa_preview_without_mask_is_global_only(tmp_path: Path):
    path = tmp_path / "ct.nii.gz"
    nib.save(nib.Nifti1Image(np.zeros((20, 20, 20), dtype=np.float32), np.eye(4)), path)
    assert len(preview(path, None, [10.0, 10.0, 10.0])["views"]) == 1


def test_planar_box_has_block_intersection_without_claiming_3d_mask(tmp_path: Path):
    path = tmp_path / "ct.nii.gz"
    nib.save(nib.Nifti1Image(np.zeros((32, 32, 32), dtype=np.float32), np.eye(4)), path)
    result = preview(path, None, [8.0, 8.0, 16.0], [20.0, 20.0, 16.0])
    assert result["views"][0]["mapping"]
    assert all(0 < block["fraction"] <= 1 for block in result["views"][0]["mapping"])


def test_fine_annotation_grid_preserves_model_parent_and_is_not_model_tokens(tmp_path: Path):
    path = tmp_path / "ct.nii.gz"
    nib.save(nib.Nifti1Image(np.zeros((32, 32, 32), dtype=np.float32), np.eye(4)), path)
    point = [10.0, 12.0, 18.0]
    model = preview(path, None, point)
    fine16 = preview(path, None, point, grid_size=16)
    fine32 = preview(path, None, point, grid_size=32)
    assert model["grid_role"] == "model_target_tokens"
    assert fine16["grid_role"] == fine32["grid_role"] == "annotation_subgrid_only"
    parent = model["views"][0]["mapping"]["block_ijk"]
    assert [x // 4 for x in fine16["views"][0]["mapping"]["block_ijk"]] == parent
    assert [x // 8 for x in fine32["views"][0]["mapping"]["block_ijk"]] == parent
    assert preview(path, None, point, [14.0, 15.0, 18.0], grid_size=32)["views"][0]["mapping"]
