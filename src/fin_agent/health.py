from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .models import (
    CalculatedMetric,
    FinancialFact,
    HealthAssessment,
    HealthComponent,
    ParsedDocument,
)


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fact_ref(fact: FinancialFact | None) -> list[dict]:
    return [fact.source.to_dict()] if fact and fact.source else []


def _component(code: str, name: str, score: str, maximum: str, value: str, rationale: str, refs: list[dict]) -> HealthComponent:
    return HealthComponent(code, name, Decimal(score), Decimal(maximum), value, rationale, refs)


def _latest_fact(facts: list[FinancialFact], code: str, kind: str = "value") -> FinancialFact | None:
    candidates = [fact for fact in facts if fact.metric_code == code and fact.comparison_kind == kind]
    return candidates[-1] if candidates else None


def assess_health(
    parsed: ParsedDocument,
    facts: list[FinancialFact],
    calculations: list[CalculatedMetric],
) -> HealthAssessment:
    if parsed.meta.report_type == "business_update":
        return HealthAssessment(
            profile="business_update",
            score=None,
            grade="不适用",
            status="insufficient",
            coverage_ratio=Decimal("0"),
            methodology="经营快报仅披露区间型收益增速，不适用完整财报健康评分。",
            components=[],
            limitations=["需结合完整利润表、资产负债表和现金流量表后再评分。"],
        )
    components: list[HealthComponent] = []
    maximum_total = Decimal("100")
    if parsed.meta.industry_profile == "bank":
        capital = _latest_fact(facts, "capital_adequacy_ratio")
        if capital:
            value = capital.value
            score = "30" if value >= 14 else ("20" if value >= Decimal("11.5") else "5")
            components.append(_component("capital_adequacy", "资本充足率", score, "30", f"{value}%", "内部评级阈值：≥14%得满分，11.5%—14%得20分。", _fact_ref(capital)))
        provision = _latest_fact(facts, "provision_coverage_ratio")
        if provision:
            value = provision.value
            score = "25" if value >= 200 else ("18" if value >= 150 else "5")
            components.append(_component("provision_coverage", "拨备覆盖率", score, "25", f"{value}%", "内部评级阈值：≥200%得满分，150%—200%得18分。", _fact_ref(provision)))
        npl = _latest_fact(facts, "nonperforming_loan_ratio")
        if npl:
            value = npl.value
            score = "25" if value <= Decimal("1.5") else ("18" if value <= 2 else "5")
            components.append(_component("npl_ratio", "不良贷款率", score, "25", f"{value}%", "内部评级阈值：≤1.5%得满分，1.5%—2%得18分。", _fact_ref(npl)))
        nim = _latest_fact(facts, "net_interest_margin")
        nim_prior = _latest_fact(facts, "net_interest_margin", "prior_value")
        if nim:
            if nim_prior:
                decline = nim_prior.value - nim.value
                score = "20" if decline <= 0 else ("12" if decline <= Decimal("0.15") else "5")
                rationale = "净息差未下降得满分，下降不超过0.15个百分点得12分。"
                refs = _fact_ref(nim) + _fact_ref(nim_prior)
            else:
                score, rationale, refs = "10", "缺少可比期净息差，按中性分计入并降低覆盖度。", _fact_ref(nim)
            components.append(_component("net_interest_margin", "净利息收益率", score, "20", f"{nim.value}%", rationale, refs))
        methodology = "银行内部评级：资本充足率、拨备覆盖率、不良贷款率和净利息收益率，共100分。"
    else:
        cash = next((item for item in reversed(calculations) if item.code == "cash_conversion" and item.value is not None), None)
        if cash:
            value = cash.value or Decimal("0")
            score = "25" if value >= 1 else ("20" if value >= Decimal("0.8") else ("10" if value >= Decimal("0.5") else "0"))
            components.append(_component("cash_conversion", "利润现金含量", score, "25", f"{value}倍", "经营现金流/归母净利润：≥1得满分，0.8—1得20分，0.5—0.8得10分。", [{"calculation": cash.to_dict()}]))
        for code, name, weight in (("revenue", "营业收入增长", "20"), ("net_profit_parent", "归母净利润增长", "20")):
            yoy = _latest_fact(facts, code, "reported_yoy_pct")
            if yoy:
                score = weight if yoy.value >= 10 else ("15" if yoy.value > 0 else "5")
                components.append(_component(f"{code}_growth", name, score, weight, f"{yoy.value}%", "同比≥10%得满分，0—10%得15分，非正增长得5分。", _fact_ref(yoy)))
        assets = _latest_fact(facts, "total_assets")
        liabilities = _latest_fact(facts, "total_liabilities")
        if assets and liabilities and assets.normalized_value != 0:
            ratio = liabilities.normalized_value / assets.normalized_value * 100
            score = "20" if ratio <= 50 else ("12" if ratio <= 70 else "5")
            components.append(_component("debt_ratio", "资产负债率", score, "20", f"{_q(ratio)}%", "资产负债率≤50%得满分，50%—70%得12分。行业差异需人工复核。", _fact_ref(assets) + _fact_ref(liabilities)))
        nonrec = next((item for item in reversed(calculations) if item.code == "nonrecurring_impact_ratio" and item.value is not None), None)
        if nonrec:
            value = abs(nonrec.value or Decimal("0"))
            score = "15" if value <= Decimal("0.1") else ("8" if value <= Decimal("0.3") else "0")
            components.append(_component("nonrecurring_impact", "非经常性损益影响", score, "15", str(nonrec.value), "绝对影响比例≤0.1得满分，0.1—0.3得8分。", [{"calculation": nonrec.to_dict()}]))
        methodology = "非金融企业内部评级：利润现金含量、收入增长、利润增长、资产负债率和非经常性损益影响，共100分。"

    available_max = sum((item.max_score for item in components), Decimal("0"))
    coverage = available_max / maximum_total if maximum_total else Decimal("0")
    if available_max == 0:
        return HealthAssessment(parsed.meta.industry_profile, None, "未评级", "insufficient", Decimal("0"), methodology, [], ["缺少评分所需的结构化指标。"])
    normalized = sum((item.score for item in components), Decimal("0")) / available_max * 100
    score = _q(normalized)
    grade = "A" if score >= 85 else ("B" if score >= 70 else ("C" if score >= 55 else "D"))
    status = "complete" if coverage >= Decimal("0.8") else ("partial" if coverage >= Decimal("0.5") else "insufficient")
    limitations = ["该评级是可解释的内部筛查意见，不是信用评级或投资建议。"]
    if coverage < 1:
        limitations.append(f"指标覆盖率为{_q(coverage * 100)}%，缺失项未按零分处理，最终得分按已覆盖权重归一化。")
    return HealthAssessment(parsed.meta.industry_profile, score, grade, status, _q(coverage), methodology, components, limitations)
