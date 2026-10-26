# 最小权限与审核说明

- `activeTab`：用户点击扩展后，临时访问当前标签页。没有常驻站点 host_permissions。
- `scripting`：在当前页执行公开内容提取器，只返回可见标题/作者/链接/互动原文。
- `storage`：保存非秘密安装标识、连接、目标项目和回执，支持弹窗重开与 MV3 后台休眠恢复。
- `nativeMessaging`：通过本机已安装的 `org.creativeagent.collector` 与桌面应用通信，消息不含视频文件。

不申请 `cookies`、`tabs`、`history`、`webRequest`、`downloads`、`<all_urls>`、平台持久访问或 localhost 网络权限。`chrome.tabs.query({active:true,currentWindow:true})` 只定位用户主动操作的当前标签，不获取其他标签页内容。

所有脚本随包发布，无远程可执行代码；不绕过登录、网站或设备策略。首次桌面确认后才能向明确授权的项目保存。模型、付费数据、广告、发布和预算不属于本扩展能力。
