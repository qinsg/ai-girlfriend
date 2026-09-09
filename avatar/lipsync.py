"""MuseTalk MLX lower-face renderer for short streaming audio chunks."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile
import wave

import cv2
import numpy as np

MOUTH_INFERENCE_FPS = 8


@dataclass
class PreparedAvatar:
    frames: list[np.ndarray]
    latents: list
    face_box: tuple[int, int, int, int]
    fps: int


def lower_face_mask(height: int, width: int) -> np.ndarray:
    """Return a feathered mask that changes the mouth but preserves the eyes."""
    mask = np.zeros((height, width), dtype=np.float32)
    top = int(height * 0.43)
    bottom = int(height * 0.98)
    center = (width // 2, int(height * 0.70))
    axes = (int(width * 0.47), int(height * 0.31))
    cv2.ellipse(mask, center, axes, 0, 0, 360, 1.0, -1)
    mask[:top] = 0
    mask[bottom:] = 0
    ramp = max(2, int(height * 0.12))
    mask[top : top + ramp] *= np.linspace(0, 1, ramp, dtype=np.float32)[:, None]
    kernel = max(5, int(min(height, width) * 0.09) | 1)
    return cv2.GaussianBlur(mask, (kernel, kernel), 0)[..., None]


def blend_lower_face(base: np.ndarray, generated: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """Paste a generated MuseTalk crop into one idle frame without touching eyes."""
    x1, y1, x2, y2 = box
    height, width = y2 - y1, x2 - x1
    face = cv2.resize(generated, (width, height), interpolation=cv2.INTER_LANCZOS4)
    original = base[y1:y2, x1:x2].astype(np.float32)
    alpha = lower_face_mask(height, width)
    result = base.copy()
    result[y1:y2, x1:x2] = np.clip(original * (1 - alpha) + face * alpha, 0, 255).astype(np.uint8)
    return result


class MuseTalkLipSync:
    def __init__(self, model_dir: Path):
        import mlx.core as mx
        from musetalk_mlx.pipeline_mlx import MuseTalkPipeline

        mx.set_default_device(mx.gpu)
        self.mx = mx
        self.pipe = MuseTalkPipeline.from_pretrained_mlx(model_dir)
        self.avatars: dict[str, PreparedAvatar] = {}
        self.warmed_characters: set[str] = set()

    def prepare(self, character: str, idle_path: Path, meta_path: Path) -> PreparedAvatar:
        cached = self.avatars.get(character)
        if cached is not None:
            return cached

        meta = json.loads(meta_path.read_text())
        fps = int(meta.get("fps", 25))
        box = tuple(int(value) for value in meta["faceBox"])
        capture = cv2.VideoCapture(str(idle_path))
        frames: list[np.ndarray] = []
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
        capture.release()
        if not frames:
            raise RuntimeError(f"Idle video has no frames: {idle_path}")

        x1, y1, x2, y2 = box
        latents = []
        for frame in frames:
            crop = cv2.resize(frame[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
            latents.append(self.pipe.get_latents_for_unet(crop))
        avatar = PreparedAvatar(frames=frames, latents=latents, face_box=box, fps=fps)
        self.avatars[character] = avatar
        return avatar

    def warmup(self, character: str, idle_path: Path, meta_path: Path) -> None:
        """Compile the audio encoder and common one-second inference shape."""
        if character in self.warmed_characters:
            return
        avatar = self.prepare(character, idle_path, meta_path)
        with tempfile.TemporaryDirectory(prefix="musetalk-warmup-") as tmp:
            silence = Path(tmp) / "silence.wav"
            with wave.open(str(silence), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(bytes(24000 * 2))
            chunks = self.pipe.encode_audio_from_wav(silence, fps=MOUTH_INFERENCE_FPS)
            count = int(chunks.shape[0])
            latents = self.mx.concatenate(avatar.latents[:count], axis=0)
            self.pipe.run_batched(latents, chunks, batch_size=count)
        self.warmed_characters.add(character)

    def render_chunk(
        self,
        character: str,
        idle_path: Path,
        meta_path: Path,
        audio: bytes,
        start_frame: int,
        output_path: Path,
    ) -> tuple[int, int]:
        avatar = self.prepare(character, idle_path, meta_path)
        with tempfile.TemporaryDirectory(prefix="musetalk-chunk-") as tmp:
            audio_path = Path(tmp) / "audio.wav"
            audio_path.write_bytes(audio)
            # MuseTalk is the expensive stage. Infer sparse mouth keyframes and
            # interpolate them onto the 25 fps idle clock; this preserves fluid
            # playback without making the neural model process 25 faces/s.
            audio_chunks = self.pipe.encode_audio_from_wav(audio_path, fps=MOUTH_INFERENCE_FPS)
            keyframe_count = int(audio_chunks.shape[0])
            output_frame_count = max(
                1,
                round(keyframe_count * avatar.fps / MOUTH_INFERENCE_FPS),
            )
            keyframe_indices = [
                (start_frame + round(index * avatar.fps / MOUTH_INFERENCE_FPS)) % len(avatar.frames)
                for index in range(keyframe_count)
            ]
            latent_stack = self.mx.concatenate([avatar.latents[index] for index in keyframe_indices], axis=0)
            # Large batches are substantially faster on high-memory Apple
            # Silicon because VAE decode and UNet setup are amortized once per
            # chunk. Cap at 32 to keep the same path usable on smaller Macs.
            generated = self.pipe.run_batched(
                latent_stack,
                audio_chunks,
                batch_size=min(32, max(1, keyframe_count)),
            )

            height, width = avatar.frames[0].shape[:2]
            silent_path = Path(tmp) / "silent.mp4"
            encoder = subprocess.Popen(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-f", "rawvideo", "-pix_fmt", "bgr24",
                    "-s", f"{width}x{height}", "-r", str(avatar.fps), "-i", "-",
                    "-an", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                    "-crf", "20", "-pix_fmt", "yuv420p", str(silent_path),
                ],
                stdin=subprocess.PIPE,
            )
            assert encoder.stdin is not None
            for output_index in range(output_frame_count):
                position = output_index * MOUTH_INFERENCE_FPS / avatar.fps
                before = min(int(position), keyframe_count - 1)
                after = min(before + 1, keyframe_count - 1)
                mix = position - before
                face = cv2.addWeighted(generated[before], 1 - mix, generated[after], mix, 0)
                source_index = (start_frame + output_index) % len(avatar.frames)
                frame = blend_lower_face(avatar.frames[source_index], face, avatar.face_box)
                encoder.stdin.write(np.ascontiguousarray(frame).tobytes())
            encoder.stdin.close()
            if encoder.wait() != 0:
                raise RuntimeError("ffmpeg failed to encode the MuseTalk chunk")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error", "-i", str(silent_path), "-i", str(audio_path),
                    "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart",
                    str(output_path),
                ],
                check=True,
            )
        return (start_frame + output_frame_count) % len(avatar.frames), output_frame_count
