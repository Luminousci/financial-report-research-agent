from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import utc_now_iso, write_json
from .pipeline import FinancialReportPipeline


def run_benchmark(settings: Settings, gold_dir: Path, output_path: Path | None = None) -> dict[str, Any]:
    cases = sorted(gold_dir.glob("*.json"))
    pipeline = FinancialReportPipeline(settings)
    results: list[dict[str, Any]] = []
    for case_path in cases:
        gold = json.loads(case_path.read_text(encoding="utf-8"))
        input_path = (settings.project_root.parent / gold["input_path"]).resolve()
        tolerance = Decimal(gold.get("tolerance", "0.01"))
        bundle = pipeline.analyze(input_path, ocr_mode="never", llm_mode="never")
        actual = {
            (fact.metric_code, fact.period_label, fact.comparison_kind): fact.normalized_value
            for fact in bundle.facts
        }
        checks = []
        for expected in gold["expected_facts"]:
            key = (
                expected["metric_code"],
                expected["period_label"],
                expected["comparison_kind"],
            )
            expected_value = Decimal(expected["value"])
            actual_value = actual.get(key)
            passed = actual_value is not None and abs(actual_value - expected_value) <= tolerance
            checks.append(
                {
                    "key": key,
                    "expected": str(expected_value),
                    "actual": str(actual_value) if actual_value is not None else None,
                    "passed": passed,
                }
            )
        actual_findings = {finding.rule_id for finding in bundle.findings}
        finding_checks = [
            {"rule_id": rule_id, "passed": rule_id in actual_findings}
            for rule_id in gold.get("expected_findings", [])
        ]
        provenance_rate = (
            sum(fact.source is not None and fact.source.page > 0 for fact in bundle.facts)
            / max(1, len(bundle.facts))
        )
        results.append(
            {
                "case_id": gold["case_id"],
                "input_path": str(input_path),
                "fact_accuracy": sum(item["passed"] for item in checks) / max(1, len(checks)),
                "finding_recall": (
                    sum(item["passed"] for item in finding_checks) / len(finding_checks)
                    if finding_checks else 1.0
                ),
                "provenance_rate": provenance_rate,
                "fact_checks": checks,
                "finding_checks": finding_checks,
                "run_dir": bundle.run_dir,
            }
        )
    report = {
        "generated_at": utc_now_iso(),
        "case_count": len(results),
        "mean_fact_accuracy": sum(item["fact_accuracy"] for item in results) / max(1, len(results)),
        "mean_finding_recall": sum(item["finding_recall"] for item in results) / max(1, len(results)),
        "mean_provenance_rate": sum(item["provenance_rate"] for item in results) / max(1, len(results)),
        "cases": results,
    }
    if output_path:
        write_json(output_path, report)
    return report
