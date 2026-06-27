"""程序说明：构造 OpenAI 兼容响应，包括普通回复、流式片段和工具调用。"""

import json
import time
import uuid


def new_id() -> str:
    """A fresh ``chatcmpl-...`` id, as the OpenAI API returns."""
    return f"chatcmpl-{uuid.uuid4().hex}"


def completion_response(text: str, model: str, conversation_id=None) -> dict:
    """A non-streaming ``chat.completion`` object.

    ``conversation_id`` is Copilot's own conversation id, surfaced as an extra
    top-level field (not part of OpenAI's schema, so standard clients ignore it)
    for callers that want to track the upstream thread.
    """
    return {
        "id": new_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "conversation_id": conversation_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def tool_calls_response(tool_calls: list, model: str, conversation_id=None) -> dict:
    """构造 OpenAI 标准的非流式工具调用响应。

    服务端只把模型意图转换成 `tool_calls`，实际工具执行由 AI IDE 或客户端完成。
    """
    normalized = []
    for idx, call in enumerate(tool_calls):
        item = dict(call)
        item.setdefault("id", f"call_{uuid.uuid4().hex[:24]}")
        item.setdefault("type", "function")
        function = dict(item.get("function") or {})
        args = function.get("arguments", "{}")
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        function["arguments"] = args
        item["function"] = function
        normalized.append(item)

    return {
        "id": new_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "conversation_id": conversation_id,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": normalized,
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def sse_event(payload: dict) -> str:
    """Serialize a payload as a Server-Sent Events ``data:`` line."""
    return f"data: {json.dumps(payload)}\n\n"


def stream_chunk(
    cid: str, created: int, model: str, delta: dict, finish=None, conversation_id=None
) -> dict:
    """A single ``chat.completion.chunk`` object for streaming responses.

    ``conversation_id`` (Copilot's upstream id) is added as an extra top-level
    field when known — typically only on the final chunk, since a new
    conversation's id isn't available until the stream has started.
    """
    chunk = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if conversation_id is not None:
        chunk["conversation_id"] = conversation_id
    return chunk
