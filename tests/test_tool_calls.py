"""程序说明：验证 OpenAI tool_calls 兼容层的请求解析、提示词和响应格式。"""

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.api import app
from server.api import chat_completions
from server.openai_format import tool_calls_response
from server.prompt import build_tool_prompt, parse_tool_calls
from server.schemas import ChatCompletionRequest


class ToolCallPromptTests(unittest.TestCase):
    """验证工具定义会被转换成 Copilot 可遵循的严格 JSON 提示。"""

    def test_schema_accepts_tools_and_tool_choice(self):
        req = ChatCompletionRequest(**{
            "model": "copilot",
            "messages": [{"role": "user", "content": "查天气"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "查询天气",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }],
            "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
        })

        self.assertEqual(req.tools[0]["function"]["name"], "get_weather")
        self.assertEqual(req.tool_choice["function"]["name"], "get_weather")

    def test_build_tool_prompt_contains_strict_output_contract(self):
        prompt = build_tool_prompt(
            "查上海天气",
            [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "查询天气",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }],
            {"type": "function", "function": {"name": "get_weather"}},
        )

        self.assertIn("get_weather", prompt)
        self.assertIn("tool_calls", prompt)
        self.assertIn("只能输出 JSON", prompt)
        self.assertIn("查上海天气", prompt)

    def test_parse_tool_calls_normalizes_arguments(self):
        calls = parse_tool_calls("""
        {
          "tool_calls": [
            {"name": "get_weather", "arguments": {"city": "上海"}}
          ]
        }
        """)

        self.assertEqual(calls[0]["type"], "function")
        self.assertEqual(calls[0]["function"]["name"], "get_weather")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["city"], "上海")

    def test_parse_openai_style_tool_calls(self):
        calls = parse_tool_calls({
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{\"path\":\"README.md\"}"},
            }]
        })

        self.assertEqual(calls[0]["id"], "call_1")
        self.assertEqual(calls[0]["function"]["name"], "read_file")


class ToolCallApiTests(unittest.TestCase):
    """验证 API 能把 Copilot 的工具 JSON 转成 OpenAI 标准 tool_calls。"""

    def setUp(self):
        self.http = TestClient(app)

    @patch("server.api.client.chat")
    def test_non_stream_request_returns_tool_calls(self, chat):
        chat.return_value = type("Reply", (), {
            "text": '{"tool_calls":[{"name":"get_weather","arguments":{"city":"上海"}}]}',
            "conversation_id": "conv-tool",
        })()

        body = chat_completions(ChatCompletionRequest(**{
            "model": "copilot",
            "messages": [{"role": "user", "content": "查上海天气"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "查询天气",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }],
            "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
        }))

        message = body["choices"][0]["message"]
        self.assertEqual(body["choices"][0]["finish_reason"], "tool_calls")
        self.assertIsNone(message["content"])
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "get_weather")
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"])["city"], "上海")

    def test_tool_calls_response_shape(self):
        body = tool_calls_response(
            [{"type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"README.md\"}"}}],
            "copilot",
            "conv-1",
        )

        self.assertEqual(body["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(body["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "read_file")

    @patch("server.api.client.chat")
    def test_stream_request_returns_tool_calls_delta(self, chat):
        chat.return_value = type("Reply", (), {
            "text": '{"tool_calls":[{"name":"get_weather","arguments":{"city":"上海"}}]}',
            "conversation_id": "conv-stream-tool",
        })()

        response = self.http.post("/v1/chat/completions", json={
            "model": "copilot",
            "stream": True,
            "messages": [{"role": "user", "content": "查上海天气"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "查询天气",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }],
        })

        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn('"tool_calls"', text)
        self.assertIn('"finish_reason": "tool_calls"', text)
        self.assertIn("data: [DONE]", text)


if __name__ == "__main__":
    unittest.main()
