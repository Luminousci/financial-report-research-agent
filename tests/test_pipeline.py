import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fin_agent.config import Settings
from fin_agent.pipeline import FinancialReportPipeline


class PipelineFailureAuditTests(unittest.TestCase):
    def test_failed_run_finalizes_manifest_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "broken.pdf"
            input_path.write_bytes(b"not-a-pdf")
            output_dir = root / "outputs"
            with patch.dict(
                os.environ,
                {
                    "FIN_AGENT_OUTPUT_DIR": str(output_dir),
                    "OCR_PROVIDER": "none",
                },
                clear=False,
            ):
                settings = Settings.load(root / "missing.env")
                pipeline = FinancialReportPipeline(settings)
                with patch(
                    "fin_agent.pipeline.parse_pdf",
                    side_effect=RuntimeError("synthetic parse failure"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "synthetic parse failure"):
                        pipeline.analyze(input_path, ocr_mode="never", llm_mode="never")

            runs = [path for path in output_dir.iterdir() if path.is_dir()]
            self.assertEqual(len(runs), 1)
            manifest = json.loads((runs[0] / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["error_type"], "RuntimeError")
            self.assertTrue((runs[0] / "ocr_metadata.json").is_file())
            self.assertFalse((runs[0] / "tmp").exists())


if __name__ == "__main__":
    unittest.main()
