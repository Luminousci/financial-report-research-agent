from __future__ import annotations

import platform
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .anomaly import detect_anomalies
from .audit import AuditLogger
from .business_update import detect_business_update_signals, extract_business_metrics
from .calculations import calculate_metrics
from .config import Settings
from .deepseek import DeepSeekClient, DeepSeekError
from .health import assess_health
from .extractor import FinancialTableExtractor
from .io_utils import read_json, slugify, utc_now_iso, write_json
from .models import AnalysisBundle
from .ocr import OCRProvider, provider_from_settings
from .pdf_parser import parse_pdf
from .report import render_html
from .retrieval import retrieve_narrative_evidence
from .validation import validate


class FinancialReportPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.extractor = FinancialTableExtractor(
            settings.project_root / "config" / "metric_dictionary.json"
        )

    def analyze(
        self,
        input_path: Path,
        *,
        output_root: Path | None = None,
        ocr_mode: str | None = None,
        llm_mode: str = "auto",
    ) -> AnalysisBundle:
        input_path = input_path.resolve()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        run_id = f"{timestamp}_{slugify(input_path.stem)}"
        run_dir = (output_root or self.settings.output_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        ocr_provider = provider_from_settings(self.settings)
        try:
            return self._analyze_impl(
                input_path,
                run_id=run_id,
                run_dir=run_dir,
                ocr_provider=ocr_provider,
                ocr_mode=ocr_mode,
                llm_mode=llm_mode,
            )
        except Exception as exc:
            ocr_calls = ocr_provider.call_records if ocr_provider else []
            write_json(run_dir / "ocr_metadata.json", ocr_calls)
            manifest_path = run_dir / "manifest.json"
            manifest = read_json(manifest_path) if manifest_path.exists() else {"run_id": run_id}
            manifest.update(
                {
                    "status": "failed",
                    "completed_at": utc_now_iso(),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                    "ocr_provider": ocr_provider.name if ocr_provider else None,
                    "ocr_calls": ocr_calls,
                }
            )
            write_json(manifest_path, manifest)
            raise
        finally:
            temp_dir = run_dir / "tmp"
            if temp_dir.exists() and temp_dir.resolve().is_relative_to(run_dir.resolve()):
                shutil.rmtree(temp_dir)

    def _analyze_impl(
        self,
        input_path: Path,
        *,
        run_id: str,
        run_dir: Path,
        ocr_provider: OCRProvider | None,
        ocr_mode: str | None,
        llm_mode: str,
    ) -> AnalysisBundle:
        temp_dir = run_dir / "tmp"
        audit = AuditLogger(run_dir / "events.jsonl", run_id)
        manifest: dict[str, Any] = {
            "run_id": run_id,
            "status": "running",
            "started_at": utc_now_iso(),
            "agent_version": __version__,
            "python_version": sys.version,
            "platform": platform.platform(),
            "input_path": str(input_path),
            "settings": self.settings.redacted(),
            "steps": [],
        }
        write_json(run_dir / "manifest.json", manifest)

        with audit.step("parse_document", "pdf_parser", {"path": str(input_path)}) as state:
            parsed = parse_pdf(
                input_path,
                self.settings,
                temp_dir,
                ocr_provider=ocr_provider,
                ocr_mode=ocr_mode,
            )
            state["output"] = {
                "document_id": parsed.meta.document_id,
                "pages": parsed.meta.page_count,
                "tables": len(parsed.tables),
                "ocr_pages": parsed.meta.ocr_pages,
                "ocr_calls": ocr_provider.call_records if ocr_provider else [],
            }
        write_json(run_dir / "document.json", parsed)
        write_json(
            run_dir / "ocr_metadata.json",
            ocr_provider.call_records if ocr_provider else [],
        )

        with audit.step("extract_financial_facts", "financial_table_extractor") as state:
            facts = self.extractor.extract(parsed)
            state["output"] = [fact.to_dict() for fact in facts]
        write_json(run_dir / "financial_facts.json", [fact.to_dict() for fact in facts])

        with audit.step("extract_business_metrics", "business_update_extractor") as state:
            business_metrics = extract_business_metrics(parsed)
            state["output"] = [item.to_dict() for item in business_metrics]
        write_json(
            run_dir / "business_metrics.json",
            [item.to_dict() for item in business_metrics],
        )

        with audit.step("extract_nonrecurring_items", "financial_table_extractor") as state:
            nonrecurring_items = self.extractor.extract_nonrecurring_items(parsed)
            state["output"] = [item.to_dict() for item in nonrecurring_items]
        write_json(
            run_dir / "nonrecurring_items.json",
            [item.to_dict() for item in nonrecurring_items],
        )

        with audit.step("retrieve_narrative_evidence", "keyword_retriever") as state:
            narrative_evidence = retrieve_narrative_evidence(parsed)
            state["output"] = [item.to_dict() for item in narrative_evidence]
        write_json(
            run_dir / "narrative_evidence.json",
            [item.to_dict() for item in narrative_evidence],
        )

        # External-evidence ingestion is intentionally disabled in this release.
        # Keep the empty bundle field for backward-compatible artifact loading.
        external_evidence = []

        with audit.step("calculate_metrics", "deterministic_calculator") as state:
            calculations = calculate_metrics(facts)
            state["output"] = [item.to_dict() for item in calculations]
        write_json(run_dir / "calculated_metrics.json", [item.to_dict() for item in calculations])

        with audit.step("assess_financial_health", "transparent_scorecard") as state:
            health_assessment = assess_health(parsed, facts, calculations)
            state["output"] = health_assessment.to_dict()
        write_json(run_dir / "health_assessment.json", health_assessment)

        with audit.step("validate", "validation_engine") as state:
            validations = validate(parsed, facts, business_metrics)
            state["output"] = [item.to_dict() for item in validations]
        write_json(run_dir / "validation_issues.json", [item.to_dict() for item in validations])

        with audit.step("detect_anomalies", "rule_engine") as state:
            findings = detect_anomalies(
                parsed,
                facts,
                calculations,
                self.settings.project_root / "config" / "anomaly_rules.json",
            )
            findings.extend(detect_business_update_signals(business_metrics))
            state["output"] = [item.to_dict() for item in findings]
        write_json(run_dir / "findings.json", [item.to_dict() for item in findings])

        llm_analysis = None
        llm_metadata: dict[str, Any] = {"status": "skipped"}
        should_use_llm = llm_mode == "always" or (
            llm_mode == "auto" and self.settings.llm_configured()
        )
        if should_use_llm:
            if not self.settings.llm_configured():
                raise RuntimeError("llm_mode=always but DEEPSEEK_API_KEY is missing")
            deepseek_client = DeepSeekClient(self.settings)
            try:
                with audit.step("deepseek_analysis", "deepseek_chat_completions") as state:
                    llm_analysis, llm_metadata = deepseek_client.analyze(
                        parsed,
                        facts,
                        calculations,
                        validations,
                        findings,
                        narrative_evidence,
                        nonrecurring_items,
                        health_assessment,
                        business_metrics,
                        run_dir / "cache",
                    )
                    state["output"] = llm_metadata
            except DeepSeekError as exc:
                parsed.meta.limitations.append(f"DeepSeek analysis failed: {exc}")
                llm_metadata = deepseek_client.last_call_metadata | {
                    "status": "failed",
                    "error": str(exc),
                }
        elif llm_mode not in {"auto", "never"}:
            raise ValueError(f"Unsupported LLM mode: {llm_mode}")
        write_json(run_dir / "llm_metadata.json", llm_metadata)
        if llm_analysis is not None:
            write_json(run_dir / "llm_analysis.json", llm_analysis)
            write_json(
                run_dir / "agent_outputs.json",
                llm_analysis.get("agent_results", {}),
            )

        bundle = AnalysisBundle(
            document=parsed.meta,
            facts=facts,
            calculations=calculations,
            validations=validations,
            findings=findings,
            narrative_evidence=narrative_evidence,
            nonrecurring_items=nonrecurring_items,
            external_evidence=external_evidence,
            health_assessment=health_assessment,
            llm_analysis=llm_analysis,
            run_id=run_id,
            run_dir=str(run_dir.resolve()),
            business_metrics=business_metrics,
        )
        write_json(run_dir / "analysis_bundle.json", bundle)
        render_html(bundle, run_dir / "report.html")

        manifest.update(
            {
                "status": "completed",
                "completed_at": utc_now_iso(),
                "document_id": parsed.meta.document_id,
                "document_sha256": parsed.meta.sha256,
                "ocr_provider": ocr_provider.name if ocr_provider else None,
                "ocr_pages": parsed.meta.ocr_pages,
                "ocr_calls": ocr_provider.call_records if ocr_provider else [],
                "deepseek": llm_metadata,
                "outputs": [
                    "analysis_bundle.json",
                    "ocr_metadata.json",
                    "financial_facts.json",
                    "business_metrics.json",
                    "calculated_metrics.json",
                    "validation_issues.json",
                    "findings.json",
                    "narrative_evidence.json",
                    "nonrecurring_items.json",
                    "health_assessment.json",
                    "report.html",
                    "events.jsonl",
                ] + (["llm_analysis.json", "agent_outputs.json"] if llm_analysis is not None else []),
            }
        )
        write_json(run_dir / "manifest.json", manifest)
        return bundle
