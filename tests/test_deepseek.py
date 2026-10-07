import json
import unittest
from unittest.mock import patch

from fin_agent.deepseek import DeepSeekClient, _post_json


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps({"id": "response-1", "choices": []}).encode("utf-8")


class DeepSeekTransportTest(unittest.TestCase):
    def test_json_parser_accepts_fenced_object(self):
        parsed = DeepSeekClient._parse_json_content('```json\n{"summary_text":"ok"}\n```')
        self.assertEqual(parsed, {"summary_text": "ok"})

    def test_openai_compatible_request(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse()

        payload = {
            "model": "deepseek-flash",
            "messages": [{"role": "user", "content": "test"}],
            "response_format": {"type": "json_object"},
        }
        with patch("fin_agent.deepseek.urllib.request.urlopen", side_effect=fake_urlopen):
            response = _post_json(
                "https://api.deepseek.com/chat/completions", payload, "secret", 90
            )

        request = captured["request"]
        self.assertEqual(response["id"], "response-1")
        self.assertEqual(captured["timeout"], 90)
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(json.loads(request.data.decode("utf-8")), payload)


if __name__ == "__main__":
    unittest.main()
