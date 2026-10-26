# 共享本地接口（0.5.0）

服务仅监听127.0.0.1。每个 `/api/` 请求都需要 `Authorization: Bearer <本次令牌>`；校验 Host 与 Origin，不能暴露到公网。令牌只保存在当前会话内存中。

## 项目与本地素材

- `GET/POST /api/projects`：项目清单/创建 `{name,budget,currency}`。
- `GET /api/projects/{p}/state`：授权有效的素材、模型配置（无 Key）、会话、笔记、解析、我的指令和任务历史。
- `POST /api/projects/{p}/models`：`{provider,label,model_id,role,protocol,base_url,api_key}`；编辑增加 id，密钥留空保留。分析/视觉/向量模型由用户接入付费；生成用途配置返回410。模型 Key 在应用设置中保存至系统密钥库，不做付费连通测试。
- `POST /api/projects/{p}/assets?source_kind=own`：multipart `file`，单文件≤1GB；上传和链接入口位于当前对话。
- `POST /api/projects/{p}/download`：`{url,rights_confirmed:true,source_kind}`；仅直接媒体，验证公网 IP 与 TLS 名称，不猜平台私有接口。平台视频内容使用下述 CMM 预检。
- `POST /api/projects/{p}/transcriptions`：`{asset_ids,idempotency_key}`；随包本地口播识别，外部费用为0，使用本机资源。保留时间戳及失败记录，不把部分失败写成成功。

## 分析、解析与笔记

- `POST /api/projects/{p}/actions`：异步模型任务；共同字段为 `{kind,binding_id,question,asset_ids,quote,idempotency_key}`。kind 支持 analysis/followup/parse/embedding/scene_embedding/search；后几项为当前对话工具或检索能力，不意味着独立主页面。
- analysis：`mode` 为 directed/exploration/inspiration；`analysis_kind` 为 video/spoken。`scope` 包含 `project_id,asset_ids,reference_ids,group_ids` 等明确筛选；本地素材与行业参考合计≤200条。CMM 引用必须来自本项目且仍有保留/模型处理授权。`groups` 不能包含范围外素材。`discussion_only:true` 表示无素材讨论，scope 中不得同时选择素材、参考、分组或 discovery。
- video 路径使用明确确认的 `native_video`/`video_route`；不隐式切换为抽帧或口播。spoken 使用已识别的口播；分析前先完成缺失的本地转写，并如实记录无音轨/识别失败。
- followup：提供 `session_id`，继承原会话范围和分析方式，不自动扩大素材或来源授权。
- parse：提供 `fields` 和≤100个 `asset_ids`。从当前讨论发起时附 `source_session_id` 与 `source_turn_index`，保留所选轮次上下文；上游行业证据不是可随意下载的本地素材。
- `POST /api/projects/{p}/notes`：`{session_id,title,turn_index?,finding_indexes?}`，用户明确保存；重复保存同一选段幂等并保留编辑。`PUT /notes/{id}`：`{markdown}`；`GET /notes/{id}/export?format=md|pdf|xlsx|csv`。
- 我的指令：`POST /api/projects/{p}/instructions`；编辑 `/instructions/{id}`，渲染 `/instructions/{id}/render`。复用要求存为指令，不新建独立解析工具页面。
- `GET /api/projects/{p}/parses/{id}/export?format=xlsx|csv|md|pdf`；`POST /api/projects/{p}/reports/import` 接收≤20MB UTF-8 CSV；`POST /review`：`{asset_ids,start_date?,end_date?}`。报表复盘和脚本比较在当前对话中使用。

模型任务 `quote` 为项目 CNY/USD 预留，不是供应商承诺的最高费用；执行前确认素材外传、模型和预算。幂等 `idempotency_key` 必填，响应丢失继续使用原标识，不能另建编号重发。读取、模型使用、笔记与导出均再次检查来源授权。

## 蝉妈妈 CMM

CMM 是唯一可新建任务的行业数据服务；旧 `/industry` 多供应商连接、查询、选择/导入与重放入口已停用，历史数据仍按原授权读取。

- `GET /api/projects/{p}/cmm`：连接、权益、response_schema_ready、积分预留和任务。`PUT /cmm/connection`：`{api_key,label?}`，只在应用设置中输入 Key；`POST /cmm/permissions`：`{intent}` 检查官方权益。来源保留/处理/导出授权通过 `PATCH /cmm/connection/rights` 单独确认，不能从会员或 Key 推断。
- `POST /api/projects/{p}/cmm/preflight`：`{operation,query}`，仅本地检查，无供应商调用。返回 `max_points_per_call,tariff_verified,tariff_basis,tariff_evidence,response_schema_ready,disabled_reason`。若 `max_points_per_call=null` 或 `tariff_verified=false`，费率未知，禁止提交内容任务；先取得明确收费依据并核验每次最大积分。`response_schema_ready=false` 也禁止创建任务，不能先收费再试解析。
- 管理费率：`PUT /cmm/tariffs/{operation}`：`{max_points_per_call,evidence,verified:true}`；只接受用户明确核验的最大每次费率，不猜视频工具或轮询收费。积分与 CNY/USD 模型预算分账，预留不是已核实扣费。
- `POST /cmm/searches`：`{kind:commerce|popular|qianchuan,keyword,category?,start_date,end_date,target_count?,max_calls,max_points,idempotency_key}`；`POST /cmm/categories`：`{keyword,kind:product|author,idempotency_key}`。当前搜索、分类和详情的完整返回 schema 尚未实账核验，创建前拦截，不能宣称可用或用空结果掩盖缺口。
- 内容工具：`POST /cmm/jobs`：`{operation,query,max_calls,max_points,idempotency_key}`。文案 extract_video_copywriting 必须有 video_url；分镜 video_storyboard 需 video_url 或蝉妈妈 video_id；异步 video_understanding 需非空 video_urls 数组。仅用户明确给出针对性要求时传 custom_prompt。千川 material_id 不能代替口播工具的视频链接。
- `GET /cmm/jobs/{j}`、`GET /cmm/jobs/{j}/results`；`POST /cmm/jobs/{j}/select`：`{result_ids}`；`GET /cmm/references`。用户选中的 reference_ids 才进入分析范围；有文本/分镜就保留真实来源，不创建占位 asset_id。
- 已选参考继续提取：`POST /cmm/references/{id}/content/preflight`：`{kind:copywriting|storyboard|understanding}`；核验权限与费用后，`POST /cmm/references/{id}/content` 加入 `max_calls,max_points,idempotency_key`。保留父引用和原始授权期限，当前授权续期不自动续期旧来源。
- 异步理解：`POST /cmm/jobs/{j}/poll|recover|cancel`。只按持久化的同一 task_id 轮询；未知提交不能重发。cancel 只停止本地操作，不宣称供应商任务已取消，已请求部分预留仍待核账。

`live_verified=false` 表示真实账号效果和收费仍未验证；保存 Key 或通过权益检查不能改写这一结论。缺失指标不补零、销售区间不改精确值、相关性不写因果。所有新付费调用都需要对应的用户凭证与明确预算。

## 历史生成任务

- 新建 `POST /api/projects/{p}/generation` 已停用，返回410；不再提供新提交参数示例。生成模型新配置与旧生成 `/jobs/{j}/resume` 也返回410，不能重新提交旧任务。
- 未提交、仍 queued 的旧生成任务不再执行，取消后按0实际费用核对；不将取消描述为生成完成。
- 已提交且持有真实 `provider_request_id` 的历史任务，可 `POST /api/projects/{p}/jobs/{j}/poll` 查询同一原任务；仍需原配置/凭证和来源权限。结果未知时不更换编号、不重发提交。
- `POST /jobs/{j}/reconcile` 仅把 needs_attention 与真实供应商编号关联，不创建新供应商任务。`/settle` 需 `{actual_cost,confirmed:true}`，`/archive` 需 `{rights_confirmed:true}`；实际成本未知保持待核账，执行中不能释放预留。

## 调用约束

CLI 的非凭证 JSON 可写入本地临时文件，再调用 `creative-agent api /api/projects/ID/actions --method POST --json-file 路径`；CLI 当前支持 GET/POST/PUT，PATCH 设置在应用中完成。令牌、模型 Key 和 CMM Key 不能写进这些请求文件或项目导出。普通任务状态从 `/state` 读取，`GET /jobs/{j}/events?after=0` 读取增量事件；CMM 使用自己的 job 查询和积分账本。

广告创建、发布和预算变更仍需单独确认。不能把本次分析授权扩展到其他项目、来源、账户或新的计费批次。
