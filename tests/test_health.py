import unittest
from decimal import Decimal

from fin_agent.health import assess_health
from fin_agent.models import CalculatedMetric, DocumentMeta, FinancialFact, ParsedDocument


class HealthTests(unittest.TestCase):
    def test_non_financial_assessment_is_transparent_and_complete(self):
        meta = DocumentMeta(
            document_id="abc", path="sample.pdf", file_name="sample.pdf", sha256="0" * 64,
            page_count=1, company_name="测试公司", security_code="000001", report_year=2025,
            report_type="annual", industry_profile="non_financial", accounting_standard="CAS"
        )
        facts = [
            FinancialFact("revenue", "营业收入", Decimal("110"), "110", "元", "CNY", Decimal("1"), "FY2025", "annual", "value"),
            FinancialFact("revenue", "营业收入", Decimal("10"), "10", "%", "CNY", Decimal("1"), "FY2025", "annual", "reported_yoy_pct"),
            FinancialFact("net_profit_parent", "归母净利润", Decimal("20"), "20", "元", "CNY", Decimal("1"), "FY2025", "annual", "value"),
            FinancialFact("net_profit_parent", "归母净利润", Decimal("12"), "12", "%", "CNY", Decimal("1"), "FY2025", "annual", "reported_yoy_pct"),
            FinancialFact("total_assets", "总资产", Decimal("100"), "100", "元", "CNY", Decimal("1"), "FY2025", "point_in_time", "value"),
            FinancialFact("total_liabilities", "总负债", Decimal("40"), "40", "元", "CNY", Decimal("1"), "FY2025", "point_in_time", "value"),
        ]
        calculations = [
            CalculatedMetric("cash_conversion", "经营现金流/归母净利润", Decimal("1.1"), "倍", "x/y", {}, "FY2025"),
            CalculatedMetric("nonrecurring_impact_ratio", "非经常性损益影响", Decimal("0.05"), "比例", "x/y", {}, "FY2025"),
        ]
        assessment = assess_health(ParsedDocument(meta, [], []), facts, calculations)
        self.assertEqual(assessment.status, "complete")
        self.assertEqual(assessment.coverage_ratio, Decimal("1.00"))
        self.assertEqual(assessment.grade, "A")
        self.assertEqual(len(assessment.components), 5)


if __name__ == "__main__":
    unittest.main()
