import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fin_agent.document import classify_document, infer_report_identity


class DocumentTests(unittest.TestCase):
    def test_infers_company_code_period_and_source_page(self):
        identity = infer_report_identity(
            ["珠海格力电器股份有限公司 证券代码：000651 2026年第一季度报告"],
            "待导入.pdf",
        )
        self.assertEqual(identity["security_code"], "000651")
        self.assertEqual(identity["company_name"], "格力电器")
        self.assertEqual(identity["company_full_name"], "珠海格力电器股份有限公司")
        self.assertEqual(identity["report_period"], "2026Q1")
        self.assertEqual(identity["report_type"], "q1")
        self.assertEqual(identity["source_pages"], [1])
        self.assertGreaterEqual(identity["confidence"], 90)

    def test_infers_generic_company_and_filename_fallback(self):
        generic = infer_report_identity(
            ["北京示例科技股份有限公司 股票代码 688001 2025年半年度报告"],
            "报告.pdf",
        )
        self.assertEqual(generic["company_name"], "北京示例科技")
        self.assertEqual(generic["security_code"], "688001")
        self.assertEqual(generic["report_period"], "2025H1")

        scan = infer_report_identity([], "600519_贵州茅台_2025FY_年度报告.pdf")
        self.assertEqual(scan["company_name"], "贵州茅台")
        self.assertEqual(scan["security_code"], "600519")
        self.assertEqual(scan["report_period"], "2025FY")
        self.assertTrue(scan["requires_ocr"])
        self.assertIn("文件名辅助", scan["source"])

    def test_infers_labelled_company_and_english_stock_code(self):
        identity = infer_report_identity(
            ["公司名称：上海示例科技股份有限公司  Stock Code: 603108  2023年度报告"],
            "2023年报.pdf",
        )
        self.assertEqual(identity["company_name"], "上海示例科技")
        self.assertEqual(identity["security_code"], "603108")
        self.assertEqual(identity["report_period"], "2023FY")

    def test_short_hk_quarter_is_business_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "泡泡玛特第一季度报告.pdf"
            path.write_bytes(b"pdf")
            with patch("fin_agent.document.sha256_file", return_value="a" * 64):
                meta = classify_document(
                    path,
                    2,
                    "POP MART INTERNATIONAL GROUP LIMITED 2025年第一季度业务状况",
                )
            self.assertEqual(meta.report_type, "business_update")
            self.assertEqual(meta.accounting_standard, "IFRS")


if __name__ == "__main__":
    unittest.main()
