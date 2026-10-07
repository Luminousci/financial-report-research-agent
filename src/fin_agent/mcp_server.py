from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer

from . import __version__
from .config import Settings
from .pipeline import FinancialReportPipeline
from .webapp import list_reports


mcp = MCPServer(
    "financial-report-agent",
    version=__version__,
    description="Evidence-first, auditable financial report analysis in a controlled data environment.",
    instructions=(
        "List controlled reports before analysis. Use analyze_financial_report only for a listed PDF, "
        "then read whitelisted artifacts by run_id. Treat generated analysis as research assistance."
    ),
)

READABLE_ARTIFACTS = {
    "manifest.json",
    "analysis_bundle.json",
    "financial_facts.json",
    "business_metrics.json",
    "calculated_metrics.json",
    "validation_issues.json",
    "findings.json",
    "narrative_evidence.json",
    "nonrecurring_items.json",
    "health_assessment.json",
    "ocr_metadata.json",
    "llm_metadata.json",
    "llm_analysis.json",
    "report.html",
    "events.jsonl",
}


def _settings() -> Settings:
    return Settings.load()


def _resolve_report_path(settings: Settings, input_path: str) -> Path:
    candidate = Path(input_path).resolve()
    if candidate.suffix.lower() != ".pdf" or not candidate.is_file():
        raise ValueError("input_path must identify an existing PDF")
    allowed = any(
        candidate.is_relative_to(root.resolve())
        for root in settings.data_roots
        if root.exists()
    )
    if not allowed:
        raise PermissionError("input_path is outside FIN_AGENT_DATA_ROOTS")
    return candidate


def _resolve_artifact_path(settings: Settings, run_id: str, artifact: str) -> Path:
    if artifact not in READABLE_ARTIFACTS:
        raise ValueError(f"artifact is not readable; allowed: {sorted(READABLE_ARTIFACTS)}")
    output_root = settings.output_dir.resolve()
    run_dir = (output_root / run_id).resolve()
    if not run_dir.is_relative_to(output_root) or run_dir.parent != output_root:
        raise PermissionError("run_id escapes the configured output directory")
    path = (run_dir / artifact).resolve()
    if not path.is_relative_to(run_dir) or not path.is_file():
        raise FileNotFoundError(f"analysis artifact does not exist: {run_id}/{artifact}")
    return path


@mcp.tool()
def financial_agent_status() -> dict[str, Any]:
    """Return redacted configuration and controlled-data readiness."""
    settings = _settings()
    reports = list_reports(settings)
    return {
        "agent_version": __version__,
        "pdf_count": len(reports),
        "data_roots": [
            {"path": str(root), "exists": root.exists()} for root in settings.data_roots
        ],
        "deepseek_ready": settings.llm_configured(),
        "ocr_ready": settings.ocr_configured(),
        "ocr_provider": settings.ocr_provider,
        "output_dir": str(settings.output_dir),
    }


@mcp.tool()
def list_financial_reports() -> dict[str, Any]:
    """List PDF reports inside the configured controlled data roots."""
    settings = _settings()
    reports = list_reports(settings)
    return {
        "count": len(reports),
        "reports": [
            {
                "path": str(path),
                "file_name": path.name,
                "relative_root": next(
                    (
                        str(path.relative_to(root))
                        for root in settings.data_roots
                        if path.is_relative_to(root)
                    ),
                    path.name,
                ),
            }
            for path in reports
        ],
    }


@mcp.tool()
def analyze_financial_report(
    input_path: str,
    ocr_mode: Literal["auto", "never", "force"] = "auto",
    llm_mode: Literal["auto", "never", "always"] = "auto",
) -> dict[str, Any]:
    """Analyze one controlled PDF and return its run id, evidence counts and report path."""
    settings = _settings()
    path = _resolve_report_path(settings, input_path)
    bundle = FinancialReportPipeline(settings).analyze(
        path,
        ocr_mode=ocr_mode,
        llm_mode=llm_mode,
    )
    health = bundle.health_assessment
    return {
        "run_id": bundle.run_id,
        "run_dir": bundle.run_dir,
        "company": bundle.document.company_name,
        "report_type": bundle.document.report_type,
        "facts": len(bundle.facts),
        "business_metrics": len(bundle.business_metrics),
        "calculations": len(bundle.calculations),
        "findings": len(bundle.findings),
        "validation_issues": len(bundle.validations),
        "health": health.to_dict() if health else None,
        "report": str(Path(bundle.run_dir) / "report.html"),
    }


@mcp.tool()
def read_analysis_artifact(
    run_id: str,
    artifact: str = "analysis_bundle.json",
) -> dict[str, Any]:
    """Read a whitelisted JSON, JSONL, HTML or text artifact from one completed run."""
    settings = _settings()
    path = _resolve_artifact_path(settings, run_id, artifact)
    if path.stat().st_size > 2_000_000:
        raise ValueError("artifact exceeds the 2 MB MCP read limit")
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        content: Any = json.loads(text)
    elif path.suffix == ".jsonl":
        content = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        content = text
    return {
        "run_id": run_id,
        "artifact": artifact,
        "path": str(path),
        "content": content,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
