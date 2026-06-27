# Windows Copilot API

![Windows Copilot API — a free, OpenAI-compatible API for your Microsoft Copilot account](assets/windows-copilot-api-banner.png)

Windows Copilot API 是一个本地运行的 Microsoft/M365 Copilot 代理服务。它复用你电脑上已经登录的 Copilot 会话，把 Copilot 封装成 OpenAI 兼容接口，方便 Python 程序、AI IDE、OpenAI SDK 或其他 OpenAI-compatible 客户端调用。

项目不是微软官方产品，也不属于 Microsoft、GitHub Copilot 或 OpenAI。它自动化的是网页端 Copilot/M365 Chat 体验，请只在个人或授权环境中使用，并遵守相关服务条款。

## 目录

- [项目能做什么](#项目能做什么)
- [运行机制](#运行机制)
- [环境要求](#环境要求)
- [安装与登录](#安装与登录)
- [启动本地 API 服务](#启动本地-api-服务)
- [OpenAI 兼容接口](#openai-兼容接口)
- [Python 库调用](#python-库调用)
- [流式输出](#流式输出)
- [多轮会话](#多轮会话)
- [图片输入](#图片输入)
- [OpenAI tool_calls 兼容层](#openai-tool_calls-兼容层)
- [AI IDE 接入](#ai-ide-接入)
- [命令行用法](#命令行用法)
- [认证与 token 快照](#认证与-token-快照)
- [并发与限流](#并发与限流)
- [Docker 运行](#docker-运行)
- [测试与验证](#测试与验证)
- [项目结构](#项目结构)
- [常见问题](#常见问题)
- [限制说明](#限制说明)
- [许可证](#许可证)

## 项目能做什么

- 把本机 Copilot 会话封装成 `http://127.0.0.1:8000/v1`。
- 提供 OpenAI 兼容的 `/v1/models` 和 `/v1/chat/completions`。
- 支持普通文本聊天、流式输出、多轮会话和图片输入。
- 支持 OpenAI SDK、curl、AI IDE 等 OpenAI-compatible 客户端。
- 支持 `tools`、`tool_choice` 和 `message.tool_calls` 的最小兼容实现。
- 支持 `stream=true + tools`，可返回 SSE 格式的 `delta.tool_calls`。
- 支持本地 Python 库直接调用，不一定要启动 HTTP 服务。

## 运行机制

项目使用你本机浏览器登录后的 Microsoft/M365 Copilot 会话，保存必要的登录快照到 `session/` 目录。服务启动后，客户端请求会进入本地 FastAPI 服务，再由项目内部的 Copilot 客户端转发到 M365 ChatHub。

文本请求优先走纯 HTTP/ChatHub 链路。图片输入仍需要浏览器上传路径。登录、Cloudflare clearance、ChatHub token 和 Cookie 会被保存到 `session/token.json`，用于后续请求复用。

## 环境要求

- Python 3.9 或更高版本。
- 能正常访问 Microsoft/M365 Copilot 的账号。
- Windows、macOS、Linux 均可运行；本仓库当前主要在 Windows PowerShell 下验证。
- 首次登录需要可见浏览器。

## 安装与登录

克隆项目后进入项目目录：

```powershell
git clone "<your-repo-url>"
cd "Windows-Copilot-API"
```

建议创建虚拟环境：

```powershell
python -m venv "venv"
".\venv\Scripts\Activate.ps1"
```

如果 PowerShell 阻止激活脚本，可先允许当前用户执行本地脚本：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

安装依赖：

```powershell
python -m pip install -r "requirements.txt"
python -m playwright install chromium
```

首次登录：

```powershell
python -m copilot login
```

命令会打开浏览器。你需要完成 Microsoft/M365 Copilot 登录，以及可能出现的人机验证。登录完成后，程序会保存会话到 `session/`，后续启动服务时会自动复用。

## 启动本地 API 服务

默认启动：

```powershell
python "app.py"
```

默认监听：

```text
http://127.0.0.1:8000
```

OpenAI 兼容 base URL：

```text
http://127.0.0.1:8000/v1
```

如果要改端口或监听地址：

```powershell
$env:HOST="0.0.0.0"
$env:PORT="8080"
python "app.py"
```

也可以直接用 uvicorn：

```powershell
python -m uvicorn server.api:app --host 0.0.0.0 --port 8080
```

## OpenAI 兼容接口

当前提供两个主要接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/v1/models` | 返回模型列表，目前只有 `copilot` |
| `POST` | `/v1/chat/completions` | OpenAI 兼容聊天接口 |

查看模型：

```powershell
curl.exe "http://127.0.0.1:8000/v1/models"
```

普通聊天：

```powershell
curl.exe "http://127.0.0.1:8000/v1/chat/completions" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer anything" `
  -d "{"model":"copilot","messages":[{"role":"user","content":"你好，简单介绍一下你自己"}]}"
```

OpenAI Python SDK：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="anything",  # 本地服务会忽略该值，但 SDK 要求填写
)

resp = client.chat.completions.create(
    model="copilot",
    messages=[
        {"role": "user", "content": "你好，简单介绍一下你自己"}
    ],
)

print(resp.choices[0].message.content)
```

## Python 库调用

如果不想启动 HTTP 服务，可以直接调用 Python 库：

```python
from copilot import CopilotClient

client = CopilotClient()

reply = client.chat("用一句话介绍你自己")
print(reply.text)
```

继续同一个会话：

```python
reply2 = client.chat(
    "继续刚才的话题",
    conversation_id=reply.conversation_id,
)
print(reply2.text)
```

直接流式调用：

```python
from copilot import CopilotClient

client = CopilotClient()

for chunk in client.stream("写一首五言绝句"):
    print(chunk, end="", flush=True)
```

## 流式输出

HTTP 流式调用：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="anything",
)

stream = client.chat.completions.create(
    model="copilot",
    messages=[
        {"role": "user", "content": "写一首五言绝句"}
    ],
    stream=True,
)

for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)
```

curl 流式调用：

```powershell
curl.exe "http://127.0.0.1:8000/v1/chat/completions" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer anything" `
  -d "{"model":"copilot","stream":true,"messages":[{"role":"user","content":"写一首五言绝句"}]}"
```

## 多轮会话

每次新请求如果不传 `conversation_id`，会尽量开启新的 Copilot 会话。响应里会返回 `conversation_id`，客户端可以保存它并在下一轮传回。

示例：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="anything",
)

first = client.chat.completions.create(
    model="copilot",
    messages=[
        {"role": "user", "content": "帮我起一个项目名"}
    ],
)

conversation_id = first.model_extra.get("conversation_id")

second = client.chat.completions.create(
    model="copilot",
    messages=[
        {"role": "user", "content": "继续，给我 5 个更短的名字"}
    ],
    extra_body={
        "conversation_id": conversation_id
    },
)

print(second.choices[0].message.content)
```

## 图片输入

接口支持 OpenAI 风格的 `content` parts，可以传入本地图片路径：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="anything",
)

resp = client.chat.completions.create(
    model="copilot",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "描述这张图片"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "file:///C:/Users/your-name/Pictures/demo.png"
                    },
                },
            ],
        }
    ],
)

print(resp.choices[0].message.content)
```

当前图片能力限制：

- 只支持本地图片文件。
- 不支持远程图片 URL。
- 不支持 `data:` URL。
- 图片请求目前仍依赖浏览器上传路径。
- 图片请求暂不支持用 `conversation_id` 续聊。

## OpenAI tool_calls 兼容层

项目新增了最小 OpenAI `tool_calls` 兼容层，用于让 AI IDE 识别工具调用意图。

支持字段：

- `tools`
- `tool_choice`
- 非流式 `message.tool_calls`
- 流式 `delta.tool_calls`
- `finish_reason=tool_calls`

重要说明：

- 服务端不会执行工具。
- 工具由 AI IDE 或客户端执行。
- Copilot 不原生返回 OpenAI 标准 `tool_calls`，本项目会把工具定义转成提示词，让 Copilot 输出工具调用 JSON，再转换成 OpenAI 标准格式。
- 这是兼容层，不是 Microsoft Declarative Agent 或 Copilot 原生 Plugin/MCP 体系。

### 非流式 tool_calls 示例

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="anything",
)

resp = client.chat.completions.create(
    model="copilot",
    messages=[
        {"role": "user", "content": "探查一下本地环境，调用工具，使用 powershell"}
    ],
    tools=[
        {
            "type": "function",
            "function": {
                "name": "run_powershell",
                "description": "在本机 PowerShell 中执行命令",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "PowerShell 命令"
                        }
                    },
                    "required": ["command"]
                }
            }
        }
    ],
)

msg = resp.choices[0].message
print(msg.tool_calls)
```

可能返回：

```json
[
  {
    "id": "call_1",
    "type": "function",
    "function": {
      "name": "run_powershell",
      "arguments": "{"command":"Get-ComputerInfo"}"
    }
  }
]
```

### 流式 tool_calls 示例

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="anything",
)

stream = client.chat.completions.create(
    model="copilot",
    stream=True,
    messages=[
        {"role": "user", "content": "探查一下本地环境，调用工具，使用 powershell"}
    ],
    tools=[
        {
            "type": "function",
            "function": {
                "name": "run_powershell",
                "description": "在本机 PowerShell 中执行命令",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"}
                    },
                    "required": ["command"]
                }
            }
        }
    ],
)

for chunk in stream:
    delta = chunk.choices[0].delta
    if getattr(delta, "tool_calls", None):
        print(delta.tool_calls)
```

流式 SSE 会包含：

```text
delta.tool_calls
finish_reason: tool_calls
data: [DONE]
```

### tool_choice

可以强制指定工具：

```json
{
  "tool_choice": {
    "type": "function",
    "function": {
      "name": "run_powershell"
    }
  }
}
```

如果不想让本轮触发工具：

```json
{
  "tool_choice": "none"
}
```

## AI IDE 接入

在 AI IDE 中选择 OpenAI-compatible provider：

```text
Base URL: http://127.0.0.1:8000/v1
Model: copilot
API Key: anything
```

如果 IDE 本身在本机运行，请优先使用 `127.0.0.1`。如果 IDE 的 Agent/执行器在沙箱、WSL、Docker 或远端容器里，`127.0.0.1` 指向的是那个环境自身，不一定是你的 Windows 本机。

AI IDE 工具调用需要 IDE 自己具备工具执行能力。本项目只负责返回 OpenAI 标准 `tool_calls`，不会替 IDE 读文件、写文件或执行命令。

建议在 IDE 中确认：

- 当前模型使用的是 `copilot`。
- Provider base URL 是 `http://127.0.0.1:8000/v1`。
- Agent Mode 已启用。
- Workspace/File Access 已允许访问项目目录。
- Terminal/PowerShell 工具权限已开启。
- 如果刚更新服务，重新开启新 Chat 或 reload IDE 窗口，避免旧连接缓存。

## 命令行用法

登录：

```powershell
python -m copilot login
```

快速提问：

```powershell
python -m copilot ask "你好"
```

启动服务：

```powershell
python "app.py"
```

## 认证与 token 快照

登录后，项目会在 `session/` 下保存本机会话：

| 文件或目录 | 说明 |
| --- | --- |
| `session/profile/` | 浏览器 profile |
| `session/token.json` | token、Cookie、ChatHub 快照 |
| `session/login.log` | 登录过程日志 |
| `session/ws_capture.log` | WebSocket 捕获日志 |
| `session/diagnostic_report.txt` | 诊断报告 |

如果出现 ChatHub `401`、token 过期、Cookie 失效，重新登录：

```powershell
python -m copilot login
```

如果登录或首个请求失败，运行诊断：

```powershell
python "tests/diagnostic.py"
```

诊断会刷新会话并写入报告。报告会脱敏 token、Cookie、邮箱等敏感信息。

## 并发与限流

项目桥接的是单个 Copilot 账号。上游 ChatHub 不适合并发多会话，所以服务端用锁串行化请求。并发 HTTP 请求会排队，不会真正并行发送到上游。

默认限流参数：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RATE_LIMIT_RPM` | `12` | 每分钟最多接受请求数，`0` 表示关闭 |
| `RATE_LIMIT_BURST` | `4` | 允许短时间突发请求数 |

修改限流：

```powershell
$env:RATE_LIMIT_RPM="20"
$env:RATE_LIMIT_BURST="5"
python "app.py"
```

压力测试：

```powershell
python "tests/stress.py"
python "tests/stress.py" --max 64 --timeout 120 --url "http://127.0.0.1:8000"
```

## Docker 运行

如果使用 Docker，建议先在宿主机完成登录：

```powershell
python -m copilot login
```

然后启动容器：

```powershell
docker compose up --build
```

Docker 会复用挂载的 `session/`。但容器内通常不能完成可见浏览器验证，clearance 过期后可能需要回到宿主机重新运行登录。

## 测试与验证

当前核心测试：

```powershell
python -m unittest tests.test_tool_calls
python -m unittest tests.test_multimodal_api
python -m unittest tests.test_server
```

如果 `TestClient` 相关测试缺少依赖，可安装：

```powershell
python -m pip install httpx2
```

本地服务健康检查：

```powershell
curl.exe "http://127.0.0.1:8000/v1/models"
```

普通聊天验证：

```powershell
curl.exe "http://127.0.0.1:8000/v1/chat/completions" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer anything" `
  -d "{"model":"copilot","messages":[{"role":"user","content":"只回复 OK"}]}"
```

## 项目结构

| 路径 | 说明 |
| --- | --- |
| `app.py` | 服务启动入口 |
| `copilot/` | Copilot 登录、鉴权、浏览器和 HTTP/ChatHub 驱动 |
| `server/` | FastAPI OpenAI 兼容服务 |
| `server/schemas.py` | 请求模型，包含 `tools/tool_choice` |
| `server/prompt.py` | 消息解析、图片解析、工具提示和工具 JSON 解析 |
| `server/openai_format.py` | OpenAI 格式响应构造 |
| `server/api.py` | HTTP 路由、流式输出、tool_calls 分支 |
| `examples/` | 示例脚本 |
| `tests/` | 单元测试、诊断和压力测试 |
| `session/` | 本地登录态，已 git-ignore |
| `Docs/` | 项目文档 |

## 常见问题

### `/v1/models` 能访问，但聊天失败

先确认 `session/token.json` 是否过期。最简单的处理方式是重新登录：

```powershell
python -m copilot login
```

### 出现 ChatHub 401

通常是 token、Cookie 或 ChatHub 快照过期。重新登录后重启服务：

```powershell
python -m copilot login
python "app.py"
```

### 出现 Cloudflare clearance 问题

运行：

```powershell
python -m copilot login
```

如果浏览器出现验证，请手动完成。

### AI IDE 说访问不到本地路径

这通常不是模型 API 问题，而是 IDE 的 Agent/执行环境没有访问你的 Windows 文件系统。请确认 IDE 的 Terminal/Agent 不是运行在 WSL、Docker、远端 sandbox 或临时目录里。

可以在 IDE 终端测试：

```powershell
pwd
Test-Path "c:\Users\patde\Documents\GitHub\Windows-Copilot-API"
dir "c:\Users\patde\Documents\GitHub\Windows-Copilot-API"
```

### AI IDE 不触发工具

确认 IDE 支持 OpenAI `tool_calls`，并且 Agent Mode、Workspace Access、Terminal Access 已开启。本项目只返回工具调用意图，不执行工具。

### `stream=true + tools` 报错

当前版本已支持最小流式 `tool_calls`。如果仍然报旧错误，请重启服务并在 IDE 中新开 Chat 或 reload 窗口。

## 限制说明

- 当前只暴露一个模型：`copilot`。
- 服务端不执行工具，只返回 `tool_calls`。
- `tool_calls` 是兼容层，不是 Copilot 原生 OpenAI wire format。
- 工具调用 JSON 依赖 Copilot 按提示输出，极端情况下可能退化为普通文本。
- 图片输入仍走浏览器路径。
- 单账号上游请求会串行化，不适合高并发网关。
- `session/` 含登录态，不要提交到 git，也不要分享给他人。

## 许可证

项目使用 [MIT License](LICENSE)。本项目为非官方实现，使用者需要自行承担账号、数据和服务条款相关责任。
