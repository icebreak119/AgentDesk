# AgentDesk 独立化与 Web 登录方案

## 1. 独立化目标

AgentDesk 的比赛仓目标是从历史开发工作区中抽出一个可以单独运行、单独打包、单独提交的 Agent Infra 项目。

保留：
- `orchestrator/`
- `skills/`
- `docs/contracts/`
- `runtime/douyin/`

不保留：
- 第三方桌面壳
- 其他业务线 UI
- 企微完整实现

## 2. 抖音账号登录能否移植到 Web 端

可以，但要分清“Web 控制台”和“执行节点”。

### 可行方式

- Web 端提供登录发起、任务状态、结果查看
- Runtime 节点负责真正打开浏览器、扫码、采集 Cookie / 账号态
- 登录结果写回 SQLite / profile 目录

### 不建议的方式

- 纯前端直接托管抖音登录

原因：
- 浏览器安全沙箱不适合长期保存高敏凭证
- 登录采集需要可控的本地浏览器会话
- 评委更关注可验证的 Runtime，而不是把认证逻辑硬塞进页面

## 3. 现在仓内的实现形态

- `runtime/douyin/channels/douyin_reverse_ipc/http_api.py`
- `runtime/douyin/channels/douyin_reverse_ipc/login_service.py`

Web 端可以通过 HTTP 调用发起登录采集任务，Runtime 节点后台执行 Playwright。

## 4. 推荐架构

1. Web Console 点击“托管账号登录”
2. 后端创建 login job
3. Runtime 节点启动浏览器并完成采集
4. 采集结果写入 `_douyin_im_accounts.db`
5. Web Console 轮询 job 状态并刷新账号状态

## 5. 赛题表达

答辩时建议说：

> 抖音托管登录已抽象成 Web 可触发的 Runtime 任务，但浏览器会话仍由执行节点承担，以保证安全性、可观测性和可回放性。
