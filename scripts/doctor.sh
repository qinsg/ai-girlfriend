#!/usr/bin/env bash
set -u

cd "$(dirname "$0")/.."
failed=0
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
for command_name in docker uv llama-server curl; do
  if command -v "$command_name" >/dev/null; then
    echo "[ok] ${command_name}"
  else
    echo "[缺少] ${command_name}"
    failed=1
  fi
done

[[ -x .venv/bin/speech-to-speech ]] && echo "[ok] 官方 speech-to-speech" || echo "[缺少] 请运行 ./scripts/bootstrap-macos.sh"
if [[ -n "${LLM_API_KEY:-}" ]]; then
  curl -fsS -H "Authorization: Bearer ${LLM_API_KEY}" http://127.0.0.1:8080/health >/dev/null 2>&1 \
    && echo "[ok] llama.cpp" || echo "[未运行] llama.cpp"
else
  echo "[未配置] 请运行 ./scripts/setup-env.sh"
fi
curl -fsS "http://127.0.0.1:${REALTIME_PORT:-8765}/v1/usage" >/dev/null 2>&1 \
  && echo "[ok] Realtime voice" || echo "[未运行] Realtime voice"
docker compose ps
exit "$failed"
