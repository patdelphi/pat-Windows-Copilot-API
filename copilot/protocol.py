"""程序说明：集中保存当前 Copilot M365 ChatHub 协议所需的最小常量。"""

# 诊断抓包确认当前网页端聊天已迁移到 M365 ChatHub。
CHAT_HUB_HOST = "wss://substrate.office.com"

# SignalR 文本消息以 Record Separator 结尾；发送和接收都按这个分隔符切帧。
SIGNALR_RECORD_SEPARATOR = "\x1e"

# 当前链路的最小握手：先声明 json 协议，再发一个 type=6 心跳，随后才能发送聊天调用。
SIGNALR_HANDSHAKE_FRAME = {"protocol": "json", "version": 1}
SIGNALR_PING_FRAME = {"type": 6}

# 每次请求都会重写这些动态字段，避免复用浏览器抓包中的旧会话值。
CHAT_HUB_DYNAMIC_QUERY_KEYS = {
    "chatsessionid",
    "XRoutingParameterSessionKey",
    "clientrequestid",
    "X-SessionId",
    "ConversationId",
    "access_token",
}
