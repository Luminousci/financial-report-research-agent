import tempfile
import unittest
from io import BytesIO
from dataclasses import replace
from pathlib import Path

from pypdf import PdfWriter

from fin_agent.config import Settings
from fin_agent.webapp import (
    AnalysisJobRegistry,
    basic_auth_matches,
    inspect_pdf_report,
    merge_report_fields,
    parse_multipart_report,
    render_home_page,
    render_job_page,
    standardized_report_filename,
)


class WebAppTests(unittest.TestCase):
    def _settings(self, root: Path) -> Settings:
        data_root = root / "reports"
        data_root.mkdir()
        (data_root / "示例财报.pdf").write_bytes(b"%PDF-1.4")
        example_root = root / "财报数据" / "示例"
        example_root.mkdir(parents=True)
        (example_root / "000001_示例公司_2025FY_年度报告.pdf").write_bytes(b"%PDF-1.4")
        evidence_root = root / "evidence"
        evidence_root.mkdir()
        (evidence_root / "README.md").write_text("说明", encoding="utf-8")
        return replace(
            Settings.load(),
            project_root=root,
            output_dir=root / "outputs",
            data_roots=[data_root],
            external_evidence_dir=evidence_root,
            deepseek_api_key="",
            aistudio_access_token="",
            ocr_provider="aistudio",
        )

    def test_live_home_shows_controls_without_external_evidence_feature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            page = render_home_page(self._settings(Path(temporary_directory)), offline=False)
            self.assertIn("示例财报.pdf", page)
            self.assertIn('name="ocr"', page)
            self.assertIn('name="llm"', page)
            self.assertNotIn("外部证据", page)
            self.assertNotIn("external_evidence", page)
            self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", page)
            self.assertIn('data-design-seed="2257504322086257687"', page)
            self.assertIn("--paper:#FFF6D8", page)
            self.assertIn("麦穗终端", page)
            self.assertIn('window.addEventListener("pageshow",restoreButton)', page)
            self.assertIn('data-default-label="生成可审计分析报告"', page)
            self.assertIn("分析将在后台执行", page)
            self.assertIn("Cloudflare 长连接超时", page)
            self.assertIn('class="agent-stage"', page)
            self.assertEqual(page.count('class="trace-item'), 6)
            self.assertNotIn('id="motion-toggle"', page)
            self.assertNotIn("暂停动画", page)
            self.assertIn("prefers-reduced-motion:reduce", page)
            self.assertIn("traceIndex=(traceIndex+1)%traceItems.length", page)
            self.assertIn("/assets/market-analysis-background.jpg", page)
            design = (Path(__file__).parents[1] / "config" / "frontend_design.json").read_text(encoding="utf-8")
            self.assertIn("high-contrast-pill-action", design)
            self.assertIn("borderless-transparent-surfaces", design)
            self.assertIn("OddCommon-inspired borderless editorial system", page)
            self.assertIn('class="secondary-action" href="#recent-reports"', page)
            self.assertIn('id="report-file" type="file"', page)
            self.assertIn('/api/import-report', page)
            self.assertIn('/api/inspect-report', page)
            self.assertIn('reportFile.addEventListener("change",inspectSelectedReport)', page)
            self.assertIn("自动识别完成", page)
            self.assertIn('id="report-select"', page)
            self.assertIn('id="source-example"', page)
            self.assertIn('id="source-local"', page)
            self.assertIn("示例研报", page)
            self.assertIn("本地研报", page)
            self.assertIn("受控示例财报", page)
            self.assertNotIn("先选择财报来源", page)
            self.assertNotIn("两种来源不会同时显示", page)
            self.assertNotIn("使用随项目交付的三份 PDF", page)
            self.assertIn("当前环境内置 1 份示例 PDF", page)
            self.assertIn('id="example-panel" role="tabpanel" aria-labelledby="source-example" hidden', page)
            self.assertIn('id="local-panel" role="tabpanel" aria-labelledby="source-local" hidden', page)
            self.assertIn('setReportSource("")', page)
            self.assertNotIn('class="source-choice active"', page)
            self.assertIn('setReportSource("local")', page)
            self.assertIn("正在读取 PDF 并定位公司及报告期", page)
            self.assertNotIn("前 8 页", page)
            self.assertIn("股票代码_公司简称_报告期_报告类型.pdf", page)
            self.assertIn("IntersectionObserver", page)
            self.assertIn("scroll-reveal", page)
            self.assertIn('classList.toggle("is-visible",entry.isIntersecting)', page)
            self.assertNotIn("revealObserver.unobserve", page)

    def test_offline_home_forces_hidden_never_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            page = render_home_page(self._settings(Path(temporary_directory)), offline=True)
            self.assertIn("离线兜底已启用", page)
            self.assertIn('name="ocr" value="never"', page)
            self.assertIn('name="llm" value="never"', page)
            self.assertNotIn('value="force"', page)

    def test_optional_basic_authentication(self) -> None:
        import base64

        encoded = base64.b64encode("viewer:temporary-secret".encode("utf-8")).decode("ascii")
        self.assertTrue(basic_auth_matches(f"Basic {encoded}", "viewer", "temporary-secret"))
        self.assertFalse(basic_auth_matches(f"Basic {encoded}", "viewer", "wrong-secret"))
        self.assertFalse(basic_auth_matches(None, "viewer", "temporary-secret"))
        self.assertTrue(basic_auth_matches(None, "", ""))

    def test_analysis_job_registry_returns_isolated_snapshots(self) -> None:
        registry = AnalysisJobRegistry()
        job_id = registry.create("示例财报.pdf")
        snapshot = registry.get(job_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["status"], "queued")
        snapshot["status"] = "tampered"
        registry.update(job_id, status="running", message="正在分析")
        current = registry.get(job_id)
        self.assertEqual(current["status"], "running")
        self.assertEqual(current["message"], "正在分析")

    def test_job_page_polls_and_replaces_history_on_completion(self) -> None:
        page = render_job_page("abc123")
        self.assertIn("/api/job?", page)
        self.assertIn("window.location.replace(data.report_url)", page)
        self.assertIn("无需重复提交", page)
        self.assertIn("abc123", page)
        self.assertIn("正在生成<br>财报分析报告", page)
        self.assertNotIn("正在生成两页", page)

    def test_standardized_report_filename(self) -> None:
        self.assertEqual(
            standardized_report_filename("600519", " 贵州 茅台 ", "2025fy", "annual"),
            "600519_贵州茅台_2025FY_年度报告.pdf",
        )
        with self.assertRaisesRegex(ValueError, "报告期"):
            standardized_report_filename("600519", "贵州茅台", "2025", "annual")

    def test_parse_multipart_report(self) -> None:
        boundary = "----financial-agent-test"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"security_code\"\r\n\r\n600519\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"report_file\"; filename=\"report.pdf\"\r\n"
            "Content-Type: application/pdf\r\n\r\n"
        ).encode("utf-8") + b"%PDF-1.7 test\r\n" + f"--{boundary}--\r\n".encode("utf-8")
        fields, filename, payload = parse_multipart_report(
            f"multipart/form-data; boundary={boundary}", body
        )
        self.assertEqual(fields["security_code"], "600519")
        self.assertEqual(filename, "report.pdf")
        self.assertTrue(payload.startswith(b"%PDF-"))

    def test_inspect_pdf_uses_filename_when_page_has_no_text(self) -> None:
        stream = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(stream)
        result = inspect_pdf_report(
            stream.getvalue(),
            "600519_贵州茅台_2025FY_年度报告.pdf",
        )
        self.assertEqual(result["security_code"], "600519")
        self.assertEqual(result["company_name"], "贵州茅台")
        self.assertEqual(result["report_period"], "2025FY")
        self.assertEqual(result["report_type"], "annual")
        self.assertTrue(result["requires_ocr"])
        self.assertEqual(result["page_count"], 1)

    def test_inspect_pdf_checks_up_to_eight_pages(self) -> None:
        stream = BytesIO()
        writer = PdfWriter()
        for _ in range(10):
            writer.add_blank_page(width=612, height=792)
        writer.write(stream)
        result = inspect_pdf_report(stream.getvalue(), "600519_贵州茅台_2025FY_年度报告.pdf")
        self.assertEqual(result["pages_inspected"], 8)

    def test_merge_report_fields_preserves_manual_edits_and_fills_missing(self) -> None:
        merged = merge_report_fields(
            {"security_code": "", "company_name": "人工名称", "report_period": "", "report_type": ""},
            {
                "security_code": "600519",
                "company_name": "贵州茅台",
                "report_period": "2025FY",
                "report_type": "annual",
            },
        )
        self.assertEqual(merged["security_code"], "600519")
        self.assertEqual(merged["company_name"], "人工名称")
        self.assertEqual(merged["report_period"], "2025FY")
        self.assertEqual(merged["report_type"], "annual")


if __name__ == "__main__":
    unittest.main()
