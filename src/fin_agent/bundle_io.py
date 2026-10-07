from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import (
    AnalysisBundle,
    BusinessMetric,
    CalculatedMetric,
    DocumentMeta,
    ExternalEvidence,
    FinancialFact,
    Finding,
    HealthAssessment,
    HealthComponent,
    NarrativeEvidence,
    NonRecurringItem,
    PageRecord,
    ParsedDocument,
    SourceRef,
    TableRecord,
    ValidationIssue,
)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _source(value: dict[str, Any] | None) -> SourceRef | None:
    return SourceRef(**value) if value is not None else None


def analysis_bundle_from_dict(data: dict[str, Any]) -> AnalysisBundle:
    """Rehydrate a persisted analysis bundle without rerunning the pipeline."""

    document_data = dict(data["document"])
    document_data["unit_scale"] = _decimal(document_data["unit_scale"])
    document = DocumentMeta(**document_data)

    facts: list[FinancialFact] = []
    for raw in data.get("facts", []):
        item = dict(raw)
        item["value"] = _decimal(item["value"])
        item["unit_scale"] = _decimal(item["unit_scale"])
        item["source"] = _source(item.get("source"))
        facts.append(FinancialFact(**item))

    calculations: list[CalculatedMetric] = []
    for raw in data.get("calculations", []):
        item = dict(raw)
        item["value"] = _decimal(item.get("value"))
        calculations.append(CalculatedMetric(**item))

    narrative_evidence: list[NarrativeEvidence] = []
    for raw in data.get("narrative_evidence", []):
        item = dict(raw)
        item["source"] = _source(item["source"])
        narrative_evidence.append(NarrativeEvidence(**item))

    nonrecurring_items: list[NonRecurringItem] = []
    for raw in data.get("nonrecurring_items", []):
        item = dict(raw)
        item["amount"] = _decimal(item["amount"])
        item["unit_scale"] = _decimal(item["unit_scale"])
        item["source"] = _source(item["source"])
        nonrecurring_items.append(NonRecurringItem(**item))

    business_metrics: list[BusinessMetric] = []
    for raw in data.get("business_metrics", []):
        item = dict(raw)
        item["lower_value"] = _decimal(item["lower_value"])
        item["upper_value"] = _decimal(item["upper_value"])
        item["source"] = _source(item["source"])
        business_metrics.append(BusinessMetric(**item))

    health_data = dict(
        data.get("health_assessment")
        or {
            "profile": document.industry_profile,
            "score": None,
            "grade": "未评级",
            "status": "insufficient",
            "coverage_ratio": "0",
            "methodology": "该历史运行生成于评级模块加入前，仅展示已保存的事实、计算与校验结果。",
            "components": [],
            "limitations": ["历史运行未保存财务健康评级结果。"],
        }
    )
    health_data["score"] = _decimal(health_data.get("score"))
    health_data["coverage_ratio"] = _decimal(health_data["coverage_ratio"])
    health_components: list[HealthComponent] = []
    for raw in health_data.get("components", []):
        item = dict(raw)
        item["score"] = _decimal(item["score"])
        item["max_score"] = _decimal(item["max_score"])
        health_components.append(HealthComponent(**item))
    health_data["components"] = health_components

    return AnalysisBundle(
        document=document,
        facts=facts,
        calculations=calculations,
        validations=[ValidationIssue(**item) for item in data.get("validations", [])],
        findings=[Finding(**item) for item in data.get("findings", [])],
        narrative_evidence=narrative_evidence,
        nonrecurring_items=nonrecurring_items,
        external_evidence=[ExternalEvidence(**item) for item in data.get("external_evidence", [])],
        health_assessment=HealthAssessment(**health_data),
        llm_analysis=data.get("llm_analysis"),
        run_id=data["run_id"],
        run_dir=data["run_dir"],
        business_metrics=business_metrics,
    )


def load_analysis_bundle(path: Path) -> AnalysisBundle:
    return analysis_bundle_from_dict(json.loads(path.read_text(encoding="utf-8")))


def parsed_document_from_dict(data: dict[str, Any]) -> ParsedDocument:
    meta_data = dict(data["meta"])
    meta_data["unit_scale"] = _decimal(meta_data["unit_scale"])
    return ParsedDocument(
        meta=DocumentMeta(**meta_data),
        pages=[PageRecord(**item) for item in data.get("pages", [])],
        tables=[TableRecord(**item) for item in data.get("tables", [])],
    )


def load_parsed_document(path: Path) -> ParsedDocument:
    return parsed_document_from_dict(json.loads(path.read_text(encoding="utf-8")))
