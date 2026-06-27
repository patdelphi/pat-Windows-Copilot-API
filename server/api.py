"""程序说明：把 Copilot 客户端封装成 OpenAI Chat Completions 兼容 HTTP API。"""

import threading
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from copilot import CopilotClient
from copilot.driver import ClearanceRequired

from .config import MODEL_NAME, RATE_LIMIT_BURST, RATE_LIMIT_RPM
from .openai_format import (
    completion_response,
    new_id,
    sse_event,
    stream_chunk,
    tool_calls_response,
)
from .prompt import build_tool_prompt, extract_prompt_and_image, parse_tool_calls, should_use_tools
from .ratelimit import TokenBucket
from .schemas import ChatCompletionRequest

app = FastAPI(title="Copilot OpenAI-compatible API", version="1.0.0")
# Server runs headless and must never pop a visible browser mid-request. With
# both recovery passes disabled, an expired clearance surfaces immediately as a
# 503 (see ClearanceRequired handling below) so an operator can re-clear out of
# band (`python -m copilot login`). Headless auto-solve is intentionally off:
# it's unreliable on low-trust egress and a failed pass can wedge the session.
client = CopilotClient(interactive_clear=False, headless_clear=False)

_CLEARANCE_HELP = (
    "Cloudflare clearance expired and could not be refreshed headlessly. "
    "Re-clear in a browser: run `python -m copilot login` (or `python tests/diagnostic.py`) "
    "and pass the 'verify you're human' check, then retry."
)

# Self-imposed rate limit on top of the concurrency lock below: this caps
# requests-per-minute, the lock caps requests-in-flight. See server/ratelimit.py.
_rate_limiter = TokenBucket(RATE_LIMIT_RPM, RATE_LIMIT_BURST)


def _rate_limited_response():
    """Spend a token; return an OpenAI-shaped 429 if none left, else ``None``."""
    allowed, wait = _rate_limiter.try_acquire()
    if allowed:
        return None
    secs = max(1, round(wait))
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(secs)},
        content={"error": {
            "message": (
                f"Rate limit exceeded (>{RATE_LIMIT_RPM:g} req/min). "
                f"Retry in {secs}s."
            ),
            "type": "rate_limit_error",
            "code": "rate_limit_exceeded",
        }},
    )

# Copilot's per-account chat socket doesn't tolerate concurrent conversations
# from one process (parallel requests error out or hang). This server bridges a
# single signed-in account, so we serialize upstream calls: concurrent HTTP
# requests queue here and run one at a time. Predictable, at the cost of
# parallelism — fine for a personal bridge.
_upstream_lock = threading.Lock()


def _stream(prompt: str, model: str, conversation_id=None, image=None, tools=None, tool_choice=None):
    """Yield OpenAI ``chat.completion.chunk`` SSE events for ``prompt``.

    ``conversation_id`` continues an existing Copilot thread; ``None`` starts a
    fresh one (its id is emitted on the final chunk).
    """
    cid = new_id()
    created = int(time.time())
    try:
        with _upstream_lock:  # one upstream chat at a time (released on disconnect)
            yield sse_event(stream_chunk(cid, created, model, {"role": "assistant"}))
            if should_use_tools(tools, tool_choice):
                # Copilot 不原生输出 OpenAI 流式 tool_calls；这里先让 Copilot 返回完整
                # 工具 JSON，再包装成一个 SSE tool_calls delta，供 AI IDE 触发工具。
                upstream_prompt = build_tool_prompt(prompt, tools, tool_choice)
                reply = client.chat(upstream_prompt, conversation_id=conversation_id, image=image)
                tool_calls = parse_tool_calls(reply.text)
                if tool_calls:
                    yield sse_event(stream_chunk(
                        cid,
                        created,
                        model,
                        {"tool_calls": [
                            {"index": idx, **call}
                            for idx, call in enumerate(tool_calls)
                        ]},
                    ))
                    yield sse_event(stream_chunk(
                        cid,
                        created,
                        model,
                        {},
                        finish="tool_calls",
                        conversation_id=reply.conversation_id,
                    ))
                    yield "data: [DONE]\n\n"
                    return
                yield sse_event(stream_chunk(cid, created, model, {"content": reply.text}))
                yield sse_event(stream_chunk(
                    cid,
                    created,
                    model,
                    {},
                    finish="stop",
                    conversation_id=reply.conversation_id,
                ))
                yield "data: [DONE]\n\n"
                return
            stream = client.stream(prompt, conversation_id=conversation_id, image=image)
            for piece in stream:
                if isinstance(piece, str) and piece:
                    yield sse_event(stream_chunk(cid, created, model, {"content": piece}))
            # Copilot's conversation id is known once the stream has run; emit it
            # on the final chunk so callers can track the upstream thread.
            yield sse_event(
                stream_chunk(
                    cid, created, model, {}, finish="stop",
                    conversation_id=stream.conversation_id,
                )
            )
    except ClearanceRequired:
        yield sse_event(
            stream_chunk(cid, created, model, {"content": f"\n[error: {_CLEARANCE_HELP}]"}, finish="error")
        )
    except Exception as exc:  # surface errors to the client instead of hanging
        yield sse_event(
            stream_chunk(cid, created, model, {"content": f"\n[error: {exc}]"}, finish="error")
        )
    yield "data: [DONE]\n\n"


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {"id": MODEL_NAME, "object": "model", "created": 0, "owned_by": "microsoft"}
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    try:
        prompt, image = extract_prompt_and_image(req.messages)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": str(exc), "type": "invalid_request_error"}},
        )
    if not prompt.strip():
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "no text content in messages", "type": "invalid_request_error"}},
        )
    model = req.model or MODEL_NAME

    # Enforce the per-minute ceiling before touching the upstream lock, so excess
    # callers get a fast 429 instead of piling up behind the serialized queue.
    limited = _rate_limited_response()
    if limited is not None:
        return limited

    if req.stream:
        return StreamingResponse(
            _stream(
                prompt,
                model,
                req.conversation_id,
                image=image,
                tools=req.tools,
                tool_choice=req.tool_choice,
            ),
            media_type="text/event-stream",
        )

    try:
        with _upstream_lock:  # serialize: one upstream chat at a time
            # 工具调用第一版只返回 OpenAI tool_calls，不在服务端执行工具。
            upstream_prompt = (
                build_tool_prompt(prompt, req.tools, req.tool_choice)
                if should_use_tools(req.tools, req.tool_choice)
                else prompt
            )
            reply = client.chat(upstream_prompt, conversation_id=req.conversation_id, image=image)
    except ClearanceRequired:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": _CLEARANCE_HELP, "type": "clearance_required"}},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": {"message": str(exc), "type": "upstream_error"}},
        )
    if should_use_tools(req.tools, req.tool_choice):
        tool_calls = parse_tool_calls(reply.text)
        if tool_calls:
            return tool_calls_response(tool_calls, model, reply.conversation_id)
    return completion_response(reply.text, model, reply.conversation_id)


@app.get("/")
def root():
    return {"service": "Copilot OpenAI-compatible API", "endpoints": ["/v1/models", "/v1/chat/completions"]}
