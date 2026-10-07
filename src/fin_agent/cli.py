from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

from .benchmark import run_benchmark
from .config import Settings
from .io_utils import utc_now_iso, write_json
from .integration_check import check_aistudio_ocr, check_deepseek, write_integration_report
from .pipeline import FinancialReportPipeline
from .timeline import write_company_timeline
from .webapp import list_reports, serve


def _settings(args: argparse.Namespace) -> Settings:
    return Settings.load(Path(args.env).resolve() if args.env else None)


def command_doctor(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dependencies = {
        name: bool(importlib.util.find_spec(name))
        for name in ("pydantic", "pypdf", "pdfplumber", "PIL", "mcp", "requests")
    }
    report = {
        "python": sys.version,
        "dependencies": dependencies,
        "pdftoppm": shutil.which("pdftoppm"),
        "settings": settings.redacted(),
        "data_roots": [
            {"path": str(root), "exists": root.exists()} for root in settings.data_roots
        ],
        "pdf_count": len(list_reports(settings)),
        "deepseek_ready": settings.llm_configured(),
        "ocr_ready": settings.ocr_configured(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(dependencies.values()) else 2


def command_list(args: argparse.Namespace) -> int:
    settings = _settings(args)
    for path in list_reports(settings):
        print(path)
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    settings = _settings(args)
    bundle = FinancialReportPipeline(settings).analyze(
        Path(args.input),
        output_root=Path(args.output).resolve() if args.output else None,
        ocr_mode=args.ocr,
        llm_mode=args.llm,
    )
    health = bundle.health_assessment
    print(json.dumps({
        "run_id": bundle.run_id,
        "run_dir": bundle.run_dir,
        "facts": len(bundle.facts),
        "calculations": len(bundle.calculations),
        "findings": len(bundle.findings),
        "validation_issues": len(bundle.validations),
        "health_assessment": ({
            "score": str(health.score),
            "grade": health.grade,
            "coverage_ratio": str(health.coverage_ratio),
            "status": health.status,
        } if health else None),
        "report": str(Path(bundle.run_dir) / "report.html"),
    }, ensure_ascii=False, indent=2))
    return 0


def command_batch(args: argparse.Namespace) -> int:
    settings = _settings(args)
    input_dir = Path(args.input_dir).resolve()
    paths = sorted(input_dir.rglob("*.pdf"))
    if args.limit:
        paths = paths[: args.limit]
    summaries = []
    pipeline = FinancialReportPipeline(settings)
    for path in paths:
        try:
            bundle = pipeline.analyze(path, ocr_mode=args.ocr, llm_mode=args.llm)
            errors = [issue for issue in bundle.validations if issue.severity == "error"]
            warnings = [issue for issue in bundle.validations if issue.severity == "warning"]
            health = bundle.health_assessment
            summaries.append({
                "file": str(path),
                "status": "completed",
                "run_dir": bundle.run_dir,
                "company": bundle.document.company_name,
                "report_type": bundle.document.report_type,
                "industry_profile": bundle.document.industry_profile,
                "facts": len(bundle.facts),
                "calculations": len(bundle.calculations),
                "findings": len(bundle.findings),
                "validation_errors": len(errors),
                "validation_warnings": len(warnings),
                "health_grade": health.grade if health else None,
                "health_coverage_ratio": str(health.coverage_ratio) if health else None,
            })
        except Exception as exc:
            summaries.append({"file": str(path), "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    report = {
        "generated_at": utc_now_iso(),
        "input_dir": str(input_dir),
        "file_count": len(paths),
        "completed_count": sum(item["status"] == "completed" for item in summaries),
        "failed_count": sum(item["status"] == "failed" for item in summaries),
        "zero_fact_count": sum(item.get("facts") == 0 for item in summaries),
        "validation_error_count": sum(item.get("validation_errors", 0) for item in summaries),
        "validation_warning_count": sum(item.get("validation_warnings", 0) for item in summaries),
        "cases": summaries,
    }
    if args.summary_output:
        write_json(Path(args.summary_output).resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    has_failed_case = any(item["status"] == "failed" for item in summaries)
    has_validation_error = report["validation_error_count"] > 0
    return 1 if has_failed_case or (args.fail_on_validation_error and has_validation_error) else 0


def command_timeline(args: argparse.Namespace) -> int:
    settings = _settings(args)
    paths = sorted(Path(args.input_dir).resolve().rglob("*.pdf"))
    pipeline = FinancialReportPipeline(settings)
    bundles = [
        pipeline.analyze(path, ocr_mode=args.ocr, llm_mode="never") for path in paths
    ]
    grouped: dict[str, list] = {}
    for bundle in bundles:
        key = bundle.document.company_name or bundle.document.security_code or bundle.document.file_name
        grouped.setdefault(key, []).append(bundle)
    reports = []
    for company_bundles in grouped.values():
        run_dir = write_company_timeline(company_bundles, settings.output_dir)
        reports.append({"company": company_bundles[0].document.company_name, "run_dir": str(run_dir), "report": str(run_dir / 'report.html')})
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0


def command_serve(args: argparse.Namespace) -> int:
    serve(
        _settings(args),
        host=args.host,
        port=args.port,
        offline=args.offline,
        open_browser=args.open_browser,
    )
    return 0


def command_benchmark(args: argparse.Namespace) -> int:
    settings = _settings(args)
    gold_dir = Path(args.gold_dir).resolve() if args.gold_dir else settings.project_root / "benchmark" / "gold"
    output_path = Path(args.output).resolve() if args.output else settings.output_dir / "benchmark_report.json"
    report = run_benchmark(settings, gold_dir, output_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["mean_fact_accuracy"] == 1 and report["mean_finding_recall"] == 1 else 1


def command_verify_integrations(args: argparse.Namespace) -> int:
    settings = _settings(args)
    output_dir = (
        Path(args.output).resolve()
        if args.output
        else settings.output_dir / f"integration_check_{utc_now_iso().replace(':', '').replace('-', '')[:15]}"
    )
    checks = []
    if args.only in {"all", "deepseek"}:
        checks.append(check_deepseek(settings))
    if args.only in {"all", "ocr"}:
        pdf_path = Path(args.pdf).resolve() if args.pdf else None
        image_path = Path(args.image).resolve() if args.image else None
        if image_path is None and pdf_path is None:
            reports = list_reports(settings)
            pdf_path = reports[0] if reports else None
        checks.append(
            check_aistudio_ocr(
                settings,
                output_dir=output_dir,
                image_path=image_path,
                pdf_path=pdf_path,
                page_number=args.page,
            )
        )
    report_path = write_integration_report(output_dir, checks)
    result = {
        "all_passed": bool(checks) and all(item.get("status") == "passed" for item in checks),
        "report": str(report_path),
        "checks": checks,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["all_passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fin-agent", description="证据驱动的财报分析智能体")
    parser.add_argument("--env", help=".env文件路径，默认使用项目根目录/.env")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="检查依赖、数据目录和密钥配置")
    doctor.set_defaults(func=command_doctor)

    listing = subparsers.add_parser("list", help="列出受控目录中的PDF")
    listing.set_defaults(func=command_list)

    analyze = subparsers.add_parser("analyze", help="分析单份财报")
    analyze.add_argument("--input", required=True)
    analyze.add_argument("--output")
    analyze.add_argument("--ocr", choices=["auto", "never", "force"], default="auto")
    analyze.add_argument("--llm", choices=["auto", "never", "always"], default="auto")
    analyze.set_defaults(func=command_analyze)

    batch = subparsers.add_parser("batch", help="批量分析目录中的PDF")
    batch.add_argument("--input-dir", required=True)
    batch.add_argument("--limit", type=int)
    batch.add_argument("--ocr", choices=["auto", "never", "force"], default="auto")
    batch.add_argument("--llm", choices=["auto", "never", "always"], default="auto")
    batch.add_argument("--summary-output", help="写入批量回归汇总JSON")
    batch.add_argument("--fail-on-validation-error", action="store_true", help="任一报告出现error级校验问题时返回非零状态")
    batch.set_defaults(func=command_batch)

    timeline = subparsers.add_parser("timeline", help="按公司串联多期报告并推导单季度及环比")
    timeline.add_argument("--input-dir", required=True)
    timeline.add_argument("--ocr", choices=["auto", "never", "force"], default="auto")
    timeline.set_defaults(func=command_timeline)

    server = subparsers.add_parser("serve", help="启动本地Web界面")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    server.add_argument("--offline", action="store_true", help="服务端强制关闭OCR和DeepSeek")
    server.add_argument("--open-browser", action="store_true", help="启动后打开默认浏览器")
    server.set_defaults(func=command_serve)

    benchmark = subparsers.add_parser("benchmark", help="运行人工金标准回归测试")
    benchmark.add_argument("--gold-dir")
    benchmark.add_argument("--output")
    benchmark.set_defaults(func=command_benchmark)

    integration = subparsers.add_parser(
        "verify-integrations",
        help="使用真实凭据执行DeepSeek和AI Studio最小化联调验收",
    )
    integration.add_argument("--only", choices=["all", "deepseek", "ocr"], default="all")
    integration.add_argument("--pdf", help="OCR验收使用的PDF；未指定时选择受控目录第一份")
    integration.add_argument("--image", help="OCR验收使用的PNG/JPEG图片，优先于--pdf")
    integration.add_argument("--page", type=int, default=1, help="PDF测试页码，默认1")
    integration.add_argument("--output", help="验收输出目录")
    integration.set_defaults(func=command_verify_integrations)
    return parser



def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
