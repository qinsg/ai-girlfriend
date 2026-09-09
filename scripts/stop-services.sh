#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
for service_name in avatar speech llama; do
  pid_file="logs/${service_name}.pid"
  [[ -f "$pid_file" ]] || continue
  pid="$(tr -d '[:space:]' < "$pid_file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    command_line="$(ps -p "$pid" -o command=)"
    if [[ "$command_line" == *speech-to-speech* || "$command_line" == *llama-server* || "$command_line" == *"uvicorn service:app"* ]]; then
      kill "$pid"
      echo "已停止 ${service_name}，PID ${pid}。"
    else
      echo "PID ${pid} 与 ${service_name} 不匹配，未停止。" >&2
      exit 1
    fi
  fi
  rm -f "$pid_file"
done
