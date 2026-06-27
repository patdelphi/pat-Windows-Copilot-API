"""程序说明：通过 curl_cffi 直连当前 M365 ChatHub 的纯 HTTP Copilot 驱动。"""

import copy
from http.cookiejar import Cookie, CookieJar
import json
import time
import uuid
from select import select
from typing import Dict, Optional
from urllib.parse import quote

from curl_cffi.const import CurlECode, CurlInfo
from curl_cffi.curl import CurlError
from curl_cffi.requests import CurlWsFlag, Session

# curl_cffi 的 WebSocket 在空闲时会一直 AGAIN 重试，这里仍手动驱动读循环。
_CURL_SOCKET_BAD = -1

from .models import AbstractProvider, Conversation, ImageResponse, ImageType
from .browser import BrowserCopilot
from .protocol import (
    CHAT_HUB_DYNAMIC_QUERY_KEYS,
    CHAT_HUB_HOST,
    SIGNALR_HANDSHAKE_FRAME,
    SIGNALR_PING_FRAME,
    SIGNALR_RECORD_SEPARATOR,
)


class ClearanceRequired(RuntimeError):
    """为兼容上层错误处理而保留的异常类型。"""


class Copilot(AbstractProvider):
    label = "Microsoft Copilot"
    url = "https://m365.cloud.microsoft"
    working = True
    supports_stream = True
    default_model = "Copilot"
    needs_auth = True

    def create_completion(
            self,
            prompt: str,
            stream: bool = False,
            proxy: str = None,
            timeout: int = 900,
            image: ImageType = None,
            conversation: Optional[Conversation] = None,
            conversation_id: str = None,
            return_conversation: bool = False,
            cookies: Dict[str, str] = None,
            access_token: str = None,
            identity_type: str = None,
            **kwargs
        ):
        """向当前 M365 ChatHub 发送一轮对话并流式返回文本。"""
        # Resolve auth: explicit args win, else fall back to the conversation's.
        if cookies is None and conversation is not None:
            cookies = conversation.cookie_jar
        if access_token is None and conversation is not None:
            access_token = conversation.access_token
        chat_hub = kwargs.get("chat_hub")
        chat_request_template = kwargs.get("chat_request_template")

        if not access_token:
            raise RuntimeError("当前新协议必须使用已登录 access_token，请先运行 `python -m copilot login`。")
        if not chat_hub or not chat_request_template:
            raise RuntimeError("缺少 ChatHub 协议快照，请重新运行 `python -m copilot login`。")
        if image is not None:
            if conversation is not None or conversation_id is not None:
                raise RuntimeError("当前图片上传仅支持新会话，暂不支持 conversation_id。")
            yield from self._browser_fallback(
                prompt, proxy, timeout, None, return_conversation, access_token, image=image
            )
            return

        continuing = conversation is not None or conversation_id is not None
        snapshot_query = dict((chat_hub or {}).get("query") or {})
        request_id = uuid.uuid4().hex
        session_id = str(uuid.uuid4())
        conversation_id = conversation_id or str(uuid.uuid4())
        websocket_url = self._build_websocket_url(
            chat_hub, access_token, request_id, session_id, conversation_id
        )
        send_frame = self._build_chat_frame(
            chat_request_template,
            prompt,
            request_id,
            session_id,
            is_start_of_session=not continuing,
        )
        cookie_jar = self._build_cookie_jar(cookies)

        with Session(
            timeout=timeout,
            proxy=proxy,
            impersonate="chrome",
            cookies=cookie_jar,
        ) as session:
            session.get(f"{self.url}/chat/")
            try:
                wss = session.ws_connect(
                    websocket_url,
                    headers={
                        "Origin": self.url,
                        "Cache-Control": "no-cache",
                        "Pragma": "no-cache",
                    },
                    referer=f"{self.url}/chat/",
                    impersonate="chrome",
                )
                if return_conversation:
                    yield Conversation(conversation_id, session.cookies.jar, access_token)
                wss.send(self._encode_signalr_frame(SIGNALR_HANDSHAKE_FRAME), CurlWsFlag.TEXT)
                wss.send(self._encode_signalr_frame(SIGNALR_PING_FRAME), CurlWsFlag.TEXT)
                wss.send(self._encode_signalr_frame(send_frame), CurlWsFlag.TEXT)
                yield from self._read_stream(wss, timeout)
                return
            except CurlError as exc:
                if "Refused WebSockets upgrade: 401" in str(exc):
                    raise RuntimeError(
                        "纯 HTTP 文本链路仍被 ChatHub 拒绝（WS 401）。"
                        "当前已禁用文本浏览器 fallback，请检查最新 token 快照与会话 Cookie。"
                    ) from exc
                raise

    def _read_stream(self, wss, timeout: int, idle_timeout: int = 60):
        """读取 SignalR 流式回复，并尽量避免重复输出完整文本。"""
        buffer = b""
        started = False
        last_msg = None
        emitted_message_ids = set()

        overall_deadline = time.time() + timeout
        while True:
            idle_deadline = time.time() + idle_timeout
            try:
                chunk = self._recv_frame(wss, min(overall_deadline, idle_deadline))
            except Exception:
                break  # socket closed/errored -> end of stream
            if chunk is None:  # deadline passed with no frame
                if time.time() >= overall_deadline:
                    raise TimeoutError(f"Copilot stream exceeded {timeout}s")
                raise TimeoutError(
                    f"Copilot chat socket went silent for {idle_timeout}s; "
                    f"last frame was {last_msg!r}."
                )

            buffer += chunk if isinstance(chunk, (bytes, bytearray)) else chunk.encode("utf-8")
            messages, buffer = self._drain_signalr_frames(buffer)
            for msg in messages:
                last_msg = msg
                msg_type = msg.get("type")
                if msg_type == 1 and msg.get("target") == "update":
                    for arg in msg.get("arguments") or []:
                        text = arg.get("writeAtCursor")
                        if text:
                            started = True
                            yield text
                        for item in arg.get("messages") or []:
                            if item.get("author") != "bot":
                                continue
                            if item.get("messageType") == "ReferencesListComplete":
                                continue
                            item_id = item.get("messageId") or item.get("requestId")
                            # 首帧通常带一小段开头文本，后续再用 writeAtCursor 续写；
                            # 这里仅在尚未开始流式输出时发一次，避免重复拼接整段全文。
                            if started or item_id in emitted_message_ids:
                                continue
                            text = item.get("text") or self._extract_card_text(item)
                            if text:
                                emitted_message_ids.add(item_id)
                                started = True
                                yield text
                elif msg_type == 3:
                    return
                elif msg_type == 7:
                    raise RuntimeError(f"Copilot chat hub error: {msg}")

        if not started:
            raise RuntimeError(f"Invalid response: {last_msg}")

    @staticmethod
    def _recv_frame(wss, deadline: float):
        """Block for one complete WS frame, or return ``None`` past ``deadline``.

        Reassembles libcurl's fragments like ``curl_cffi``'s own ``recv()`` but
        breaks out of the ``CURLE_AGAIN`` wait once ``deadline`` (epoch seconds)
        is reached, so an idle socket can't hang us indefinitely. Non-AGAIN curl
        errors (e.g. a closed connection) propagate to the caller.
        """
        sock_fd = wss.curl.getinfo(CurlInfo.ACTIVESOCKET)
        if sock_fd == _CURL_SOCKET_BAD:
            raise ConnectionError("WebSocket has no active socket")
        chunks = []
        while True:
            try:
                chunk, frame = wss.recv_fragment()
                chunks.append(chunk)
                if frame.bytesleft == 0 and frame.flags & CurlWsFlag.CONT == 0:
                    return b"".join(chunks)
            except CurlError as e:
                if e.code != CurlECode.AGAIN:
                    raise
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                select([sock_fd], [], [], min(0.5, remaining))

    @staticmethod
    def _encode_signalr_frame(payload: dict) -> bytes:
        return (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + SIGNALR_RECORD_SEPARATOR
        ).encode("utf-8")

    @staticmethod
    def _drain_signalr_frames(buf: bytes):
        text = buf.decode("utf-8", errors="ignore")
        parts = text.split(SIGNALR_RECORD_SEPARATOR)
        if not parts:
            return [], b""
        messages = []
        for part in parts[:-1]:
            part = part.strip()
            if not part:
                continue
            try:
                messages.append(json.loads(part))
            except json.JSONDecodeError:
                continue
        return messages, parts[-1].encode("utf-8")

    @staticmethod
    def _build_cookie_jar(cookies) -> CookieJar:
        """把 token 快照里的 Cookie 明细恢复成带域名信息的 CookieJar。"""
        if isinstance(cookies, CookieJar):
            return cookies
        jar = CookieJar()
        if isinstance(cookies, dict):
            for name, value in cookies.items():
                jar.set_cookie(Copilot._make_cookie(name, value, "m365.cloud.microsoft"))
            return jar
        for item in cookies or []:
            name = item.get("name")
            value = item.get("value")
            domain = item.get("domain") or "m365.cloud.microsoft"
            if not name:
                continue
            jar.set_cookie(Copilot._make_cookie(name, value, domain, item.get("path") or "/", item.get("secure", False)))
        return jar

    @staticmethod
    def _make_cookie(name: str, value: str, domain: str, path: str = "/", secure: bool = False) -> Cookie:
        """创建标准库 Cookie 对象，供 curl_cffi 复用浏览器登录态。"""
        return Cookie(
            version=0,
            name=name,
            value=value or "",
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=bool(domain),
            domain_initial_dot=str(domain).startswith("."),
            path=path,
            path_specified=True,
            secure=bool(secure),
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )

    @staticmethod
    def _build_websocket_url(chat_hub: dict, access_token: str, request_id: str, session_id: str, conversation_id: str):
        query = dict((chat_hub or {}).get("query") or {})
        query["chatsessionid"] = request_id
        query["XRoutingParameterSessionKey"] = request_id
        query["clientrequestid"] = request_id
        query["X-SessionId"] = session_id
        query["ConversationId"] = conversation_id
        query["access_token"] = access_token
        pairs = [f"{quote(str(k), safe='')}={quote(str(v), safe=',@:-._~')}" for k, v in query.items()]
        return f"{CHAT_HUB_HOST}{chat_hub['path']}?{'&'.join(pairs)}"

    @staticmethod
    def _build_chat_frame(template: dict, prompt: str, request_id: str, session_id: str, is_start_of_session: bool):
        frame = copy.deepcopy(template)
        frame["type"] = 4
        frame["target"] = "chat"
        frame["invocationId"] = "0"
        arg = frame["arguments"][0]
        arg["clientCorrelationId"] = request_id
        arg["sessionId"] = session_id
        arg["traceId"] = request_id
        arg["isStartOfSession"] = is_start_of_session
        client_info = arg.setdefault("clientInfo", {})
        client_info["clientSessionId"] = session_id
        message = arg.setdefault("message", {})
        message["text"] = prompt
        message["requestId"] = request_id
        message.setdefault("author", "user")
        message.setdefault("inputMethod", "Keyboard")
        message.setdefault("messageType", "Chat")
        return frame

    @staticmethod
    def _extract_card_text(item: dict) -> str:
        for card in item.get("adaptiveCards") or []:
            for body in card.get("body") or []:
                text = body.get("text")
                if text:
                    return text
        return ""

    @staticmethod
    def _browser_fallback(
        prompt: str,
        proxy: str,
        timeout: int,
        conversation_id: str,
        return_conversation: bool,
        access_token: str,
        image: ImageType = None,
    ):
        bot = BrowserCopilot(headless=True, proxy=proxy)
        try:
            bot.start()
            stream = bot.stream_chat(prompt, timeout=timeout, image=image)
            if return_conversation:
                yield Conversation(bot.current_conversation_id or conversation_id, CookieJar(), access_token)
            yield from stream
        finally:
            bot.close()
