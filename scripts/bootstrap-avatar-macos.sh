#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

runtime_dir="$PWD/.runtime/fasterliveportrait-mlx"
musetalk_runtime_dir="$PWD/.runtime/musetalk-mlx"
checkpoint_dir="$PWD/models/avatar/checkpoints"
musetalk_model_dir="$PWD/models/avatar/musetalk-1.5-fp16"
runtime_revision="d5361f4806c14fe2051eecb1dd5a89930f46db0d"
weights_revision="2cc2ac92c9fe65ca4fb68cb1a1556ead285e7391"
musetalk_runtime_revision="c6eb30ebd1ed4d043983209813370153de9346bf"
musetalk_weights_revision="ad54104a0129121fe2ea67471250c0656c985284"
avatar_uv_cache="$PWD/.cache/uv-avatar"
avatar_hf_cache="$PWD/.cache/huggingface-avatar"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "数字人自动安装脚本只支持 Apple Silicon Mac。" >&2
  exit 1
fi

for command_name in git uv ffmpeg; do
  command -v "$command_name" >/dev/null || {
    echo "缺少 ${command_name}，请先安装后重试。" >&2
    exit 1
  }
done

mkdir -p "$PWD/.runtime" "$checkpoint_dir" "$musetalk_model_dir" "$avatar_uv_cache" "$avatar_hf_cache"

if [[ ! -d "$runtime_dir/.git" ]]; then
  git clone https://github.com/ivanfioravanti/fasterliveportrait-mlx.git "$runtime_dir"
fi

if [[ "$(git -C "$runtime_dir" rev-parse HEAD)" != "$runtime_revision" ]]; then
  git -C "$runtime_dir" fetch --depth 1 origin "$runtime_revision"
  git -C "$runtime_dir" checkout --detach "$runtime_revision"
fi

if [[ ! -d "$musetalk_runtime_dir/.git" ]]; then
  git clone https://github.com/xocialize/musetalk-mlx.git "$musetalk_runtime_dir"
fi

if [[ "$(git -C "$musetalk_runtime_dir" rev-parse HEAD)" != "$musetalk_runtime_revision" ]]; then
  git -C "$musetalk_runtime_dir" fetch --depth 1 origin "$musetalk_runtime_revision"
  git -C "$musetalk_runtime_dir" checkout --detach "$musetalk_runtime_revision"
fi

UV_CACHE_DIR="$avatar_uv_cache" uv sync \
  --project "$runtime_dir" \
  --python 3.12

UV_CACHE_DIR="$avatar_uv_cache" uv pip install \
  --python "$runtime_dir/.venv/bin/python" \
  --editable "$musetalk_runtime_dir"

HF_ENDPOINT=https://huggingface.co \
HF_HOME="$avatar_hf_cache" \
HF_HUB_DISABLE_XET=1 \
"$runtime_dir/.venv/bin/hf" download \
  ivanfioravanti/FasterLivePortrait-MLX-weights \
  --revision "$weights_revision" \
  --max-workers 1 \
  --include 'liveportrait_mlx/*.npz' \
  --local-dir "$checkpoint_dir"

if ! HF_ENDPOINT=https://huggingface.co \
  HF_HOME="$avatar_hf_cache" \
  HF_HUB_DISABLE_XET=1 \
  "$runtime_dir/.venv/bin/hf" download \
    mlx-community/MuseTalk-1.5-fp16 \
    --revision "$musetalk_weights_revision" \
    --max-workers 1 \
    --local-dir "$musetalk_model_dir"; then
  echo "Hugging Face CLI 下载 MuseTalk 失败，改用固定版本的 HTTPS 文件地址。"
  for model_file in config.json unet.safetensors vae.safetensors whisper_encoder.safetensors; do
    curl -fL --retry 3 \
      "https://huggingface.co/mlx-community/MuseTalk-1.5-fp16/resolve/${musetalk_weights_revision}/${model_file}?download=true" \
      -o "$musetalk_model_dir/${model_file}"
  done
fi

echo "持续待机与分块口型所需的 MLX 运行时、模型权重已安装。"
