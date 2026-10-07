from __future__ import annotations

import re
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from .config import Settings
from .document import classify_document
from .io_utils import normalize_text
from .models import PageRecord, ParsedDocument, TableRecord
from .ocr import OCRProvider


TABLE_SIGNALS = (
    "主要财务数据",
    "主要会计数据",
    "非经常性损益",
    "资产负债表",
    "利润表",
    "现金流量表",
    "财务报表",
)


def text_quality(text: str) -> tuple[float, list[str]]:
    stripped = text.strip()
    reasons: list[str] = []
    if len(stripped) < 30:
        reasons.append("text_too_short")
    non_space = [ch for ch in stripped if not ch.isspace()]
    if not non_space:
        return 0.0, reasons or ["empty_text"]
    cjk = sum("\u4e00" <= ch <= "\u9fff" for ch in non_space)
    ascii_printable = sum(32 <= ord(ch) <= 126 for ch in non_space)
    chinese_punctuation = sum(ch in "，。；：！？（）《》【】、％￥“”‘’" for ch in non_space)
    replacement = stripped.count("\ufffd")
    unusual = len(non_space) - cjk - ascii_printable - chinese_punctuation
    good_ratio = (cjk + ascii_printable + chinese_punctuation) / len(non_space)
    unusual_ratio = max(0, unusual) / len(non_space)
    score = min(1.0, len(stripped) / 500) * 0.35 + good_ratio * 0.65
    if unusual_ratio > 0.25:
        reasons.append("unusual_unicode_ratio_high")
        score -= 0.35
    if replacement:
        reasons.append("replacement_characters")
        score -= min(0.3, replacement / max(1, len(stripped)))
    if cjk == 0 and any("\u4e00" <= ch <= "\u9fff" for ch in stripped) is False:
        reasons.append("no_chinese_text")
    return max(0.0, min(1.0, score)), reasons


def _render_page(pdf_path: Path, page_number: int, output_dir: Path, dpi: int) -> Path:
    executable = shutil.which("pdftoppm")
    if not executable:
        raise RuntimeError("pdftoppm is required for OCR page rendering but was not found")
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / f"page_{page_number:04d}"
    command = [
        executable,
        "-f",
        str(page_number),
        "-l",
        str(page_number),
        "-singlefile",
        "-r",
        str(dpi),
        "-png",
        str(pdf_path),
        str(prefix),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        raise RuntimeError(f"pdftoppm failed: {completed.stderr[:500]}")
    image_path = prefix.with_suffix(".png")
    if not image_path.exists():
        raise RuntimeError("pdftoppm did not create the expected image")
    return image_path


def _clean_table(table: list[list[object | None]]) -> list[list[str | None]]:
    cleaned: list[list[str | None]] = []
    for row in table:
        output_row: list[str | None] = []
        for cell in row:
            if cell is None:
                output_row.append(None)
            else:
                text = re.sub(r"[ \t]+", " ", str(cell)).strip()
                output_row.append(text or None)
        if any(cell is not None for cell in output_row):
            cleaned.append(output_row)
    return cleaned


def parse_pdf(
    path: Path,
    settings: Settings,
    temp_dir: Path,
    ocr_provider: OCRProvider | None = None,
    ocr_mode: str | None = None,
) -> ParsedDocument:
    path = path.resolve()
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Only PDF inputs are supported: {path}")
    reader = PdfReader(path)
    native_texts: list[str] = []
    page_records: list[PageRecord] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        score, reasons = text_quality(text)
        native_texts.append(text)
        page_records.append(
            PageRecord(
                page=index,
                text=text,
                method="pypdf",
                quality_score=score,
                needs_ocr=score < 0.55,
                quality_reasons=reasons,
            )
        )

    first_pages_text = "\n".join(native_texts[:5])
    meta = classify_document(path, len(reader.pages), first_pages_text)
    early_text = normalize_text("\n".join(native_texts[:20])).upper()
    if "人民币百万元" in early_text or "RMB’000,000" in early_text or "RMB'000,000" in early_text:
        meta.unit_scale = Decimal("1000000")
    elif "人民币千元" in early_text or "RMB’000" in early_text or "RMB'000" in early_text:
        meta.unit_scale = Decimal("1000")
    good_native_pages = sum(not page.needs_ocr for page in page_records)
    meta.native_text_ratio = good_native_pages / max(1, len(page_records))

    effective_mode = (ocr_mode or settings.ocr_mode).lower()
    if effective_mode not in {"auto", "never", "force"}:
        raise ValueError(f"Unsupported OCR mode: {effective_mode}")
    if effective_mode == "force":
        candidates = [page.page for page in page_records]
    elif effective_mode == "auto":
        candidates = [page.page for page in page_records if page.needs_ocr]
    else:
        candidates = []

    if len(candidates) > settings.ocr_max_pages_per_document:
        meta.limitations.append(
            f"OCR candidate pages ({len(candidates)}) exceeded the configured limit "
            f"({settings.ocr_max_pages_per_document}); only the first candidates were processed."
        )
        candidates = candidates[: settings.ocr_max_pages_per_document]

    if candidates and ocr_provider is None:
        meta.limitations.append(
            f"{len(candidates)} page(s) require OCR, but no OCR provider is configured."
        )
    elif ocr_provider is not None:
        by_page = {page.page: page for page in page_records}
        for page_number in candidates:
            image_path = _render_page(path, page_number, temp_dir / "ocr_pages", settings.ocr_dpi)
            recognized = ocr_provider.recognize(image_path)
            score, reasons = text_quality(recognized)
            record = by_page[page_number]
            record.text = recognized
            record.method = f"ocr:{ocr_provider.name}"
            record.quality_score = score
            record.needs_ocr = False
            record.quality_reasons = reasons
            meta.ocr_pages.append(page_number)

    candidate_pages = set(range(1, min(len(page_records), 15) + 1))
    for page in page_records:
        if any(signal in page.text for signal in TABLE_SIGNALS):
            candidate_pages.add(page.page)

    tables: list[TableRecord] = []
    with pdfplumber.open(path) as pdf:
        for page_number in sorted(candidate_pages):
            page = pdf.pages[page_number - 1]
            try:
                extracted = page.extract_tables()
            except Exception:
                extracted = []
            for table_index, table in enumerate(extracted):
                rows = _clean_table(table)
                if rows:
                    tables.append(
                        TableRecord(
                            page=page_number,
                            table_index=table_index,
                            rows=rows,
                        )
                    )

    if meta.report_type == "business_update":
        meta.limitations.append(
            "The document is a short business update rather than a complete quarterly financial report."
        )
    return ParsedDocument(meta=meta, pages=page_records, tables=tables)
