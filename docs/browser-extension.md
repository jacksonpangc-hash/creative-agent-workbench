# 浏览器收藏工具

## 日常使用

Chrome / Edge 商店版目前 **待上架**。没有真实商店链接前，桌面设置不会伪造安装入口。已安装内部版本的用户可继续内部体验；这不等于公开商店交付已经通过。

1. 打开创意工作台，选择要保存参考的项目。
2. 在收藏扩展中点“连接创意工作台”。首次连接后，到桌面“设置 → 参考来源”点“允许保存到当前项目”。
3. 回到扩展点“刷新连接”，选择保存到的项目。
4. 打开抖音或快手公开网页，点击扩展的“读取当前页面”，勾选需要的内容，再点“保存选中的参考”。
5. 回执说明新增、已有、失败和待补视频数量；桌面参考来源设置也显示当前项目最近三次回执。关闭弹窗、重新打开或浏览器后台休眠后，连接与项目选择仍保留。

收藏的是页面可见公开信息及链接。作者主页单独标注，不计入待补视频；不会把评论用户或“我的”导航当作当前视频的作者。互动指标不代表销量，链接收藏不代表已取得视频。需要画面分析时，在桌面为对应参考补充合法视频。撤销连接不会删除已保存资料。

## 连接异常

- 桌面未启动：在扩展“连接遇到问题”中点“打开桌面应用”，启动后刷新连接。
- 首次授权待确认：桌面“设置 → 参考来源”明确允许目标项目。
- 授权到期、已撤销：扩展重新连接，桌面再次确认。默认授权上限 180 天，不因关闭弹窗失效。
- 项目已删除或未获准：选择仍存在且已授权的项目，或重新连接并在桌面确认。
- 版本不兼容：更新桌面和扩展；协议版本当前为 1。
- 公司设备策略限制：联系管理员；不绕过策略，仍可在桌面粘贴链接、导入表格或文件。
- 保存超时：先查询上次回执，或保留当前选择重试。相同请求不会重复新增记录或观察；不同内容不能复用请求编号。

“开发与排障：单次导入”保留旧版本配对兼容，2 分钟配对码、一次导入权限，不属于正常连接流程。

## 本机桥与安全边界

扩展仅申请 `activeTab`、`scripting`、`storage`、`nativeMessaging`。没有 Cookie、全标签页或常驻平台域名访问权限。后台为 Manifest V3 service worker；本地 storage 只保存安装实例、连接标识、项目选择、请求号和公开回执。系统密钥库保存按工作区隔离的桥接密钥，扩展拿不到桌面 root bearer、模型或供应商密钥。

Rust host 使用 stdin/stdout 的 4 字节小端长度前缀。请求和响应上限 256 KiB，单批 1–50 条、URL 上限 4096 字符；不转发视频字节。白名单仅为 `get_status`、`list_allowed_projects`、`save_references`、`get_import_receipt`、`open_workbench`、`revoke_connection`。每次保存明确带项目，服务检查本机用户、扩展精确来源、安装实例、协议、到期和撤销状态，再复用 `Discovery.collect` 和 `assets.import` 商业许可。

连接和回执存于现有业务 SQLite 的独立本机表；不作为项目资料导出。收藏写入与幂等回执使用同一 SQLite 事务。Native host 从浏览器命令行获得来源，不能用消息内字段冒充来源；发送到本机 API 使用独立密钥，不开放 CORS。

## 发行集成与用户级维护

Cargo 新增 `creative-browser-host` 二进制，Tauri 默认主程序仍为 `creative-agent-desktop`。打包流水线需构建并作为 externalBin 一同签名/打包。冻结 worker 必须包含 `src/creative_agent/browser-release-config.json`。Tauri 启动时调用 worker `browser-host-register --refresh-existing`，以实际安装位置刷新已有注册；未配置正式商店 ID 时不会自动启用开发 ID。更新或移动 `.app` 后，下次启动更新路径。

macOS manifest：`~/Library/Application Support/Google/Chrome/NativeMessagingHosts/org.creativeagent.collector.json` 和 `~/Library/Application Support/Microsoft Edge/NativeMessagingHosts/org.creativeagent.collector.json`。

Windows manifest：用户应用数据目录下 `native-messaging/org.creativeagent.collector.json`；注册表仅 `HKCU\Software\Google\Chrome\NativeMessagingHosts\org.creativeagent.collector` 及 Microsoft Edge 同级路径。不写 HKLM，不要求管理员权限。

卸载器调用 `creative-worker browser-host-unregister`。命令只删除仍指向当前安装的 manifest / HKCU 项，不删除其他安装已接管的记录。macOS 拖拽到废纸篓没有平台卸载回调：应先在维护流程运行本命令；仅删除 `.app` 会残留非秘密 manifest，下次安装启动覆盖修复，不能声称拖拽卸载已自动清理。

## 内部开发包（仅开发/内部体验）

开发固定 ID：`mecbogehfhfihdmmdnghcfidebpfaoef`，由公钥固定，与未来 Chrome / Edge 正式 ID 分离。开发私钥只在被 Git 忽略的 `.state/creative-loop-054/browser-dev-key.pem`，不进 ZIP、不进安装包、不提交。

从当前源码生成三份独立包：

```sh
python3 browser-extension/package.py --kind all --output .state/creative-loop-054/extensions
```

仅 development 包带公开 `key`，可在隔离浏览器会话加载生成的解压目录；Chrome / Edge 商店提交 ZIP 不带开发 key。

内部明确注册（勿覆盖其他任务或用户真实 host；验收使用隔离 HOME/数据目录）：

```text
creative-worker --data-dir ABS_DATA browser-host-register --host ABS_NATIVE_HOST --worker ABS_WORKER --development --home ABS_ISOLATED_HOME
```

host 必须以相同 `CREATIVE_DATA_DIR=ABS_DATA` 环境运行。常规安装使用系统默认数据目录，无需配置环境变量。源码入口可用 `python -m creative_agent.cli` 代替 worker 管理命令，但 Rust host 的 `worker_path` 必须指向真正可执行、支持 CLI 协议的 worker，不能将 Python 解释器冒充成 worker。

API 集成：

```python
register_browser_bridge(app, store, vault, root, port=port, license_runtime=commercial)
```

开发 API 若直接构建测试服务，可明确传 `allowed_origins=['chrome-extension://mecbogehfhfihdmmdnghcfidebpfaoef/']`；生产从正式发行配置和本机已登记白名单读取。

## 外部发行条件

尚需真实 Chrome/Edge 开发者账号、两店正式 ID 和 URL、商店审核、公开隐私政策承载地址、正式应用签名/公证及 Windows 安装/卸载实机确认。先完成各店 ID 后同时更新 `browser-extension/release-config.json` 与 worker 中的发行配置。正式 IDs 不得复用开发 ID，也不能假定 Chrome 与 Edge 相同。

`browser-extension/store-materials/` 提供隐私政策正文、最小权限解释与商店文案。ZIP 可提交不代表审核通过；源码/契约检查、内部浏览器验收和正式商店发行分开记录。
