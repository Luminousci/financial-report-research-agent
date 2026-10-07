from __future__ import annotations

import re

from .models import NarrativeEvidence, ParsedDocument, SourceRef


QUERY_TERMS: dict[str, tuple[str, ...]] = {
    "performance_drivers": ("增长", "下降", "变动原因", "主要原因", "销量", "价格", "毛利率", "收入", "利润"),
    "cash_flow": ("经营活动现金流", "现金流量", "应收", "存货", "合同负债", "回款", "结算"),
    "nonrecurring": ("非经常性损益", "政府补助", "公允价值", "资产处置", "一次性"),
    "accounting_policy": ("会计政策", "会计估计", "口径", "追溯调整", "重述", "准则变化"),
    "risk": ("风险", "不确定性", "减值", "诉讼", "担保", "关联交易", "持续经营"),
}


def _chunks(text: str, size: int = 520, overlap: int = 80) -> list[str]:
    cleaned = re.sub(r"[ \t]+", " ", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[。！？；])|\n\n+", cleaned) if part.strip()]
    result: list[str] = []
    current = ""
    expanded_parts: list[str] = []
    for part in parts:
        if len(part) <= size:
            expanded_parts.append(part)
            continue
        start = 0
        while start < len(part):
            expanded_parts.append(part[start:start + size])
            if start + size >= len(part):
                break
            start += size - overlap
    for part in expanded_parts:
        if len(current) + len(part) + 1 <= size:
            current = f"{current}\n{part}".strip()
            continue
        if current:
            result.append(current)
        current = (current[-overlap:] + "\n" + part).strip() if current else part
    if current:
        result.append(current)
    return result


def retrieve_narrative_evidence(parsed: ParsedDocument, per_category: int = 3) -> list[NarrativeEvidence]:
    candidates: list[NarrativeEvidence] = []
    for page in parsed.pages:
        for chunk in _chunks(page.text):
            compact = chunk.replace(" ", "")
            for category, terms in QUERY_TERMS.items():
                hits = sum(compact.count(term) for term in terms)
                distinct = sum(1 for term in terms if term in compact)
                if not hits:
                    continue
                score = round(distinct * 2.0 + hits * 0.35 + min(len(chunk), 520) / 5200, 4)
                candidates.append(
                    NarrativeEvidence(
                        category=category,
                        text=chunk,
                        score=score,
                        source=SourceRef(
                            document_id=parsed.meta.document_id,
                            file_name=parsed.meta.file_name,
                            page=page.page,
                            evidence_text=chunk,
                            extraction_method=page.method,
                            confidence=max(0.5, page.quality_score),
                        ),
                    )
                )
    selected: list[NarrativeEvidence] = []
    for category in QUERY_TERMS:
        category_items = sorted(
            (item for item in candidates if item.category == category),
            key=lambda item: (-item.score, item.source.page, item.text),
        )
        seen: set[tuple[int, str]] = set()
        for item in category_items:
            key = (item.source.page, item.text[:120])
            if key in seen:
                continue
            seen.add(key)
            selected.append(item)
            if len(seen) >= per_category:
                break
    return selected
