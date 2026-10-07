import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from fin_agent.models import AnalysisBundle, BusinessMetric, DocumentMeta, HealthAssessment, SourceRef
from fin_agent.report import render_html
from fin_agent.report_compact import (
    _analysis_markup,
    _analysis_text,
    _clean_analysis_text,
    _metric_rows,
    _trend_markup,
)


class ReportThemeTests(unittest.TestCase):
    def test_report_uses_frontend_theme_and_navigation(self) -> None:
        document = DocumentMeta(
            document_id="doc-1",
            path="example.pdf",
            file_name="示例年报.pdf",
            sha256="a" * 64,
            page_count=1,
            company_name="示例公司",
            security_code="000001",
            report_year=2025,
            report_type="annual",
            industry_profile="non_financial",
            accounting_standard="CAS",
            native_text_ratio=1.0,
        )
        health = HealthAssessment(
            profile="non_financial",
            score=None,
            grade="未评级",
            status="insufficient",
            coverage_ratio=Decimal("0"),
            methodology="测试方法",
            components=[],
        )
        bundle = AnalysisBundle(
            document=document,
            facts=[],
            calculations=[],
            validations=[],
            findings=[],
            narrative_evidence=[],
            nonrecurring_items=[],
            external_evidence=[],
            health_assessment=health,
            llm_analysis=None,
            run_id="test-run",
            run_dir="test-run",
            business_metrics=[
                BusinessMetric(
                    metric_code="overall_revenue_yoy",
                    metric_name="整体收益同比",
                    lower_value=Decimal("245"),
                    upper_value=Decimal("250"),
                    unit="%",
                    comparison_kind="yoy_pct",
                    period_label="FY2025_Q3",
                    scope="整体收益",
                    source=SourceRef("doc-1", "示例年报.pdf", 1, evidence_text="整体收益同比增长245%-250%"),
                )
            ],
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "report.html"
            render_html(bundle, output_path)
            page = output_path.read_text(encoding="utf-8")
        self.assertIn("麦穗终端", page)
        self.assertIn("--accent:#F3FFC9", page)
        self.assertIn('class="back-link" href="/"', page)
        self.assertIn("经营更新指标", page)
        self.assertIn("245–250%", page)
        self.assertIn("/assets/market-analysis-background.jpg", page)
        self.assertIn("IntersectionObserver", page)
        self.assertIn("scroll-reveal", page)
        self.assertIn("本次运行未启用DeepSeek", page)
        self.assertEqual(page.count('class="report-page'), 2)
        self.assertIn("@page{size:A4 landscape", page)
        self.assertNotIn("叙事证据检索", page)
        self.assertNotIn("受控外部证据", page)
        self.assertIn('classList.toggle("is-visible",entry.isIntersecting)', page)
        self.assertIn('id="chat-drawer"', page)
        self.assertIn('data-run-id="test-run"', page)
        self.assertIn('fetch("/api/chat"', page)
        self.assertIn("证据问答", page)
        self.assertIn("经营判断与风险研判", page)
        self.assertIn('class="company-title"', page)
        self.assertIn('style="--company-title-size:86px">示例公司</span>', page)
        self.assertNotIn("示例公司<br>", page)
        self.assertIn("缺少可比季度输入", _metric_rows(bundle))
        self.assertIn("报告类型", page)
        self.assertIn("行业配置", page)
        self.assertIn("文本覆盖", page)
        self.assertIn("程序复算与质量校验", page)
        self.assertIn("结构化质量校验", page)
        self.assertNotIn("01 / 02", page)
        self.assertNotIn("02 / 02", page)
        self.assertIn("height:auto;min-height:0", page)
        self.assertIn("overflow:visible", page)

    def test_analysis_labels_are_bold_and_split_into_separate_lines(self) -> None:
        markup = _analysis_markup("披露事实：收入增长。程序计算：同比3%。分析推论：增长持续性仍需核查。")
        self.assertIn("<span class='analysis-line fact'><strong>事实披露：</strong>", markup)
        self.assertIn("<span class='analysis-line inference'><strong>分析推论：</strong>", markup)
        self.assertEqual(markup.count("class='analysis-line"), 3)

    def test_legacy_analysis_is_split_into_fact_and_inference_lines(self) -> None:
        markup = _analysis_markup("营业收入同比增长3.00%。非息收入是增长的主要来源。")
        self.assertIn("<strong>事实披露：</strong>", markup)
        self.assertIn("<strong>分析推论：</strong>", markup)

    def test_trend_colours_follow_mainland_market_convention(self) -> None:
        markup = _trend_markup("营业收入同比增长3.00%，净利润同比下降2.00%。")
        self.assertIn("class='trend-up'>同比增长3.00%", markup)
        self.assertIn("class='trend-down'>同比下降2.00%", markup)

    def test_internal_reference_metadata_is_hidden_from_report_copy(self) -> None:
        text = _clean_analysis_text(
            "营业收入同比下降3.22%（document_id: 4443b73a09dded50, page 1）。"
            "净利率下降（calculation: net_margin）。"
        )
        self.assertEqual(text, "营业收入同比下降3.22%。净利率下降。")
        self.assertNotIn("document_id", text)
        self.assertNotIn("page 1", text)

    def test_analysis_text_is_not_truncated(self) -> None:
        long_text = "披露事实：" + "总负债同比增长。" * 80 + "分析推论：信息应完整展示。"
        result = _analysis_text(
            {"performance_analysis": long_text},
            "performance_analysis",
            "performance",
        )
        self.assertTrue(result.endswith("分析推论：信息应完整展示。"))
        self.assertNotIn("…", result)


if __name__ == "__main__":
    unittest.main()
