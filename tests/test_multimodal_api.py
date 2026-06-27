"""程序说明：验证 OpenAI 风格图片消息的解析与 API 透传。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.api import app
from server.prompt import extract_prompt_and_image


class PromptImageParsingTests(unittest.TestCase):
    """验证图片消息输入约束。"""

    def setUp(self):
        fd, self.image_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.image_path):
            os.remove(self.image_path)

    def test_extracts_single_local_image_and_text(self):
        prompt, image = extract_prompt_and_image([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看下这张图里是什么"},
                    {"type": "image_url", "image_url": {"url": self.image_path}},
                ],
            }
        ])
        self.assertEqual(prompt, "看下这张图里是什么")
        self.assertEqual(image, self.image_path)

    def test_accepts_file_scheme_image_path(self):
        prompt, image = extract_prompt_and_image([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "描述图片"},
                    {"type": "image_url", "image_url": {"url": f"file:///{self.image_path.replace(os.sep, '/')}"}},
                ],
            }
        ])
        self.assertEqual(prompt, "描述图片")
        self.assertEqual(image, self.image_path)

    def test_rejects_multiple_images(self):
        with self.assertRaisesRegex(ValueError, "仅支持单张图片"):
            extract_prompt_and_image([
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "比较两张图"},
                        {"type": "image_url", "image_url": {"url": self.image_path}},
                        {"type": "image_url", "image_url": {"url": self.image_path}},
                    ],
                }
            ])

    def test_rejects_remote_image_url(self):
        with self.assertRaisesRegex(ValueError, "仅支持本地图片路径"):
            extract_prompt_and_image([
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "描述图片"},
                        {"type": "image_url", "image_url": {"url": "https://example.com/demo.png"}},
                    ],
                }
            ])


class MultimodalApiTests(unittest.TestCase):
    """验证图片参数能从 API 层透传到客户端。"""

    def setUp(self):
        fd, self.image_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        self.client = TestClient(app)

    def tearDown(self):
        if os.path.exists(self.image_path):
            os.remove(self.image_path)

    @patch("server.api.client.chat")
    def test_non_stream_request_passes_image_to_client(self, chat):
        chat.return_value = type("Reply", (), {"text": "ok", "conversation_id": "conv-1"})()
        response = self.client.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请描述图片"},
                        {"type": "image_url", "image_url": {"url": self.image_path}},
                    ],
                }
            ],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(chat.call_args.kwargs["image"], self.image_path)
        self.assertEqual(chat.call_args.args[0], "请描述图片")

    @patch("server.api.client.stream")
    def test_stream_request_passes_image_to_client(self, stream):
        stream.return_value = iter(["ok"])
        response = self.client.post("/v1/chat/completions", json={
            "model": "copilot",
            "stream": True,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请描述图片"},
                        {"type": "image_url", "image_url": {"url": self.image_path}},
                    ],
                }
            ],
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertEqual(stream.call_args.kwargs["image"], self.image_path)
        self.assertEqual(stream.call_args.args[0], "请描述图片")


if __name__ == "__main__":
    unittest.main()
