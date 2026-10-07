from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from .models import CalculatedMetric, FinancialFact, Finding, ParsedDocument


def _ref(fact: FinancialFact) -> dict:
    return fact.source.to_dict() if fact.source else {}


def detect_anomalies(
    parsed: ParsedDocument,
    facts: list[FinancialFact],
    calculations: list[CalculatedMetric],
    rules_path: Path,
) -> list[Finding]:
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    findings: list[Finding] = []
    profile = parsed.meta.industry_profile

    yoy: dict[tuple[str, str], FinancialFact] = {
        (fact.metric_code, fact.period_label): fact
        for fact in facts
        if fact.comparison_kind == "reported_yoy_pct"
    }
    periods = {period for _, period in yoy.keys()}
    rule = rules["cash_profit_growth_divergence"]
    if profile in rule["scope"]:
        for period in periods:
            profit = yoy.get(("net_profit_parent", period))
            cfo = yoy.get(("operating_cash_flow", period))
            if profit and cfo and profit.value > 0 and cfo.value < 0:
                gap = profit.value - cfo.value
                findings.append(
                    Finding(
                        rule_id="cash_profit_growth_divergence",
                        title=rule["name"],
                        severity="high" if gap >= Decimal("20") else "medium",
                        classification="calculation",
                        description=(
                            f"{period}归母净利润同比{profit.value}%而经营活动现金流量净额同比"
                            f"{cfo.value}%，方向背离{gap}个百分点。"
                        ),
                        evidence_refs=[_ref(profit), _ref(cfo)],
                        counter_evidence="需结合预收款、应收项目、存货、税费及季节性进一步核验。",
                        applicable_scope=profile,
                    )
                )

    cash_rule = rules["low_cash_conversion"]
    if profile in cash_rule["scope"]:
        threshold = Decimal(str(cash_rule["threshold"]))
        for metric in calculations:
            if metric.code == "cash_conversion" and metric.value is not None and metric.value < threshold:
                findings.append(
                    Finding(
                        rule_id="low_cash_conversion",
                        title=cash_rule["name"],
                        severity=cash_rule["severity"],
                        classification="calculation",
                        description=f"{metric.period_label}经营现金流/归母净利润为{metric.value}倍，低于规则阈值{threshold}倍。",
                        evidence_refs=[{"calculation": metric.to_dict()}],
                        counter_evidence="单期现金转换率可能受结算时点和季节性影响，应结合多期累计结果。",
                        applicable_scope=profile,
                    )
                )

    nonrec_rule = rules["nonrecurring_dependency"]
    if profile in nonrec_rule["scope"]:
        threshold = Decimal(str(nonrec_rule["threshold"]))
        for metric in calculations:
            if (
                metric.code == "nonrecurring_impact_ratio"
                and metric.value is not None
                and abs(metric.value) > threshold
            ):
                findings.append(
                    Finding(
                        rule_id="nonrecurring_dependency",
                        title=nonrec_rule["name"],
                        severity=nonrec_rule["severity"],
                        classification="calculation",
                        description=f"{metric.period_label}非经常性损益影响比例为{metric.value}，超过规则阈值{threshold}。",
                        evidence_refs=[{"calculation": metric.to_dict()}],
                        counter_evidence="需核对非经常性损益项目是否具有持续性及公司对经常性项目的重新界定。",
                        applicable_scope=profile,
                    )
                )
    return findings

