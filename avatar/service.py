"""Continuous idle portrait and cached-viseme service for Apple Silicon.

The heavy MLX runtime lives under ``.runtime/`` and its checkpoints under
``models/``. Both directories are machine-local. This small adapter is the part
owned by this repository: LivePortrait creates a seamless blinking idle loop;
LivePortrait also pre-renders sharp mouth poses selected by browser audio level.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = Path(
    os.environ.get(
        "AVATAR_RUNTIME_DIR",
        PROJECT_ROOT / ".runtime" / "fasterliveportrait-mlx",
    )
).expanduser().resolve()
CHECKPOINT_DIR = Path(
    os.environ.get(
        "AVATAR_CHECKPOINT_DIR",
        PROJECT_ROOT / "models" / "avatar" / "checkpoints",
    )
).expanduser().resolve()
JOB_ROOT = Path(
    os.environ.get("AVATAR_JOB_DIR", PROJECT_ROOT / ".runtime" / "avatar-jobs")
).expanduser().resolve()
PROFILE = os.environ.get("AVATAR_MLX_PROFILE", "quality").strip() or "quality"
IDLE_DIR = Path(
    os.environ.get("AVATAR_IDLE_DIR", PROJECT_ROOT / ".runtime" / "avatar-idle")
).expanduser().resolve()
VISEME_DIR = Path(
    os.environ.get("AVATAR_VISEME_DIR", PROJECT_ROOT / ".runtime" / "avatar-visemes")
).expanduser().resolve()
IDLE_DRIVER = RUNTIME_DIR / "assets" / "examples" / "driving" / "d13.mp4"
IDLE_CACHE_VERSION = 2
VISEME_CACHE_VERSION = 4
# Ratios above roughly 0.25 over-stretch the LivePortrait lip-retargeting
# keypoints on photographic faces: teeth remain sharp, but the upper lip and
# mouth corners visibly split. Six closer poses preserve motion without tearing.
VISEME_LIP_RATIOS = (0.0, 0.04, 0.08, 0.12, 0.17, 0.22)

PORTRAITS = {
    "xiaoman": PROJECT_ROOT / "demo" / "assets" / "avatars" / "xiaoman.png",
    "xiaoxue": PROJECT_ROOT / "demo" / "assets" / "avatars" / "xiaoxue.png",
}

if not RUNTIME_DIR.is_dir():
    raise RuntimeError(
        f"FasterLivePortrait-MLX runtime is missing: {RUNTIME_DIR}. "
        "Run ./scripts/bootstrap-avatar-macos.sh first."
    )

sys.path.insert(0, str(RUNTIME_DIR))
sys.path.insert(1, str(PROJECT_ROOT))
# The upstream pipeline resolves its bundled mask templates relative to the
# process working directory. Keep all project-owned paths absolute, then run
# the model from its pinned runtime directory.
os.chdir(RUNTIME_DIR)
os.environ.setdefault("FLIP_CHECKPOINT_DIR", str(CHECKPOINT_DIR))
os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from omegaconf import OmegaConf  # noqa: E402
from src.pipelines.gradio_live_portrait_pipeline import (  # noqa: E402
    GradioLivePortraitPipeline,
)
from src.runtime_assets import resolve_runtime_config  # noqa: E402

app = FastAPI(title="Local audio-driven avatar")
render_lock = asyncio.Lock()
pipeline: GradioLivePortraitPipeline | None = None


def _source_path(character: str) -> Path:
    source = PORTRAITS.get(character)
    if source is None:
        raise HTTPException(status_code=400, detail=f"Unknown character: {character}")
    if not source.is_file():
        raise HTTPException(status_code=503, detail=f"Portrait is missing: {source}")
    return source


def _load_pipeline() -> GradioLivePortraitPipeline:
    global pipeline
    if pipeline is not None:
        return pipeline

    config_path = RUNTIME_DIR / "configs" / "mlx_infer.yaml"
    infer_cfg = OmegaConf.load(config_path)
    # This service only drives human portraits. Resolve the pinned local human
    # weights directly instead of making the upstream helper require its animal
    # and legacy JoyVASA bundles too.
    resolve_runtime_config(infer_cfg, checkpoint_root=CHECKPOINT_DIR)
    infer_cfg.infer_params.flag_pasteback = True
    infer_cfg.infer_params.driving_multiplier = 1.0
    pipeline = GradioLivePortraitPipeline(cfg=infer_cfg)
    return pipeline


def _idle_paths(character: str) -> tuple[Path, Path]:
    _source_path(character)
    return IDLE_DIR / f"{character}.mp4", IDLE_DIR / f"{character}.json"


def _viseme_paths(character: str) -> tuple[list[Path], Path]:
    _source_path(character)
    directory = VISEME_DIR / character
    return [directory / f"{index}.png" for index in range(len(VISEME_LIP_RATIOS))], directory / "meta.json"


def _mouth_alpha(
    image_shape: tuple[int, ...], landmarks: np.ndarray
) -> tuple[np.ndarray, list[int]]:
    """Build a feathered mouth mask in source-image coordinates.

    The browser displays portrait media with ``object-fit: cover``. Embedding
    this mask in the PNG alpha channel makes the patch follow exactly the same
    crop as the idle video; a CSS mask positioned relative to the card does not.
    """
    height, width = image_shape[:2]
    # LivePortrait's 106-point layout defines 48/66 as the mouth corners,
    # 90/102 as the vertical lip pair, and 52/61 as the lip centre pair.
    # Use those actual pixels so a different portrait does not need tuning.
    lip_center = (landmarks[52] + landmarks[61]) / 2
    mouth_width = float(np.linalg.norm(landmarks[48] - landmarks[66]))
    mouth_height = float(np.linalg.norm(landmarks[90] - landmarks[102]))
    center_x, center_y = (int(round(value)) for value in lip_center)
    radius_x = max(12, int(mouth_width * 0.72))
    radius_y = max(8, int(mouth_width * 0.22), int(mouth_height * 1.6))

    alpha = np.zeros((height, width), dtype=np.uint8)
    cv2.ellipse(
        alpha,
        (center_x, center_y),
        (radius_x, radius_y),
        0,
        0,
        360,
        255,
        thickness=-1,
        lineType=cv2.LINE_AA,
    )
    blur_radius = max(3, int(mouth_width * 0.10))
    if blur_radius % 2 == 0:
        blur_radius += 1
    alpha = cv2.GaussianBlur(alpha, (blur_radius, blur_radius), 0)
    bounds = [
        max(0, center_x - radius_x - blur_radius),
        max(0, center_y - radius_y - blur_radius),
        min(width, center_x + radius_x + blur_radius),
        min(height, center_y + radius_y + blur_radius),
    ]
    return alpha, bounds


def _ensure_visemes(character: str) -> tuple[list[Path], Path]:
    """Cache sharp LivePortrait mouth poses once; runtime playback is model-free."""
    paths, meta_path = _viseme_paths(character)
    if meta_path.is_file() and all(path.is_file() for path in paths):
        try:
            cached = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            cached = {}
        if cached.get("cacheVersion") == VISEME_CACHE_VERSION:
            return paths, meta_path

    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=f"{character}-visemes-", dir=JOB_ROOT))
    pipe = _load_pipeline()
    pipe._ensure_models_loaded(is_animal=False)
    pipe.set_mlx_profile(PROFILE)
    try:
        source_landmarks = _face_landmarks(pipe, _source_path(character))
        rendered: list[np.ndarray] = []
        for index, ratio in enumerate(VISEME_LIP_RATIOS):
            _crop, full_rgb = pipe.execute_image(
                input_eye_ratio=0.35,
                input_lip_ratio=ratio,
                input_image=str(_source_path(character)),
                flag_do_crop=True,
            )
            rendered.append(full_rgb)

        alpha, mouth_bounds = _mouth_alpha(rendered[0].shape, source_landmarks)
        generated: list[Path] = []
        for index, full_rgb in enumerate(rendered):
            output = work_dir / f"{index}.png"
            full_bgr = cv2.cvtColor(full_rgb, cv2.COLOR_RGB2BGR)
            mouth_bgra = np.dstack((full_bgr, alpha))
            if not cv2.imwrite(str(output), mouth_bgra):
                raise RuntimeError(f"Could not write viseme image: {output}")
            generated.append(output)

        target_dir = meta_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        for source, target in zip(generated, paths, strict=True):
            source.replace(target)
        temporary_meta = work_dir / "meta.json"
        temporary_meta.write_text(json.dumps({
            "cacheVersion": VISEME_CACHE_VERSION,
            "lipRatios": VISEME_LIP_RATIOS,
            "width": int(full_rgb.shape[1]),
            "height": int(full_rgb.shape[0]),
            "mouthBounds": mouth_bounds,
            "alphaMasked": True,
            "engine": "LivePortrait lip retargeting",
        }))
        temporary_meta.replace(meta_path)
        return paths, meta_path
    finally:
        pipe._park_mlx_models()
        shutil.rmtree(work_dir, ignore_errors=True)


def _face_landmarks(pipe: GradioLivePortraitPipeline, frame_path: Path) -> np.ndarray:
    frame = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"Could not read portrait frame: {frame_path}")
    faces = pipe.model_dict["face_analysis"].predict(frame)
    if not len(faces):
        raise RuntimeError("Could not detect a face in the portrait frame")
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return pipe.model_dict["landmark"].predict(rgb, faces[0])


def _face_box(pipe: GradioLivePortraitPipeline, frame_path: Path) -> tuple[int, int, int, int]:
    frame = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"Could not read idle frame: {frame_path}")
    landmarks = _face_landmarks(pipe, frame_path)
    x_min, y_min = landmarks.min(axis=0)
    x_max, y_max = landmarks.max(axis=0)
    face_width = float(x_max - x_min)
    face_height = float(y_max - y_min)
    height, width = frame.shape[:2]
    x1 = max(0, int(x_min - face_width * 0.08))
    x2 = min(width, int(x_max + face_width * 0.08))
    y1 = max(0, int(y_min - face_height * 0.22))
    y2 = min(height, int(y_max + face_height * 0.12))
    return x1, y1, x2, y2


def _ensure_idle(character: str) -> tuple[Path, Path]:
    """Build one cached, silent, seamless eye-motion loop per portrait."""
    idle_path, meta_path = _idle_paths(character)
    if idle_path.is_file() and meta_path.is_file():
        try:
            cached = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            cached = {}
        if cached.get("cacheVersion") == IDLE_CACHE_VERSION:
            return idle_path, meta_path
    if not IDLE_DRIVER.is_file():
        raise RuntimeError(f"Idle driving video is missing: {IDLE_DRIVER}")

    IDLE_DIR.mkdir(parents=True, exist_ok=True)
    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=f"{character}-idle-", dir=JOB_ROOT))
    pipe = _load_pipeline()
    pipe._ensure_models_loaded(is_animal=False)
    pipe.set_mlx_profile(PROFILE)
    try:
        driver = work_dir / "driver.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", str(IDLE_DRIVER), "-t", "4",
                "-an", "-vf", "fps=25", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(driver),
            ],
            check=True,
        )
        changed = pipe.update_cfg(
            {
                "source": str(_source_path(character)),
                "driving": str(driver),
                "flag_relative_motion": True,
                "flag_do_crop": True,
                "flag_pasteback": True,
                "flag_stitching": True,
                "flag_crop_driving_video": True,
                "driving_multiplier": 0.9,
                "animation_region": "eyes",
            }
        )
        generated, _crop, _seconds = pipe.run_video_driving(
            str(driver), str(_source_path(character)), update_ret=changed, progress=None
        )
        first_frame = work_dir / "first.png"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(generated), "-frames:v", "1", str(first_frame)],
            check=True,
        )
        box = _face_box(pipe, first_frame)
        temporary_idle = idle_path.with_suffix(".building.mp4")
        # d13 stays centred and supplies two ordinary blinks. Stretch the calm
        # open-eye intervals to create unequal 5.5s/6.5s blink spacing instead
        # of the old expression-test clip's repeated stare and side glances.
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", str(generated),
                "-filter_complex",
                "[0:v]trim=start=0:end=1.5,setpts=PTS-STARTPTS[a];"
                "[0:v]trim=start=1.5:end=2,setpts=8*(PTS-STARTPTS)[b];"
                "[0:v]trim=start=2:end=3.5,setpts=PTS-STARTPTS[c];"
                "[0:v]trim=start=3.5:end=4,setpts=10*(PTS-STARTPTS)[d];"
                "[a][b][c][d]concat=n=4:v=1:a=0,fps=25,scale=640:-2[v]",
                "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary_idle),
            ],
            check=True,
        )
        # The face box was measured before the final resize.
        capture = cv2.VideoCapture(str(generated))
        source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        capture.release()
        final = cv2.VideoCapture(str(temporary_idle))
        final_width = int(final.get(cv2.CAP_PROP_FRAME_WIDTH))
        final_height = int(final.get(cv2.CAP_PROP_FRAME_HEIGHT))
        final.release()
        scale_x, scale_y = final_width / source_width, final_height / source_height
        scaled_box = [
            int(box[0] * scale_x), int(box[1] * scale_y),
            int(box[2] * scale_x), int(box[3] * scale_y),
        ]
        temporary_idle.replace(idle_path)
        meta_path.write_text(json.dumps({
            "cacheVersion": IDLE_CACHE_VERSION,
            "fps": 25,
            "faceBox": scaled_box,
            "driver": IDLE_DRIVER.name,
        }))
        return idle_path, meta_path
    finally:
        pipe._park_mlx_models()
        shutil.rmtree(work_dir, ignore_errors=True)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ready",
        "engine": "LivePortrait idle stream + cached high-resolution visemes",
        "profile": PROFILE,
        "streamingChunks": False,
        "idleVideo": True,
        "highResolutionVisemes": True,
        "visemeCount": len(VISEME_LIP_RATIOS),
        "characters": sorted(PORTRAITS),
        "modelsLoaded": bool(pipeline and pipeline.model_dict),
    }


@app.post("/warmup")
async def warmup(character: str = Query("xiaoman")) -> dict:
    async with render_lock:
        started = time.perf_counter()
        idle_path, _idle_meta = await asyncio.to_thread(_ensure_idle, character)
        visemes, _viseme_meta = await asyncio.to_thread(_ensure_visemes, character)
        return {
            "status": "ready",
            "character": character,
            "idle": idle_path.name,
            "visemes": len(visemes),
            "seconds": round(time.perf_counter() - started, 3),
        }


@app.get("/idle/{character}")
async def idle(character: str) -> FileResponse:
    async with render_lock:
        try:
            idle_path, _meta_path = await asyncio.to_thread(_ensure_idle, character)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Idle video failed: {exc}") from exc
    return FileResponse(idle_path, media_type="video/mp4", filename=f"{character}-idle.mp4")


@app.get("/viseme/{character}/{level}")
async def viseme(character: str, level: int) -> FileResponse:
    if level < 0 or level >= len(VISEME_LIP_RATIOS):
        raise HTTPException(status_code=404, detail="Unknown viseme level")
    async with render_lock:
        try:
            paths, _meta_path = await asyncio.to_thread(_ensure_visemes, character)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Viseme generation failed: {exc}") from exc
    return FileResponse(
        paths[level],
        media_type="image/png",
        filename=f"{character}-viseme-{level}.png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
