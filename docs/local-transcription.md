# 内置本地口播识别

桌面包内置 whisper.cpp v1.9.5、multilingual small 权重和 FFmpeg。用户无需安装 Python、设置模型环境变量或首次联网下载；媒体只在本机处理，不调用付费接口。当前验收宿主为 macOS Intel x86_64，运行库按本机 CPU 优化并在 manifest 中记录指令需求，不宣称兼容所有 Intel CPU；当前包使用当前宿主CPU原生优化（i7-4870HQ / AVX2、FMA、F16C），不宣称适用于所有Intel CPU；其他CPU或架构需要各自原生构建和验收，不能改名冒充通用二进制。

转写先提取 16kHz 单声道 PCM，拒绝无音轨、无法解码、空音轨和近静音，保存分段毫秒时间戳及模型版本、模型 SHA256、语言、时间与“未人工核对”标记，成功后移除旧向量索引。每条素材独立处理；批次错误保存在 `errors`，其他素材继续，混合结果为 `needs_attention`。原始素材和失败前字幕均保留。噪音、背景音乐、方言及重叠说话仍可能误识别；口播结果需要人工核对，近静音检查不等于完整的人声检测。

## 开发与发行

构建环境需要 Python、Git、CMake 与平台 C++ 编译器，仅构建时下载模型。运行 `python scripts/prepare_asr.py`，随后 `python scripts/package_worker.py`。源代码、构建物和 466MiB 权重位于已忽略的 `.state/asr-source` 与 `.state/asr`，不可提交模型到仓库。打包脚本在资源缺失或模型哈希不一致时停止；完整资源被加入 worker，冻结运行时通过 `_MEIPASS/asr` 定位，开发时从项目 `.state/asr` 定位。

分发包必须保留 `whisper.cpp-LICENSE.txt`、`openai-whisper-LICENSE.txt` 、`THIRD_PARTY_NOTICES.txt` 及 `manifest.json`。whisper.cpp 和 OpenAI Whisper 权重采用 MIT；FFmpeg 许可证随 imageio_ffmpeg 打包保留。官方模型固定 SHA256：`1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b`。来源及构建提交记录在 manifest；具体宿主结果见 `docs/plans/bundled-asr-report.md`。

已有管理员明确设置 `CREATIVE_WHISPER_MODEL` 时仍保留 faster-whisper 本地目录兼容方式（自行安装可选组件，禁止下载）。正常桌面用户使用内置方式，无需此变量。

末段恰好结束于whisper最后30秒解码窗口边界但超出媒体时长时，仅裁剪该末段终点，并保留原始终点和调整来源；任意异常越界仍拒绝。重复相同文本占多数且至少3段时，保留内容并在转写来源中写入needs_review及核对提示，不将技术完成视为准确率合格。构建缓存绑定真实CPU/架构/系统指纹，宿主变化后必须重新构建。
