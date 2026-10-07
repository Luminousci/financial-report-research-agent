from __future__ import annotations

import re
from decimal import Decimal

from .models import BusinessMetric, Finding, ParsedDocument, SourceRef


METRIC_PATTERNS = (
    ("overall_revenue_yoy", "整体收益同比", "整体收益", r"(?:整體|整体)(?:收益|收入)"),
    ("china_revenue_yoy", "中国收益同比", "中国", r"(?:中國|中国)(?:收益|收入)"),
    ("overseas_revenue_yoy", "海外收益同比", "海外", r"海外.{0,28}?(?:收益|收入)"),
    ("offline_channel_yoy", "线下渠道收益同比", "线下渠道", r"(?:線下|线下)渠道"),
    ("online_channel_yoy", "线上渠道收益同比", "线上渠道", r"(?:線上|线上)渠道"),
    ("apac_revenue_yoy", "亚太收益同比", "亚太", r"(?:亞太|亚太)"),
    ("americas_revenue_yoy", "美洲收益同比", "美洲", r"美洲"),
    ("europe_other_revenue_yoy", "欧洲及其他地区收益同比", "欧洲及其他", r"(?:歐洲|欧洲)及其他地區|欧洲及其他地区"),
)

_RANGE = (
    r".{0,90}?同比(?:增長|增长|上升|上涨)\s*"
    r"(?P<lower>[+-]?[\d,]+(?:\.\d+)?)\s*%\s*"
    r"(?:-|\u2013|\u2014|~|～|至|到)\s*"
    r"(?P<upper>[+-]?[\d,]+(?:\.\d+)?)\s*%"
)


def _period_label(parsed: ParsedDocument, text: str) -> str:
    prefix = f"FY{parsed.meta.report_year}" if parsed.meta.report_year else "FY_UNKNOWN"
    if "第三季度" in text:
        return f"{prefix}_Q3"
    if "第一季度" in text:
        return f"{prefix}_Q1"
    if "上半年" in text or "半年" in text:
        return f"{prefix}_H1"
    return f"{prefix}_BUSINESS_UPDATE"


def _excerpt(text: str, start: int, end: int) -> str:
    left = max(text.rfind(mark, 0, start) for mark in ("\n", "。", "；")) + 1
    candidates = [position for mark in ("\n", "。", "；") if (position := text.find(mark, end)) >= 0]
    right = min(candidates) + 1 if candidates else min(len(text), end + 100)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def extract_business_metrics(parsed: ParsedDocument) -> list[BusinessMetric]:
    if parsed.meta.report_type != "business_update":
        return []
    metrics: list[BusinessMetric] = []
    seen: set[str] = set()
    all_text = "\n".join(page.text for page in parsed.pages)
    period = _period_label(parsed, all_text)
    for page in parsed.pages:
        for code, name, scope, label_pattern in METRIC_PATTERNS:
            if code in seen:
                continue
            match = re.search(f"(?:{label_pattern})" + _RANGE, page.text, flags=re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            lower = Decimal(match.group("lower").replace(",", ""))
            upper = Decimal(match.group("upper").replace(",", ""))
            if lower > upper:
                lower, upper = upper, lower
            evidence = _excerpt(page.text, match.start(), match.end())
            metrics.append(
                BusinessMetric(
                    metric_code=code,
                    metric_name=name,
                    lower_value=lower,
                    upper_value=upper,
                    unit="%",
                    comparison_kind="yoy_pct",
                    period_label=period,
                    scope=scope,
                    source=SourceRef(
                        document_id=parsed.meta.document_id,
                        file_name=parsed.meta.file_name,
                        page=page.page,
                        raw_label=scope,
                        raw_value=f"{lower}-{upper}%",
                        evidence_text=evidence,
                        extraction_method=page.method,
                        confidence=0.95,
                    ),
                )
            )
            seen.add(code)
    return metrics


def detect_business_update_signals(metrics: list[BusinessMetric]) -> list[Finding]:
    findings: list[Finding] = []
    negative = [item for item in metrics if item.lower_value < 0]
    for item in negative:
        findings.append(
            Finding(
                rule_id="business_metric_negative_growth",
                title=f"{item.scope}同比下降",
                severity="high" if item.upper_value < 0 else "medium",
                classification="fact",
                description=f"{item.period_label}{item.metric_name}披露区间为{item.display_value}。",
                evidence_refs=[item.source.to_dict()],
                counter_evidence="业务更新数据未经审计，需结合后续定期报告复核。",
                applicable_scope="business_update",
            )
        )
    for prefix, title in (("channel", "渠道增速分化显著"), ("region", "区域增速分化显著")):
        if prefix == "channel":
            group = [item for item in metrics if "channel" in item.metric_code]
        else:
            group = [item for item in metrics if item.metric_code in {"apac_revenue_yoy", "americas_revenue_yoy", "europe_other_revenue_yoy"}]
        if len(group) >= 2:
            spread = max(item.upper_value for item in group) - min(item.lower_value for item in group)
            if spread >= Decimal("100"):
                findings.append(
                    Finding(
                        rule_id=f"business_{prefix}_growth_dispersion",
                        title=title,
                        severity="medium",
                        classification="calculation",
                        description=f"各{'渠道' if prefix == 'channel' else '区域'}披露的同比增速区间跨度约为{spread}个百分点。",
                        evidence_refs=[item.source.to_dict() for item in group],
                        counter_evidence="高增速可能受低基数、渠道扩张和区域口径差异影响，需用绝对收入及利润率复核。",
                        applicable_scope="business_update",
                    )
                )
    return findings
