from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_browser_uses_source_aligned_transparent_mouth_patch():
    style = (REPO_ROOT / "demo" / "style.css").read_text()
    viseme_rule = style.split(".avatar-viseme-image {", 1)[1].split("}", 1)[0]

    assert "mask-image" not in viseme_rule
    assert "source-image coordinates" in viseme_rule
    assert "animation: avatar-idle 8s" in viseme_rule


def test_browser_drives_cached_visemes_without_requesting_lipsync_video():
    avatar = (REPO_ROOT / "demo" / "ui" / "avatar.js").read_text()

    assert "--ai-audio-level" in avatar
    assert "api/avatar/viseme/" in avatar
    assert "api/avatar/lipsync" not in avatar


def test_avatar_service_exposes_six_cached_liveportrait_visemes():
    service = (REPO_ROOT / "avatar" / "service.py").read_text()

    assert "VISEME_LIP_RATIOS = (0.0, 0.04, 0.08, 0.12, 0.17, 0.22)" in service
    assert "mouth_bgra = np.dstack((full_bgr, alpha))" in service
    assert '"alphaMasked": True' in service
    assert '@app.get("/viseme/{character}/{level}")' in service
    assert '@app.post("/lipsync")' not in service
