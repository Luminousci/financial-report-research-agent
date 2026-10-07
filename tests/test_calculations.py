import unittest
from decimal import Decimal

from fin_agent.calculations import calculate_metrics, growth_rate, safe_ratio, single_quarter
from fin_agent.models import FinancialFact


class CalculationTests(unittest.TestCase):
    @staticmethod
    def fact(code, name, value, kind="value", period="FY2025", period_type="annual"):
        return FinancialFact(
            metric_code=code,
            metric_name=name,
            value=Decimal(value),
            raw_value=value,
            unit="元",
            currency="CNY",
            unit_scale=Decimal("1"),
            period_label=period,
            period_type=period_type,
            comparison_kind=kind,
        )

    def test_growth_rate(self):
        self.assertEqual(growth_rate(Decimal("120"), Decimal("100")), Decimal("20"))

    def test_growth_rate_zero_prior_is_unavailable(self):
        self.assertIsNone(growth_rate(Decimal("120"), Decimal("0")))

    def test_single_quarter(self):
        self.assertEqual(single_quarter(Decimal("250"), Decimal("100")), Decimal("150"))

    def test_safe_ratio(self):
        self.assertEqual(safe_ratio(Decimal("50"), Decimal("100")), Decimal("0.5"))
        self.assertIsNone(safe_ratio(Decimal("50"), Decimal("0")))

    def test_two_period_cash_conversion_and_working_capital_proxy(self):
        facts = [
            self.fact("operating_cash_flow", "经营现金流", "120"),
            self.fact("operating_cash_flow", "经营现金流", "80", "prior_value"),
            self.fact("net_profit_parent", "归母净利润", "100"),
            self.fact("net_profit_parent", "归母净利润", "100", "prior_value"),
            self.fact("accounts_receivable", "应收账款", "30", period_type="point_in_time"),
            self.fact("accounts_receivable", "应收账款", "20", "prior_value", period_type="point_in_time"),
            self.fact("inventory", "存货", "50", period_type="point_in_time"),
            self.fact("inventory", "存货", "40", "prior_value", period_type="point_in_time"),
            self.fact("accounts_payable", "应付账款", "25", period_type="point_in_time"),
            self.fact("accounts_payable", "应付账款", "20", "prior_value", period_type="point_in_time"),
        ]
        metrics = {item.code: item for item in calculate_metrics(facts)}
        self.assertEqual(metrics["cash_conversion_2period_cumulative"].value, Decimal("1.0000"))
        self.assertEqual(metrics["working_capital_investment_change"].value, Decimal("15.00"))

    def test_template_ratios_are_programmatically_calculated(self):
        facts = [
            self.fact("revenue", "营业收入", "1000"),
            self.fact("revenue", "营业收入", "800", "prior_value"),
            self.fact("operating_cost", "营业成本", "600"),
            self.fact("operating_cost", "营业成本", "520", "prior_value"),
            self.fact("net_profit_parent", "归母净利润", "120"),
            self.fact("net_profit_parent", "归母净利润", "80", "prior_value"),
            self.fact("total_assets", "总资产", "2000", period_type="point_in_time"),
            self.fact("total_assets", "总资产", "1800", "prior_value", period_type="point_in_time"),
            self.fact("total_liabilities", "总负债", "1000", period_type="point_in_time"),
            self.fact("total_liabilities", "总负债", "900", "prior_value", period_type="point_in_time"),
            self.fact("current_assets", "流动资产", "600", period_type="point_in_time"),
            self.fact("current_assets", "流动资产", "500", "prior_value", period_type="point_in_time"),
            self.fact("current_liabilities", "流动负债", "300", period_type="point_in_time"),
            self.fact("current_liabilities", "流动负债", "250", "prior_value", period_type="point_in_time"),
        ]
        metrics = {item.code: item for item in calculate_metrics(facts)}
        self.assertEqual(metrics["gross_margin"].value, Decimal("40.00"))
        self.assertEqual(metrics["net_margin"].value, Decimal("12.00"))
        self.assertEqual(metrics["debt_ratio"].value, Decimal("50.00"))
        self.assertEqual(metrics["current_ratio"].value, Decimal("2.00"))
        self.assertEqual(metrics["gross_margin_prior"].value, Decimal("35.00"))


if __name__ == "__main__":
    unittest.main()
