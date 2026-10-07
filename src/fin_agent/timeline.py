from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .io_utils import slugify, write_json
from .models import AnalysisBundle, FinancialFact


FLOW_METRICS = {
    "revenue",
    "total_revenue",
    "operating_profit",
    "total_profit",
    "net_profit",
    "net_profit_parent",
    "net_profit_excl_nonrecurring",
    "operating_cash_flow",
}


def _representative(facts: list[FinancialFact]) -> FinancialFact:
    counts = Counter(str(fact.normalized_value) for fact in facts)
    winning_value = counts.most_common(1)[0][0]
    return next(fact for fact in facts if str(fact.normalized_value) == winning_value)


def _fact_map(bundles: list[AnalysisBundle]) -> dict[tuple[str, str], FinancialFact]:
    grouped: dict[tuple[str, str], list[FinancialFact]] = defaultdict(list)
    for bundle in bundles:
        for fact in bundle.facts:
            if fact.comparison_kind == "value" and fact.metric_code in FLOW_METRICS:
                grouped[(fact.metric_code, fact.period_label)].append(fact)
    return {key: _representative(facts) for key, facts in grouped.items()}


def _year_from_period(period: str) -> str:
    return period.split("_", 1)[0]


def build_company_timeline(bundles: list[AnalysisBundle]) -> dict[str, Any]:
    if not bundles:
        raise ValueError("At least one analysis bundle is required")
    company_names = [bundle.document.company_name for bundle in bundles if bundle.document.company_name]
    company = Counter(company_names).most_common(1)[0][0] if company_names else "未知公司"
    facts = _fact_map(bundles)
    years = sorted({_year_from_period(period) for _, period in facts})
    derived: list[dict[str, Any]] = []

    for year in years:
        for metric in sorted(FLOW_METRICS):
            q1 = facts.get((metric, f"{year}_Q1"))
            h1 = facts.get((metric, f"{year}_H1"))
            q3 = facts.get((metric, f"{year}_Q3_single"))
            ytd_q3 = facts.get((metric, f"{year}_YTD_Q3"))
            annual = facts.get((metric, year))
            quarter_values: dict[str, tuple[Decimal, list[FinancialFact], str]] = {}
            if q1:
                quarter_values["Q1"] = (q1.normalized_value, [q1], "Q1披露值")
            if q1 and h1:
                quarter_values["Q2"] = (
                    h1.normalized_value - q1.normalized_value,
                    [h1, q1],
                    "H1累计值-Q1值",
                )
            if q3:
                quarter_values["Q3"] = (q3.normalized_value, [q3], "Q3单季披露值")
            elif ytd_q3 and h1:
                quarter_values["Q3"] = (
                    ytd_q3.normalized_value - h1.normalized_value,
                    [ytd_q3, h1],
                    "前三季度累计值-H1累计值",
                )
            if annual and ytd_q3:
                quarter_values["Q4"] = (
                    annual.normalized_value - ytd_q3.normalized_value,
                    [annual, ytd_q3],
                    "全年值-前三季度累计值",
                )

            previous: tuple[str, Decimal] | None = None
            for quarter in ("Q1", "Q2", "Q3", "Q4"):
                if quarter not in quarter_values:
                    continue
                value, source_facts, formula = quarter_values[quarter]
                qoq: Decimal | None = None
                qoq_status = "unavailable"
                if previous and previous[1] != 0:
                    qoq = (value - previous[1]) / abs(previous[1]) * Decimal("100")
                    qoq_status = "ok"
                derived.append(
                    {
                        "metric_code": metric,
                        "metric_name": source_facts[0].metric_name,
                        "period": f"{year}_{quarter}",
                        "value": str(value),
                        "currency": source_facts[0].currency,
                        "formula": formula,
                        "qoq_pct": str(qoq.quantize(Decimal('0.01'))) if qoq is not None else None,
                        "qoq_status": qoq_status,
                        "source_refs": [
                            fact.source.to_dict() for fact in source_facts if fact.source is not None
                        ],
                    }
                )
                previous = (quarter, value)

    return {
        "company_name": company,
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_runs": [
            {
                "run_id": bundle.run_id,
                "file_name": bundle.document.file_name,
                "document_sha256": bundle.document.sha256,
                "report_type": bundle.document.report_type,
            }
            for bundle in bundles
        ],
        "derived_quarters": derived,
        "limitations": [
            "仅对流量指标执行累计值相减；资产负债表时点指标不作单季度推导。",
            "环比受季节性影响，应结合至少两年同季数据验证。",
        ],
    }


def render_timeline_html(timeline: dict[str, Any], output_path: Path) -> None:
    rows = []
    for item in timeline["derived_quarters"]:
        refs = "；".join(
            f"{ref.get('file_name')} 第{ref.get('page')}页" for ref in item["source_refs"]
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(item['metric_name'])}</td>"
            f"<td>{html.escape(item['period'])}</td>"
            f"<td class='num'>{Decimal(item['value']):,.2f}</td>"
            f"<td class='num'>{html.escape(item['qoq_pct'] or 'n.a.')}%</td>"
            f"<td>{html.escape(item['formula'])}</td>"
            f"<td>{html.escape(refs)}</td>"
            "</tr>"
        )
    source_rows = "".join(
        f"<li>{html.escape(item['file_name'])}（{html.escape(item['report_type'])}，SHA256 {html.escape(item['document_sha256'])}）</li>"
        for item in timeline["source_runs"]
    )
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(timeline['company_name'])}跨期财务时间轴</title><style>
body{{margin:0;background:#f4f6f8;color:#172033;font:14px/1.6 Arial,"Microsoft YaHei",sans-serif}}main{{max-width:1200px;margin:auto;padding:32px}}header{{background:#153a5b;color:white;padding:28px;border-radius:12px}}section{{background:white;border:1px solid #d9e0e7;border-radius:10px;padding:18px;overflow:auto;margin-top:16px}}table{{border-collapse:collapse;width:100%;min-width:920px}}th,td{{padding:9px;border-bottom:1px solid #d9e0e7;text-align:left;vertical-align:top}}th{{background:#eaf0f5;color:#153a5b}}.num{{text-align:right;font-variant-numeric:tabular-nums}}h2{{color:#153a5b;margin-top:28px}}
</style></head><body><main><header><h1>{html.escape(timeline['company_name'])}跨期财务时间轴</h1><p>累计值拆分、单季度推导与环比复算</p></header>
<h2>季度推导</h2><section><table><thead><tr><th>指标</th><th>期间</th><th>金额</th><th>环比</th><th>公式</th><th>证据</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="6">缺少可配对的跨期报告</td></tr>'}</tbody></table></section>
<h2>来源文件</h2><section><ul>{source_rows}</ul></section>
<h2>适用边界</h2><section><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in timeline['limitations'])}</ul></section>
</main></body></html>"""
    output_path.write_text(document, encoding="utf-8")


def write_company_timeline(bundles: list[AnalysisBundle], output_root: Path) -> Path:
    timeline = build_company_timeline(bundles)
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{slugify(timeline['company_name'])}_timeline"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "company_timeline.json", timeline)
    render_timeline_html(timeline, run_dir / "report.html")
    return run_dir
