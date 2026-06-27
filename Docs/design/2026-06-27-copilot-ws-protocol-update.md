# 程序说明

本文档记录 Windows-Copilot-API 项目中 Copilot 聊天协议迁移的最小设计，目标是将当前失效的旧版 WebSocket 链路替换为诊断日志中已验证可用的新链路，并在不改动 OpenAI 兼容接口层的前提下恢复文本聊天能力。

## 背景

当前项目在 `copilot/protocol.py` 中硬编码使用 `wss://copilot.microsoft.com/c/api/chat?api-version=2`。
实测中，浏览器端 Copilot 已能正常对话，但项目驱动层仍返回 `Refused WebSockets upgrade: 403`。

结合 `session/ws_capture.log` 可确认：

- 网页端当前成功聊天的真实链路已迁移到 `wss://substrate.office.com/m365Copilot/Chathub/...`
- 浏览器端能正常发送消息并收到流式回复
- 当前代理与登录态已基本可用，问题集中在项目使用了过时的协议地址与握手格式

## 目标

- 将驱动层切换到新协议链路
- 保持 `server/api.py` 对外 OpenAI 兼容接口不变
- 保持现有 `session/token.json` 和 `session/profile` 认证来源不变
- 先恢复文本聊天能力，不扩展图片、插件、建议词等增强能力

## 非目标

- 不做旧协议与新协议双栈兼容
- 不新增自动登录、自动修复 clearance 或新的浏览器控制逻辑
- 不修改 FastAPI 路由、请求格式或响应格式
- 不处理与文本聊天恢复无关的重构

## 方案选择

### 方案 A：直接切新协议

直接将当前驱动层切到 `substrate.office.com/m365Copilot/Chathub` 真实链路，按最新抓包结果重建连接、握手和流式解析。

优点：

- 改动集中，排查成本最低
- 与当前真实网页行为一致
- 最快恢复可用性

缺点：

- 放弃旧协议兼容
- 后续如微软再次切协议，需要重新抓包更新

### 方案 B：双协议兼容

保留旧链路并新增新链路，运行时自动探测或失败切换。

优点：

- 理论上兼容更多历史环境

缺点：

- 分支逻辑更多，测试面更大
- 当前已知旧链路失效，保留价值有限

结论：采用方案 A。

## 设计

### 1. 协议常量层

修改 `copilot/protocol.py`，不再保存旧版 `copilot.microsoft.com/c/api/chat` 常量，改为抽取新链路中可复用的协议常量，包括但不限于：

- 新 WebSocket 基础地址模板
- 握手帧格式
- 发送消息帧格式中固定字段
- 需要识别的回复事件类型

该文件仍作为协议单一事实来源，避免握手字段散落在驱动中。

### 2. 驱动层

修改 `copilot/driver.py`：

- 基于 `session/token.json` 中保存的 ChatHub `access_token` 生成新链路连接参数
- 复用带域名信息的完整 cookies 与代理配置
- 按抓包结果建立 WebSocket 连接
- 发送新的握手帧与消息帧
- 从新协议的返回帧中提取机器人文本，拼装为现有 `chat()` / `stream()` 可消费的数据流

实现原则：

- 仅覆盖当前已抓到的文本聊天最小路径
- 对未知事件类型忽略而不是报错中断
- 每次请求重写 `request_id`、`session_id`、`ConversationId` 这类动态字段，避免复用抓包中的旧会话值
- 文本链路若再次命中 `401`，直接暴露错误，不再自动回退浏览器

### 3. 接口层

`server/api.py` 不做功能改动。
服务端仍通过 `CopilotClient.chat()` 与 `CopilotClient.stream()` 暴露 OpenAI 兼容能力。

### 4. 错误处理

- 连接失败：保留现有上游异常包装逻辑
- 鉴权失败：仍由现有 token / cookies 加载逻辑抛错；文本 `WS 401` 直接返回明确错误
- 未识别帧：忽略并继续读取，避免单个非关键事件导致整轮对话失败
- 长时间无回复：沿用现有超时控制，避免请求悬挂

## 最终实现补充

在实际落地过程中，纯 HTTP 文本链路的 `401` 最终定位为三个具体问题：

1. `session/token.json` 只保存了 `microsoft.com` 域的简化 Cookie 字典，缺少 `m365.cloud.microsoft` / `copilot.cloud.microsoft` 这类新链路实际依赖的登录态。
2. 浏览器导出的 `access_token` 有时来自本地缓存，而不是真实 ChatHub 握手 URL 中那枚可用于 `substrate.office.com` 的 token。
3. 纯 HTTP 驱动早期复用了抓包中的旧 `clientrequestid`、`X-SessionId` 和 `ConversationId`，导致命中新旧会话串线或返回空结果。

因此最终实现补充了以下收敛方案：

- `browser.py` 导出 `cookie_records`，保留 Cookie 的域名、路径和 secure 信息
- `browser.py` 在 `https://m365.cloud.microsoft/chat/` 页面执行 warm-up，并优先导出真实 ChatHub 握手中的 token
- `auth.py` 把 ChatHub 快照、完整 Cookie 明细和最新 token 一并持久化
- `client.py` 优先透传 `cookie_records`
- `driver.py` 使用 `CookieJar` 复放完整 Cookie，并为每次文本请求重新生成动态会话字段
- 文本链路移除浏览器 fallback，只保留图片链路的浏览器上传路径

## 验证计划

按以下顺序验证：

1. `python -m copilot ask "请只回复：ok"` 可返回文本
2. `POST /v1/chat/completions` 返回 200 且内容正常
3. `stream=true` 时能持续输出文本分片
4. `conversation_id` 续聊可返回同一会话的后续结果

若第 1 步失败，则先看驱动层日志与异常，不进入 API 层排查。

## 已完成验证

本轮实现后已完成以下实测：

- 纯 HTTP 驱动直连文本请求返回 `probe-ok`
- OpenAI 兼容接口文本非流式返回 `api-ok`
- OpenAI 兼容接口文本流式返回 `stream-ok`
- `conversation_id` 续聊返回 `conv-one` / `conv-two`
- 既有自动化测试 `tests.test_multimodal_api` 与 `tests.test_server` 通过

## 风险

- 新链路中的动态参数较多，当前实现可能只覆盖当前账号、当前页面路径下的最小可用子集
- 微软若继续调整握手字段，仍需重新抓包更新
- 图片链路当前仍依赖浏览器上传路径，尚未推进到纯 HTTP
- 由于本次优先恢复文本能力，后续高级能力可能仍不可用

## 回滚方式

若新实现完全不可用，可回退本次对 `copilot/protocol.py` 与 `copilot/driver.py` 的改动，恢复到当前版本。
由于本次不改外部接口层，回滚范围可控制在协议与驱动两个文件。
