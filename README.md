# Creative Agent 电商创意工作台

当前版本 **0.5.5 开发结项快照（内部候选版）**。在本地整理商品资料、研究参考视频和自有镜头，生成、修改并保存拍摄脚本。用户自行连接模型及可选付费数据服务。

本轮开发于 2026-10-26 结束。本仓库发布经过筛选的最终源码，不含客户资料、密钥、账单或私有开发历史。已完成范围与未通过条件见 [0.5.5 交付状态](docs/releases/0.5.5.md)。这份结项快照不代表正式商用验收通过。

下载： [GitHub Releases](https://github.com/jacksonpangc-hash/creative-agent-workbench/releases)。当前只有 macOS Intel 内部体验包，未完成正式签名和公证；没有经过验收的 Windows 或 Apple Silicon 安装包。

主要入口为“创作台”和“我的素材”，任务为“写脚本”“拆视频”“找参考”。没有新视频生成、仿拍或独立 DAM 工具导航。

## 现有能力与验收边界

- 中文检索采用固定 FastEmbed、FTS5 和 Qdrant Local。安装包携带离线模型；向量相似不是事实或效果证明。
- 视频按实际原生输入或抽样画面处理，台词使用本地 whisper.cpp 与 VAD。观察与台词保存时间、版本、来源和质量状态；自动识别仍须核对。
- 创作前分别读取当前商品事实、参考表达、自有镜头和人工偏好。显式选材限制范围，发送模型前重新核验资料权利与版本。
- 脚本可回看原片、逐行改稿、保存新版本和按已保存版本导出。备份包含媒体及历史依据；恢复为新项目，不恢复收费任务和凭证。
- 浏览器收藏使用 Manifest V3 与 Native Messaging，首次连接在桌面确认项目。正式 Chrome／Edge 商店 ID 与链接尚未取得，不能按已上架销售。
- 模型费用与蝉妈妈积分分账；不确定提交待核账，不自动重发、充值或切换付费来源。
- 对话与脚本的未完成编辑保存在本机项目内；读取失败可重试读取，发生版本冲突时保留两份，显式保存脚本才生成脚本版本。刷新当前本机标签页可继续连接，重启服务后使用新的应用启动入口。
- 可按具体素材与版本选择分析成果、引用真实片段继续创作、以已保存脚本为基线生成新修订；字段模板和成果索引保留版本关系。
- 内置 647 条按 CC BY 4.0 署名的 TikTok 历史创意结构参考，支持离线查找；不包含原视频、实时热度或成交指标，不能当作完整行业数据库。

真实商品创作质量、两平台完整收藏链、蝉妈妈商业链路、正式授权部署及各目标设备均须独立验收。本机代码或契约通过不能替代这些结果。当前不承诺正式商用就绪。

## 使用

从桌面应用进入“设置”连接自己的模型，添加商品和素材，再描述目标、时长与拍摄限制。结果中的“查看创作依据”提供商品依据和可回看的片段。人工修改后保存新版本，在“全部创作 → 脚本”重开。详情见 [使用说明](docs/creative-loop-054/user-guide.md)。

默认资料目录为 macOS 的 `~/Library/Application Support/CreativeAgent` 或 Windows 的 `%LOCALAPPDATA%\CreativeAgent`。密钥只保存在系统密钥库。项目资料、账号许可和浏览器授权分别管理；第三方使用限制继续生效。

## 开发与构建

需要 Python 3.12、锁定的 Python／Node 依赖、Rust 及目标系统的原生构建工具。客户使用完整桌面包不需要这些开发工具。

```sh
uv sync --extra dev --extra build
npm ci
npm run build
.venv/bin/python scripts/prepare_asr.py
.venv/bin/python scripts/prepare_vad.py
.venv/bin/python scripts/prepare_local_embeddings.py
```

打包前还须准备经核验且含对应源码的 FFmpeg 分发资源，不能将未核验的系统二进制塞进安装包。构建和资源检查规则见 [发行说明](docs/distribution.md) 与 [第三方声明](THIRD_PARTY_NOTICES.md)。

```sh
.venv/bin/python scripts/package_worker.py
.venv/bin/python scripts/build_native_host.py
npm run tauri:build -- --bundles app
```

上述构建按当前宿主系统与架构执行；不能跨平台改名冒充 Windows 或 ARM 包。模型资产准备只发生在构建阶段，桌面运行不自动下载权重。启动链接含本次会话令牌，不要分享。

## 文档与许可

[架构](docs/architecture.md) · [创作上下文](docs/creative-context.md) · [视频理解](docs/video-understanding.md) · [数据来源](docs/data-sources.md) · [浏览器收藏](docs/browser-extension.md) · [授权服务](docs/licensing.md) · [资料归属与隐私](docs/privacy-and-ownership.md)

源码保持 **AGPL-3.0-only**，见 [LICENSE](LICENSE)。账号收费不改变开源许可义务，也不保证本地源码不可被修改。分发前须履行实际 FFmpeg、模型、字体与其他依赖义务。本次发布源码与内部候选包，不代表商业服务上线或扩展商店上架。

可公开源码通过 `scripts/package_public_source.py` 从明确提交导出，包含文件清单与哈希，排除私有开发记录、用户媒体、台词、供应商响应、账单、凭证和 Git 历史。该检查不是正式法律或安全认证。
