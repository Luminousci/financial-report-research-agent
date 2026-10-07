from __future__ import annotations

import argparse
from pathlib import Path

from fin_agent.bundle_io import load_analysis_bundle, load_parsed_document
from fin_agent.business_update import detect_business_update_signals, extract_business_metrics
from fin_agent.health import assess_health
from fin_agent.io_utils import write_json
from fin_agent.report import render_html
from fin_agent.validation import validate


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh persisted report HTML without rerunning analysis.")
    parser.add_argument("output_root", nargs="?", default="outputs", help="Directory containing run folders")
    args = parser.parse_args()

    output_root = Path(args.output_root).resolve()
    refreshed = 0
    failed = 0
    for bundle_path in sorted(output_root.glob("*/analysis_bundle.json")):
        try:
            bundle = load_analysis_bundle(bundle_path)
            document_path = bundle_path.with_name("document.json")
            if bundle.document.report_type == "business_update" and document_path.exists():
                parsed = load_parsed_document(document_path)
                previous_count = len(bundle.business_metrics)
                bundle.business_metrics = extract_business_metrics(parsed)
                bundle.health_assessment = assess_health(parsed, bundle.facts, bundle.calculations)
                bundle.validations = validate(parsed, bundle.facts, bundle.business_metrics)
                bundle.findings = [
                    item for item in bundle.findings if not item.rule_id.startswith("business_")
                ] + detect_business_update_signals(bundle.business_metrics)
                if len(bundle.business_metrics) != previous_count and bundle.llm_analysis is not None:
                    bundle.llm_analysis = None
                    if "历史运行已补充经营指标，DeepSeek结论需重新运行后生成。" not in bundle.document.limitations:
                        bundle.document.limitations.append(
                            "历史运行已补充经营指标，DeepSeek结论需重新运行后生成。"
                        )
                write_json(bundle_path.with_name("business_metrics.json"), bundle.business_metrics)
                write_json(bundle_path.with_name("health_assessment.json"), bundle.health_assessment)
                write_json(bundle_path.with_name("validation_issues.json"), bundle.validations)
                write_json(bundle_path.with_name("findings.json"), bundle.findings)
                write_json(bundle_path, bundle)
            render_html(bundle, bundle_path.with_name("report.html"))
            refreshed += 1
            print(f"refreshed: {bundle_path.parent.name}")
        except Exception as exc:  # keep processing independent archived runs
            failed += 1
            print(f"failed: {bundle_path.parent.name}: {type(exc).__name__}: {exc}")
    print(f"done: refreshed={refreshed}, failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
