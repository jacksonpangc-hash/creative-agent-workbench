# 商业授权实现与运行边界

本实现是本地桌面软件的授权模块及独立授权服务代码。当前没有部署生产服务，没有签发客户授权；本地隔离PostgreSQL的28项授权测试已通过，真实域名/TLS、跨设备和连续24小时离线联测仍未通过，见 `followup-050/license-policy-diff.json`。仓库现有 AGPL 声明及第三方依赖仍须权利人审查；新增授权机制不能自动把既有代码变为可闭源分发作品。

## 已实现的规则

- 管理员开通 3 / 30 / 180 / 365 天套餐。首次成功登录且绑定设备后起算，开通时不提前消耗时长。
- 同一授权绑定一个系统密钥库中的随机设备编号。正常换机可在旧设备主动释放，再在新设备登录，沿用原到期时间。旧设备不可用时，换机由管理员显式指定新设备编号；旧刷新凭证立即撤销，新设备使用原授权编号及密码登录。
- Ed25519 签名的租约最多 24 小时，且严格不超过真实授权到期时间。客户端只有公钥；签名私钥只放授权服务器。
- 联网启动立即刷新，此后每 60 秒刷新。临时网络错误保留未过期租约；收到授权拒绝后，在系统密钥库中持久记录撤销状态，禁止新任务。已经发出的第三方请求不能追回或保证中途取消。
- 续期从 `max(当前到期时间, 当前时间)` 延长。未首次激活不提供续期，停用后续期不自动解禁。
- 管理员迁移与停用在服务端立即生效。已经离线的旧设备仍可能用其未过期租约运行至 24 小时窗口结束；这是离线能力的明确成本。
- 到期保留本地阅读与已有资料的保全导出。这不绕过已有供应商数据留存/导出限制。
- 租约文件不含密码或刷新令牌。刷新令牌、设备编号、已观察时间与撤销状态存现有 SecretStore 系统密钥库，拒绝明文降级。登录密码使用后不保存；服务端仅存 scrypt 盐化哈希。
- 时间回退检查结合进程单调时间与密钥库内最高已观察时间。回退时要求校准时间。它不能抵抗拥有本机管理员权限者修改程序、恢复整个系统快照、破坏密钥库或重放系统时间；离线客户端不存在绝对防破解保证。

## 桌面运行接口

`runtime_from_config(data_dir, vault, config_path=None, mode=None)` 创建运行时。源码开发必须显式传 `mode='internal'`；它显示“内部开发模式，未验证商业授权”。商业包中的 `creative_agent/commercial-license.json` 优先于一切调用参数，不能被 internal 模式覆盖。配置内容如下，公钥使用 32 字节 Ed25519 公钥的 URL-safe Base64（可省略末尾 `=`）：

```json
{"required":true,"service_url":"https://licenses.your-domain.example","public_key":"<operator-owned-public-key>"}
```

构建商业包必须把配置一并放入 Python 包目录，并在构建时检查存在。商业包还携带 `commercial-release.json` 标记；只要模块旁存在该标记，缺少同目录授权配置就拒绝启动，不能用 internal 或外部 config_path 降级。无配置的内部预览包不能称为商业发行。配置公钥和 URL 不属于用户可编辑的应用设置。

`register_license(app, runtime)` 注册下列本机路由，复用本机会话 Bearer。服务器端定时刷新由 `runtime.start()` 启动，退出调用 `stop()`。UI 不承担授权强制责任。

| 路由 | 请求 | 返回 |
|---|---|---|
| GET `/api/license/status` | 无 | mode、can_work、reason、device_id；有效时 expires_at、offline_until |
| POST `/api/license/login` | license_id, password | 授权状态，不返回刷新令牌 |
| POST `/api/license/refresh` | `{}` | 授权状态 |
| POST `/api/license/release` | `{}` | 释放本机授权并停止本机新工作，返回授权状态 |

所有创建分析、写入、外部付费/模型请求及后台恢复/调度入口必须调用 `runtime.require_work(feature)`；仅阅读和保全导出可以 `allow_action('read' / 'conservation_export')`。已有业务来源授权仍独立执行。HTTP 校验之后、异步任务实际开始前需要再次校验，以防排队期间过期或停用。

授权功能列表写入签名租约，由 `require_work(feature)` 校验；不信任 UI 所传权限。当前允许 workspace.use、product.manage、assets.import、assets.analyze、discovery.public、discovery.paid、video.breakdown、creative.generate、script.generate、script.export、providers.configure。管理员省略 features 时开通全部已知功能；显式子集不得含未知项。保全已有本地内容的导出独立于这些新工作权限。

## 独立服务接口

生产入口：`uvicorn creative_agent.license_service:application --factory`。只接收客户名称、授权密码、设备编号、时长和授权管理数据，不接收项目素材、分析内容或模型密钥。未知字段被拒绝。

管理员先 POST `/admin/login`，请求 `{"password":"..."}`，获取有效 8 小时的随机 Bearer token；服务器只持久存令牌哈希。退出 POST `/admin/logout` 撤销会话。无预置账号、密码或私钥。管理员密码应为运营者独立生成的高强度密码，最低 12 字符；登录有数据库持久节流及反向代理按 IP 限流。管理员打开授权服务的 `/admin` 网页即可管理账号：开通客户、选择套餐及功能、查看授权、续期、停用、恢复使用、迁移或释放设备。网页只在内存保存管理员令牌，不使用 localStorage/sessionStorage；刷新页面需重新登录，退出会撤销当前会话。页面对外可加载，但账号数据和所有管理操作仍要求管理员 Bearer。界面使用独立脚本与严格同源 CSP，客户名称和错误信息按文本渲染。

| 管理员路由（均 Bearer） | 请求 |
|---|---|
| GET `/admin/licenses` | 列出客户授权，包含首次激活、设备、到期与停用状态；不返回密码哈希 |
| POST `/admin/licenses` | customer, password, days，可选 features 功能子集；返回授权 id |
| POST `/admin/licenses/{id}/renew` | days |
| POST `/admin/licenses/{id}/disable` | `{}` |
| POST `/admin/licenses/{id}/enable` | `{}`；恢复使用、不延长到期时间、撤销旧刷新令牌；客户需重新登录 |
| POST `/admin/licenses/{id}/release` | `{}`；管理员释放设备，保留原到期时间 |
| POST `/admin/licenses/{id}/transfer` | device_id（从新设备设置页复制） |
| GET `/admin/licenses/{id}/audit` | 开通、激活、登录、续期、停用与换机审计 |

客户端 HTTPS API：

- POST `/v1/login`：`license_id, password, device_id`；返回 `lease, refresh_token, expires_at, refresh_after_seconds`。
- POST `/v1/refresh`：`license_id, refresh_token, device_id`；返回 `lease, expires_at, refresh_after_seconds`。刷新令牌无须每分钟轮换；换机、停用或重新登录会使旧令牌失效。
- POST `/v1/release`：与 refresh 相同字段；校验当前设备与令牌，清除绑定、撤销令牌、增加授权版本并记录审计；返回 `released: true`。客户端立即记录本机已释放并清除刷新令牌。
- 返回 403 表示拒绝当前授权，客户端必须持久停止新工作。临时 5xx/连接失败可使用剩余离线租约；仍不能超过离线与真实到期边界。
- 时间均为 UTC Unix 秒，所有响应 `Cache-Control: no-store`。客户端不接受 HTTP、URL 内嵌凭证或重定向；使用标准 TLS 证书验证。

## 部署材料与秘密

2026-10-25 升级：生产入口另要求 `LICENSE_ADMIN_TOTP_FILE`，文件由运营者在安全环境生成并与其验证器配对，包含至少 160 位随机数据的 Base32 密钥；Compose 读取本地 `secrets/admin-totp.base32`。不随源码、镜像、备份说明包或桌面应用分发。开发工厂可显式不启用二次验证，生产 `application()` 不允许缺省。

管理员登录请求增加 `otp` 六位验证码。使用 RFC 6238、30 秒周期、允许前后一周期时钟差；数据库原子记录已使用周期，服务重启后也不能重放同一验证码。密码或验证码错误均不创建管理会话。登录表单提交即清空密码和验证码，不进入浏览器持久存储。生产数据库、管理员密码和验证器密钥应分别保管。

管理员验证器遗失时，由有服务器运维权限的负责人核实身份，在维护窗口更换挂载的二次验证密钥、与新验证器配对并重启授权服务；同时撤销现有管理员会话，记录人工操作。没有公开的绕过验证或自动关闭 MFA 接口。不得把测试密钥用作实际运营配置。

管理网页静态资源位于 `deploy/license/console.html/.js/.css`。Docker 自动带入这些文件并设置 `LICENSE_CONSOLE_DIR=/app/console`；源码直接运行默认读取仓库 `deploy/license/`。单独以 wheel 安装服务时，需要同时提供该目录并设置 `LICENSE_CONSOLE_DIR`。

`deploy/license/compose.yaml` 提供 PostgreSQL 16、独立 Python 服务与 nginx HTTPS 代理。生产入口拒绝 SQLite；测试可用 SQLite 临时文件。已在本机隔离PostgreSQL补跑，容器和公网HTTPS部署仍未验收。SQLite、PostgreSQL和部署证据分开记录。

管理员在部署主机自行准备 `deploy/license/secrets/`（已忽略，不能提交）：

- `postgres-password`：随机数据库口令。
- `database-url`：`postgresql://licensing:<URL-encoded-password>@postgres:5432/licensing`。可用 `LICENSE_DATABASE_URL_FILE` 挂载；服务也支持显式 `LICENSE_DATABASE_URL` 环境变量。
- `signing-key.pem`：运营者生成的 Ed25519 PKCS8 PEM 私钥；只读挂载 `LICENSE_SIGNING_KEY_FILE`。不要放入桌面包。
- `admin-password.hash`：用 `creative_agent.license_service.hash_password()` 和交互式 getpass 生成，挂载 `LICENSE_ADMIN_HASH_FILE`。
- `fullchain.pem` 与 `tls-private-key.pem`：域名 TLS 证书与私钥。域名必须与商业包内 HTTPS URL 相符。

秘密目录建议权限 0700；文件应只允许运营者与服务 UID 10001 读取，密钥和数据库备份需要加密、独立权限及恢复演练。compose 使用文件型 secrets，其宿主文件权限仍由运营者负责。禁止把密码直接放到 shell 参数或可共享命令历史里。服务只通过 nginx 发布 443 端口，数据库和内部 HTTP 不开放宿主端口。此模板没有自动申请证书、备份或密钥轮换流程；上线前应完成运营配置。

## 仍需实证的交付关卡

1. 既有代码和依赖的闭源商业发行权利审查。
2. 运营者实际 PostgreSQL、TLS 域名、秘密挂载和备份恢复；PostgreSQL 并发首次激活/换机事务。
3. 正式桌面包携带正确公钥与强制授权配置，重启后仍然生效。
4. macOS/Windows 系统密钥库、首次激活、60 秒联网停用、跨设备迁移、真实离线 24 小时与时间校准验证。
5. 全部任务入口和后台恢复的授权强制，以及到期后的本地阅读/保全导出回归。

本地自动化仅证明所覆盖的代码行为，不代表上述商业部署和设备关卡已通过。

## 资料库与商业账号

首次有效签名激活后，本机资料库绑定该授权账号。更换账号不能直接接管原资料库；使用原账号，或为新账号选择独立资料目录。账号到期、停用或释放设备均不删除绑定，不影响原资料的查看和保全导出。

归属、刷新凭据和时间状态按规范化资料目录隔离，设备标识仍按应用签名公钥保存。首次激活与刷新使用工作空间锁；本轮验证了同机多个运行时的并发激活，独立进程及 Windows 锁实现仍需目标机验证。它不承诺抵抗拥有文件系统和密钥库完全控制权的系统管理员。
