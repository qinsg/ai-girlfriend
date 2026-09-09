# 参考音频

声音克隆只使用你本人录制或已明确获授权的音频。

将音频处理成 5 至 10 秒、单声道、24 kHz WAV：

```bash
mkdir -p voices/xiaoman
ffmpeg -i source.wav -ss 00:00:00 -t 8 -ac 1 -ar 24000 voices/xiaoman/reference.wav
```

然后在 `.env` 中设置：

```dotenv
TTS_MODE=clone
CHARACTER_FILE=./config/characters.clone.json
TTS_REFERENCE_AUDIO=voices/xiaoman/reference.wav
TTS_REFERENCE_TEXT=参考音频中逐字对应的文本
```
