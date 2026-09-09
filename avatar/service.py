"""Continuous idle portrait and chunked lip-sync service for Apple Silicon.

The heavy MLX runtime lives under ``.runtime/`` and its checkpoints under
``models/``. Both directories are machine-local. This small adapter is the part
owned by this repository: LivePortrait creates a seamless blinking idle loop;
MuseTalk MLX changes only the lower face for short TTS audio chunks.
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

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask


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
MAX_AUDIO_BYTES = int(os.environ.get("AVATAR_MAX_AUDIO_BYTES", str(4 * 1024 * 1024)))
MUSE_MODEL_DIR = Path(
    os.environ.get(
        "AVATAR_MUSETALK_MODEL_DIR",
        PROJECT_ROOT / "models" / "avatar" / "musetalk-1.5-fp16",
    )
).expanduser().resolve()
IDLE_DIR = Path(
    os.environ.get("AVATAR_IDLE_DIR", PROJECT_ROOT / ".runtime" / "avatar-idle")
).expanduser().resolve()
IDLE_DRIVER = RUNTIME_DIR / "assets" / "examples" / "driving" / "d14.mp4"

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

from avatar.lipsync import MuseTalkLipSync  # noqa: E402


app = FastAPI(title="Local audio-driven avatar")
render_lock = asyncio.Lock()
pipeline: GradioLivePortraitPipeline | None = None
lipsync_pipeline: MuseTalkLipSync | None = None


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


def _face_box(pipe: GradioLivePortraitPipeline, frame_path: Path) -> tuple[int, int, int, int]:
    frame = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"Could not read idle frame: {frame_path}")
    faces = pipe.model_dict["face_analysis"].predict(frame)
    if not len(faces):
        raise RuntimeError("Could not detect a face in the generated idle video")
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    landmarks = pipe.model_dict["landmark"].predict(rgb, faces[0])
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
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", str(generated),
                "-filter_complex",
                "[0:v]fps=25,scale=640:-2,split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0[v]",
                "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
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
        meta_path.write_text(json.dumps({"fps": 25, "faceBox": scaled_box}))
        return idle_path, meta_path
    finally:
        pipe._park_mlx_models()
        shutil.rmtree(work_dir, ignore_errors=True)


def _load_lipsync() -> MuseTalkLipSync:
    global lipsync_pipeline
    if lipsync_pipeline is None:
        required = ("config.json", "unet.safetensors", "vae.safetensors", "whisper_encoder.safetensors")
        missing = [name for name in required if not (MUSE_MODEL_DIR / name).is_file()]
        if missing:
            raise RuntimeError("MuseTalk MLX weights are missing: " + ", ".join(missing))
        lipsync_pipeline = MuseTalkLipSync(MUSE_MODEL_DIR)
    return lipsync_pipeline


def _cleanup_job(video_path: Path) -> None:
    shutil.rmtree(video_path.parent, ignore_errors=True)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ready",
        "engine": "LivePortrait idle stream + MuseTalk 1.5 MLX",
        "profile": PROFILE,
        "streamingChunks": True,
        "idleVideo": True,
        "museTalkLoaded": lipsync_pipeline is not None,
        "characters": sorted(PORTRAITS),
        "modelsLoaded": bool(pipeline and pipeline.model_dict),
    }


@app.post("/warmup")
async def warmup(character: str = Query("xiaoman")) -> dict:
    async with render_lock:
        started = time.perf_counter()
        idle_path, meta_path = await asyncio.to_thread(_ensure_idle, character)
        lip = await asyncio.to_thread(_load_lipsync)
        await asyncio.to_thread(lip.warmup, character, idle_path, meta_path)
        return {
            "status": "ready",
            "character": character,
            "idle": idle_path.name,
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


@app.post("/lipsync")
async def lipsync(
    request: Request,
    character: str = Query("xiaoman"),
    start_frame: int = Query(0, ge=0),
) -> FileResponse:
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="WAV body is empty")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="WAV body is too large")
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise HTTPException(status_code=415, detail="Expected a PCM WAV request body")

    async with render_lock:
        started = time.perf_counter()
        try:
            idle_path, meta_path = await asyncio.to_thread(_ensure_idle, character)
            lip = await asyncio.to_thread(_load_lipsync)
            JOB_ROOT.mkdir(parents=True, exist_ok=True)
            job_dir = Path(tempfile.mkdtemp(prefix=f"{character}-lip-", dir=JOB_ROOT))
            output = job_dir / "chunk.mp4"
            next_frame, frame_count = await asyncio.to_thread(
                lip.render_chunk,
                character,
                idle_path,
                meta_path,
                audio,
                start_frame,
                output,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"MuseTalk lip-sync failed: {exc}") from exc

    return FileResponse(
        output,
        media_type="video/mp4",
        filename=f"{character}-chunk.mp4",
        headers={
            "X-Avatar-Render-Seconds": f"{time.perf_counter() - started:.3f}",
            "X-Avatar-Start-Frame": str(start_frame),
            "X-Avatar-Next-Frame": str(next_frame),
            "X-Avatar-Frame-Count": str(frame_count),
        },
        background=BackgroundTask(_cleanup_job, output),
    )
