from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .calculations import growth_rate
from .models import BusinessMetric, FinancialFact, ParsedDocument, ValidationIssue


def validate(
    parsed: ParsedDocument,
    facts: list[FinancialFact],
    business_metrics: list[BusinessMetric] | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if parsed.meta.report_type == "business_update":
        issues.append(
            ValidationIssue(
                code="document_scope_limited",
                severity="info",
                message=(
                    f"该文件为简短业务公告，已提取{len(business_metrics or [])}项区间型经营指标；"
                    "不包含完整财务报表，系统不会补造利润、现金流等缺失指标。"
                ),
            )
        )
        return issues

    if not facts:
        issues.append(
            ValidationIssue(
                code="no_financial_facts",
                severity="error",
                message="未从候选财务表格中提取到标准化指标。",
            )
        )
        return issues

    required = {"revenue", "net_profit_parent"}
    present = {fact.metric_code for fact in facts if fact.comparison_kind == "value"}
    for metric in sorted(required - present):
        issues.append(
            ValidationIssue(
                code="required_metric_missing",
                severity="warning",
                message=f"缺少核心指标：{metric}",
            )
        )

    grouped: dict[tuple[str, str, str], list[FinancialFact]] = defaultdict(list)
    for fact in facts:
        grouped[(fact.metric_code, fact.period_label, fact.comparison_kind)].append(fact)
        if fact.source is None:
            issues.append(
                ValidationIssue(
                    code="source_missing",
                    severity="error",
                    message=f"{fact.metric_name}缺少来源定位。",
                )
            )

    for key, candidates in grouped.items():
        values = {fact.normalized_value for fact in candidates}
        if len(values) > 1:
            issues.append(
                ValidationIssue(
                    code="conflicting_values",
                    severity="warning",
                    message=f"同一字段存在冲突值：{key} -> {sorted(str(v) for v in values)}",
                    evidence_refs=[fact.source.to_dict() for fact in candidates if fact.source],
                )
            )

    by_metric_period: dict[tuple[str, str], dict[str, FinancialFact]] = defaultdict(dict)
    for fact in facts:
        by_metric_period[(fact.metric_code, fact.period_label)][fact.comparison_kind] = fact
    for (metric, period), values in by_metric_period.items():
        current = values.get("value")
        prior = values.get("prior_value")
        reported = values.get("reported_yoy_pct")
        if current and prior and reported:
            recomputed = growth_rate(current.normalized_value, prior.normalized_value)
            if recomputed is not None and abs(recomputed - reported.value) > Decimal("0.15"):
                issues.append(
                    ValidationIssue(
                        code="reported_change_mismatch",
                        severity="warning",
                        message=(
                            f"{metric} {period} 披露变动{reported.value}%与程序复算"
                            f"{recomputed.quantize(Decimal('0.01'))}%不一致。"
                        ),
                        evidence_refs=[
                            fact.source.to_dict()
                            for fact in (current, prior, reported)
                            if fact.source
                        ],
                    )
                )

    for period in {fact.period_label for fact in facts}:
        values = {
            fact.metric_code: fact.normalized_value
            for fact in facts
            if fact.period_label == period and fact.comparison_kind == "value"
        }
        if {"total_assets", "total_liabilities", "total_equity"} <= values.keys():
            difference = values["total_assets"] - values["total_liabilities"] - values["total_equity"]
            tolerance = max(Decimal("1"), abs(values["total_assets"]) * Decimal("0.000001"))
            if abs(difference) > tolerance:
                issues.append(
                    ValidationIssue(
                        code="balance_sheet_not_tied",
                        severity="error",
                        message=f"资产负债表未勾稽，差额为{difference}。",
                    )
                )
    return issues
