# 本地赛博 AI 女友

这是基于 Hugging Face [`speech-to-speech`](https://github.com/huggingface/speech-to-speech) 1.0.0 的本地实时语音项目，也是对[零度博客原教程](https://www.freedidi.com/24928.html)的 Apple Silicon 升级版。

它不是文字聊天套壳。浏览器持续采集麦克风，后端完成语音检测、中文识别、大模型回复和语音合成，再把声音流式送回浏览器。用户可以在 AI 说话时直接插话。

```text
浏览器麦克风
    ↓ 24 kHz PCM
Silero VAD + Smart Turn
    ↓
Whisper large-v3-turbo MLX
    ↓
Qwen3 8B GGUF + llama.cpp Metal
    ↓
Qwen3-TTS MLX
    ↓
浏览器扬声器
```

## 当前支持范围

这套开箱脚本目前只支持 Apple Silicon Mac：

- macOS，处理器架构为 `arm64`
- 已在 M3 Max、128GB 统一内存上完成端到端验证
- 默认模型是 Qwen3-8B Q4，建议至少预留 20GB 磁盘空间
- 建议使用 Chrome 或 Edge，Safari 也可以运行，但音频设备切换能力较少

Intel Mac、Windows、Linux 和 NVIDIA CUDA 的通用思路见[完整部署方案](./本地赛博%20AI%20女友完整部署方案.md)，但本仓库的自动脚本没有为这些平台适配。

## 为什么不是全部放进 Docker

Docker Compose 负责官方浏览器 Demo、角色配置挂载和容器重启。LLM、STT 和 TTS 在 macOS 宿主机运行。

原因很直接：Docker Desktop 的 Linux 虚拟机不能使用 macOS 的 Metal 和 MLX。把模型也塞进容器会退回 CPU，实时语音延迟会明显增加。`./scripts/up.sh` 把 Compose 和宿主机模型进程编排成一个启动入口。

默认端口：

| 服务 | 运行位置 | 地址 |
|---|---|---|
| 浏览器界面 | Docker Compose | `http://127.0.0.1:7860` |
| speech-to-speech Realtime | macOS 宿主机 | `ws://127.0.0.1:8765/v1/realtime` |
| llama.cpp | macOS 宿主机 | `http://127.0.0.1:8080` |

三个服务都只监听本机地址，不会默认暴露到局域网。

## 一、安装系统依赖

### 1. 确认机器架构

打开终端：

```bash
uname -s
uname -m
```

预期输出分别是 `Darwin` 和 `arm64`。

### 2. 安装 Homebrew

如果 `brew --version` 没有输出版本号，先按照 [Homebrew 官网](https://brew.sh/)安装 Homebrew。Homebrew 提示缺少 Command Line Tools 时，执行：

```bash
xcode-select --install
```

### 3. 安装 uv、llama.cpp 和可选的 ffmpeg

```bash
brew update
brew install uv llama.cpp
brew install ffmpeg
```

`ffmpeg` 只在制作声音克隆参考音频时使用。项目用 `uv` 创建 Python 3.12 环境，缺少 Python 3.12 时，uv 会自动下载。安装方法可参考 [uv 官方文档](https://docs.astral.sh/uv/getting-started/installation/)和 [llama.cpp 官方仓库](https://github.com/ggml-org/llama.cpp)。

检查命令是否可用：

```bash
uv --version
llama-server --version
ffmpeg -version
```

如果新版 llama.cpp 只安装了统一的 `llama` 命令，没有 `llama-server`，请改用 Homebrew 的 `llama.cpp` 包。本项目启动脚本当前调用 `llama-server`。

### 4. 安装并启动 Docker Desktop

下载适用于 Apple Silicon 的 [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/)，完成安装后启动 Docker Desktop。等菜单栏图标显示 Docker 已运行，再检查：

```bash
docker version
docker compose version
```

只安装 Docker 命令行工具不够，Docker Desktop 后台服务也必须运行。

## 二、取得项目代码

GitHub 仓库创建完成后，其他用户可以执行：

```bash
git clone https://github.com/<你的用户名>/<仓库名>.git
cd <仓库名>
```

如果通过 ZIP 下载源码，需要恢复脚本的执行权限：

```bash
chmod +x scripts/*.sh
```

## 三、初始化本地环境

在项目根目录执行：

```bash
./scripts/bootstrap-macos.sh
```

脚本会完成这些工作：

1. 检查当前系统是不是 Apple Silicon Mac。
2. 检查 `uv`、`llama-server` 和 `docker`。
3. 从 `.env.example` 创建本机 `.env`。
4. 生成一个仅供本机 llama.cpp 使用的随机 API 密钥。
5. 创建 `.venv` 并按 `uv.lock` 安装 Python 依赖。
6. 下载 NLTK 的必要数据。
7. 创建 `.cache/`、`logs/` 和 `voices/` 运行目录。

`.env` 包含本机密钥，已经被 `.gitignore` 排除。不要把它提交到 GitHub。

初始化完成后可查看配置：

```bash
sed -n '1,200p' .env
```

## 四、启动完整语音服务

```bash
./scripts/up.sh
```

第一次启动会自动下载以下模型：

- Qwen3-8B GGUF Q4，用于对话回复
- `mlx-community/whisper-large-v3-turbo`，用于中文语音识别
- Qwen3-TTS 1.7B MLX 6bit，用于语音合成
- Silero VAD 和 Smart Turn，用于语音起止与轮次判断

模型下载和首次预热需要一些时间。终端最后出现下面的地址才算启动完成：

```text
实时语音已启动：http://localhost:7860
```

打开 [http://localhost:7860](http://localhost:7860)，点击中央圆球，允许浏览器访问麦克风，然后直接说中文。

不要关闭 Docker Desktop。`llama-server` 与 `speech-to-speech` 会以本机后台进程运行，日志写入 `logs/`。

## 五、验证部署

运行一键检查：

```bash
./scripts/doctor.sh
```

正常情况下应看到：

```text
[ok] docker
[ok] uv
[ok] llama-server
[ok] curl
[ok] 官方 speech-to-speech
[ok] llama.cpp
[ok] Realtime voice
```

也可以逐项检查：

```bash
docker compose ps
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8765/v1/usage
```

`docker compose ps` 中的 `demo` 应为 `Up`，两个 `curl` 命令都应返回 JSON。

一次完整对话应经历这些状态：

```text
监听 → 用户说话 → 处理 → AI 说话 → 监听
```

打开右上角 Conversation 可以查看双方的实时转写。AI 播放语音时再次开口，可以测试打断功能。

## 六、停止和重新启动

停止全部服务：

```bash
./scripts/down.sh
```

重新启动：

```bash
./scripts/up.sh
```

只查看运行状态：

```bash
./scripts/doctor.sh
docker compose ps
```

修改模型、端口或 TTS 模式后，先执行 `./scripts/down.sh`，再执行 `./scripts/up.sh`。

## 七、配置说明

本机配置保存在 `.env`。常用变量如下：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `DEMO_PORT` | `7860` | 浏览器界面端口 |
| `REALTIME_PORT` | `8765` | Realtime WebSocket 端口 |
| `LLM_HF_MODEL` | `Qwen/Qwen3-8B-GGUF:Q4_K_M` | llama.cpp 自动下载的 GGUF |
| `LLM_ALIAS` | `girlfriend-main` | speech-to-speech 调用的模型名 |
| `LLM_CONTEXT` | `8192` | LLM 上下文长度 |
| `LLM_API_KEY` | 自动生成 | 本机 llama.cpp API 密钥 |
| `STT_MODEL` | `mlx-community/whisper-large-v3-turbo` | MLX Whisper 模型 |
| `STT_LANGUAGE` | `zh` | 识别语言 |
| `TTS_MODE` | `custom` | `custom` 内置音色或 `clone` 声音克隆 |
| `TTS_QUANTIZATION` | `6bit` | Qwen3-TTS MLX 量化级别 |
| `TTS_SPEAKER` | `Serena` | 默认内置音色 |
| `CHARACTER_FILE` | `./config/characters.custom.json` | 浏览器角色配置 |
| `STARTUP_GREETING` | 中文提示词 | 连接成功后的主动问候 |

### 切换到 14B LLM

默认 8B 更适合实时语音。如果机器内存足够，且更看重回复质量，可以修改 `.env`：

```dotenv
LLM_HF_MODEL=Qwen/Qwen3-14B-GGUF:Q4_K_M
```

然后重启：

```bash
./scripts/down.sh
./scripts/up.sh
```

首次切换会下载新的 GGUF。8B 模型不会被项目脚本自动删除。

### 修改角色

默认角色在 [`config/characters.custom.json`](./config/characters.custom.json)。每个角色需要三个字段：

```json
{
  "角色标识": {
    "label": "设置面板中显示的名称",
    "voice": "Serena",
    "instructions": "角色设定和回复约束"
  }
}
```

修改 JSON 后重新构建 Demo：

```bash
docker compose up -d --build demo
```

网页设置中切换角色时，`instructions` 和 `voice` 会同时更新，无需重新加载 TTS 模型。

## 八、声音克隆

只使用本人录制或已经获得明确授权的声音。

准备一段 5 到 10 秒、背景安静、只有一个人说话的音频，然后转换为单声道 24kHz WAV：

```bash
mkdir -p voices/xiaoman
ffmpeg -i source.wav -ss 00:00:00 -t 8 -ac 1 -ar 24000 voices/xiaoman/reference.wav
```

修改 `.env`：

```dotenv
TTS_MODE=clone
CHARACTER_FILE=./config/characters.clone.json
TTS_REFERENCE_AUDIO=voices/xiaoman/reference.wav
TTS_REFERENCE_TEXT=参考音频中逐字对应的文本
```

然后重启：

```bash
./scripts/down.sh
./scripts/up.sh
```

克隆模式会改用 Qwen3-TTS Base 模型，因此第一次启动需要下载另一套 TTS 权重。参考音频位于 `voices/`，默认不会提交到 GitHub。

## 九、日志和排错

### 查看日志

```bash
docker compose logs -f demo
tail -f logs/llama.log
tail -f logs/speech.log
```

按 `Ctrl+C` 只会退出日志查看，不会停止后台服务。

### 页面打不开

先检查 Docker Desktop 和容器：

```bash
docker version
docker compose ps
docker compose logs --tail 100 demo
```

如果 `7860` 端口被占用，修改 `.env` 中的 `DEMO_PORT`，例如：

```dotenv
DEMO_PORT=7861
```

重启后访问 `http://localhost:7861`。

### 卡在模型下载

查看两个宿主机日志：

```bash
tail -f logs/llama.log
tail -f logs/speech.log
```

`scripts/start-voice.sh` 已强制 MLX 模型使用 Hugging Face 官方 HTTPS，并关闭 Xet 下载，以避开部分镜像的大文件重定向问题。网络中断后重新执行 `./scripts/up.sh` 即可继续使用缓存。

### 有文字但没有声音

1. 打开右上角 Settings。
2. 点击“测试扬声器”。
3. 测试音也听不到时，在 Speakers 中选择实际使用的设备，例如 `MacBook Pro Speakers (Built-in)` 或已连接的 AirPods。
4. 不要选择没有扬声器的 HDMI 显示器、Teams 或 WeMeet 虚拟设备。
5. 刷新页面并重新开始对话。

新版前端会检查 Web Audio 是否被浏览器挂起。浏览器仍阻止播放时，页面会提示再次点击中央圆球启用声音。

### 能听见开场白，但说话没有反应

1. 强制刷新页面，确保加载的是 `audio-24k-v3` 前端。
2. 在 Settings 的 Microphone 中选择 `MacBook Pro Microphone (Built-in)`。
3. 把 Noise gate 拉到最左侧 `Off`。
4. 确认麦克风按钮没有显示静音状态。
5. 在 macOS“系统设置 → 隐私与安全性 → 麦克风”中允许当前浏览器访问麦克风。
6. 结束旧对话并重新开始。

当前版本会自动优先使用内建麦克风，并通过静音输出连接保持采集 Worklet 持续运行。

### llama.cpp 启动失败

```bash
tail -n 200 logs/llama.log
llama-server --version
```

Homebrew 升级后若命令路径发生变化，先执行：

```bash
brew reinstall llama.cpp
```

### 清理残留进程

正常情况使用：

```bash
./scripts/down.sh
```

脚本只会停止 PID 文件中且命令行与 `llama-server` 或 `speech-to-speech` 匹配的进程，不会按名称误杀其他程序。

## 十、准备上传 GitHub

仓库应提交源代码、配置模板和锁文件，不应提交模型、日志、密钥、缓存和个人参考音频。如果当前目录还不是 Git 仓库，先执行：

```bash
git init
```

然后检查忽略规则：

```bash
git status --short
git status --ignored --short
git check-ignore -v .env logs/llama.log voices/xiaoman/reference.wav
```

最后一条命令应显示这三个文件都被 `.gitignore` 排除。

加入文件并查看待提交清单：

```bash
git add .
git status
```

先认真查看 `git status`。确认没有 `.env`、日志、模型文件和个人音频后再提交：

```bash
git commit -m "Initial local speech-to-speech deployment"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

建议提交 `uv.lock` 和 `demo/package-lock.json`。它们锁定 Python与前端依赖版本，可以减少另一台机器安装出不同结果的概率。

## 项目目录

```text
.
├── config/                     # 角色和音色配置
├── demo/                       # 官方 Realtime 浏览器客户端
├── scripts/                    # 初始化、启动、停止和诊断脚本
├── src/speech_to_speech/       # 官方语音管线
├── voices/                     # 本机声音克隆素材，不提交音频
├── docker-compose.yml          # 浏览器 Demo 容器
├── .env.example                # 可提交的配置模板
├── pyproject.toml              # Python 项目与依赖
└── uv.lock                     # Python 依赖锁文件，应提交
```

更完整的技术选型、原教程升级对照和其他平台说明见[本地赛博 AI 女友完整部署方案](./本地赛博%20AI%20女友完整部署方案.md)。Hugging Face 上游原始说明保存在 [`UPSTREAM.md`](./UPSTREAM.md)。

## 许可证

项目主体来自 Hugging Face `speech-to-speech`，使用 Apache-2.0 许可证。发布修改版本时请保留 [`LICENSE`](./LICENSE) 和上游版权信息。
