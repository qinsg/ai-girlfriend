# 数字人运行服务

这个目录保存项目自有的 FastAPI 适配层。服务由两个相互独立的画面通道组成：

- LivePortrait 根据角色照片预先生成静音待机循环，画面始终播放，负责眨眼和视线动作。
- MuseTalk 1.5 MLX 接收约 1 秒的 PCM WAV，只重绘待机帧的下半脸，生成带声音的短口型片段。

浏览器在待机视频上方连续播放这些短片。某段播放时，服务串行生成下一段；回复结束后只隐藏口型层，下面的待机画面没有中断，因此不会出现“视频播完又跳回照片”的切换。

`./scripts/bootstrap-avatar-macos.sh` 会安装以下固定版本：

- [FasterLivePortrait-MLX](https://github.com/ivanfioravanti/fasterliveportrait-mlx) commit `d5361f4806c14fe2051eecb1dd5a89930f46db0d`
- [FasterLivePortrait-MLX weights](https://huggingface.co/ivanfioravanti/FasterLivePortrait-MLX-weights) revision `2cc2ac92c9fe65ca4fb68cb1a1556ead285e7391`
- [musetalk-mlx](https://github.com/xocialize/musetalk-mlx) commit `c6eb30ebd1d4d043983209813370153de9346bf`
- [MuseTalk 1.5 MLX fp16 weights](https://huggingface.co/mlx-community/MuseTalk-1.5-fp16) revision `ad54104a0129121fe2ea67471250c0656c985284`

运行时代码位于 `.runtime/`，模型权重位于 `models/avatar/`，生成的待机缓存位于 `.runtime/avatar-idle/`。这些机器本地文件均已被 `.gitignore` 排除。

正常使用只需从仓库根目录执行 `./scripts/up.sh` 和 `./scripts/down.sh`。首次预热会生成待机视频、检测脸部区域并预编码每帧的 VAE latent，后续对话直接复用缓存。

接口：

- `GET /health`：服务能力和模型加载状态。
- `POST /warmup?character=xiaoman`：生成待机循环并预编码角色。
- `GET /idle/xiaoman`：返回可循环播放的静音 MP4。
- `POST /lipsync?character=xiaoman&start_frame=0`：接收 PCM WAV，返回短口型 MP4，并通过响应头返回下一段应继续使用的帧位置。

FasterLivePortrait-MLX 与 musetalk-mlx 代码采用 MIT 许可证；模型文件遵循各自的上游许可证。不要把下载后的模型权重复制进仓库，重新分发或商用前应分别核对 LivePortrait、MuseTalk 及模型页面的条款。
