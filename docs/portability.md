# 媒体备份与一次性迁移

`creative_agent.portability` 以 ZIP 媒体包备份项目，并通过公开 Store 契约恢复到明确选择的空项目。可用于本地→本地恢复与本地→PostgreSQL 目标；本轮只运行真实本地 Store/媒体往返，PostgreSQL 实例、权限与部署流程需由团队运行时集成验收。

## 操作入口

```python
from creative_agent.portability import export_bundle, import_bundle

# source_store/source_media 是既有本地实例；project_id 必须是当前授权项目。
backup = export_bundle(source_store, source_media, project_id, destination_zip,
                       authorize=validate_export_rights)

# 目标由管理员先建立空项目，目标 Store 自身继续实施身份/RLS/读写/导出权限。
receipt = import_bundle(target_store, target_media, destination_zip,
                        target_project_id=target_project_id,
                        expected_sha256=backup['sha256'])
```

`export_bundle` 无论是否传 authorize，都强制调用 `validate_bundle_export`：内部执行 `Industry.validate_export` 与 `LicensedAccess` 对记录、派生内容、媒体和任务的当前授权检查。authorize 只可增加调用方检查，传空函数也不能跳过既有许可。端点和命令调用方仍负责验证操作者身份、当前项目权限与目标组织。不要把本地 bearer 服务当作团队迁移服务。

包名已存在时禁止覆盖；保留原始备份和校验值。以可信独立通道保存整包 SHA-256，导入时传入 expected_sha256。包内校验能检出损坏；它不是数字签名，也不能证明一个未知包的来源可信。

## 包内结构与校验

- `manifest.json`：格式/版本、bundle UUID、源项目、快照大小与 SHA-256、媒体条目大小/哈希及其记录字段绑定。
- `snapshot.json`：原项目与预算账本说明、各类型记录、完整版本/历史快照、旧任务与事件。系统密钥、secret_ref、凭证字段、签名 URL 查询参数和原始本地路径均不导出。
- `media/{sha256}.{extension}`：真实原片/图片、海报、镜头图与索引等记录引用文件。重复字节仅存一份，所有字段绑定保留。素材原片同时校验记录中的 sha256，原片被外部替换时不能把旧身份沿用到新内容。

读取所有清单、记录引用和媒体校验值通过之后，才修改目标记录或媒体。媒体分块读至临时校验区，避免把整个视频包装入内存。单包解压总量上限10GiB，JSON 上限32MiB；这些是当前实现限制，不是已完成的吞吐基准。超出时需先确定分项目/分包策略，不能悄悄丢媒体。

拒绝：绝对路径、`..`、反斜杠/盘符、归档目录/符号链接/特殊文件、重复或大小写冲突成员、清单外文件、非内容寻址媒体名、重复字段绑定、损坏/缺失文件、源项目外路径、目标媒体目录符号链接，以及规范化来源中的跨项目/丢失引用。不会使用 extractall 解压任意归档路径。

## 身份、历史与计费

公开 `Store.restore_record(project_id,kind,data,record_id)` 只插入不存在的记录；执行既有秘密字段、项目范围和事务检查，任何已有 ID 都不覆盖。PostgresStore 可以继承相同 SQL 兼容实现，仍受自身 RLS 和当前身份约束。

正常记录保留原 UUID、创建/更新时间、版本数组、确认标记和制作任务中的确认版本快照。只将实际 `project_id` 字段改成目标项目；`facts-旧项目` 固定记录 ID 改为 `facts-新项目`，使产品事实仍可正常读取。原项目名、预算和会计信息在历史/迁移回执中保留，迁移不会自动修改目标预算。

所有旧执行任务均保存为 `migration_job` 类型记录：

```text
id: 原任务 UUID
kind: 原任务业务类型
state: archived
original_state: 原状态（包括 submitting/submitted/needs_attention）
archival: true
replay_allowed: false
payload / quote / actual_cost / provider_request_id / result / events: 清理凭证后的原始历史
requires_reconciliation: actual_cost 原本缺失时为 true
```

不会调用 create_job，不生成原生 jobs 或队列行，不把 transient/已计费任务变成新请求。`Store.job` 在没有原生任务时只读回退到此归档；transition/finish/settle/cancel 仍只能操作真实任务行，归档任务不能重放。模型/来源连接标记 has_key=false、requires_rebind=true，模型还会禁用；用户需在目标安全凭证库重新配置，不能从备份恢复密钥。

原来实际费用缺失的任务继续缺失，原预留和已花费记录只作为历史账本。没有把旧报价当作实际费用，也没有把旧账本悄悄复制成目标可执行预算。管理员必须先核对供应商状态、账单和目标组织剩余预算，再确认新任务。

## 一次性、失败与恢复

目标必须为既有空项目（无记录、无原生任务）。项目媒体目录以独占 `.migration.lock` 阻止同一共享存储上的并发迁移。完成后写入 `migration_receipt`，包含整包 SHA、源/目标项目、计数与会计核对标志；重复导入拒绝。

导入前停止源写入与目标应用/worker，保留原库/媒体快照。校验源、记录恢复和文件移动不是跨数据库/文件系统的一个分布式事务。可捕获失败时，只回滚本次插入的记录与本次创建的媒体；不覆盖或删除已有项目。进程被强制终止、权限中途撤销而使回滚也被拒绝时，需管理员先检查锁、残留记录与媒体，再选择明确清理或换一个干净目标；不能自动重跑或自动续发计费任务。

完成后至少核对：回执的包 SHA、媒体原片与海报哈希、脚本全部历史和确认版本、制作任务的固定版本快照、来源引用、产品事实、归档任务/未知费用，以及原生待执行队列为空。保留原库和旧包直到核对完成。源与目标组织许可、服务停机窗口、数据库原生备份/PITR、真实 PostgreSQL 身份/RLS 和跨平台文件系统均属部署验收，离线往返不证明这些关口已通过。


## 可运行命令

所有命令通过新增入口运行，不调用供应商。把下面中文占位内容替换为实际目录和项目 ID；本地项目使用既有 `creative.sqlite3` 与 `media` 目录，先通过原项目管理入口建立空目标项目。

```bash
python -m creative_agent.tools_cli --data-dir "源数据目录" export --project "源项目ID" --output "备份.zip"
python -m creative_agent.tools_cli --data-dir "目标数据目录" import --target-project "空目标项目ID" --archive "备份.zip" --sha256 "导出返回的完整SHA256"
python -m creative_agent.tools_cli --data-dir "数据目录" cost-ledger --project "项目ID" --output "来源费用.csv"
```

团队目标或团队备份由受信服务器操作者执行。显式提供 `--postgres-dsn-file`（私有权限文件或受保护 Docker secret，禁止符号链接）、`--organization`、`--subject` 和媒体 `--data-dir`；不会接受命令行明文 DSN。设置 Actor 后必须通过数据库实际组织管理员检查，以及目标项目 read/export/write 权限。不会使用 owner/BYPASSRLS 凭证绕过运行时安全检查。组织/subject 不是可公开代入的登录接口，仅供已获服务器密钥文件访问权的离线管理员。

```bash
python -m creative_agent.tools_cli --data-dir "团队数据目录" --postgres-dsn-file "受保护runtime-dsn文件" --organization "目标组织ID" --subject "已登记管理员subject" import --target-project "空团队项目ID" --archive "备份.zip" --sha256 "导出SHA256"
```

命令遇到失败返回2，不自动重试，不打印 DSN 或底层异常中的凭证。备份/费用输出文件已存在时拒绝覆盖。真实 PostgreSQL 命令需在目标部署中完成执行验证；本轮命令测试为本地实际媒体往返。
