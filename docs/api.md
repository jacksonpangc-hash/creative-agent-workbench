# 0.4.0 收敛接口

新行业调用只允许蝉妈妈；旧来源新连接/新任务返回410。生成新配置、新任务及重新执行返回410；已提交历史任务仍可查询、取消和核账。CMM接口见[接入说明](industry-integration.md)。本轮 CMM 在团队模式明确返回501，不宣称个人密钥可安全复用于团队。

分析范围支持 `reference_ids`，与本地 `asset_ids` 合计最多200；引用上游内容保留提供方、观察方式、时间与版本。回答可带 `content` Markdown 正文，事实引用仍需通过范围校验。笔记和自定义指令保存幂等，重复保存保留已编辑版本。

# 共享本地接口

服务仅监听127.0.0.1。每个 `/api/` 请求都需要 `Authorization: Bearer <本次令牌>`；校验Host与Origin，不能把API暴露到公网。页面立即清除URL中的令牌。本地运行的临时令牌同时保留在当前标签页、当前来源的 sessionStorage 中，以支持刷新；不写入 localStorage、Cookie、项目或备份。重启服务后旧令牌失效，使用新启动链接。模型 Key 留在系统密钥库，团队登录令牌仍只在内存中。

- `GET/POST /api/projects`：项目清单/创建 `{name,budget,currency}`。
- `GET /api/projects/{p}/state`：素材、模型配置（无Key）、会话、解析、笔记和任务。
- `POST /api/projects/{p}/models`：`{provider,label,model_id,role,protocol,base_url,api_key}`；编辑时增加id，密钥留空保留。只写系统密钥库，不做付费测试。
- `POST /api/projects/{p}/assets?source_kind=own`：multipart `file`；单文件≤1GB。
- `POST /api/projects/{p}/download`：`{url,rights_confirmed:true,source_kind}`；只接直接媒体，固定验证后的公网IP与TLS名称，不猜平台私有接口。
- `POST /api/projects/{p}/actions`：异步任务，`{kind,binding_id,question,asset_ids,quote,idempotency_key}`。kind为analysis/followup/parse/embedding/search。analysis支持mode、groups、scope；parse需fields，最多100素材；followup需session_id；embedding建立检索索引，search通过相同向量模型检索。native_video只有明确选择才用，不隐式降级。
- **已取消，返回410** `POST /api/projects/{p}/generation`（以下为历史契约，不可新建）：`{binding_id,strategy_binding_id?,quote,idempotency_key,request}`，quote为每条预留。request包括project_id、reference_asset_id、reference_image_ids（≤6）、reference_start_ms、reference_duration（≤15）、prompt、mode(faithful/reasoning)、duration、batch(1–8)、ratio、resolution、audio。reasoning必须有strategy_binding_id。
- `POST /api/projects/{p}/jobs/{j}/poll|cancel|resume|reconcile|settle|archive`：poll只查询，resume仅恢复queued；reconcile将needs_attention与真实provider_request_id关联，不会重新提交；settle需 `{actual_cost,confirmed:true}`，archive需 `{rights_confirmed:true}`。
- `GET /api/projects/{p}/jobs/{j}/events?after=0`：持久事件增量。
- `POST /api/projects/{p}/notes`：`{session_id,title,turn_index?,finding_indexes?}`；PUT `/notes/{id}` `{markdown}`；GET `/notes/{id}/export?format=md|pdf|xlsx|csv`。
- `GET /api/projects/{p}/parses/{id}/export?format=xlsx|csv|md|pdf`。
- `POST /api/projects/{p}/reports/import`：UTF-8 CSV，≤20MB；`POST /review`：`{asset_ids,start_date?,end_date?}`。
- `GET /api/projects/{p}/clusters?first_three=true&recent_days=7&source_kind=own,finished&lifecycle=active`：明确标记色彩近似聚类，不宣称语义聚类或因果。

使用CLI调用时，JSON写入本地临时文件，再执行 `creative-agent api /api/projects/ID/actions --method POST --json-file 路径`。凭证不能写入这些请求文件；模型Key设置请在应用中完成。任务请求中的quote是本地预留，不是供应商承诺的最高费用。执行前确认素材外传、模型与预留预算。

所有计费请求的 `idempotency_key` 必填。客户端遇到响应丢失时保留相同编号，不能生成新编号重试；用户明确开始新操作后才更换。每个任务保存不可变模型、端点和版本化密钥引用，编辑设置不改变旧任务。仍有活跃任务时不允许删除依赖的模型。旧版无快照任务只供人工核对，不自动使用最新配置调用。

`POST /jobs/{j}/favorite` 接受 `{favorite:true|false}`；`GET /assets/{id}/clip?start_ms=0&duration=5` 返回实际裁剪的≤15秒参考片段。核账只适用于完成、失败、取消和需核对状态，不能释放执行中任务的预留。

## 0.2 策略工作台扩展

- AnalysisScope新增`group_ids`（最多20，项目内解析并集，最多200）和`date_basis=published|imported|statistics`。发布日期缺失排除；导入日期按记录UTC日；统计日期按关联报表日。只有统计日期限制报表聚合时段，其他日期只筛素材。入队冻结实际asset_ids并清除group_ids，后续分组改动不扩大范围。
- 新分析结果包含`result`：定向comparisons/recommendations；探索patterns/differences/opportunities/evidence_gaps；灵感phase/questions/directions/script_outline/brief。灵感允许信息充分时直接产出，不强制经过固定轮次。旧记录仍可展示，新模型调用必须满足模式结构。
- `POST /projects/{p}/briefs`：session_id、非负turn_index、finding_indexes、title、objective、可选hypotheses。按项目读取会话并快照结论、证据、范围。
- `GET /projects/{p}/briefs[/{id}]`：项目内列表/读取。
- `PUT /projects/{p}/briefs/{id}`：expected_version、title、objective、constraints、hypotheses、references（EvidenceRef列表）、confirmed；原子比较修订版本，不匹配409。来源不可编辑，片段和引用不得越界。
- GenerationRequest可选brief_id；需已确认简报，任务payload.brief_snapshot保留提交版本，供应商提示带入目标/限制/假设。幂等重试沿用原快照，不读取新修订替换它；成片归档携带brief_id/brief_version/generation_job_id，复盘lineage返回原session_id。
- 解析Action可带source_session_id/source_turn_index。工具层校验项目与所选素材在来源证据内，快照source_context随任务/解析记录保存。

简报另保存可编辑`script_outline`；从已收敛灵感结果建立时自动读取该轮的制作约束、假设和大纲，仍需用户确认。旧客户端未传脚本的修订保留已有脚本。报告包含模式专有章节并保存result_snapshot/answer_snapshot/turn_index/conclusion_version，Markdown/PDF导出不再遗漏这些章节。
