from __future__ import annotations

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .io_utils import sha256_file, stable_hash, utc_now_iso


class OCRError(RuntimeError):
    pass


class OCRProvider(ABC):
    name = "base"

    def __init__(self) -> None:
        self.call_records: list[dict[str, Any]] = []

    @abstractmethod
    def recognize(self, image_path: Path) -> str:
        raise NotImplementedError


def _request(
    url: str,
    *,
    data: bytes,
    headers: dict[str, str],
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OCRError(f"OCR HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise OCRError(f"OCR network error: {exc.reason}") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OCRError(f"OCR returned non-JSON content: {payload[:500]}") from exc


def _extract_text_from_response(payload: Any) -> str:
    if isinstance(payload, str):
        stripped = payload.strip()
        if stripped.startswith(("{", "[")):
            try:
                return _extract_text_from_response(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        return payload
    if isinstance(payload, list):
        parts = [_extract_text_from_response(item) for item in payload]
        return "\n".join(part for part in parts if part)
    if not isinstance(payload, dict):
        return ""

    words_result = payload.get("words_result")
    if isinstance(words_result, list):
        return "\n".join(
            str(item.get("words", "")) for item in words_result if isinstance(item, dict)
        ).strip()

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message", {})
            if isinstance(message, dict) and message.get("content"):
                return str(message["content"])

    # PaddleX OCR commonly returns result.ocrResults[].prunedResult.rec_texts.
    # High-performance serving may wrap the same object as outputs[].data[]
    # where each data item is itself a serialized JSON string.
    for key in ("rec_texts", "recTexts"):
        values = payload.get(key)
        if isinstance(values, list):
            return "\n".join(str(value) for value in values if value not in (None, "")).strip()

    priority_keys = (
        "text",
        "markdown",
        "content",
        "output_text",
        "ocrResults",
        "prunedResult",
        "result",
        "outputs",
        "output",
        "data",
    )
    for key in priority_keys:
        if key in payload:
            text = _extract_text_from_response(payload[key])
            if text:
                return text
    return ""


class AIStudioOCR(OCRProvider):
    name = "aistudio"

    def __init__(
        self,
        url: str,
        access_token: str,
        timeout: int,
        request_style: str,
        auth_scheme: str = "token",
        model: str = "PaddleOCR-VL-1.6",
        poll_interval_seconds: float = 5.0,
        poll_timeout_seconds: int = 1800,
    ):
        super().__init__()
        self.url = url
        self.access_token = access_token
        self.timeout = timeout
        self.request_style = request_style
        self.auth_scheme = auth_scheme.lower()
        self.model = model
        self.poll_interval_seconds = max(0.0, poll_interval_seconds)
        self.poll_timeout_seconds = max(1, poll_timeout_seconds)

    def _headers(self, content_type: str | None = "application/json") -> dict[str, str]:
        headers: dict[str, str] = {}
        if content_type:
            headers["Content-Type"] = content_type
        if self.auth_scheme == "token":
            headers["Authorization"] = f"token {self.access_token}"
        elif self.auth_scheme == "bearer":
            headers["Authorization"] = f"bearer {self.access_token}"
        elif self.auth_scheme == "x-api-key":
            headers["X-API-Key"] = self.access_token
        elif self.auth_scheme != "none":
            raise OCRError(
                "Unsupported AISTUDIO_AUTH_SCHEME. Use token, bearer, x-api-key, or none."
            )
        return headers

    @staticmethod
    def _response_json(response: requests.Response, phase: str) -> dict[str, Any]:
        if response.status_code != 200:
            raise OCRError(
                f"AI Studio {phase} HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise OCRError(
                f"AI Studio {phase} returned non-JSON content: {response.text[:500]}"
            ) from exc
        if not isinstance(payload, dict):
            raise OCRError(f"AI Studio {phase} returned a non-object JSON response")
        return payload

    def _recognize_job(self, image_path: Path) -> tuple[str, str, dict[str, Any]]:
        optional_payload = {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
        headers = self._headers(content_type=None)
        data = {
            "model": self.model,
            "optionalPayload": json.dumps(optional_payload, ensure_ascii=False),
        }
        mime = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
        try:
            with image_path.open("rb") as stream:
                submission_response = requests.post(
                    self.url,
                    headers=headers,
                    data=data,
                    files={"file": (image_path.name, stream, mime)},
                    timeout=self.timeout,
                )
        except requests.RequestException as exc:
            raise OCRError(f"AI Studio job submission network error: {exc}") from exc
        submission = self._response_json(submission_response, "job submission")
        try:
            job_id = str(submission["data"]["jobId"])
        except (KeyError, TypeError) as exc:
            raise OCRError(f"AI Studio job response did not contain data.jobId: {submission}") from exc

        deadline = time.monotonic() + self.poll_timeout_seconds
        poll_count = 0
        final_status: dict[str, Any] | None = None
        while time.monotonic() <= deadline:
            poll_count += 1
            try:
                poll_response = requests.get(
                    f"{self.url.rstrip('/')}/{urllib.parse.quote(job_id, safe='')}",
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise OCRError(f"AI Studio job polling network error: {exc}") from exc
            status_payload = self._response_json(poll_response, "job polling")
            try:
                state = str(status_payload["data"]["state"]).lower()
            except (KeyError, TypeError) as exc:
                raise OCRError(
                    f"AI Studio polling response did not contain data.state: {status_payload}"
                ) from exc
            if state == "done":
                final_status = status_payload
                break
            if state == "failed":
                error_msg = status_payload.get("data", {}).get("errorMsg", "unknown error")
                raise OCRError(f"AI Studio OCR job failed: {error_msg}")
            if state not in {"pending", "running"}:
                raise OCRError(f"AI Studio OCR job returned unknown state: {state}")
            if self.poll_interval_seconds:
                time.sleep(self.poll_interval_seconds)
        if final_status is None:
            raise OCRError(
                f"AI Studio OCR job timed out after {self.poll_timeout_seconds} seconds"
            )

        try:
            jsonl_url = str(final_status["data"]["resultUrl"]["jsonUrl"])
        except (KeyError, TypeError) as exc:
            raise OCRError(
                f"AI Studio completed job did not contain data.resultUrl.jsonUrl: {final_status}"
            ) from exc
        try:
            result_response = requests.get(jsonl_url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise OCRError(f"AI Studio result download network error: {exc}") from exc
        if result_response.status_code != 200:
            raise OCRError(
                f"AI Studio result download HTTP {result_response.status_code}: "
                f"{result_response.text[:500]}"
            )

        markdown_parts: list[str] = []
        parsed_lines: list[dict[str, Any]] = []
        for line_number, raw_line in enumerate(result_response.text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OCRError(
                    f"AI Studio JSONL result has invalid JSON at line {line_number}"
                ) from exc
            if not isinstance(item, dict):
                continue
            parsed_lines.append(item)
            results = item.get("result", {}).get("layoutParsingResults", [])
            if not isinstance(results, list):
                continue
            for result in results:
                if not isinstance(result, dict):
                    continue
                markdown = result.get("markdown", {})
                if isinstance(markdown, dict) and markdown.get("text"):
                    markdown_parts.append(str(markdown["text"]).strip())

        text = "\n\n".join(part for part in markdown_parts if part).strip()
        if not text:
            text = _extract_text_from_response(parsed_lines).strip()
        if not text:
            raise OCRError("AI Studio JSONL result did not contain Markdown or OCR text")
        response_fingerprint = {
            "submission": submission,
            "final_status": final_status,
            "jsonl_hash": stable_hash(result_response.text),
        }
        metadata = {
            "job_id": job_id,
            "model": self.model,
            "poll_count": poll_count,
            "final_state": "done",
            "result_lines": len(parsed_lines),
        }
        return text, stable_hash(response_fingerprint), metadata

    def recognize(self, image_path: Path) -> str:
        input_sha256 = sha256_file(image_path)
        if self.request_style == "paddleocr_job":
            payload_fingerprint = {
                "input_sha256": input_sha256,
                "model": self.model,
                "optionalPayload": {
                    "useDocOrientationClassify": False,
                    "useDocUnwarping": False,
                    "useChartRecognition": False,
                },
            }
            payload: dict[str, Any] | None = None
        else:
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            if self.request_style == "paddlex":
                payload = {
                    "file": encoded,
                    "fileType": 1,
                    "useDocOrientationClassify": False,
                    "useDocUnwarping": False,
                    "useTextlineOrientation": True,
                }
            else:
                mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
                payload = {
                    "image": f"data:{mime};base64,{encoded}",
                    "task": "ocr",
                    "prompt": "请识别页面中的全部文字和表格，保留阅读顺序与数值。",
                }
            payload_fingerprint = payload
        request_hash = stable_hash(payload_fingerprint)
        started_at = utc_now_iso()
        started = time.perf_counter()
        record: dict[str, Any] = {
            "provider": self.name,
            "status": "running",
            "started_at": started_at,
            "input_sha256": input_sha256,
            "request_hash": request_hash,
            "request_style": self.request_style,
            "auth_scheme": self.auth_scheme,
        }
        try:
            if self.request_style == "paddleocr_job":
                text, response_hash, job_metadata = self._recognize_job(image_path)
                record.update(job_metadata)
            else:
                assert payload is not None
                response = _request(
                    self.url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                text = _extract_text_from_response(response)
                response_hash = stable_hash(response)
            if not text:
                raise OCRError(
                    "AI Studio response did not contain recognizable text. "
                    "Please provide the deployment page's curl/Python example so the adapter can be aligned."
                )
            record.update(
                {
                    "status": "passed",
                    "response_hash": response_hash,
                    "recognized_characters": len(text),
                }
            )
            return text
        except Exception as exc:
            record.update(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )
            raise
        finally:
            record["finished_at"] = utc_now_iso()
            record["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.call_records.append(record)


class BaiduCloudOCR(OCRProvider):
    name = "baidu_cloud"

    def __init__(self, api_key: str, secret_key: str, endpoint: str, timeout: int):
        super().__init__()
        self.api_key = api_key
        self.secret_key = secret_key
        self.endpoint = endpoint
        self.timeout = timeout
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        token_url = "https://aip.baidubce.com/oauth/2.0/token"
        form = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.api_key,
                "client_secret": self.secret_key,
            }
        ).encode("ascii")
        response = _request(
            token_url,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        token = response.get("access_token")
        if not token:
            raise OCRError(f"Unable to obtain Baidu access token: {response}")
        expires_in = int(response.get("expires_in", 2592000))
        self._access_token = str(token)
        self._token_expires_at = time.time() + max(60, expires_in - 300)
        return self._access_token

    def recognize(self, image_path: Path) -> str:
        token = self._get_access_token()
        separator = "&" if "?" in self.endpoint else "?"
        url = f"{self.endpoint}{separator}access_token={urllib.parse.quote(token)}"
        form = urllib.parse.urlencode(
            {
                "image": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                "detect_direction": "true",
                "paragraph": "true",
            }
        ).encode("ascii")
        request_fingerprint = {
            "image_sha256": sha256_file(image_path),
            "detect_direction": True,
            "paragraph": True,
        }
        started = time.perf_counter()
        record: dict[str, Any] = {
            "provider": self.name,
            "status": "running",
            "started_at": utc_now_iso(),
            "input_sha256": request_fingerprint["image_sha256"],
            "request_hash": stable_hash(request_fingerprint),
        }
        try:
            response = _request(
                url,
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
            if "error_code" in response:
                raise OCRError(f"Baidu OCR error: {response}")
            text = _extract_text_from_response(response)
            if not text:
                raise OCRError("Baidu OCR returned an empty recognition result")
            record.update(
                {
                    "status": "passed",
                    "response_hash": stable_hash(response),
                    "recognized_characters": len(text),
                }
            )
            return text
        except Exception as exc:
            record.update(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )
            raise
        finally:
            record["finished_at"] = utc_now_iso()
            record["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.call_records.append(record)


def provider_from_settings(settings: Settings) -> OCRProvider | None:
    if settings.ocr_provider == "aistudio" and settings.ocr_configured():
        return AIStudioOCR(
            settings.aistudio_api_url,
            settings.aistudio_access_token,
            settings.aistudio_timeout_seconds,
            settings.aistudio_request_style,
            settings.aistudio_auth_scheme,
            settings.aistudio_model,
            settings.aistudio_poll_interval_seconds,
            settings.aistudio_poll_timeout_seconds,
        )
    if settings.ocr_provider == "baidu_cloud" and settings.ocr_configured():
        return BaiduCloudOCR(
            settings.baidu_ocr_api_key,
            settings.baidu_ocr_secret_key,
            settings.baidu_ocr_endpoint,
            settings.baidu_ocr_timeout_seconds,
        )
    return None
