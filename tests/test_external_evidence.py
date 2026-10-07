import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from fin_agent.external_evidence import iter_external_evidence_files, load_external_evidence
from fin_agent.models import DocumentMeta


class ExternalEvidenceTests(unittest.TestCase):
    def _document(self) -> DocumentMeta:
        return DocumentMeta(
            document_id="doc-1",
            path="report.pdf",
            file_name="report.pdf",
            sha256="0" * 64,
            page_count=1,
            company_name="测试公司",
            security_code="600000",
            report_year=2025,
            report_type="annual",
            industry_profile="non_financial",
            accounting_standard="CAS",
            unit_scale=Decimal("1"),
        )

    def test_loads_only_company_matched_controlled_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            matched = directory / "测试公司-行业价格.json"
            matched.write_text(
                json.dumps(
                    {
                        "title": "测试公司行业价格",
                        "content": "原材料价格同比下降。",
                        "as_of_date": "2026-09-01",
                        "tags": ["industry"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (directory / "其他公司.md").write_text("不相关材料", encoding="utf-8")

            evidence = load_external_evidence(directory, self._document())

            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0].title, "测试公司行业价格")
            self.assertEqual(evidence[0].as_of_date, "2026-09-01")
            self.assertEqual(evidence[0].tags, ["industry"])
            self.assertEqual(len(evidence[0].sha256), 64)
            self.assertTrue(Path(evidence[0].source_path).is_absolute())

    def test_reference_files_are_not_counted_as_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "README.md").write_text("说明", encoding="utf-8")
            (directory / "example.schema.json").write_text("{}", encoding="utf-8")
            (directory / "custom.schema.json").write_text("{}", encoding="utf-8")
            actual = directory / "测试公司-政策.md"
            actual.write_text("测试公司政策材料", encoding="utf-8")

            self.assertEqual(iter_external_evidence_files(directory), [actual])
            evidence = load_external_evidence(directory, self._document())
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0].source_path, str(actual.resolve()))


if __name__ == "__main__":
    unittest.main()
