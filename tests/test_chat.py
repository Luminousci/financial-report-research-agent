import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fin_agent.chat import ChatError, ChatService, build_candidates, retrieve
from fin_agent.config import Settings


class ChatServiceTests(unittest.TestCase):
    def _settings_and_run(self, root: Path) -> tuple[Settings, str, Path]:
        output_dir = root / "outputs"
        run_id = "20260927_test_report"
        run_dir = output_dir / run_id
        run_dir.mkdir(parents=True)
        document = {
            "meta": {"file_name": "示例年报.pdf", "document_id": "doc-1"},
            "pages": [
                {
                    "page": 8,
                    "text": "公司本期营业收入为120亿元，上年同期为100亿元，主要因核心产品销量增长。",
                    "method": "pypdf",
                    "quality_score": 0.99,
                },
                {
                    "page": 16,
                    "text": "经营活动产生的现金流量净额下降，主要因应收账款增加和结算时点变化。",
                    "method": "pypdf",
                    "quality_score": 0.98,
                },
            ],
        }
        bundle = {
            "document": document["meta"],
            "facts": [
                {
                    "metric_code": "revenue",
                    "metric_name": "营业收入",
                    "value": "12000000000",
                    "unit": "元",
                    "period_label": "FY2025",
                    "comparison_kind": "value",
                    "source": {
                        "page": 8,
                        "file_name": "示例年报.pdf",
                        "evidence_text": "营业收入 | 12,000,000,000 | 10,000,000,000",
                    },
                }
            ],
            "calculations": [
                {
                    "code": "revenue_yoy_recomputed",
                    "name": "营业收入同比（程序复算）",
                    "value": "20",
                    "unit": "%",
                    "formula": "(本期-上期)/上期*100",
                    "inputs": {"current": "120", "prior": "100"},
                    "period_label": "FY2025",
                    "status": "ok",
                }
            ],
            "business_metrics": [],
            "narrative_evidence": [],
            "nonrecurring_items": [],
            "findings": [],
            "external_evidence": [],
        }
        (run_dir / "document.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        (run_dir / "analysis_bundle.json").write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        (run_dir / "events.jsonl").write_text("", encoding="utf-8")
        (run_dir / "manifest.json").write_text(
            json.dumps({"run_id": run_id, "outputs": ["analysis_bundle.json", "events.jsonl"]}),
            encoding="utf-8",
        )
        settings = replace(
            Settings.load(),
            project_root=root,
            output_dir=output_dir,
            data_roots=[],
            external_evidence_dir=root / "external",
            deepseek_api_key="",
        )
        return settings, run_id, run_dir

    def test_hybrid_retrieval_prefers_relevant_page_and_fact(self) -> None:
        document = {
            "meta": {"file_name": "示例年报.pdf"},
            "pages": [{"page": 3, "text": "营业收入增长主要来自产品销量提升。"}],
        }
        bundle = {
            "facts": [{
                "metric_name": "营业收入", "value": "120", "unit": "亿元",
                "period_label": "FY2025", "comparison_kind": "value",
                "source": {"page": 3, "file_name": "示例年报.pdf", "evidence_text": "营业收入120亿元"},
            }],
            "calculations": [], "business_metrics": [], "narrative_evidence": [],
            "nonrecurring_items": [], "findings": [], "external_evidence": [],
        }
        hits = retrieve(build_candidates(document, bundle), "营业收入为什么增长？")
        self.assertTrue(hits)
        self.assertEqual(hits[0].page, 3)
        self.assertTrue(any(item.kind == "fact" for item in hits))

    def test_offline_chat_persists_citations_history_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings, run_id, run_dir = self._settings_and_run(Path(temporary_directory))
            service = ChatService(settings, offline=True)
            response = service.ask(run_id, "营业收入为什么增长？")
            self.assertEqual(response["mode"], "retrieval_fallback")
            self.assertTrue(response["citations"])
            self.assertIn("第8页", response["answer"])
            session_id = response["session_id"]
            history = service.history(run_id, session_id)
            self.assertEqual([item["role"] for item in history["messages"]], ["user", "assistant"])
            self.assertTrue((run_dir / "chat" / f"{session_id}.jsonl").is_file())
            self.assertIn("controlled_evidence_chat", (run_dir / "events.jsonl").read_text(encoding="utf-8"))
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("chat/", manifest["outputs"])
            self.assertEqual(manifest["chat"]["session_count"], 1)

    def test_rejects_run_directory_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings, _, _ = self._settings_and_run(Path(temporary_directory))
            service = ChatService(settings, offline=True)
            with self.assertRaises(ChatError):
                service.ask("../outside", "测试问题")


if __name__ == "__main__":
    unittest.main()
