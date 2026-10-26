# 视频输入、观察与台词边界

本文对应 0.5.4 创作闭环实现。这里描述代码和已准备的资源；真实模型识别质量、性能、浏览器行为与最终安装包以集中检查记录为准。

## 实际输入

`VideoUnderstanding.prepare()` 从同项目素材读取实际媒体。图片能力模型接收最多 36 张真实解码 JPEG；帧选择覆盖场景边界、前 3 秒与全片分布。帧记录包括稳定 frame ID、JPEG SHA256、源帧索引、原始 PTS、时间基、毫秒坐标和尺寸，不把请求的 seek 时间当作实际 PTS。缓存先验证文件和身份，失效后重新解码。

原生视频路线传入实际视频；按范围解析时生成对应片段并保留原片坐标映射。传入视频即使包含音轨，也不代表实现了音乐、声线或语气理解。

每份输入保存为现有记录模型下的 `media_input_manifest`，对象名 `MediaInputManifest`。其稳定 hash 包含项目、素材/媒体版本、真实帧、此次提交字幕段、抽样覆盖、管线版本及所选模型和接口配置。准备状态为 pending；真实请求的运行/完成/失败状态和供应商有返回时的请求 ID 另外记录，不把准备输入称为模型已看懂。清单不包含 Key、Authorization、图片 base64 或本地文件路径。

## 逐条结论

`evidence_policy.validate_observations()` 对照清单检查 `claim_kind / statement / interpretation / support`：

| 结论 | 必须引用的输入 |
| --- | --- |
| 画面观察 `visual_observation` | 本次实际帧 ID/PTS，或已输入原生视频的有效时间范围 |
| 画面文字 `screen_text` | 同上；数字、价格、单位保留疑点，不直接成为商品事实 |
| 台词内容 `spoken_content` / `transcript_content` | 质量策略允许的具体 transcript segment ID 与原时码 |
| 严格逐字依据 `spoken_evidence` | 已人工校对的具体字幕段 |
| 创意目的 | inferred；有依据仍是推断 |
| 新的拍摄建议 | proposed；不能声称是原片发生的事件 |

整条视频 ID、错误模态、串素材/版本、错误帧时间和越界范围不能替代具体支持。有效结论经 `persist_observations()` 保存为现有 `observation` 记录，附清单、模型配置版本、质量、允许用途和请求 ID。持久化时再次检查当前媒体与字幕版本。

这些检查能证明引用来自实际输入，不能保证自然语言语义正确。抽样帧不能证明抽样间的连续动作或极短瞬间；当前没有独立全片 OCR 或声学理解能力。

## 字段与逐镜解析

`Workflows.parse()` 复用现有 parse、模板快照与 source_context。实际视频模型绑定自动进入可验证路线，不用整条 route 替所有字段决定模态。

模型在整体或对应镜头返回 observations，并用 `field_ids` 关联模板字段。每个 cell 另存 observation IDs、claim kind、interpretation、quality 和 allowed uses。无有效支持的 observed 值为 null，状态为 not_observed 或 insufficient；不把缺失值补零。逐镜引用还须落在对应镜头区间内。

原分析的项目、素材与冻结片段范围从主库再检查，调用者提供的 source_context 不能扩张范围。已完成素材的结果按条保存，后续失败保留此前成果。新的视频 parsed_text 只纳入质量允许检索的细粒度结论。

旧解析/无模型绑定的兼容调用及当前图片旧解析保留原记录结构并标 `legacy_evidence`，不补造不存在的 frame ID。历史可打开性不等于细粒度验收完成。

## 台词的三种状态

共享 `transcript_policy(asset, purpose)` 分开处理：

- execution：pending / running / succeeded / failed / cancelled。
- quality：unknown / usable / suspect / unusable。
- review：unreviewed / corrected。

空文本、错误/越界时码不可用；异常重复、明显语言冲突、异常文字时长比例、无语音区域输出等产生疑点。这些是风险信号，不是估计准确率。老 ASR 没有质量记录时保持 unknown。

usable 但未校对的自动台词可用于主题检索、结构参考、解析和创作背景，须保留自动识别提示。逐字引用要求 corrected。suspect、unusable、unknown 不进入正向台词索引，不作为关键口播依据。任意台词均不能自动验证商品功效。

本地原始疑点字幕仍可查看和纠正，但不会夹在视频观察的 policy 或 metadata 里再次作为已允许文本发送。字幕修正沿用 `/assets/{id}/subtitles`，显式 reviewed 和预期台词修订版本；同一文字稿被并行修改时应拒绝覆盖并让用户重新读取。

## 官方 VAD 与资源

默认保持 whisper.cpp v1.9.5，commit `d1be6fde11ac6e0407606b4e42fe72d34add8037`。`scripts/prepare_vad.py` 只在准备发行资源时取得与该版本匹配的官方 Silero v6.2.0 转换模型及 MIT 许可；应用运行时不下载权重，不要求用户配置 shell 环境。

- 模型：`ggml-silero-v6.2.0.bin`，SHA256 `2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987`。
- 模型仓库固定 revision：`9ffd54a1e1ee413ddf265af9913beaf518d1639b`。
- MIT 许可来源固定 commit：`be95df9152c0d7618fa1edfeb296fc3dae32376f`；许可 SHA256 `2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b`。

实际调用 `--vad --vad-model`，不再把近零振幅分段当作语音分类。固定 threshold 0.5、最短语音 250ms、最短静音 100ms、最长段 30s、边缘 30ms、overlap 0.1s，文字上下文 0，关闭温度 fallback，默认语言 auto。VAD 和 ASR 均保留原片时间映射。结果记录音频时长、耗时、参数与质量原因；无语音不当作成功文字稿。

取消只终止本次拥有的子进程。相同媒体 hash、模型/参数和字幕 hash 可复用结果；人工校正默认保留。失败不会自动切引擎或多模型 A/B 重跑，也不会抹掉原有可用字幕。既有显式配置的可选 faster-whisper 保持兼容，不作为失败回退。

## 素材详情

播放器常驻，分析、台词、分镜沿用现有入口。逐条结论按自身 claim kind 与 interpretation 显示，画面和字幕的时间按钮使用毫秒坐标；点击支持项直接跳回对应输入。旧粗粒度引用显示“旧版依据”，不展示成已逐条核对。

台词页分别显示执行、质量和人工校对状态。疑点或未知台词可回听、修改，不能直接作为允许的对话依据。保存失败保留编辑内容；取消校正恢复原台词。原始分镜只用于定位，不等于模型结论。

## 验证范围

实现增加了细粒度/错模态/串版本/越界/旧质量未知、解析字段支持、范围不可扩张、字幕修正与精确跳转等用例。按本轮授权不在分任务独立运行测试、构建、设计检测或耗时真实 ASR，由主线最后集中检查。用例存在和资源准备不等于真实识别质量或最终商业包已验收。
