"""程序说明：把 OpenAI ``messages`` 数组解析成 Copilot 可用的文本与图片输入。

Copilot's protocol has no role/system channel — it takes one prompt string per
turn — so we collapse the whole conversation into one piece of text.
"""

import os
from pathlib import Path
from typing import Any, List, Optional, Union
from urllib.parse import unquote, urlparse

from .schemas import ChatMessage


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
