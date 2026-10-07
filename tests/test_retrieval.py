import unittest

from fin_agent.models import DocumentMeta, PageRecord, ParsedDocument
from fin_agent.retrieval import retrieve_narrative_evidence


class RetrievalTests(unittest.TestCase):
    def test_page_level_provenance_is_preserved(self):
        parsed = ParsedDocument(
            meta=DocumentMeta(
                document_id="abc", path="sample.pdf", file_name="sample.pdf", sha256="0" * 64,
                page_count=1, company_name="测试公司", security_code="000001", report_year=2025,
                report_type="annual", industry_profile="non_financial", accounting_standard="CAS"
            ),
            pages=[PageRecord(
                page=7,
                text="营业收入增长，主要原因是销量提升。经营活动现金流下降，主要受回款结算时点影响。",
                method="pypdf",
                quality_score=0.96,
                needs_ocr=False,
            )],
            tables=[],
        )
        evidence = retrieve_narrative_evidence(parsed)
        self.assertTrue(any(item.category == "performance_drivers" for item in evidence))
        self.assertTrue(any(item.category == "cash_flow" for item in evidence))
        self.assertTrue(all(item.source.page == 7 for item in evidence))


if __name__ == "__main__":
    unittest.main()
