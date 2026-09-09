#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export UV_CACHE_DIR="$PWD/.cache/uv"
export NLTK_DATA="$PWD/.cache/nltk"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "这个脚本只用于 Apple Silicon Mac。" >&2
  exit 1
fi

for command_name in uv llama-server docker; do
  command -v "$command_name" >/dev/null || {
    echo "缺少 ${command_name}，请先用 Homebrew 或 Docker Desktop 安装。" >&2
    exit 1
  }
done

./scripts/setup-env.sh
uv sync --python 3.12 --no-dev
mkdir -p .cache/nltk logs voices/xiaoman
.venv/bin/python -m nltk.downloader -d "$PWD/.cache/nltk" punkt_tab averaged_perceptron_tagger_eng

echo "官方 speech-to-speech 依赖已安装。模型会在第一次 ./scripts/up.sh 时按需下载。"
