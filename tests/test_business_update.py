import unittest

from fin_agent.business_update import detect_business_update_signals, extract_business_metrics
from fin_agent.health import assess_health
from fin_agent.models import DocumentMeta, PageRecord, ParsedDocument


class BusinessUpdateTests(unittest.TestCase):
    def _parsed(self) -> ParsedDocument:
        meta = DocumentMeta(
            document_id="doc-business",
            path="update.pdf",
            file_name="第三季度报告.pdf",
            sha256="a" * 64,
            page_count=2,
            company_name="泡泡玛特国际集团有限公司",
            security_code="09992",
            report_year=2025,
            report_type="business_update",
            industry_profile="non_financial",
            accounting_standard="IFRS",
            native_text_ratio=0.0,
            ocr_pages=[1, 2],
        )
        pages = [
            PageRecord(
                page=1,
                method="ocr:aistudio",
                quality_score=0.99,
                needs_ocr=False,
                text=(
                    "二零二五年第三季度整體收益（未經審核），較去年同期同比增長245%-250%，"
                    "其中中國收益同比增長185%-190%，海外 $ ^{1} $ 收益同比增長365%-370%。\n"
                    "線下渠道同比增長130%-135%；線上渠道同比增長300%-305%。"
                ),
            ),
            PageRecord(
                page=2,
                method="ocr:aistudio",
                quality_score=0.98,
                needs_ocr=False,
                text="亞太同比增長170%-175%；美洲同比增長1,265%-1,270%；歐洲及其他地區同比增長735%-740%。",
            ),
        ]
        return ParsedDocument(meta=meta, pages=pages, tables=[])

    def test_extracts_range_metrics_with_page_provenance(self) -> None:
        metrics = extract_business_metrics(self._parsed())
        by_code = {item.metric_code: item for item in metrics}
        self.assertEqual(len(metrics), 8)
        self.assertEqual(str(by_code["overall_revenue_yoy"].lower_value), "245")
        self.assertEqual(str(by_code["overall_revenue_yoy"].upper_value), "250")
        self.assertEqual(str(by_code["americas_revenue_yoy"].upper_value), "1270")
        self.assertEqual(by_code["americas_revenue_yoy"].source.page, 2)
        self.assertEqual(by_code["overall_revenue_yoy"].period_label, "FY2025_Q3")

    def test_business_update_uses_applicability_instead_of_health_grade(self) -> None:
        parsed = self._parsed()
        assessment = assess_health(parsed, [], [])
        self.assertEqual(assessment.grade, "不适用")
        self.assertEqual(assessment.profile, "business_update")
        self.assertIn("经营快报", assessment.methodology)

    def test_detects_channel_and_regional_dispersion(self) -> None:
        findings = detect_business_update_signals(extract_business_metrics(self._parsed()))
        rule_ids = {item.rule_id for item in findings}
        self.assertIn("business_channel_growth_dispersion", rule_ids)
        self.assertIn("business_region_growth_dispersion", rule_ids)


if __name__ == "__main__":
    unittest.main()
