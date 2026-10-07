import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fin_agent.mcp_server import _resolve_artifact_path, _resolve_report_path


class MCPBoundaryTests(unittest.TestCase):
    def test_report_must_be_inside_controlled_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            allowed = root / "data"
            allowed.mkdir()
            report = allowed / "report.pdf"
            report.write_bytes(b"pdf")
            outside = root / "outside.pdf"
            outside.write_bytes(b"pdf")
            settings = SimpleNamespace(data_roots=[allowed])
            self.assertEqual(_resolve_report_path(settings, str(report)), report.resolve())
            with self.assertRaises(PermissionError):
                _resolve_report_path(settings, str(outside))

    def test_artifact_reader_rejects_traversal_and_non_whitelisted_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "outputs"
            run = output / "run-1"
            run.mkdir(parents=True)
            artifact = run / "manifest.json"
            artifact.write_text("{}", encoding="utf-8")
            settings = SimpleNamespace(output_dir=output)
            self.assertEqual(
                _resolve_artifact_path(settings, "run-1", "manifest.json"),
                artifact.resolve(),
            )
            with self.assertRaises(PermissionError):
                _resolve_artifact_path(settings, "../outside", "manifest.json")
            with self.assertRaises(ValueError):
                _resolve_artifact_path(settings, "run-1", "secret.txt")


if __name__ == "__main__":
    unittest.main()
