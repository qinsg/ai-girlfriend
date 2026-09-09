#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] || ./scripts/setup-env.sh
set -a
source .env
set +a
mkdir -p logs

if [[ -f logs/llama.pid ]]; then
  pid="$(tr -d '[:space:]' < logs/llama.pid)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "llama-server 已在运行，PID ${pid}。"
  else
    rm -f logs/llama.pid
  fi
fi

if [[ ! -f logs/llama.pid ]]; then
  echo "启动 ${LLM_HF_MODEL}。首次使用会自动下载 GGUF 权重。"
  nohup llama-server \
    -hf "$LLM_HF_MODEL" \
    --alias "$LLM_ALIAS" \
    --host 127.0.0.1 \
    --port 8080 \
    --api-key "$LLM_API_KEY" \
    -ngl 99 \
    -c "$LLM_CONTEXT" \
    -np 1 \
    -fa on \
    --jinja \
    --reasoning off \
    > logs/llama.log 2>&1 &
  echo "$!" > logs/llama.pid
fi

for _ in {1..1800}; do
  if curl -fsS -H "Authorization: Bearer ${LLM_API_KEY}" http://127.0.0.1:8080/health >/dev/null 2>&1; then
    echo "llama-server 已就绪。"
    exit 0
  fi
  pid="$(tr -d '[:space:]' < logs/llama.pid)"
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f logs/llama.pid
    echo "llama-server 启动失败，请查看 logs/llama.log。" >&2
    exit 1
  fi
  sleep 2
done

echo "等待 llama-server 超时，请查看 logs/llama.log。" >&2
exit 1
