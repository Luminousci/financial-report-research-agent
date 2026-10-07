import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fin_agent.ocr import AIStudioOCR, OCRError, _extract_text_from_response


class _FakeResponse:
    def __init__(self, payload: dict):
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


class _FakeRequestsResponse:
    def __init__(self, payload=None, *, text=None, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self.payload


class OCRResponseTest(unittest.TestCase):
    def test_extracts_paddlex_ocr_results(self):
        payload = {
            "result": {
                "ocrResults": [
                    {"prunedResult": {"rec_texts": ["营业收入", "123.45亿元"]}}
                ]
            }
        }
        self.assertEqual(_extract_text_from_response(payload), "营业收入\n123.45亿元")

    def test_extracts_high_performance_serving_json_string(self):
        inner = {
            "errorCode": 0,
            "result": {
                "ocrResults": [
                    {"prunedResult": {"rec_texts": ["归母净利润", "42.00亿元"]}}
                ]
            },
        }
        payload = {"outputs": [{"data": [json.dumps(inner, ensure_ascii=False)]}]}
        self.assertEqual(_extract_text_from_response(payload), "归母净利润\n42.00亿元")


class AIStudioRequestTest(unittest.TestCase):
    def _run_request(self, auth_scheme: str):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            response = {
                "result": {
                    "ocrResults": [
                        {"prunedResult": {"rec_texts": ["测试文本"]}}
                    ]
                }
            }
            return _FakeResponse(response)

        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "page.png"
            image.write_bytes(b"fake-png")
            client = AIStudioOCR(
                "https://example.invalid/ocr",
                "secret-token",
                45,
                "paddlex",
                auth_scheme,
            )
            with patch("fin_agent.ocr.urllib.request.urlopen", side_effect=fake_urlopen):
                text = client.recognize(image)
            captured["call_records"] = client.call_records
        return text, captured

    def test_paddlex_request_and_token_authorization(self):
        text, captured = self._run_request("token")
        request = captured["request"]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(text, "测试文本")
        self.assertEqual(captured["timeout"], 45)
        self.assertEqual(request.get_header("Authorization"), "token secret-token")
        self.assertEqual(body["fileType"], 1)
        self.assertEqual(body["file"], "ZmFrZS1wbmc=")
        self.assertFalse(body["useDocOrientationClassify"])
        record = captured["call_records"][0]
        self.assertEqual(record["status"], "passed")
        self.assertEqual(len(record["request_hash"]), 64)
        self.assertEqual(len(record["response_hash"]), 64)
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertNotIn("secret-token", json.dumps(record))

    def test_bearer_and_x_api_key_authorization(self):
        _, bearer = self._run_request("bearer")
        _, api_key = self._run_request("x-api-key")
        self.assertEqual(
            bearer["request"].get_header("Authorization"), "bearer secret-token"
        )
        self.assertEqual(api_key["request"].get_header("X-api-key"), "secret-token")

    def test_rejects_unknown_authorization_scheme(self):
        client = AIStudioOCR("https://example.invalid", "x", 10, "paddlex", "custom")
        with self.assertRaises(OCRError):
            client._headers()

    def test_paddleocr_job_submission_polling_and_jsonl(self):
        captured = {"polls": 0}
        submission = _FakeRequestsResponse({"data": {"jobId": "job-123"}})
        running = _FakeRequestsResponse(
            {"data": {"state": "running", "extractProgress": {"totalPages": 1, "extractedPages": 0}}}
        )
        done = _FakeRequestsResponse(
            {
                "data": {
                    "state": "done",
                    "extractProgress": {"totalPages": 1, "extractedPages": 1},
                    "resultUrl": {"jsonUrl": "https://result.invalid/result.jsonl"},
                }
            }
        )
        jsonl = json.dumps(
            {
                "result": {
                    "layoutParsingResults": [
                        {"markdown": {"text": "# 财务报告\n营业收入 123.45亿元"}}
                    ]
                }
            },
            ensure_ascii=False,
        )

        def fake_post(url, **kwargs):
            captured["post_url"] = url
            captured["post_kwargs"] = kwargs
            return submission

        def fake_get(url, **kwargs):
            if url.endswith("result.jsonl"):
                return _FakeRequestsResponse(text=jsonl)
            captured["polls"] += 1
            return running if captured["polls"] == 1 else done

        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "page.png"
            image.write_bytes(b"fake-png")
            client = AIStudioOCR(
                "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
                "secret-token",
                30,
                "paddleocr_job",
                "bearer",
                "PaddleOCR-VL-1.6",
                0,
                60,
            )
            with patch("fin_agent.ocr.requests.post", side_effect=fake_post), patch(
                "fin_agent.ocr.requests.get", side_effect=fake_get
            ):
                text = client.recognize(image)

        self.assertIn("营业收入 123.45亿元", text)
        self.assertEqual(captured["polls"], 2)
        self.assertEqual(captured["post_kwargs"]["headers"]["Authorization"], "bearer secret-token")
        self.assertEqual(captured["post_kwargs"]["data"]["model"], "PaddleOCR-VL-1.6")
        optional = json.loads(captured["post_kwargs"]["data"]["optionalPayload"])
        self.assertFalse(optional["useChartRecognition"])
        self.assertEqual(captured["post_kwargs"]["files"]["file"][0], "page.png")
        record = client.call_records[0]
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["job_id"], "job-123")
        self.assertEqual(record["poll_count"], 2)
        self.assertEqual(record["model"], "PaddleOCR-VL-1.6")
        self.assertEqual(len(record["response_hash"]), 64)
        self.assertNotIn("secret-token", json.dumps(record))


if __name__ == "__main__":
    unittest.main()
