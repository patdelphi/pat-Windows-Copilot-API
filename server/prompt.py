"""程序说明：解析 OpenAI 消息，生成 Copilot 提示词，并处理工具调用 JSON。

Copilot's protocol has no role/system channel — it takes one prompt string per
turn — so we collapse the whole conversation into one piece of text.
"""

import json
import os
from pathlib import Path
from typing import Any, List, Optional, Union
from urllib.parse import unquote, urlparse

from .schemas import ChatMessage


_TOOL_JSON_PREFIX = "你正在充当 OpenAI Chat Completions 的工具调用规划器。"


def _message_role(message: Union[ChatMessage, dict]) -> str:
    return message.role if isinstance(message, ChatMessage) else str(message.get("role", ""))


def _message_content(message: Union[ChatMessage, dict]):
    return message.content if isinstance(message, ChatMessage) else message.get("content")


def content_text(content: Optional[Union[str, List[Any]]]) -> str:
    """Extract plain text from a message's content (string or content-parts)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if isinstance(part, dict):
            if part.get("type") == "text":
                parts.append(part.get("text", ""))
        else:
            parts.append(str(part))
    return "\n".join(p for p in parts if p)


def _normalize_local_image_url(url: str) -> str:
    """将本地图片 URL 统一转换为绝对文件路径。"""
    if not url:
        raise ValueError("图片 URL 不能为空。")
    # Windows 绝对路径会被 urlparse 误判成 scheme，例如 `C:\foo.png` -> scheme=`c`。
    if len(url) >= 3 and url[1] == ":" and url[2] in ("\\", "/"):
        full = str(Path(url).expanduser().resolve())
        if not Path(full).is_file():
            raise ValueError(f"图片文件不存在: {full}")
        return full
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https", "data"):
        raise ValueError("当前仅支持本地图片路径，不支持远程 URL 或 data URL。")
    if parsed.scheme == "file":
        path = unquote(parsed.path or "")
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        norm = os.path.normpath(path)
    elif parsed.scheme:
        raise ValueError("当前仅支持本地图片路径。")
    else:
        norm = os.path.normpath(url)
    full = str(Path(norm).expanduser().resolve())
    if not Path(full).is_file():
        raise ValueError(f"图片文件不存在: {full}")
    return full


def extract_prompt_and_image(messages: List[Union[ChatMessage, dict]]):
    """从 OpenAI 风格消息中提取 prompt 和单张本地图片路径。"""
    image_path = None
    for message in messages:
        content = _message_content(message)
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            candidate = _normalize_local_image_url((part.get("image_url") or {}).get("url", ""))
            if image_path and candidate != image_path:
                raise ValueError("当前仅支持单张图片。")
            if image_path:
                raise ValueError("当前仅支持单张图片。")
            image_path = candidate
    return messages_to_prompt(messages), image_path


def _tool_name(tool: Any) -> str:
    """从 OpenAI tool 定义中提取函数名。"""
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function") if tool.get("type") == "function" else tool
    if not isinstance(function, dict):
        return ""
    return str(function.get("name") or "")


def _tool_spec(tool: Any) -> dict:
    """提取模型需要看到的最小工具定义。"""
    if not isinstance(tool, dict):
        return {}
    function = tool.get("function") if tool.get("type") == "function" else tool
    if not isinstance(function, dict):
        return {}
    return {
        "name": function.get("name"),
        "description": function.get("description", ""),
        "parameters": function.get("parameters", {"type": "object", "properties": {}}),
    }


def _forced_tool_name(tool_choice: Any) -> str:
    """解析 tool_choice 中显式指定的工具名。"""
    if not isinstance(tool_choice, dict):
        return ""
    function = tool_choice.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return ""


def should_use_tools(tools: Optional[List[Any]], tool_choice: Any = None) -> bool:
    """判断本轮是否需要尝试工具调用。"""
    if not tools or tool_choice == "none":
        return False
    return True


def build_tool_prompt(prompt: str, tools: Optional[List[Any]], tool_choice: Any = None) -> str:
    """把 OpenAI tools 转成 Copilot 可遵循的严格 JSON 输出协议。"""
    specs = [_tool_spec(tool) for tool in tools or [] if _tool_name(tool)]
    forced_name = _forced_tool_name(tool_choice)
    if forced_name:
        specs = [spec for spec in specs if spec.get("name") == forced_name] or specs
    contract = {
        "tool_calls": [
            {
                "name": forced_name or "<tool_name>",
                "arguments": {"param": "value"},
            }
        ]
    }
    return (
        f"{_TOOL_JSON_PREFIX}\n"
        "你会收到用户请求和可用工具。请判断是否应该调用工具。\n"
        "如果需要调用工具，只能输出 JSON，不要输出 Markdown、解释、代码块或自然语言。\n"
        "JSON 结构必须是：\n"
        f"{json.dumps(contract, ensure_ascii=False, indent=2)}\n\n"
        "可用工具：\n"
        f"{json.dumps(specs, ensure_ascii=False, indent=2)}\n\n"
        f"tool_choice：{json.dumps(tool_choice, ensure_ascii=False)}\n\n"
        "用户请求：\n"
        f"{prompt}"
    )


def _extract_json_payload(text: str) -> Optional[Any]:
    """从纯文本或 Markdown 代码块中提取第一个 JSON 对象。"""
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return None


def parse_tool_calls(payload: Any) -> Optional[List[dict]]:
    """把 Copilot 输出的工具 JSON 标准化成 OpenAI `tool_calls`。

    支持两类输入：简化格式 `{"tool_calls":[{"name": "..."}]}`，以及 OpenAI
    原生格式 `{"tool_calls":[{"type":"function","function":{...}}]}`。
    """
    data = payload if isinstance(payload, dict) else _extract_json_payload(str(payload or ""))
    if not isinstance(data, dict):
        return None
    raw_calls = data.get("tool_calls")
    if raw_calls is None and data.get("tool_call") is not None:
        raw_calls = [data.get("tool_call")]
    if not isinstance(raw_calls, list) or not raw_calls:
        return None

    calls = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            arguments = function.get("arguments", "{}")
        else:
            name = raw.get("name")
            arguments = raw.get("arguments", {})
        if not name:
            continue
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        calls.append({
            "id": raw.get("id") or f"call_{len(calls) + 1}",
            "type": "function",
            "function": {
                "name": str(name),
                "arguments": arguments,
            },
        })
    return calls or None


def messages_to_prompt(messages: List[ChatMessage]) -> str:
    """Flatten an OpenAI ``messages`` array into a single Copilot prompt."""
    system = "\n\n".join(
        content_text(_message_content(m)) for m in messages if _message_role(m) == "system" and _message_content(m)
    )
    convo = [m for m in messages if _message_role(m) != "system"]

    if len(convo) == 1 and _message_role(convo[0]) == "user":
        body = content_text(_message_content(convo[0]))  # simple single-turn request
    else:
        lines = []
        for m in convo:
            label = "User" if _message_role(m) == "user" else "Assistant"
            lines.append(f"{label}: {content_text(_message_content(m))}")
        lines.append("Assistant:")  # cue Copilot to continue
        body = "\n".join(lines)

    if system and body:
        return f"{system}\n\n{body}"
    return system or body
