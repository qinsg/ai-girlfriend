# 数字人运行服务

这个目录保存项目自有的 FastAPI 适配层。当前画面由两个通道组成：

- LivePortrait 根据角色照片预先生成静音待机循环，持续负责眨眼和视线动作。
- LivePortrait lip retargeting 为每个角色预先生成 6 档高清嘴形。服务用 106 点人脸关键点定位嘴唇，并把羽化区域写入 PNG 透明通道。浏览器根据正在播放的回复音频实时选择嘴形，只覆盖嘴部区域。

这条链路不再为每段回复生成 MuseTalk 视频。声音进入 AudioWorklet 后即可播放，嘴形直接跟随输出分析器，没有逐句渲染等待，也没有 256×256 VAE 口块放大造成的模糊。

`./scripts/bootstrap-avatar-macos.sh` 会安装以下固定版本：

- [FasterLivePortrait-MLX](https://github.com/ivanfioravanti/fasterliveportrait-mlx) commit `d5361f4806c14fe2051eecb1dd5a89930f46db0d`
- [FasterLivePortrait-MLX weights](https://huggingface.co/ivanfioravanti/FasterLivePortrait-MLX-weights) revision `2cc2ac92c9fe65ca4fb68cb1a1556ead285e7391`
- 同一权重仓库中的 LivePortrait lip-retargeting 运行文件

运行时代码位于 `.runtime/`，模型权重位于 `models/avatar/`，待机缓存位于 `.runtime/avatar-idle/`，高清嘴形缓存位于 `.runtime/avatar-visemes/`。这些机器本地文件均已被 `.gitignore` 排除。

正常使用只需从仓库根目录执行 `./scripts/up.sh` 和 `./scripts/down.sh`。首次预热会生成待机视频和 6 张嘴形，后续启动直接复用缓存。嘴形的开口比例限制在 `0.00` 到 `0.22`，避免 LivePortrait 在大幅度 retargeting 时拉裂嘴角。

接口：

- `GET /health`：服务能力和模型状态。
- `POST /warmup?character=xiaoman`：生成待机循环和高清嘴形缓存。
- `GET /idle/xiaoman`：返回可循环播放的静音 MP4。
- `GET /viseme/xiaoman/0` 至 `/viseme/xiaoman/5`：返回闭嘴到明显张嘴、按人脸关键点定位的透明 PNG 嘴部贴片。

FasterLivePortrait-MLX 代码采用 MIT 许可证；模型文件遵循其上游许可证。不要把下载后的模型权重或 `.runtime/` 缓存提交到仓库，重新分发或商用前应核对 LivePortrait 及模型页面的条款。
