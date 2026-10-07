import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from fin_agent.bundle_io import load_analysis_bundle
from fin_agent.models import AnalysisBundle, DocumentMeta, HealthAssessment


class BundleIoTests(unittest.TestCase):
    def test_load_analysis_bundle_restores_decimal_values(self) -> None:
        bundle = AnalysisBundle(
            document=DocumentMeta(
                document_id="doc",
                path="demo.pdf",
                file_name="demo.pdf",
                sha256="abc",
                page_count=1,
                company_name="测试公司",
                security_code=None,
                report_year=2025,
                report_type="annual",
                industry_profile="non_financial",
                accounting_standard="CAS",
                unit_scale=Decimal("10000"),
            ),
            facts=[],
            calculations=[],
            validations=[],
            findings=[],
            narrative_evidence=[],
            nonrecurring_items=[],
            external_evidence=[],
            health_assessment=HealthAssessment(
                profile="non_financial",
                score=Decimal("82.5"),
                grade="A",
                status="complete",
                coverage_ratio=Decimal("1"),
                methodology="test",
                components=[],
            ),
            llm_analysis={"summary": "ok"},
            run_id="run",
            run_dir="output/run",
        )
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "analysis_bundle.json"
            path.write_text(json.dumps(bundle.to_dict(), ensure_ascii=False), encoding="utf-8")
            loaded = load_analysis_bundle(path)

        self.assertEqual(loaded.document.unit_scale, Decimal("10000"))
        self.assertEqual(loaded.health_assessment.score, Decimal("82.5"))
        self.assertEqual(loaded.llm_analysis, {"summary": "ok"})


if __name__ == "__main__":
    unittest.main()
