# 0.4.0 当前模型使用方式

策略页仅显示视频分析与口播分析，兼容型号由系统匹配，高级设置可指定。GPT/Claude 的图像型号通过内置实际画面序列与本地口播使用；这不等于模型原生读取完整视频。纯文本型号仅处理可靠文字稿。检索为高级可选配置。视频生成配置和新任务已取消，以下生成说明仅供历史记录核对，不能据此启用新任务。真实付费账号和输出质量未验收。

# 模型与商业通道

更新：2026-10-19，0.3.1。设置按“分析模型／视频生成”组织，向量模型位于“搜索增强”。三种策略模式共用分析服务，视频／口播是任务选择。`model_catalog.py` 以具体型号和已实现输入路线决定可选项，不按厂商名称推定能力。所有真实模型账户和输出质量仍待用户授权验证。

| 厂商 | 可用的视频分析路线 | 独立制作／检索能力 |
|---|---|---|
| 阿里云百炼 | Qwen3 VL Plus：内置实际抽帧＋已有台词；不冒称原生视频通道接通 | text-embedding-v4 |
| 火山引擎 | 未核验的账户接入点不能作为视觉分析型号 | 精确Seedance参考协议，见生成通道说明 |
| 智谱 | GLM 4.6V：内置实际抽帧＋已有台词 | embedding-3 |
| MiniMax | M3系列：已实现原生视频输入；M2.7仅口播/文字推理 | H3生成协议 |
| Kimi | K2.6：已实现原生视频输入 | 未接视频生成 |
| DeepSeek | 已登记Flash视觉型号：内置实际抽帧＋已有台词 | 未接视频生成 |
| OpenAI | 已登记GPT视觉型号：内置实际抽帧＋已有台词 | text-embedding-3-small/large |
| Google | 已登记Gemini图像型号：内置实际抽帧＋已有台词；原生视频协议仍待适配 | 未接当前生成通道 |
| Anthropic | 已登记Claude视觉型号：内置实际抽帧＋已有台词 | 未接视频生成 |
| 可灵 | 不作为分析服务 | Kling O1参考视频生成；与视频同时参考图片的限制见生成通道说明 |

新分析／解析任务由服务器冻结 `video_route`。内置流程运行实际FFmpeg，按镜头、前3秒和全片分布提取最多36张帧，保留实际PTS、对应图像顺序、最大观察间隔和台词来源。无时间戳台词时明确声音缺口；不能将抽样称为逐帧理解，也不推定原生视频接口一定支持全部音轨。纯文字型号不能接收图片或视频。能力的可执行说明见 [内置视频理解](../skills/video-understanding/SKILL.md)。

旧任务的模型、凭证、计费快照不回写；没有新路线快照的旧任务保持原行为。预留费用前检查兼容性、范围和本地媒体，执行前再次核对，错误不会回显密钥。视频生成单独发起，不随分析自动执行。真实账户、地区、额度、费用和质量须分开验收。

## 来源

- MiniMax：[M3 Anthropic协议](https://platform.minimax.io/docs/api-reference/text-anthropic-api)、[H3创建](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)、[查询](https://platform.minimax.io/docs/api-reference/video-generation-v2-query)、[取消](https://platform.minimax.io/docs/api-reference/video-generation-v2-delete)。查询路径为 `/v2/query/video_generation/{task_id}`，取消为 `/v2/video_generation/{task_id}`。
- 阿里云：[Wan3](https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference)。工作空间、模型、Key与地域必须一致。视频不能假定可内联Base64。
- 火山引擎：[视频生成API](https://www.volcengine.com/docs/82379/1520757)。接入点、地区与参考输入能力仍需按账户核对。
- DeepSeek：[视觉接口](https://api-docs.deepseek.com/guides/vision)、[模型/计费](https://api-docs.deepseek.com/quick_start/pricing)。当前Flash支持图片，不能沿用“DeepSeek没有多模态”的旧假设。
- Kimi：[模型总览](https://platform.moonshot.cn/docs/api/models-overview)、[Quickstart](https://platform.moonshot.cn/docs/guide/kimi-k2-5-quickstart)。页面名称和现行内容可能不同，不把历史URL名当作现行型号。
- Google：[视频理解](https://ai.google.dev/gemini-api/docs/video-understanding)、[视频生成](https://ai.google.dev/gemini-api/docs/video)。Interactions新路径另需适配验证。
- 图片型号补充来源：[OpenAI GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1)（已标记为旧版，账户开放情况未实测）、[Anthropic型号总览](https://platform.claude.com/docs/en/models/overview)、[千问视觉](https://help.aliyun.com/zh/model-studio/vision)、[智谱GLM-4.6V](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-4.6v)。
- OpenAI：[图片视觉](https://developers.openai.com/api/docs/guides/images-vision)、[视频文档](https://developers.openai.com/api/docs/guides/video-generation)。
- 蝉妈妈：[正式Skill与参考文档](https://cdn-cmm-ai-open.chanmama.com/.well-known/agent-skills/cmm-api/SKILL.md)。网关 `https://ai-api.chanmama.com`；权限查询与execute分离，会员不代替正式API权限。当前仅权限查询在UI可达，真实素材操作的字段、费用与留存权未验收。

## 本轮正式数据文档补核（2026-10-19，未执行商业调用）

已重新读取官方 `cmm-api/SKILL.md`（文档自述版本2026-10-14）、`references/video.md`、`references/video-tools.md`。可确认：千川跑量素材查询 `video_library_custom_search` 使用 `start_date/end_date`；常规视频关键词库 `video_library_custom_search_video`、带货关键词库 `video_commerce_video_library_custom_search_top_selling_video` 使用 `create_start_time/create_end_time`。榜单不能代替关键词检索。`video_detail_info` 使用 `aweme_id`，不能把它当作所有接口通用素材ID。

数据为T+1，商业“近N天”截止昨天，与本地素材按发布日期筛选不是同一时间约定。视频工具分为 `extract_video_copywriting`、`video_storyboard`、`video_understanding`；前者不支持用千川 `material_id` 替代链接，后者是异步任务。分镜文档列出 `st/ed/clip_video_url/voice`，但未核实真实响应中的时间单位、URL留存期与下载/模型处理权，不自动导入执行，也不与本地毫秒时间混用。

正式接通仍须逐项验证：账户 `rights/group_id` 与查询周期、具体返回字段和空值/销售区间、分页去重、供应商更新时间与抓取时间、稳定来源ID、重叠时间窗口增量刷新和删除/失效处理、点数及失败计费、数据留存/模型处理权。稳定ID应以通道+实体类型+供应商ID组合，保留发布日期/统计日期/获取日期各自含义。当前行业参考页已有连接、查询计划、授权选材与媒体导入流程；蝉妈妈实际响应映射、账户、费用与质量仍未通过商业验收，不能把文档核验和模拟响应当作业务已跑通。
