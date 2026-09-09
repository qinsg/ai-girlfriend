#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env 已存在，未覆盖。"
  exit 0
fi

cp .env.example .env
api_key="$(openssl rand -hex 24)"
sed -i '' "s/请运行_scripts_setup-env.sh_生成/${api_key}/" .env
echo "已创建 .env，并生成仅供本机 llama.cpp 使用的随机密钥。"
