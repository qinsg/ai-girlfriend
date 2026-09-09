#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -x .venv/bin/speech-to-speech ]] || ./scripts/bootstrap-macos.sh
./scripts/start-llm.sh
docker compose up -d --build demo
./scripts/start-voice.sh

set -a
source .env
set +a
echo "实时语音已启动：http://localhost:${DEMO_PORT}"
echo "点击中央圆球，允许麦克风后直接说话。"
