#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] || ./scripts/setup-env.sh
set -a
source .env
set +a

[[ -x .venv/bin/speech-to-speech ]] || ./scripts/bootstrap-macos.sh
./scripts/start-llm.sh
./scripts/start-voice.sh
if [[ "${AVATAR_ENABLED:-true}" == "true" ]]; then
  ./scripts/start-avatar.sh
  export AVATAR_URL="http://host.docker.internal:${AVATAR_PORT:-9871}"
else
  export AVATAR_URL=""
fi
docker compose up -d --build demo

echo "实时语音已启动：http://localhost:${DEMO_PORT}"
echo "点击通话按钮，允许麦克风后直接说话。"
