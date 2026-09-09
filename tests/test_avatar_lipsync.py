import importlib.util
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture(scope="module")
def lipsync_module():
    pytest.importorskip("cv2")
    path = Path(__file__).resolve().parents[1] / "avatar" / "lipsync.py"
    spec = importlib.util.spec_from_file_location("avatar_lipsync", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_lower_face_mask_preserves_eyes_and_feathers_mouth(lipsync_module):
    mask = lipsync_module.lower_face_mask(200, 160)

    assert mask.shape == (200, 160, 1)
    assert float(mask[:80].max()) == 0
    assert float(mask[145, 80, 0]) > 0.9
    assert 0 < float(mask[90, 80, 0]) < 1


def test_blend_lower_face_never_changes_pixels_above_mask(lipsync_module):
    base = np.zeros((300, 300, 3), dtype=np.uint8)
    generated = np.full((256, 256, 3), 255, dtype=np.uint8)
    result = lipsync_module.blend_lower_face(base, generated, (50, 50, 250, 250))

    assert not result[:130].any()
    assert result[195, 150].mean() > 240
