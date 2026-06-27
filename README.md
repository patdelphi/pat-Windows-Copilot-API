# Windows Copilot API

![Windows Copilot API — a free, OpenAI-compatible API for your Microsoft Copilot account](assets/windows-copilot-api-banner.png)

Windows Copilot API is a local Microsoft/M365 Copilot bridge. It reuses the Copilot session already signed in on your computer and exposes it as an OpenAI-compatible API for Python scripts, AI IDEs, the OpenAI SDK, and other OpenAI-compatible clients.

This is not an official Microsoft, GitHub Copilot, or OpenAI project. It automates the web-based Copilot/M365 Chat experience, so use it only in personal or authorized environments and follow the relevant service terms.

[中文文档](readme-zh.md)

## Contents

- [What this project does](#what-this-project-does)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation and sign-in](#installation-and-sign-in)
- [Start the local API server](#start-the-local-api-server)
- [OpenAI-compatible API](#openai-compatible-api)
- [Python library usage](#python-library-usage)
- [Streaming output](#streaming-output)
- [Multi-turn conversations](#multi-turn-conversations)
- [Image input](#image-input)
- [OpenAI tool_calls compatibility](#openai-tool_calls-compatibility)
- [AI IDE integration](#ai-ide-integration)
- [Command line usage](#command-line-usage)
- [Auth and token snapshots](#auth-and-token-snapshots)
- [Concurrency and rate limiting](#concurrency-and-rate-limiting)
- [Docker usage](#docker-usage)
- [Tests and validation](#tests-and-validation)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Note to the original author](#note-to-the-original-author)
- [License](#license)

## What this project does

- Exposes your local Copilot session as `http://127.0.0.1:8000/v1`.
- Provides OpenAI-compatible `/v1/models` and `/v1/chat/completions` endpoints.
- Supports text chat, streaming output, multi-turn conversations, and image input.
- Works with the OpenAI SDK, curl, AI IDEs, and other OpenAI-compatible clients.
- Provides a minimal compatibility layer for `tools`, `tool_choice`, and `message.tool_calls`.
- Supports `stream=true + tools` by returning SSE `delta.tool_calls`.
- Can also be used directly as a Python library without starting the HTTP server.

## How it works

The project uses the Microsoft/M365 Copilot session stored in your local browser profile. After sign-in, it saves a local snapshot under `session/`, including the token, cookies, and ChatHub request metadata required by the HTTP driver.

Client requests go to the local FastAPI server. The server parses OpenAI-style messages, forwards the request to the internal Copilot client, and converts the result back into OpenAI-compatible response shapes. Text requests use the M365 ChatHub path. Image input still uses the browser upload path.

## Requirements

- Python 3.9 or newer.
- A Microsoft/M365 account that can access Copilot.
- Windows, macOS, or Linux. This fork is mainly verified on Windows PowerShell.
- A visible browser for the first sign-in.

## Installation and sign-in

Clone the repository and enter the project directory:

```powershell
git clone "<your-repo-url>"
cd "Windows-Copilot-API"
```

Create a virtual environment:

```powershell
python -m venv "venv"
".\venv\Scripts\Activate.ps1"
```

If PowerShell blocks the activation script, allow local scripts for the current user:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Install dependencies:

```powershell
python -m pip install -r "requirements.txt"
python -m playwright install chromium
```

Sign in once:

```powershell
python -m copilot login
```

A browser window opens. Complete Microsoft/M365 Copilot sign-in and any human verification if it appears. When setup finishes, the project stores the local session under `session/` and reuses it for future requests.

## Start the local API server

Start the default server:

```powershell
python "app.py"
```

Default address:

```text
http://127.0.0.1:8000
```

OpenAI-compatible base URL:

```text
http://127.0.0.1:8000/v1
```

Change host or port:

```powershell
$env:HOST="0.0.0.0"
$env:PORT="8080"
python "app.py"
```

You can also start it with uvicorn:

```powershell
python -m uvicorn server.api:app --host 0.0.0.0 --port 8080
```

## OpenAI-compatible API

Available endpoints:

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/v1/models` | Lists the single `copilot` model |
| `POST` | `/v1/chat/completions` | OpenAI-compatible chat endpoint |

List models:

```powershell
curl.exe "http://127.0.0.1:8000/v1/models"
```

Basic chat request:

```powershell
curl.exe "http://127.0.0.1:8000/v1/chat/completions" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer anything" `
  -d "{"model":"copilot","messages":[{"role":"user","content":"Hello, introduce yourself briefly."}]}"
```

OpenAI Python SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="anything",  # Required by the SDK, ignored by the local server.
)

resp = client.chat.completions.create(
    model="copilot",
    messages=[
        {"role": "user", "content": "Hello, introduce yourself briefly."}
    ],
)

print(resp.choices[0].message.content)
```

## Python library usage

You can call the Python library directly without starting the HTTP server:

```python
from copilot import CopilotClient

client = CopilotClient()

reply = client.chat("Introduce yourself in one sentence.")
print(reply.text)
```

Continue the same conversation:

```python
reply2 = client.chat(
    "Continue the same topic.",
    conversation_id=reply.conversation_id,
)
print(reply2.text)
```

Direct streaming:

```python
from copilot import CopilotClient

client = CopilotClient()

for chunk in client.stream("Write a short haiku."):
    print(chunk, end="", flush=True)
```

## Streaming output

HTTP streaming with the OpenAI SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="anything",
)

stream = client.chat.completions.create(
    model="copilot",
    messages=[
        {"role": "user", "content": "Write a short haiku."}
    ],
    stream=True,
)

for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)
```

HTTP streaming with curl:

```powershell
curl.exe "http://127.0.0.1:8000/v1/chat/completions" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer anything" `
  -d "{"model":"copilot","stream":true,"messages":[{"role":"user","content":"Write a short haiku."}]}"
```

## Multi-turn conversations

If you do not pass `conversation_id`, the request starts a new Copilot conversation whenever possible. The response includes `conversation_id`; pass it back to continue the same upstream thread.

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="anything",
)

first = client.chat.completions.create(
    model="copilot",
    messages=[
        {"role": "user", "content": "Suggest a project name."}
    ],
)

conversation_id = first.model_extra.get("conversation_id")

second = client.chat.completions.create(
    model="copilot",
    messages=[
        {"role": "user", "content": "Give me five shorter alternatives."}
    ],
    extra_body={
        "conversation_id": conversation_id
    },
)

print(second.choices[0].message.content)
```

## Image input

The server accepts OpenAI-style content parts and can pass one local image file to Copilot:

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
                {"type": "text", "text": "Describe this image."},
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

Current image limitations:

- Local image files only.
- No remote image URLs.
- No `data:` URLs.
- Image requests still use the browser upload path.
- Image requests currently start a new conversation and do not continue with `conversation_id`.

## OpenAI tool_calls compatibility

This fork adds a minimal OpenAI `tool_calls` compatibility layer so AI IDEs can recognize tool invocation intent.

Supported fields and response shapes:

- `tools`
- `tool_choice`
- Non-streaming `message.tool_calls`
- Streaming `delta.tool_calls`
- `finish_reason=tool_calls`

Important behavior:

- The server does not execute tools.
- The client or AI IDE executes tools.
- Copilot does not natively return OpenAI-standard `tool_calls` in this transport.
- The server converts OpenAI tool definitions into a strict planning prompt, asks Copilot to produce tool-call JSON, and then converts that JSON into OpenAI-compatible response fields.
- This is a compatibility layer, not Microsoft Declarative Agent, Copilot Plugin, or MCP-native execution.

### Non-streaming tool_calls

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="anything",
)

resp = client.chat.completions.create(
    model="copilot",
    messages=[
        {"role": "user", "content": "Inspect the local environment using PowerShell."}
    ],
    tools=[
        {
            "type": "function",
            "function": {
                "name": "run_powershell",
                "description": "Run a command in local Windows PowerShell.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "PowerShell command"
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

Possible response:

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

### Streaming tool_calls

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
        {"role": "user", "content": "Inspect the local environment using PowerShell."}
    ],
    tools=[
        {
            "type": "function",
            "function": {
                "name": "run_powershell",
                "description": "Run a command in local Windows PowerShell.",
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

The SSE stream contains:

```text
delta.tool_calls
finish_reason: tool_calls
data: [DONE]
```

### tool_choice

Force a specific tool:

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

Disable tools for the current request:

```json
{
  "tool_choice": "none"
}
```

## AI IDE integration

Configure your AI IDE as an OpenAI-compatible provider:

```text
Base URL: http://127.0.0.1:8000/v1
Model: copilot
API Key: anything
```

If the IDE is running on the same Windows machine, use `127.0.0.1`. If the IDE agent or terminal runs inside WSL, Docker, a remote sandbox, or another isolated runtime, `127.0.0.1` refers to that environment rather than your Windows host.

Tool execution depends on the IDE. This project only returns OpenAI-standard tool-call intent. It does not read files, write files, run commands, or call the IDE tool layer by itself.

Recommended IDE checks:

- The selected model is `copilot`.
- The provider base URL is `http://127.0.0.1:8000/v1`.
- Agent Mode is enabled.
- Workspace or file access is enabled.
- Terminal or PowerShell tools are enabled.
- After updating the server, start a new chat or reload the IDE window to avoid stale provider state.

## Command line usage

Sign in:

```powershell
python -m copilot login
```

Ask a quick question:

```powershell
python -m copilot ask "Hello"
```

Start the API server:

```powershell
python "app.py"
```

## Auth and token snapshots

The sign-in flow stores local session data under `session/`:

| Path | Purpose |
| --- | --- |
| `session/profile/` | Browser profile |
| `session/token.json` | Token, cookies, and ChatHub snapshot |
| `session/login.log` | Sign-in log |
| `session/ws_capture.log` | WebSocket capture log |
| `session/diagnostic_report.txt` | Redacted diagnostic report |

If you see ChatHub `401`, expired token errors, or stale cookies, refresh the session:

```powershell
python -m copilot login
```

If sign-in or the first request fails, run diagnostics:

```powershell
python "tests/diagnostic.py"
```

Diagnostic output redacts sensitive values such as access tokens, cookies, OAuth codes, and emails.

## Concurrency and rate limiting

The bridge uses one Copilot account. The upstream ChatHub path does not tolerate multiple active conversations from the same process, so upstream calls are serialized with a lock. Concurrent HTTP requests queue and run one at a time.

Default rate limit settings:

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `RATE_LIMIT_RPM` | `12` | Accepted requests per minute. `0` disables the limit |
| `RATE_LIMIT_BURST` | `4` | Allowed short burst size |

Change rate limits:

```powershell
$env:RATE_LIMIT_RPM="20"
$env:RATE_LIMIT_BURST="5"
python "app.py"
```

Stress test:

```powershell
python "tests/stress.py"
python "tests/stress.py" --max 64 --timeout 120 --url "http://127.0.0.1:8000"
```

## Docker usage

Sign in on the host first:

```powershell
python -m copilot login
```

Then start Docker:

```powershell
docker compose up --build
```

Docker reuses the mounted `session/` directory. A container usually cannot complete visible browser verification, so if clearance expires, re-run login on the host.

## Tests and validation

Core tests:

```powershell
python -m unittest tests.test_tool_calls
python -m unittest tests.test_multimodal_api
python -m unittest tests.test_server
```

If `TestClient` tests need the compatibility dependency:

```powershell
python -m pip install httpx2
```

Health check:

```powershell
curl.exe "http://127.0.0.1:8000/v1/models"
```

Basic chat check:

```powershell
curl.exe "http://127.0.0.1:8000/v1/chat/completions" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer anything" `
  -d "{"model":"copilot","messages":[{"role":"user","content":"Reply with OK only"}]}"
```

## Project layout

| Path | Purpose |
| --- | --- |
| `app.py` | Server entry point |
| `copilot/` | Copilot sign-in, auth, browser, and HTTP/ChatHub driver |
| `server/` | FastAPI OpenAI-compatible server |
| `server/schemas.py` | Request models, including `tools` and `tool_choice` |
| `server/prompt.py` | Message parsing, image parsing, tool prompt construction, and tool JSON parsing |
| `server/openai_format.py` | OpenAI-compatible response builders |
| `server/api.py` | HTTP routes, streaming output, and tool-call branches |
| `examples/` | Example scripts |
| `tests/` | Unit tests, diagnostics, and stress tests |
| `session/` | Local session state, ignored by git |
| `Docs/` | Project documentation |
| `readme-zh.md` | Chinese README |

## Troubleshooting

### `/v1/models` works, but chat fails

Refresh the local session:

```powershell
python -m copilot login
```

### ChatHub 401

The token, cookies, or ChatHub snapshot are stale. Re-login and restart the server:

```powershell
python -m copilot login
python "app.py"
```

### Cloudflare clearance

Run:

```powershell
python -m copilot login
```

If the browser shows a verification challenge, complete it manually.

### AI IDE cannot access local files

This is usually an IDE agent/runtime issue rather than a model API issue. The IDE terminal or agent may be running in WSL, Docker, a remote sandbox, or a temporary workspace.

Check from the IDE terminal:

```powershell
pwd
Test-Path "c:\Users\patde\Documents\GitHub\Windows-Copilot-API"
dir "c:\Users\patde\Documents\GitHub\Windows-Copilot-API"
```

### AI IDE does not trigger tools

Make sure the IDE supports OpenAI `tool_calls`, and that Agent Mode, Workspace Access, and Terminal Access are enabled. This project returns tool-call intent but does not execute tools itself.

### `stream=true + tools` fails

The current fork supports minimal streaming `tool_calls`. If the IDE still shows an old error, restart the server and open a new IDE chat or reload the IDE window.

## Known limitations

- The server advertises one model: `copilot`.
- The server does not execute tools; it only returns `tool_calls`.
- `tool_calls` is a compatibility layer, not a native Copilot/OpenAI wire format.
- Tool-call JSON depends on Copilot following the strict planning prompt; unusual prompts may still produce normal text.
- Image input still uses the browser path.
- Upstream requests are serialized and are not suitable for a high-throughput gateway.
- `session/` contains local auth state. Do not commit it or share it.

## Note to the original author

Thank you to the original author for creating this project and making the Copilot web experience accessible through a practical local API bridge. This fork keeps the original idea intact while adding a Chinese README, expanded usage documentation, an OpenAI `tool_calls` compatibility layer, streaming `delta.tool_calls` support for AI IDEs, new tool-call tests, and clearer guidance for local IDE integration, token refresh, troubleshooting, and validation.

## License

Released under the [MIT License](LICENSE). This is an unofficial implementation, and users are responsible for their own account usage, data handling, and compliance with applicable service terms.
