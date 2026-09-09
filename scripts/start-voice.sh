#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] || ./scripts/setup-env.sh
[[ -x .venv/bin/speech-to-speech ]] || ./scripts/bootstrap-macos.sh
set -a
source .env
set +a

export NLTK_DATA="$PWD/.cache/nltk"
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HUB_DISABLE_XET=1
export HF_ENDPOINT=https://huggingface.co
export DO_NOT_TRACK=1
mkdir -p logs

if curl -fsS "http://127.0.0.1:${REALTIME_PORT}/v1/usage" >/dev/null 2>&1; then
  echo "speech-to-speech Realtime 服务已就绪。"
  exit 0
fi

if [[ -f logs/speech.pid ]]; then
  pid="$(tr -d '[:space:]' < logs/speech.pid)"
  if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
    rm -f logs/speech.pid
  fi
fi

tts_args=(--tts qwen3 --qwen3_tts_language Chinese --qwen3_tts_mlx_quantization "$TTS_QUANTIZATION")
if [[ "$TTS_MODE" == "clone" ]]; then
  [[ -f "$TTS_REFERENCE_AUDIO" ]] || {
    echo "声音克隆模式缺少参考音频：${TTS_REFERENCE_AUDIO}" >&2
    exit 1
  }
  tts_args+=(--qwen3_tts_model_name Qwen/Qwen3-TTS-12Hz-1.7B-Base --qwen3_tts_ref_audio "$TTS_REFERENCE_AUDIO")
  if [[ -n "$TTS_REFERENCE_TEXT" ]]; then
    tts_args+=(--qwen3_tts_ref_text "$TTS_REFERENCE_TEXT")
  fi
else
  tts_args+=(--qwen3_tts_model_name Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice --qwen3_tts_speaker "$TTS_SPEAKER")
fi

if [[ ! -f logs/speech.pid ]]; then
  echo "启动官方 speech-to-speech：Silero VAD + MLX Whisper + llama.cpp + Qwen3-TTS。"
  nohup .venv/bin/speech-to-speech serve \
    --host 127.0.0.1 \
    --port "$REALTIME_PORT" \
    --mac-optimal-settings \
    --stt mlx-audio-whisper \
    --mlx_audio_whisper_model_name "$STT_MODEL" \
    --language "$STT_LANGUAGE" \
    --llm_backend responses-api \
    --model_name "$LLM_ALIAS" \
    --responses_api_base_url http://127.0.0.1:8080/v1 \
    --responses_api_api_key "$LLM_API_KEY" \
    --responses_api_stream \
    --enable_live_transcription \
    --thresh 0.4 \
    --min_speech_ms 300 \
    --min_silence_ms 500 \
    --init_chat_prompt "你正在进行简短自然的中文语音对话。" \
    "${tts_args[@]}" \
    > logs/speech.log 2>&1 &
  echo "$!" > logs/speech.pid
fi

for _ in {1..1800}; do
  if curl -fsS "http://127.0.0.1:${REALTIME_PORT}/v1/usage" >/dev/null 2>&1; then
    echo "speech-to-speech Realtime 服务已就绪。"
    exit 0
  fi
  pid="$(tr -d '[:space:]' < logs/speech.pid)"
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f logs/speech.pid
    echo "speech-to-speech 启动失败，请查看 logs/speech.log。" >&2
    exit 1
  fi
  sleep 2
done

echo "等待 speech-to-speech 超时，请查看 logs/speech.log。" >&2
exit 1
