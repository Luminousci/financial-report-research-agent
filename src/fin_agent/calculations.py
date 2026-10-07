from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from .models import CalculatedMetric, FinancialFact


def safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return numerator / denominator


def growth_rate(current: Decimal, prior: Decimal) -> Decimal | None:
    if prior == 0:
        return None
    return (current - prior) / abs(prior) * Decimal("100")


def single_quarter(cumulative: Decimal, previous_cumulative: Decimal) -> Decimal:
    return cumulative - previous_cumulative


def _q(value: Decimal | None, places: str = "0.01") -> Decimal | None:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP) if value is not None else None


def calculate_metrics(facts: list[FinancialFact]) -> list[CalculatedMetric]:
    grouped: dict[tuple[str, str], dict[str, FinancialFact]] = defaultdict(dict)
    for fact in facts:
        grouped[(fact.metric_code, fact.period_label)][fact.comparison_kind] = fact

    results: list[CalculatedMetric] = []
    for (metric_code, period), values in grouped.items():
        current = values.get("value")
        prior = values.get("prior_value")
        if current and prior:
            computed = growth_rate(current.normalized_value, prior.normalized_value)
            results.append(
                CalculatedMetric(
                    code=f"{metric_code}_yoy_recomputed",
                    name=f"{current.metric_name}同比（程序复算）",
                    value=_q(computed),
                    unit="%",
                    formula="(本期值-上年同期值)/ABS(上年同期值)*100",
                    inputs={
                        "current": str(current.normalized_value),
                        "prior": str(prior.normalized_value),
                    },
                    period_label=period,
                    status="ok" if computed is not None else "unavailable",
                    note=None if computed is not None else "上年同期值为零",
                )
            )

    change_codes = {
        "accounts_receivable": "应收账款变动",
        "inventory": "存货变动",
        "prepayments": "预付款项变动",
        "accounts_payable": "应付账款变动",
        "contract_liabilities": "合同负债变动",
    }
    for (metric_code, period), values in grouped.items():
        if metric_code not in change_codes:
            continue
        current = values.get("value")
        prior = values.get("prior_value")
        if current and prior:
            difference = current.normalized_value - prior.normalized_value
            results.append(
                CalculatedMetric(
                    code=f"{metric_code}_change",
                    name=change_codes[metric_code],
                    value=_q(difference, "0.01"),
                    unit=current.currency,
                    formula="期末值-期初值",
                    inputs={"current": str(current.normalized_value), "prior": str(prior.normalized_value)},
                    period_label=period,
                )
            )

    periods = sorted({fact.period_label for fact in facts})
    for period in periods:
        period_values = {
            fact.metric_code: fact
            for fact in facts
            if fact.period_label == period and fact.comparison_kind == "value"
        }
        prior_values = {
            fact.metric_code: fact
            for fact in facts
            if fact.period_label == period and fact.comparison_kind == "prior_value"
        }

        def add_ratio_pair(
            code: str,
            name: str,
            numerator_code: str,
            denominator_code: str,
            *,
            multiplier: Decimal,
            unit: str,
            formula: str,
        ) -> None:
            current_numerator = period_values.get(numerator_code)
            current_denominator = period_values.get(denominator_code)
            prior_numerator = prior_values.get(numerator_code)
            prior_denominator = prior_values.get(denominator_code)
            for suffix, numerator, denominator in (
                ("", current_numerator, current_denominator),
                ("_prior", prior_numerator, prior_denominator),
            ):
                if not numerator or not denominator:
                    continue
                ratio = safe_ratio(numerator.normalized_value, denominator.normalized_value)
                value = ratio * multiplier if ratio is not None else None
                results.append(
                    CalculatedMetric(
                        code=f"{code}{suffix}",
                        name=f"{name}{'（上期）' if suffix else ''}",
                        value=_q(value),
                        unit=unit,
                        formula=formula,
                        inputs={
                            numerator_code: str(numerator.normalized_value),
                            denominator_code: str(denominator.normalized_value),
                        },
                        period_label=period,
                        status="ok" if value is not None else "unavailable",
                    )
                )

        revenue = period_values.get("revenue") or period_values.get("operating_revenue_total")
        revenue_prior = prior_values.get("revenue") or prior_values.get("operating_revenue_total")
        operating_cost = period_values.get("operating_cost")
        operating_cost_prior = prior_values.get("operating_cost")
        for suffix, revenue_fact, cost_fact in (
            ("", revenue, operating_cost),
            ("_prior", revenue_prior, operating_cost_prior),
        ):
            if revenue_fact and cost_fact:
                ratio = safe_ratio(
                    revenue_fact.normalized_value - cost_fact.normalized_value,
                    revenue_fact.normalized_value,
                )
                value = ratio * Decimal("100") if ratio is not None else None
                results.append(
                    CalculatedMetric(
                        code=f"gross_margin{suffix}",
                        name=f"毛利率{'（上期）' if suffix else ''}",
                        value=_q(value),
                        unit="%",
                        formula="(营业收入-营业成本)/营业收入*100",
                        inputs={
                            "revenue": str(revenue_fact.normalized_value),
                            "operating_cost": str(cost_fact.normalized_value),
                        },
                        period_label=period,
                        status="ok" if value is not None else "unavailable",
                    )
                )

        if revenue and period_values.get("net_profit_parent"):
            add_ratio_pair(
                "net_margin",
                "净利率",
                "net_profit_parent",
                "revenue" if period_values.get("revenue") else "operating_revenue_total",
                multiplier=Decimal("100"),
                unit="%",
                formula="归母净利润/营业收入*100",
            )
        add_ratio_pair(
            "debt_ratio",
            "资产负债率",
            "total_liabilities",
            "total_assets",
            multiplier=Decimal("100"),
            unit="%",
            formula="总负债/总资产*100",
        )
        add_ratio_pair(
            "current_ratio",
            "流动比率",
            "current_assets",
            "current_liabilities",
            multiplier=Decimal("1"),
            unit="倍",
            formula="流动资产/流动负债",
        )
        cfo = period_values.get("operating_cash_flow")
        profit = period_values.get("net_profit_parent")
        if cfo and profit:
            ratio = safe_ratio(cfo.normalized_value, profit.normalized_value)
            results.append(
                CalculatedMetric(
                    code="cash_conversion",
                    name="经营现金流/归母净利润",
                    value=_q(ratio, "0.0001"),
                    unit="倍",
                    formula="经营活动现金流量净额/归母净利润",
                    inputs={
                        "operating_cash_flow": str(cfo.normalized_value),
                        "net_profit_parent": str(profit.normalized_value),
                    },
                    period_label=period,
                )
            )

        if cfo and profit and cfo.period_type == "annual":
            cfo_prior = grouped.get(("operating_cash_flow", period), {}).get("prior_value")
            profit_prior = grouped.get(("net_profit_parent", period), {}).get("prior_value")
            if cfo_prior and profit_prior:
                cumulative_profit = profit.normalized_value + profit_prior.normalized_value
                cumulative_cfo = cfo.normalized_value + cfo_prior.normalized_value
                ratio = safe_ratio(cumulative_cfo, cumulative_profit)
                results.append(
                    CalculatedMetric(
                        code="cash_conversion_2period_cumulative",
                        name="两年累计经营现金流/归母净利润",
                        value=_q(ratio, "0.0001"),
                        unit="倍",
                        formula="(本年经营现金流+上年经营现金流)/(本年归母净利润+上年归母净利润)",
                        inputs={
                            "current_operating_cash_flow": str(cfo.normalized_value),
                            "prior_operating_cash_flow": str(cfo_prior.normalized_value),
                            "current_net_profit_parent": str(profit.normalized_value),
                            "prior_net_profit_parent": str(profit_prior.normalized_value),
                        },
                        period_label=period,
                        status="ok" if ratio is not None else "unavailable",
                    )
                )

        profit_ex = period_values.get("net_profit_excl_nonrecurring")
        if profit and profit_ex:
            denominator = abs(profit.normalized_value)
            impact = safe_ratio(
                profit.normalized_value - profit_ex.normalized_value,
                denominator,
            )
            results.append(
                CalculatedMetric(
                    code="nonrecurring_impact_ratio",
                    name="非经常性损益影响比例",
                    value=_q(impact, "0.0001"),
                    unit="比例",
                    formula="(归母净利润-扣非归母净利润)/ABS(归母净利润)",
                    inputs={
                        "net_profit_parent": str(profit.normalized_value),
                        "net_profit_excl_nonrecurring": str(profit_ex.normalized_value),
                    },
                    period_label=period,
                    status="ok" if impact is not None else "unavailable",
                )
            )

        working_capital_inputs: dict[str, str] = {}
        working_capital_change = Decimal("0")
        positive_codes = ("accounts_receivable", "inventory", "prepayments")
        negative_codes = ("accounts_payable", "contract_liabilities")
        positive_count = 0
        negative_count = 0
        for code in positive_codes + negative_codes:
            values = grouped.get((code, period), {})
            current_item, prior_item = values.get("value"), values.get("prior_value")
            if not current_item or not prior_item:
                continue
            change = current_item.normalized_value - prior_item.normalized_value
            working_capital_inputs[code] = str(change)
            if code in positive_codes:
                working_capital_change += change
                positive_count += 1
            else:
                working_capital_change -= change
                negative_count += 1
        if positive_count >= 2 and negative_count >= 1:
            results.append(
                CalculatedMetric(
                    code="working_capital_investment_change",
                    name="营运资金占用变动（代理口径）",
                    value=_q(working_capital_change, "0.01"),
                    unit=parsed_currency(facts),
                    formula="Δ应收账款+Δ存货+Δ预付款项-Δ应付账款-Δ合同负债（仅计入可得项目）",
                    inputs=working_capital_inputs,
                    period_label=period,
                    note="正值表示营运资金占用增加、对现金流形成压力；该口径不等同于现金流量表披露的全部营运资金变动。",
                )
            )

    by_code_period = {(item.code, item.period_label): item for item in results}
    for period in periods:
        profit_growth = by_code_period.get(("net_profit_parent_yoy_recomputed", period))
        cash_growth = by_code_period.get(("operating_cash_flow_yoy_recomputed", period))
        if profit_growth and cash_growth and profit_growth.value is not None and cash_growth.value is not None:
            results.append(
                CalculatedMetric(
                    code="profit_cash_growth_gap",
                    name="利润与经营现金流增速差",
                    value=_q(profit_growth.value - cash_growth.value),
                    unit="百分点",
                    formula="归母净利润同比-经营活动现金流量净额同比",
                    inputs={"profit_growth": str(profit_growth.value), "cash_growth": str(cash_growth.value)},
                    period_label=period,
                )
            )
    return results


def parsed_currency(facts: list[FinancialFact]) -> str:
    return facts[0].currency if facts else "CNY"
