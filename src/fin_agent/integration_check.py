from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .deepseek import _post_json
from .io_utils import sha256_file, stable_hash, utc_now_iso, write_json
from .ocr import provider_from_settings
from .pdf_parser import _render_page


def check_deepseek(settings: Settings) -> dict[str, Any]:
    """Run a minimal, low-token JSON-mode request without exposing credentials."""
    started_at = utc_now_iso()
    if not settings.llm_configured():
        return {
            "provider": "deepseek",
            "status": "not_configured",
            "started_at": started_at,
            "message": "DEEPSEEK_API_KEY is missing",
        }

    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {
                "role": "system",
                "content": "Return only a valid JSON object. Do not add Markdown.",
            },
            {
                "role": "user",
                "content": 'Return this JSON object: {"status":"ok","check":"financial-agent"}',
            },
        ],
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": 128,
        "stream": False,
    }
    request_hash = stable_hash(payload)
    started = time.perf_counter()
    try:
        response = _post_json(
            f"{settings.deepseek_base_url}/chat/completions",
            payload,
            settings.deepseek_api_key,
            settings.deepseek_timeout_seconds,
        )
        content = response["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("JSON response is not an object")
        return {
            "provider": "deepseek",
            "status": "passed",
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "requested_model": settings.deepseek_model,
            "returned_model": response.get("model"),
            "request_hash": request_hash,
            "response_hash": stable_hash(response),
            "response_id": response.get("id"),
            "usage": response.get("usage"),
            "response_keys": sorted(parsed.keys()),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except Exception as exc:
        return {
            "provider": "deepseek",
            "status": "failed",
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "requested_model": settings.deepseek_model,
            "request_hash": request_hash,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
        }


def check_aistudio_ocr(
    settings: Settings,
    *,
    output_dir: Path,
    image_path: Path | None = None,
    pdf_path: Path | None = None,
    page_number: int = 1,
) -> dict[str, Any]:
    """Recognize one page/image and retain only auditable, non-secret output."""
    started_at = utc_now_iso()
    provider = provider_from_settings(settings)
    if provider is None:
        return {
            "provider": settings.ocr_provider,
            "status": "not_configured",
            "started_at": started_at,
            "message": "OCR provider URL/token configuration is incomplete",
        }

    try:
        source_path: Path
        with tempfile.TemporaryDirectory(prefix="fin_agent_ocr_check_") as temp_dir:
            if image_path is not None:
                source_path = image_path.resolve()
                if not source_path.is_file():
                    raise FileNotFoundError(f"OCR image does not exist: {source_path}")
                rendered = source_path
                source_kind = "image"
            elif pdf_path is not None:
                source_path = pdf_path.resolve()
                if not source_path.is_file():
                    raise FileNotFoundError(f"OCR PDF does not exist: {source_path}")
                if page_number < 1:
                    raise ValueError("page_number must be >= 1")
                rendered = _render_page(
                    source_path,
                    page_number,
                    Path(temp_dir),
                    settings.ocr_dpi,
                )
                source_kind = "pdf_page"
            else:
                raise ValueError("An image_path or pdf_path is required for OCR verification")

            text = provider.recognize(rendered)

        if not text.strip():
            raise ValueError("OCR returned empty text")
        output_dir.mkdir(parents=True, exist_ok=True)
        text_path = output_dir / "ocr_recognized_text.txt"
        text_path.write_text(text, encoding="utf-8")
        return {
            "provider": provider.name,
            "status": "passed",
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "source_kind": source_kind,
            "source_path": str(source_path),
            "source_sha256": sha256_file(source_path),
            "page": page_number if source_kind == "pdf_page" else None,
            "recognized_characters": len(text),
            "recognized_lines": len(text.splitlines()),
            "text_output": str(text_path),
            "preview": text[:500],
            "call_metadata": (
                getattr(provider, "call_records", [])[-1]
                if getattr(provider, "call_records", [])
                else None
            ),
        }
    except Exception as exc:
        return {
            "provider": settings.ocr_provider,
            "status": "failed",
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
        }


def write_integration_report(output_dir: Path, checks: list[dict[str, Any]]) -> Path:
    report = {
        "generated_at": utc_now_iso(),
        "all_passed": bool(checks) and all(item.get("status") == "passed" for item in checks),
        "checks": checks,
    }
    path = output_dir / "integration_check.json"
    write_json(path, report)
    return path
