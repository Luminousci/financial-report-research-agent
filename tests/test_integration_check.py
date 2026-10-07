import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fin_agent.integration_check import (
    check_aistudio_ocr,
    check_deepseek,
    write_integration_report,
)


class _FakeOCR:
    name = "aistudio"

    def recognize(self, image_path):
        return "营业收入\n123.45亿元"


class IntegrationCheckTests(unittest.TestCase):
    def test_deepseek_check_keeps_only_auditable_metadata(self):
        settings = SimpleNamespace(
            llm_configured=lambda: True,
            deepseek_model="deepseek-flash",
            deepseek_base_url="https://api.deepseek.com",
            deepseek_api_key="secret",
            deepseek_timeout_seconds=30,
        )
        response = {
            "id": "response-1",
            "model": "deepseek-flash",
            "usage": {"total_tokens": 12},
            "choices": [
                {"message": {"content": '{"status":"ok","check":"financial-agent"}'}}
            ],
        }
        with patch("fin_agent.integration_check._post_json", return_value=response):
            result = check_deepseek(settings)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["response_id"], "response-1")
        self.assertEqual(len(result["response_hash"]), 64)
        self.assertGreaterEqual(result["duration_ms"], 0)
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("content", result)

    def test_ocr_check_writes_recognized_text(self):
        settings = SimpleNamespace(ocr_provider="aistudio", ocr_dpi=100)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "page.png"
            image.write_bytes(b"image")
            with patch(
                "fin_agent.integration_check.provider_from_settings",
                return_value=_FakeOCR(),
            ):
                result = check_aistudio_ocr(
                    settings,
                    output_dir=root / "output",
                    image_path=image,
                )
            self.assertEqual(result["status"], "passed")
            self.assertEqual(
                Path(result["text_output"]).read_text(encoding="utf-8"),
                "营业收入\n123.45亿元",
            )

    def test_report_requires_every_requested_check_to_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_integration_report(
                Path(temp_dir),
                [{"status": "passed"}, {"status": "not_configured"}],
            )
            report = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(report["all_passed"])


if __name__ == "__main__":
    unittest.main()
