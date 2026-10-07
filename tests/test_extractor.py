import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from fin_agent.extractor import FinancialTableExtractor
from fin_agent.models import DocumentMeta, PageRecord, ParsedDocument, TableRecord


class ExtractorTests(unittest.TestCase):
    def test_q1_continuation_table_does_not_shift_stock_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = Path(directory) / "metrics.json"
            dictionary.write_text(json.dumps({
                "total_assets": {"name": "总资产", "aliases": ["总资产"], "kind": "stock"},
                "basic_eps": {"name": "基本每股收益", "aliases": ["基本每股收益"], "kind": "ratio"},
            }, ensure_ascii=False), encoding="utf-8")
            parsed = ParsedDocument(
                meta=DocumentMeta(
                    document_id="abc", path="sample.pdf", file_name="sample.pdf", sha256="0" * 64,
                    page_count=1, company_name="测试公司", security_code="000001", report_year=2026,
                    report_type="q1", industry_profile="non_financial", accounting_standard="CAS"
                ),
                pages=[PageRecord(page=1, text="单位：元", method="pypdf", quality_score=1, needs_ocr=False)],
                tables=[TableRecord(page=1, table_index=0, rows=[
                    ["稀释每股收益", "0.43", "0.50", "-14.00"],
                    ["基本每股收益", "0.43", "0.50", "-14.00"],
                    ["总资产", "123747865038.34", "143905863420.75", "-14.01"],
                ])],
            )
            facts = FinancialTableExtractor(dictionary).extract(parsed)
            keyed = {(fact.metric_code, fact.comparison_kind): fact.value for fact in facts}
            self.assertEqual(keyed[("total_assets", "value")], Decimal("123747865038.34"))
            self.assertEqual(keyed[("total_assets", "prior_value")], Decimal("143905863420.75"))

    def test_semiannual_stock_table_skips_asset_share_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = Path(directory) / "metrics.json"
            dictionary.write_text(json.dumps({
                "inventory": {"name": "存货", "aliases": ["存货"], "kind": "stock"}
            }, ensure_ascii=False), encoding="utf-8")
            parsed = ParsedDocument(
                meta=DocumentMeta(
                    document_id="abc", path="sample.pdf", file_name="sample.pdf", sha256="0" * 64,
                    page_count=1, company_name="测试公司", security_code="000001", report_year=2026,
                    report_type="semiannual", industry_profile="non_financial", accounting_standard="CAS"
                ),
                pages=[PageRecord(page=1, text="单位：元", method="pypdf", quality_score=1, needs_ocr=False)],
                tables=[TableRecord(page=1, table_index=0, rows=[
                    ["项目名称", "本期期末数", "占总资产比例（%）", "上期期末数", "占总资产比例（%）", "变动比例（%）"],
                    ["存货", "1200", "18.8", "1000", "17.2", "20.0"],
                ])],
            )
            facts = FinancialTableExtractor(dictionary).extract(parsed)
            keyed = {fact.comparison_kind: fact.value for fact in facts}
            self.assertEqual(keyed["value"], Decimal("1200"))
            self.assertEqual(keyed["prior_value"], Decimal("1000"))
            self.assertEqual(keyed["reported_yoy_pct"], Decimal("20.0"))

    def test_annual_summary_keeps_current_prior_and_change_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = Path(directory) / "metrics.json"
            dictionary.write_text(json.dumps({
                "basic_eps": {"name": "基本每股收益", "aliases": ["基本每股收益（元／股）"], "kind": "ratio"}
            }, ensure_ascii=False), encoding="utf-8")
            parsed = ParsedDocument(
                meta=DocumentMeta(
                    document_id="abc", path="sample.pdf", file_name="sample.pdf", sha256="0" * 64,
                    page_count=1, company_name="测试公司", security_code="000001", report_year=2025,
                    report_type="annual", industry_profile="non_financial", accounting_standard="CAS"
                ),
                pages=[PageRecord(page=1, text="单位：元", method="pypdf", quality_score=1, needs_ocr=False)],
                tables=[TableRecord(page=1, table_index=0, rows=[
                    ["主要财务指标", "2025年", "2024年", "本期比上年同期增减(%)", "2023年"],
                    ["基本每股收益（元／股）", "65.66", "68.64", "-4.34", "59.49"],
                ])],
            )
            facts = FinancialTableExtractor(dictionary).extract(parsed)
            keyed = {fact.comparison_kind: fact.value for fact in facts}
            self.assertEqual(keyed["value"], Decimal("65.66"))
            self.assertEqual(keyed["prior_value"], Decimal("68.64"))
            self.assertEqual(keyed["reported_yoy_pct"], Decimal("-4.34"))

    def test_q3_table_semantics_and_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = Path(directory) / "metrics.json"
            dictionary.write_text(json.dumps({
                "revenue": {"name": "营业收入", "aliases": ["营业收入"], "kind": "flow"},
                "operating_cash_flow": {"name": "经营活动产生的现金流量净额", "aliases": ["经营活动产生的现金流量净额", "经营活动产生的现金流"], "kind": "flow"}
            }, ensure_ascii=False), encoding="utf-8")
            parsed = ParsedDocument(
                meta=DocumentMeta(
                    document_id="abc", path="sample.pdf", file_name="sample.pdf", sha256="0" * 64,
                    page_count=1, company_name="测试公司", security_code="000001", report_year=2025,
                    report_type="q3", industry_profile="non_financial", accounting_standard="CAS"
                ),
                pages=[PageRecord(page=1, text="单位：元", method="pypdf", quality_score=1, needs_ocr=False)],
                tables=[TableRecord(page=1, table_index=0, rows=[
                    ["项目", "本报告期", "同比", "年初至报告期末", "同比"],
                    ["营业收入", "100", "5", "280", "8"],
                    ["经营活动产生的现金流", "不适用", "不适用", "70", "-12"]
                ])]
            )
            facts = FinancialTableExtractor(dictionary).extract(parsed)
            keyed = {(f.metric_code, f.period_label, f.comparison_kind): f for f in facts}
            self.assertEqual(keyed[("revenue", "FY2025_Q3_single", "value")].normalized_value, Decimal("100"))
            self.assertEqual(keyed[("revenue", "FY2025_YTD_Q3", "value")].normalized_value, Decimal("280"))
            self.assertEqual(keyed[("operating_cash_flow", "FY2025_YTD_Q3", "reported_yoy_pct")].value, Decimal("-12"))
            self.assertEqual(keyed[("revenue", "FY2025_Q3_single", "value")].source.page, 1)

    def test_nonrecurring_table_is_not_confused_with_summary_table(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = Path(directory) / "metrics.json"
            dictionary.write_text(json.dumps({
                "revenue": {"name": "营业收入", "aliases": ["营业收入"], "kind": "flow"}
            }, ensure_ascii=False), encoding="utf-8")
            parsed = ParsedDocument(
                meta=DocumentMeta(
                    document_id="abc", path="sample.pdf", file_name="sample.pdf", sha256="0" * 64,
                    page_count=2, company_name="测试公司", security_code="000001", report_year=2025,
                    report_type="q3", industry_profile="non_financial", accounting_standard="CAS"
                ),
                pages=[
                    PageRecord(page=1, text="单位：元", method="pypdf", quality_score=1, needs_ocr=False),
                    PageRecord(page=2, text="非经常性损益项目 单位：元", method="pypdf", quality_score=1, needs_ocr=False),
                ],
                tables=[
                    TableRecord(page=1, table_index=0, rows=[
                        ["项目", "本报告期", "同比", "年初至报告期末", "同比"],
                        ["扣除非经常性损益后的净利润", "90", "1", "250", "2"],
                    ]),
                    TableRecord(page=2, table_index=0, rows=[
                        ["非经常性损益项目", "本期金额", "年初至报告期末金额", "说明"],
                        ["政府补助", "3", "8", ""],
                        ["合计", "3", "8", ""],
                    ]),
                ],
            )
            items = FinancialTableExtractor(dictionary).extract_nonrecurring_items(parsed)
            self.assertEqual(len(items), 4)
            keyed = {(item.item_name, item.period_label): item for item in items}
            self.assertEqual(keyed[("政府补助", "FY2025_Q3_single")].amount, Decimal("3"))
            self.assertEqual(keyed[("政府补助", "FY2025_YTD_Q3")].amount, Decimal("8"))
            self.assertTrue(keyed[("合计", "FY2025_YTD_Q3")].is_total)

    def test_ratio_percentage_point_change_is_not_treated_as_prior_value(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = Path(directory) / "metrics.json"
            dictionary.write_text(json.dumps({
                "weighted_roe": {"name": "加权平均净资产收益率", "aliases": ["加权平均净资产收益率"], "kind": "ratio"}
            }, ensure_ascii=False), encoding="utf-8")
            parsed = ParsedDocument(
                meta=DocumentMeta(
                    document_id="abc", path="sample.pdf", file_name="sample.pdf", sha256="0" * 64,
                    page_count=1, company_name="测试公司", security_code="000001", report_year=2025,
                    report_type="q3", industry_profile="non_financial", accounting_standard="CAS"
                ),
                pages=[PageRecord(
                    page=1,
                    text="加权平均净资产收益率 7.75 减少0.64个百分点",
                    method="pypdf",
                    quality_score=1,
                    needs_ocr=False,
                )],
                tables=[],
            )
            facts = FinancialTableExtractor(dictionary).extract(parsed)
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0].value, Decimal("7.75"))
            self.assertEqual(facts[0].comparison_kind, "value")


if __name__ == "__main__":
    unittest.main()
