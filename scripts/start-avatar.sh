#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] || ./scripts/setup-env.sh
set -a
source .env
set +a

avatar_port="${AVATAR_PORT:-9871}"
avatar_profile="${AVATAR_MLX_PROFILE:-quality}"
runtime_dir="$PWD/.runtime/fasterliveportrait-mlx"
checkpoint_dir="$PWD/models/avatar/checkpoints"

if curl -fsS "http://127.0.0.1:${avatar_port}/health" >/dev/null 2>&1; then
  echo "验证小满待机画面和高清嘴形缓存。"
  if curl -fsS -X POST "http://127.0.0.1:${avatar_port}/warmup?character=xiaoman" >/dev/null; then
    echo "数字人服务已就绪。"
    exit 0
  fi
  echo "数字人预热失败，请查看 logs/avatar.log。" >&2
  exit 1
fi

if [[ ! -x "$runtime_dir/.venv/bin/python" \
  || ! -f "$checkpoint_dir/liveportrait_mlx/warping_module.npz" \
  || ! -f "$checkpoint_dir/liveportrait_mlx/spade_generator.npz" \
  || ! -f "$checkpoint_dir/liveportrait_mlx/landmark.npz" \
  || ! -f "$checkpoint_dir/liveportrait_mlx/appearance_feature_extractor.npz" \
  || ! -f "$checkpoint_dir/liveportrait_mlx/motion_extractor.npz" \
  || ! -f "$checkpoint_dir/liveportrait_mlx/stitching.npz" \
  || ! -f "$checkpoint_dir/liveportrait_mlx/stitching_eye.npz" \
  || ! -f "$checkpoint_dir/liveportrait_mlx/stitching_lip.npz" ]]; then
  ./scripts/bootstrap-avatar-macos.sh
fi
mkdir -p logs .runtime/avatar-jobs

if [[ -f logs/avatar.pid ]]; then
  avatar_pid="$(tr -d '[:space:]' < logs/avatar.pid)"
  if [[ -n "$avatar_pid" ]] && ! kill -0 "$avatar_pid" 2>/dev/null; then
    rm -f logs/avatar.pid
  fi
fi

if [[ ! -f logs/avatar.pid ]]; then
  echo "启动本地数字人：LivePortrait 持续待机 + 高清嘴形实时驱动。"
  nohup env \
    AVATAR_RUNTIME_DIR="$runtime_dir" \
    AVATAR_CHECKPOINT_DIR="$checkpoint_dir" \
    AVATAR_MLX_PROFILE="$avatar_profile" \
    HF_ENDPOINT=https://huggingface.co \
    HF_HOME="$PWD/.cache/huggingface-avatar" \
    HF_HUB_DISABLE_XET=1 \
    "$runtime_dir/.venv/bin/python" -m uvicorn service:app \
      --app-dir "$PWD/avatar" \
      --host 127.0.0.1 \
      --port "$avatar_port" \
      > logs/avatar.log 2>&1 &
  echo "$!" > logs/avatar.pid
fi

for _ in {1..120}; do
  if curl -fsS "http://127.0.0.1:${avatar_port}/health" >/dev/null 2>&1; then
    echo "验证小满人像和数字人模型，第一次启动会稍慢。"
    if curl -fsS -X POST "http://127.0.0.1:${avatar_port}/warmup?character=xiaoman" >/dev/null; then
      echo "数字人服务已就绪。"
      exit 0
    fi
    echo "数字人预热失败，请查看 logs/avatar.log。" >&2
    exit 1
  fi
  avatar_pid="$(tr -d '[:space:]' < logs/avatar.pid)"
  if ! kill -0 "$avatar_pid" 2>/dev/null; then
    rm -f logs/avatar.pid
    echo "数字人服务启动失败，请查看 logs/avatar.log。" >&2
    exit 1
  fi
  sleep 2
done

echo "等待数字人服务超时，请查看 logs/avatar.log。" >&2
exit 1
