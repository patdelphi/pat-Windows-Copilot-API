"""程序说明：定义 OpenAI 兼容接口的请求模型，包含聊天、多模态和工具调用字段。"""

from typing import Any, List, Optional, Union

from pydantic import BaseModel

from .config import MODEL_NAME


class ChatMessage(BaseModel):
    role: str
    # content is a plain string, or OpenAI "content parts" (list of dicts), or
    # null for some tool/assistant messages.
    content: Optional[Union[str, List[Any]]] = None


class ChatCompletionRequest(BaseModel):
    messages: List[ChatMessage]
    model: Optional[str] = MODEL_NAME
    stream: bool = False
    # OpenAI tool calling fields. The server does not execute tools itself; it
    # only returns model-requested tool calls for the IDE/client to execute.
    tools: Optional[List[Any]] = None
    tool_choice: Optional[Any] = None
    # Copilot's own conversation id (returned in earlier responses). Pass it back
    # to continue that thread; omit it to start a fresh conversation. Outside
    # OpenAI's schema, but standard clients can set it via extra_body.
    conversation_id: Optional[str] = None
    # Any other OpenAI fields (temperature, max_tokens, ...) are accepted and
    # ignored — Copilot's consumer protocol doesn't expose those knobs.
