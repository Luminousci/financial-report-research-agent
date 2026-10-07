from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


class Serializable:
    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(slots=True)
class SourceRef(Serializable):
    document_id: str
    file_name: str
    page: int
    table_index: int | None = None
    row_index: int | None = None
    raw_label: str | None = None
    raw_value: str | None = None
    evidence_text: str | None = None
    extraction_method: str = "pdf_table"
    confidence: float = 1.0


@dataclass(slots=True)
class PageRecord(Serializable):
    page: int
    text: str
    method: str
    quality_score: float
    needs_ocr: bool
    quality_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TableRecord(Serializable):
    page: int
    table_index: int
    rows: list[list[str | None]]
    extraction_method: str = "pdfplumber"


@dataclass(slots=True)
class DocumentMeta(Serializable):
    document_id: str
    path: str
    file_name: str
    sha256: str
    page_count: int
    company_name: str | None
    security_code: str | None
    report_year: int | None
    report_type: Literal[
        "annual", "semiannual", "q1", "q3", "business_update", "unknown"
    ]
    industry_profile: Literal["non_financial", "bank", "unknown"]
    accounting_standard: Literal["CAS", "IFRS", "unknown"]
    currency: str = "CNY"
    unit_scale: Decimal = Decimal("1")
    native_text_ratio: float = 0.0
    ocr_pages: list[int] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedDocument(Serializable):
    meta: DocumentMeta
    pages: list[PageRecord]
    tables: list[TableRecord]


@dataclass(slots=True)
class FinancialFact(Serializable):
    metric_code: str
    metric_name: str
    value: Decimal
    raw_value: str
    unit: str
    currency: str
    unit_scale: Decimal
    period_label: str
    period_type: Literal[
        "single_quarter", "cumulative", "annual", "point_in_time", "unknown"
    ]
    comparison_kind: Literal["value", "reported_yoy_pct", "prior_value"] = "value"
    statement_scope: Literal["consolidated", "parent", "unknown"] = "consolidated"
    restated: bool = False
    source: SourceRef | None = None

    @property
    def normalized_value(self) -> Decimal:
        return self.value * self.unit_scale


@dataclass(slots=True)
class CalculatedMetric(Serializable):
    code: str
    name: str
    value: Decimal | None
    unit: str
    formula: str
    inputs: dict[str, str]
    period_label: str
    status: Literal["ok", "unavailable", "not_applicable"] = "ok"
    note: str | None = None


@dataclass(slots=True)
class ValidationIssue(Serializable):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Finding(Serializable):
    rule_id: str
    title: str
    severity: Literal["low", "medium", "high"]
    classification: Literal["fact", "calculation", "inference", "opinion"]
    description: str
    evidence_refs: list[dict[str, Any]]
    counter_evidence: str | None = None
    applicable_scope: str | None = None


@dataclass(slots=True)
class NarrativeEvidence(Serializable):
    category: str
    text: str
    score: float
    source: SourceRef


@dataclass(slots=True)
class NonRecurringItem(Serializable):
    item_name: str
    amount: Decimal
    raw_amount: str
    unit: str
    currency: str
    unit_scale: Decimal
    period_label: str
    is_total: bool
    source: SourceRef

    @property
    def normalized_amount(self) -> Decimal:
        return self.amount * self.unit_scale


@dataclass(slots=True)
class BusinessMetric(Serializable):
    metric_code: str
    metric_name: str
    lower_value: Decimal
    upper_value: Decimal
    unit: str
    comparison_kind: Literal["yoy_pct"]
    period_label: str
    scope: str
    source: SourceRef

    @property
    def display_value(self) -> str:
        if self.lower_value == self.upper_value:
            return f"{self.lower_value}{self.unit}"
        return f"{self.lower_value}–{self.upper_value}{self.unit}"


@dataclass(slots=True)
class ExternalEvidence(Serializable):
    evidence_id: str
    title: str
    content: str
    source_path: str
    sha256: str
    tags: list[str] = field(default_factory=list)
    as_of_date: str | None = None


@dataclass(slots=True)
class HealthComponent(Serializable):
    code: str
    name: str
    score: Decimal
    max_score: Decimal
    observed_value: str
    rationale: str
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class HealthAssessment(Serializable):
    profile: str
    score: Decimal | None
    grade: str
    status: Literal["complete", "partial", "insufficient"]
    coverage_ratio: Decimal
    methodology: str
    components: list[HealthComponent]
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AnalysisBundle(Serializable):
    document: DocumentMeta
    facts: list[FinancialFact]
    calculations: list[CalculatedMetric]
    validations: list[ValidationIssue]
    findings: list[Finding]
    narrative_evidence: list[NarrativeEvidence]
    nonrecurring_items: list[NonRecurringItem]
    external_evidence: list[ExternalEvidence]
    health_assessment: HealthAssessment
    llm_analysis: dict[str, Any] | None
    run_id: str
    run_dir: str
    business_metrics: list[BusinessMetric] = field(default_factory=list)
