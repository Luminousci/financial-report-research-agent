from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from .io_utils import normalize_text, sha256_file
from .models import DocumentMeta


COMPANY_PATTERNS = [
    ("贵州茅台酒股份有限公司", "600519", ["贵州茅台", "茅台"]),
    ("中国工商银行股份有限公司", "601398", ["工商银行", "工行"]),
    ("赛力斯集团股份有限公司", "601127", ["赛力斯"]),
    ("珠海格力电器股份有限公司", "000651", ["格力电器"]),
    ("内蒙古伊利实业集团股份有限公司", "600887", ["伊利股份", "伊利实业"]),
    ("泡泡玛特国际集团有限公司", "09992", ["泡泡玛特", "POPMART"]),
]

CODE_PATTERN = re.compile(
    r"(?:证券代码|股票代码|公司代码|证券代号|股票代号|股份代号|股份代码|Stock\s*Code|Share\s*Code)"
    r"\s*[:：]?\s*([0-9]{5,6})(?!\d)",
    re.IGNORECASE,
)
FILE_CODE_PATTERN = re.compile(r"(?:^|[^0-9])([036]\d{5}|0\d{4})(?!\d)")


def company_short_name(company_name: str | None) -> str | None:
    if not company_name:
        return None
    for company, _code, aliases in COMPANY_PATTERNS:
        if company_name == company:
            return aliases[0]
    short_name = company_name.strip()
    for suffix in ("股份有限公司", "集团有限公司", "有限责任公司", "有限公司"):
        if short_name.endswith(suffix):
            short_name = short_name[: -len(suffix)]
            break
    return short_name.strip() or company_name


def _detect_company(text: str, file_name: str) -> tuple[str | None, str | None]:
    combined = text + "\n" + file_name
    normalized_name = normalize_text(file_name).upper()
    for company, code, aliases in COMPANY_PATTERNS:
        if any(normalize_text(alias).upper() in normalized_name for alias in aliases):
            return company, code
    normalized_text = normalize_text(text[:5000]).upper()
    for company, code, aliases in COMPANY_PATTERNS:
        names = [company, *aliases]
        if any(normalize_text(name).upper() in normalized_text for name in names):
            return company, code
    code_match = CODE_PATTERN.search(combined)
    if code_match is None:
        code_match = FILE_CODE_PATTERN.search(file_name)
    company_match = None
    labelled_company_patterns = (
        r"(?:公司全称|公司名称|中文名称)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z·（）()]{2,40}(?:股份有限公司|集团有限公司|有限责任公司|有限公司))",
        r"([\u4e00-\u9fffA-Za-z·（）()]{2,40}(?:股份有限公司|集团有限公司|有限责任公司|有限公司))\s*(?:20\d{2}\s*年)?(?:年度报告|半年度报告|第一季度报告|第三季度报告)",
    )
    for pattern in labelled_company_patterns:
        company_match = re.search(pattern, combined, re.IGNORECASE)
        if company_match:
            break
    if company_match is None:
        company_match = re.search(
            r"([\u4e00-\u9fff]{4,30}(?:股份有限公司|集团有限公司|有限责任公司|有限公司))",
            combined,
        )
    short_name_match = re.search(
        r"(?:证券简称|股票简称|公司简称)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z·]{2,20})",
        combined,
        re.IGNORECASE,
    )
    detected_company = company_match.group(1) if company_match else None
    if detected_company is None and short_name_match:
        detected_company = short_name_match.group(1)
    return (
        detected_company,
        code_match.group(1) if code_match else None,
    )


def infer_report_identity(page_texts: list[str], file_name: str) -> dict[str, object]:
    """Infer naming metadata from the first PDF pages without persisting the file."""

    text = "\n".join(page_texts)
    combined = f"{file_name}\n{text}"
    compact = normalize_text(combined)
    company, code = _detect_company(text, file_name)
    short_name = company_short_name(company)

    title_year = re.search(
        r"(20\d{2})\s*年?\s*(?:年度报告|年报|半年度报告|半年报|中期报告|第一季度报告|第三季度报告)",
        combined,
    )
    any_year = re.search(r"(20\d{2})", combined)
    report_year = int((title_year or any_year).group(1)) if (title_year or any_year) else None

    if "第一季度" in compact:
        report_type = "q1"
        period_suffix = "Q1"
    elif "第三季度" in compact:
        report_type = "q3"
        period_suffix = "Q3"
    elif "半年度" in compact or "中期报告" in compact or "半年报" in compact:
        report_type = "semiannual"
        period_suffix = "H1"
    elif "年度报告" in compact or re.search(r"20\d{2}年报", compact):
        report_type = "annual"
        period_suffix = "FY"
    else:
        report_type = "other"
        period_suffix = None

    report_period = f"{report_year}{period_suffix}" if report_year and period_suffix else ""
    normalized_file_name = normalize_text(file_name).upper()
    page_sources: set[int] = set()
    source_from_filename = False
    evidence_terms = [value for value in (company, short_name, code) if value]
    for _company, _code, aliases in COMPANY_PATTERNS:
        if company == _company:
            evidence_terms.extend(aliases)
            break
    for page_number, page_text in enumerate(page_texts, start=1):
        normalized_page = normalize_text(page_text).upper()
        if any(normalize_text(term).upper() in normalized_page for term in evidence_terms):
            page_sources.add(page_number)
        if report_year and str(report_year) in normalized_page and any(
            signal in normalized_page for signal in ("年度报告", "半年度", "季度", "中期报告")
        ):
            page_sources.add(page_number)
    if any(normalize_text(term).upper() in normalized_file_name for term in evidence_terms):
        source_from_filename = True

    text_character_count = sum(len(normalize_text(page)) for page in page_texts)
    requires_ocr = text_character_count < 20
    completed_fields = sum(bool(value) for value in (code, short_name, report_period))
    if completed_fields == 3 and page_sources:
        confidence = 96
    elif completed_fields == 3:
        confidence = 86
    elif completed_fields == 2:
        confidence = 72
    elif completed_fields == 1:
        confidence = 48
    else:
        confidence = 20 if requires_ocr else 30
    if requires_ocr:
        confidence = min(confidence, 58)

    source_parts: list[str] = []
    if page_sources:
        source_parts.append("PDF 第" + "、".join(str(page) for page in sorted(page_sources)) + "页")
    if source_from_filename:
        source_parts.append("文件名辅助")
    if not source_parts:
        source_parts.append("PDF 前三页未定位到明确字段")

    return {
        "security_code": code or "",
        "company_name": short_name or "",
        "company_full_name": company or "",
        "report_year": report_year,
        "report_period": report_period,
        "report_type": report_type,
        "confidence": confidence,
        "source_pages": sorted(page_sources),
        "source": "；".join(source_parts),
        "requires_ocr": requires_ocr,
        "text_character_count": text_character_count,
    }


def classify_document(path: Path, page_count: int, first_pages_text: str) -> DocumentMeta:
    name = path.name
    combined = name + "\n" + first_pages_text
    compact = normalize_text(combined)
    company, code = _detect_company(first_pages_text, str(path))

    year_match = re.search(r"20\d{2}", combined)
    report_year = int(year_match.group(0)) if year_match else None

    if "第一季度" in compact:
        report_type = "q1"
    elif "第三季度" in compact:
        report_type = "q3"
    elif "半年度" in compact or "中期报告" in compact or "半年报" in compact:
        report_type = "semiannual"
    elif "年度报告" in compact or re.search(r"20\d{2}年报", compact):
        report_type = "annual"
    else:
        report_type = "unknown"

    has_statement_signals = any(
        signal in compact
        for signal in ("主要财务数据", "主要会计数据", "资产负债表", "利润表", "现金流量表")
    )
    if report_type in {"q1", "q3"} and page_count <= 3 and not has_statement_signals:
        report_type = "business_update"

    industry_profile = (
        "bank"
        if (company and "银行" in company) or "银行" in normalize_text(name)
        else "non_financial"
    )
    if code and len(code) == 6 and code[0] in {"0", "3", "6"}:
        accounting_standard = "CAS"
    else:
        accounting_standard = (
            "IFRS"
            if any(
                signal in compact
                for signal in ("国际财务报告准则", "香港联合交易所", "POPMART", "泡泡玛特")
            )
            else "CAS"
        )

    currency = "HKD" if "港元" in compact and "人民币" not in compact[:3000] else "CNY"
    unit_scale = Decimal("1")
    if "人民币百万元" in compact or "单位:百万元" in compact:
        unit_scale = Decimal("1000000")
    elif "单位:万元" in compact:
        unit_scale = Decimal("10000")

    digest = sha256_file(path)
    return DocumentMeta(
        document_id=digest[:16],
        path=str(path.resolve()),
        file_name=path.name,
        sha256=digest,
        page_count=page_count,
        company_name=company,
        security_code=code,
        report_year=report_year,
        report_type=report_type,
        industry_profile=industry_profile,
        accounting_standard=accounting_standard,
        currency=currency,
        unit_scale=unit_scale,
    )
