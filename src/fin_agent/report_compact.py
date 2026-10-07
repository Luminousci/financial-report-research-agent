from __future__ import annotations

import html
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import AnalysisBundle, CalculatedMetric, FinancialFact


def _h(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


_INTERNAL_REFERENCE_GROUP = re.compile(
    r"[（(][^（）()]*?(?:document_id|calculations?|request_hash|response_hash|period_label)"
    r"[^（）()]*?[）)]",
    flags=re.IGNORECASE,
)
_INTERNAL_REFERENCE_TOKEN = re.compile(
    r"(?:document_id|request_hash|response_hash)\s*[:：=]?\s*[A-Za-z0-9_-]+\s*[,，;；]?\s*",
    flags=re.IGNORECASE,
)
_ENGLISH_PAGE_TOKEN = re.compile(
    r"\bpage\s+\d+(?:\s*[,，]\s*page\s+\d+)*",
    flags=re.IGNORECASE,
)


def _clean_analysis_text(value: Any) -> str:
    """Remove internal transport metadata and display-only colour wording."""

    text = " ".join(str(value or "").split())
    text = _INTERNAL_REFERENCE_GROUP.sub("", text)
    text = _INTERNAL_REFERENCE_TOKEN.sub("", text)
    text = _ENGLISH_PAGE_TOKEN.sub("", text)
    text = re.sub(r"[（(]\s*[）)]", "", text)
    text = re.sub(r"(?:标红|标绿|用红色显示|用绿色显示|红色表示上涨|绿色表示下跌)", "", text)
    text = re.sub(r"\s+([，。；：,.!?])", r"\1", text)
    text = re.sub(r"([，,])\s*([，,])", r"\1", text)
    return text.strip(" ，,；;")


def _dedupe_sentences(value: Any) -> str:
    """Remove exact repeated sentences while preserving their original order."""

    text = _clean_analysis_text(value)
    sentences = [item.strip() for item in re.split(r"(?<=[。！？；])", text) if item.strip()]
    if len(sentences) <= 1:
        return text
    unique: list[str] = []
    seen: set[str] = set()
    for sentence in sentences:
        key = re.sub(r"\s+", "", sentence)
        if key in seen:
            continue
        seen.add(key)
        unique.append(sentence)
    return "".join(unique)


def _compact_sentences(value: Any, max_sentences: int) -> str:
    """Keep the leading non-repeated evidence sentences without adding ellipses."""

    text = _dedupe_sentences(value)
    sentences = [item.strip() for item in re.split(r"(?<=[。！？；])", text) if item.strip()]
    return "".join(sentences[:max_sentences]) if sentences else text


def _fact(bundle: AnalysisBundle, code: str, kind: str = "value") -> FinancialFact | None:
    items = [item for item in bundle.facts if item.metric_code == code and item.comparison_kind == kind]
    return items[-1] if items else None


def _calculation(bundle: AnalysisBundle, code: str) -> CalculatedMetric | None:
    items = [item for item in bundle.calculations if item.code == code]
    return items[-1] if items else None


def _format_number(value: Decimal, *, percent: bool = False, ratio: bool = False) -> str:
    if percent:
        return f"{value:,.2f}%"
    if ratio:
        return f"{value:,.2f}倍"
    absolute = abs(value)
    if absolute >= Decimal("100000000"):
        return f"{value / Decimal('100000000'):,.2f}亿元"
    if absolute >= Decimal("10000"):
        return f"{value / Decimal('10000'):,.2f}万元"
    return f"{value:,.2f}"


def _fact_display(item: FinancialFact | None) -> str:
    if item is None:
        return "未获取"
    if item.unit in {"%", "百分点"}:
        return _format_number(item.value, percent=True)
    return _format_number(item.normalized_value)


def _change_display(value: Decimal | None, unit: str = "%") -> tuple[str, str]:
    if value is None:
        return "—", "neutral"
    css = "positive" if value > 0 else ("negative" if value < 0 else "neutral")
    suffix = "个百分点" if unit == "百分点" else "%"
    return f"{value:+,.2f}{suffix}", css


def _metric_rows(bundle: AnalysisBundle) -> str:
    specs = [
        ("营业收入", "fact", "revenue", True),
        ("归母净利润", "fact", "net_profit_parent", True),
        ("扣非净利润", "fact", "net_profit_excl_nonrecurring", True),
        ("毛利率", "calc", "gross_margin", True),
        ("净利率", "calc", "net_margin", True),
        ("净资产收益率ROE", "fact", "weighted_roe", True),
        ("经营活动现金流净额", "fact", "operating_cash_flow", True),
        ("资产负债率", "calc", "debt_ratio", False),
        ("流动比率", "calc", "current_ratio", False),
        ("应收账款", "fact", "accounts_receivable", True),
        ("存货", "fact", "inventory", True),
    ]
    rows: list[str] = []
    for name, source_type, code, show_change in specs:
        if source_type == "fact":
            current = _fact(bundle, code)
            prior = _fact(bundle, code, "prior_value")
            reported = _fact(bundle, code, "reported_yoy_pct")
            recomputed = _calculation(bundle, f"{code}_yoy_recomputed")
            current_text = _fact_display(current)
            prior_text = _fact_display(prior)
            change_value = reported.value if reported else (recomputed.value if recomputed else None)
            change_unit = "%"
            page = current.source.page if current and current.source else "—"
        else:
            current_calc = _calculation(bundle, code)
            prior_calc = _calculation(bundle, f"{code}_prior")
            is_ratio = code == "current_ratio"
            current_text = "未获取" if not current_calc or current_calc.value is None else _format_number(current_calc.value, percent=not is_ratio, ratio=is_ratio)
            prior_text = "未获取" if not prior_calc or prior_calc.value is None else _format_number(prior_calc.value, percent=not is_ratio, ratio=is_ratio)
            change_value = (
                current_calc.value - prior_calc.value
                if show_change and current_calc and prior_calc and current_calc.value is not None and prior_calc.value is not None
                else None
            )
            change_unit = "百分点"
            referenced_code = {
                "gross_margin": "revenue",
                "net_margin": "net_profit_parent",
                "debt_ratio": "total_assets",
                "current_ratio": "current_assets",
            }[code]
            referenced_fact = _fact(bundle, referenced_code)
            page = referenced_fact.source.page if referenced_fact and referenced_fact.source else "—"
        change_text, change_class = _change_display(change_value, change_unit) if show_change else ("—", "neutral")
        rows.append(
            "<tr>"
            f"<td>{_h(name)}</td><td class='number'>{_h(current_text)}</td>"
            f"<td class='number muted'>{_h(prior_text)}</td>"
            f"<td class='number {change_class}'>{_h(change_text)}</td>"
            "<td class='number muted' title='缺少可比季度输入'>未获取</td>"
            f"<td class='page-ref'>P{_h(page)}</td></tr>"
        )
    return "".join(rows)


def _business_metric_rows(bundle: AnalysisBundle) -> str:
    if not bundle.business_metrics:
        return "<tr><td colspan='5'>未提取到区间型经营指标</td></tr>"
    rows: list[str] = []
    for item in bundle.business_metrics:
        lower = item.lower_value
        upper = item.upper_value
        trend_class = "positive" if lower > 0 and upper > 0 else ("negative" if lower < 0 and upper < 0 else "neutral")
        rows.append(
            "<tr>"
            f"<td>{_h(item.metric_name)}</td><td>{_h(item.period_label)}</td>"
            f"<td class='number {trend_class}'>{_h(item.display_value)}</td><td>{_h(item.scope)}</td>"
            f"<td class='page-ref'>P{_h(item.source.page)}</td></tr>"
        )
    return "".join(rows)


def _claims(value: Any, limit: int | None = None, count: int | None = None) -> str:
    if isinstance(value, str):
        text = _clean_analysis_text(value)
        return _clip(text, limit) if limit is not None else text
    if isinstance(value, list):
        parts = []
        selected = value if count is None else value[:count]
        for item in selected:
            parts.append(str(item.get("claim") if isinstance(item, dict) else item))
        text = _clean_analysis_text(" ".join(parts))
        return _clip(text, limit) if limit is not None else text
    return ""


def _analysis_text(
    analysis: dict[str, Any] | None,
    direct_key: str,
    legacy_key: str,
    limit: int | None = None,
) -> str:
    if not analysis:
        return "本次运行未启用DeepSeek，当前仅展示程序提取、计算和规则结果。"
    direct = _claims(analysis.get(direct_key), limit)
    if direct:
        return direct
    return _claims(analysis.get(legacy_key), limit) or "未获取"


def _analysis_markup(value: Any) -> str:
    """Render fact/calculation/inference labels as separate, readable evidence lines."""

    text = _clean_analysis_text(value or "未获取")
    labels = "事实披露|披露事实|程序计算|分析推论|核查建议"
    parts = re.split(rf"(?=(?:{labels})\s*[：:])", text)
    rows: list[str] = []
    css_names = {
        "事实披露": "fact",
        "披露事实": "fact",
        "程序计算": "calculation",
        "分析推论": "inference",
        "核查建议": "verification",
    }
    display_names = {"披露事实": "事实披露"}
    for part in parts:
        segment = part.strip()
        if not segment:
            continue
        match = re.match(rf"({labels})\s*[：:]\s*(.*)", segment)
        if match:
            label, body = match.groups()
            sentence_limits = {
                "事实披露": 6,
                "披露事实": 6,
                "程序计算": 4,
                "分析推论": 5,
                "核查建议": 2,
            }
            body = _compact_sentences(body, sentence_limits[label])
            rows.append(
                f"<span class='analysis-line {css_names[label]}'>"
                f"<strong>{_h(display_names.get(label, label))}：</strong>"
                f"<span>{_trend_markup(body)}</span></span>"
            )
        else:
            rows.extend(_unlabelled_analysis_rows(segment))
    return "".join(rows)


_TREND_PATTERN = re.compile(
    r"(?P<up>(?:(?:同比|环比|较(?:上期|上年同期|年初))\s*)?"
    r"(?:增长|上升|上涨|增加|提升|扩大|改善)(?:\s*[+＋-]?\d[\d,.]*\s*(?:%|个百分点|倍))?)"
    r"|(?P<down>(?:(?:同比|环比|较(?:上期|上年同期|年初))\s*)?"
    r"(?:下降|下跌|减少|回落|收窄|降低|跌幅)(?:\s*[+＋-]?\d[\d,.]*\s*(?:%|个百分点|倍))?)"
)


def _trend_markup(value: Any) -> str:
    """Escape text and highlight financial movements using mainland market colours."""

    text = str(value or "")
    output: list[str] = []
    cursor = 0
    for match in _TREND_PATTERN.finditer(text):
        output.append(_h(text[cursor : match.start()]))
        css_class = "trend-up" if match.lastgroup == "up" else "trend-down"
        output.append(f"<span class='{css_class}'>{_h(match.group(0))}</span>")
        cursor = match.end()
    output.append(_h(text[cursor:]))
    return "".join(output)


def _unlabelled_analysis_rows(text: str) -> list[str]:
    """Give legacy model prose a fact/inference layout without changing its wording."""

    text = _dedupe_sentences(text)
    sentences = [item.strip() for item in re.split(r"(?<=[。！？；])", text) if item.strip()]
    inference_terms = (
        "主要来源", "核心因素", "表明", "反映", "说明", "意味着", "体现", "提示",
        "风险", "承压", "扎实", "稳健", "建议", "不宜", "符合", "有限", "暂无法",
        "可能", "依赖", "影响", "需进一步", "未明确披露原因",
    )
    facts = [item for item in sentences if not any(term in item for term in inference_terms)][:6]
    inferences = [item for item in sentences if any(term in item for term in inference_terms)][:5]
    if not sentences:
        facts = [text]
    rows: list[str] = []
    if facts:
        rows.append(
            "<span class='analysis-line fact'><strong>事实披露：</strong>"
            f"<span>{_trend_markup(''.join(facts))}</span></span>"
        )
    if inferences:
        rows.append(
            "<span class='analysis-line inference'><strong>分析推论：</strong>"
            f"<span>{_trend_markup(''.join(inferences))}</span></span>"
        )
    return rows


def _summary_page_refs(bundle: AnalysisBundle) -> str:
    priority_codes = (
        "revenue",
        "net_profit_parent",
        "net_profit_excl_nonrecurring",
        "operating_cash_flow",
        "weighted_roe",
    )
    pages = sorted(
        {
            item.source.page
            for code in priority_codes
            for item in [_fact(bundle, code)]
            if item is not None and item.source is not None
        }
    )
    return "、".join(f"P{page}" for page in pages) or "未获取"


def _calculation_display(bundle: AnalysisBundle, code: str, display_kind: str) -> str:
    item = _calculation(bundle, code)
    if item is None or item.value is None:
        return "未获取"
    if display_kind == "percent_ratio":
        return _format_number(item.value * Decimal("100"), percent=True)
    if display_kind == "percent":
        return _format_number(item.value, percent=True)
    if display_kind == "points":
        return f"{item.value:,.2f}个百分点"
    if display_kind == "ratio":
        return _format_number(item.value, ratio=True)
    if display_kind == "amount":
        return _format_number(item.value)
    return str(item.value)


def _quant_review(bundle: AnalysisBundle) -> str:
    specs = [
        ("非经常性损益占比", "nonrecurring_impact_ratio", "percent_ratio"),
        ("利润现金含量", "cash_conversion", "ratio"),
        ("利润与现金流增速差", "profit_cash_growth_gap", "points"),
        ("两期累计现金转化", "cash_conversion_2period_cumulative", "ratio"),
        ("营运资金占用变动", "working_capital_investment_change", "amount"),
    ]
    items = [
        f"<div><span>{_h(label)}</span><strong>{_h(_calculation_display(bundle, code, kind))}</strong></div>"
        for label, code, kind in specs
    ]
    issue_count = len(bundle.validations)
    validation_text = "未发现问题" if issue_count == 0 else f"{issue_count}项待复核"
    items.append(f"<div><span>结构化质量校验</span><strong>{_h(validation_text)}</strong></div>")
    return "".join(items)


def _health_components(bundle: AnalysisBundle) -> str:
    components = bundle.health_assessment.components
    if not components:
        return "<p class='empty-note'>当前报告不适用完整财务健康评分。</p>"
    return "".join(
        "<div class='rating-component'>"
        f"<span>{_h(item.name)}</span><strong>{_h(item.observed_value)}</strong>"
        f"<small>{_h(item.score)} / {_h(item.max_score)}分</small></div>"
        for item in components
    )


def _report_type_label(value: str) -> str:
    return {
        "annual": "年度报告",
        "semiannual": "半年度报告",
        "q1": "第一季度报告",
        "q3": "第三季度报告",
        "business_update": "经营更新",
        "unknown": "未识别",
    }.get(value, value)


def _industry_label(value: str) -> str:
    return {"non_financial": "非金融企业", "bank": "银行", "unknown": "未识别"}.get(value, value)


def _report_content(bundle: AnalysisBundle) -> dict[str, Any]:
    analysis = bundle.llm_analysis
    tracking_raw = (analysis or {}).get("tracking_metrics", [])
    tracking: list[str] = []
    if isinstance(tracking_raw, list):
        for item in tracking_raw:
            tracking.append(_clean_analysis_text(item.get("claim") if isinstance(item, dict) else item))
    health = bundle.health_assessment
    return {
        "summary": _analysis_text(analysis, "summary_text", "summary"),
        "performance": _analysis_text(analysis, "performance_analysis", "performance"),
        "nonrecurring": _analysis_text(analysis, "nonrecurring_analysis", "performance"),
        "accounting": _analysis_text(analysis, "accounting_change_analysis", "counter_evidence"),
        "cashflow": _analysis_text(analysis, "cash_flow_divergence_analysis", "risks"),
        "other_risks": _analysis_text(analysis, "other_risk_analysis", "risks"),
        "risk_hint": _analysis_text(analysis, "risk_hint", "risks"),
        "rating_note": _claims((analysis or {}).get("rating_note")) or health.methodology,
        "tracking": tracking or ["持续跟踪收入、利润、经营现金流和营运资金变化。"],
    }


def _finding_strip(bundle: AnalysisBundle) -> str:
    if not bundle.findings:
        return "<span class='signal neutral'>未触发配置规则</span>"
    return "".join(
        f"<span class='signal {_h(item.severity)}'>{_h(item.title)}</span>"
        for item in bundle.findings
    )


def _agent_status(analysis: dict[str, Any] | None) -> str:
    if not analysis:
        return "DeepSeek 未启用"
    trace = analysis.get("agent_trace")
    if isinstance(trace, list) and trace:
        passed = sum(item.get("status") == "passed" for item in trace if isinstance(item, dict))
        return f"DeepSeek Agent {passed}/{len(trace)} 完成"
    return "DeepSeek 分析已完成"


def _company_title_size(company_name: str) -> int:
    """Keep long Chinese legal names on one desktop title line."""

    length = len(company_name.strip())
    if length <= 10:
        return 86
    if length <= 15:
        return 70
    if length <= 20:
        return 58
    if length <= 26:
        return 46
    return 38


def render_html(bundle: AnalysisBundle, output_path: Path) -> None:
    meta = bundle.document
    content = _report_content(bundle)
    health = bundle.health_assessment
    grade = "不适用" if meta.report_type == "business_update" else health.grade
    score = "n.a." if health.score is None else f"{health.score}/100"
    company_name = meta.company_name or "上市公司"
    company_title_size = _company_title_size(company_name)
    report_period = f"{meta.report_year or '未知年度'} · {meta.report_type}"
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    tracking_html = "".join(f"<li>{_h(item)}</li>" for item in content["tracking"])
    summary_pages = _summary_page_refs(bundle)
    ocr_coverage = len(set(meta.ocr_pages)) / max(1, meta.page_count)
    health_components_html = _health_components(bundle)
    quant_review_html = _quant_review(bundle)
    if bundle.business_metrics:
        metric_title = "经营更新指标"
        metric_headers = "<th>指标名称</th><th>期间</th><th>披露区间</th><th>口径</th><th>证据页</th>"
        metric_rows = _business_metric_rows(bundle)
    else:
        metric_title = "核心财务指标概览"
        metric_headers = "<th>指标名称</th><th>本期数值</th><th>上期数值</th><th>同比/变动</th><th>环比</th><th>证据页</th>"
        metric_rows = _metric_rows(bundle)
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#071718">
<title>{_h(meta.company_name or meta.file_name)} 财报智能分析报告</title>
<style>
:root{{--bg:#071513;--ink:#F5F6EF;--muted:#91A19A;--line:rgba(255,255,255,.22);--accent:#F3FFC9;--mint:#91CFB5;--orange:#F19A68;--red:#EF8175}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.72 Arial,"Microsoft YaHei",sans-serif}}
body::before{{content:"";position:fixed;inset:0;z-index:-2;background:linear-gradient(90deg,rgba(4,14,13,.98),rgba(5,17,16,.91)),url('/assets/market-analysis-background.jpg') center 42%/cover}}
body::after{{content:"";position:fixed;inset:0;z-index:-1;opacity:.045;background-image:repeating-radial-gradient(circle at 0 0,#fff 0 1px,transparent 1px 5px);background-size:9px 9px}}
a{{color:inherit;text-decoration:none}}button,textarea{{font:inherit}}.report-page{{width:min(1420px,calc(100% - 64px));min-height:100vh;margin:0 auto;padding:34px 0 48px;display:flex;flex-direction:column}}
.topbar{{display:flex;align-items:center;justify-content:space-between;min-height:52px}}.brand{{display:flex;align-items:center;gap:11px;font-weight:800;letter-spacing:.08em}}.brand-mark{{color:var(--accent);font-size:21px}}.brand small{{display:block;color:#7F9089;font-size:9px;letter-spacing:.2em}}.back-link{{border-bottom:1px solid #82938C;padding:5px 0;font-weight:700;font-size:12px}}
.hero{{display:grid;grid-template-columns:minmax(0,1fr) 240px;gap:48px;align-items:end;padding:58px 0 32px;border-bottom:1px solid var(--line)}}.eyebrow{{margin:0 0 17px;color:var(--accent);font-size:11px;font-weight:800;letter-spacing:.18em;text-transform:uppercase}}h1{{max-width:1000px;margin:0;font:800 clamp(48px,6.3vw,92px)/1.02 Arial,"Microsoft YaHei",sans-serif;letter-spacing:-.045em}}.company-title{{display:block;max-width:none;white-space:nowrap;font-size:min(var(--company-title-size),5vw);line-height:1.04;letter-spacing:-.055em}}.report-title-line{{display:block;margin-top:8px}}.filename{{margin:22px 0 0;color:var(--muted)}}
.grade{{text-align:right}}.grade small{{display:block;color:#D8DFD9;letter-spacing:.12em}}.grade strong{{display:block;margin:12px 0 8px;color:var(--accent);font:800 78px/.9 Arial,sans-serif}}.grade span{{color:var(--orange);font-weight:800}}
.meta{{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));border-bottom:1px solid var(--line)}}.meta div{{min-height:78px;padding:17px 12px;border-right:1px solid rgba(255,255,255,.11);color:var(--muted);font-size:9px}}.meta div:first-child{{padding-left:0}}.meta div:last-child{{border-right:0}}.meta .wide{{grid-column:span 2}}.meta strong{{display:block;margin-top:8px;color:var(--ink);font-size:12px;line-height:1.35;overflow-wrap:anywhere}}.meta strong.compact{{font-size:10px}}
.summary{{display:grid;grid-template-columns:150px 1fr;gap:26px;padding:22px 0;border-bottom:1px solid var(--line)}}.summary h2,.section-title{{margin:0;font:700 24px/1.12 Arial,"Microsoft YaHei",sans-serif;letter-spacing:-.025em}}.summary p{{margin:0;color:#E6ECE7;font-size:15px}}.summary-source{{display:block;margin-top:7px;color:var(--muted);font-size:9px;letter-spacing:.04em}}
.metric-heading{{display:flex;align-items:center;justify-content:space-between;margin:23px 0 10px}}.agent-state{{color:var(--mint);font-size:10px;font-weight:800;letter-spacing:.08em}}.signal-row{{display:flex;gap:7px;flex-wrap:wrap}}.signal{{padding:4px 8px;border:1px solid var(--line);border-radius:999px;color:#CBD5D0;font-size:9px}}.signal.high{{border-color:var(--red);color:#FFB1A8}}.signal.medium{{border-color:var(--orange);color:#FFC08F}}
table{{width:100%;border-collapse:collapse}}th{{padding:8px 10px 8px 0;border-bottom:1px solid rgba(255,255,255,.42);color:var(--accent);font-size:10px;text-align:left;letter-spacing:.08em}}td{{padding:7px 10px 7px 0;border-bottom:1px solid rgba(255,255,255,.1);font-size:11px}}td.number{{font-variant-numeric:tabular-nums;font-weight:700}}.muted,.page-ref{{color:var(--muted)}}.positive,.trend-up{{color:#FF776B;font-weight:800}}.negative,.trend-down{{color:#72D4A4;font-weight:800}}.page-ref{{font-size:9px}}
.page-two .topbar{{border-bottom:1px solid var(--line);padding-bottom:14px}}.page-heading{{display:flex;justify-content:space-between;align-items:end;padding:34px 0 24px}}.page-heading h1{{font-size:clamp(42px,5vw,72px)}}.page-heading span{{color:var(--accent);font-weight:800;letter-spacing:.12em}}
.analysis-grid{{display:grid;grid-template-columns:1fr 1fr;gap:0 48px;border-top:1px solid var(--line)}}.analysis-block{{padding:19px 0;border-bottom:1px solid var(--line)}}.analysis-block:nth-child(odd){{padding-right:24px;border-right:1px solid rgba(255,255,255,.11)}}.analysis-block:nth-child(even){{padding-left:0}}.analysis-block.performance{{grid-column:1/-1;padding-right:0;border-right:0}}.analysis-block small{{display:block;margin-bottom:7px;color:var(--accent);font-size:9px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}}.analysis-block h2{{margin:0 0 10px;font:700 22px/1.15 Arial,"Microsoft YaHei",sans-serif}}.analysis-copy{{color:#D5DEDA}}.analysis-line{{display:block;margin-top:7px}}.analysis-line:first-child{{margin-top:0}}.analysis-line strong{{color:#F7FAF5;font-weight:900;margin-right:.35em}}.analysis-line.inference{{margin-top:10px}}.analysis-line.inference strong{{color:var(--accent)}}.analysis-line.calculation strong{{color:var(--mint)}}.analysis-line.verification strong{{color:var(--orange)}}
.decision-grid{{display:grid;grid-template-columns:1.08fr .92fr;gap:42px;margin-top:22px}}.rating-panel,.review-panel{{padding-top:17px;border-top:1px solid rgba(255,255,255,.42)}}.rating-line{{display:flex;align-items:center;gap:24px;margin:10px 0}}.rating-line strong{{color:var(--accent);font:800 54px/.9 Arial,sans-serif}}.rating-line span{{color:var(--orange);font-weight:800}}.rating-panel>.analysis-copy,.review-panel li{{color:#D5DEDA}}.rating-components{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin-top:14px}}.rating-component{{padding-top:8px;border-top:1px solid rgba(255,255,255,.14)}}.rating-component span,.rating-component strong,.rating-component small{{display:block}}.rating-component span{{color:var(--muted);font-size:9px}}.rating-component strong{{margin:3px 0;color:var(--ink);font-size:12px}}.rating-component small{{color:var(--orange);font-size:9px}}.rating-disclaimer{{margin:10px 0 0;color:var(--muted);font-size:9px}}.quant-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:12px}}.quant-grid div{{padding:8px 0;border-top:1px solid rgba(255,255,255,.14)}}.quant-grid span,.quant-grid strong{{display:block}}.quant-grid span{{color:var(--muted);font-size:9px}}.quant-grid strong{{margin-top:3px;color:#EEF4EF;font-size:11px}}.tracking-title{{margin:14px 0 0;font-size:13px}}.review-panel ol{{margin:7px 0 0;padding-left:18px}}.review-panel li{{margin:4px 0;padding-left:3px;font-size:11px}}.empty-note{{color:var(--muted);font-size:10px}}
.report-footer{{display:flex;justify-content:space-between;gap:24px;margin-top:auto;padding-top:18px;border-top:1px solid var(--line);color:#8FA099;font-size:9px}}
.chat-launcher{{position:fixed;right:22px;bottom:22px;z-index:60;min-height:48px;padding:0 18px;border:0;border-radius:999px;background:var(--accent);color:#111715;font-weight:800;cursor:pointer}}.chat-backdrop{{position:fixed;inset:0;z-index:70;background:rgba(0,8,7,.7);opacity:0;visibility:hidden;transition:.2s}}.chat-backdrop.open{{opacity:1;visibility:visible}}.chat-drawer{{position:fixed;top:0;right:0;z-index:71;width:min(520px,100%);height:100dvh;display:grid;grid-template-rows:auto auto minmax(0,1fr) auto;background:#061412;border-left:1px solid #39514B;transform:translateX(102%);transition:transform .28s cubic-bezier(.22,1,.36,1)}}.chat-drawer.open{{transform:translateX(0)}}
.chat-head{{display:flex;justify-content:space-between;gap:16px;padding:22px;border-bottom:1px solid #29413B}}.chat-head small{{color:var(--mint);font-size:9px;font-weight:800;letter-spacing:.15em}}.chat-head h2{{margin:4px 0;font-size:24px}}.chat-head p{{margin:0;color:var(--muted);font-size:11px}}.chat-actions{{display:flex;gap:7px}}.chat-actions button{{width:36px;height:36px;border:1px solid #3A554D;background:transparent;color:var(--ink);cursor:pointer}}.chat-quick{{display:flex;gap:7px;padding:12px 18px;border-bottom:1px solid #29413B;overflow:auto}}.chat-quick button{{flex:0 0 auto;padding:7px 10px;border:1px solid #365049;border-radius:999px;background:transparent;color:#C7D4CE;font-size:11px;cursor:pointer}}.chat-stream{{min-height:0;padding:18px;overflow:auto}}.chat-message{{padding:16px 0;border-top:1px solid rgba(255,255,255,.15)}}.chat-message.user{{padding-left:13%}}.chat-role{{display:block;margin-bottom:6px;color:var(--mint);font-size:9px;font-weight:800;letter-spacing:.12em}}.chat-copy{{margin:0;white-space:pre-wrap;color:#EDF2EE;font-size:13px}}.chat-citations details{{padding:7px 0;border-bottom:1px solid rgba(255,255,255,.09)}}.chat-citations summary{{color:var(--accent);cursor:pointer;font-size:11px}}.chat-citations p{{color:var(--muted);font-size:11px}}.chat-form{{padding:14px 18px 18px;border-top:1px solid #29413B}}.chat-form label{{display:block;margin-bottom:7px;color:var(--muted);font-size:10px}}.chat-compose{{display:grid;grid-template-columns:1fr 82px;gap:9px}}.chat-compose textarea{{min-height:72px;padding:10px 0;border:0;border-bottom:1px solid #789087;background:transparent;color:var(--ink);resize:vertical}}.chat-compose button{{border:0;border-radius:999px;background:var(--accent);font-weight:800;cursor:pointer}}.chat-thinking{{display:none;margin:0 18px 8px;color:var(--muted);font-size:10px}}.chat-thinking.visible{{display:block}}
.reveal-ready .scroll-reveal{{opacity:0;filter:blur(5px);transform:translateY(24px);transition:opacity .62s cubic-bezier(.22,1,.36,1),transform .62s cubic-bezier(.22,1,.36,1),filter .5s}}.reveal-ready .scroll-reveal.is-visible{{opacity:1;filter:none;transform:none}}
@media(max-width:900px){{.report-page{{width:min(100% - 32px,1420px)}}.hero{{grid-template-columns:1fr}}.grade{{text-align:left}}.meta{{grid-template-columns:repeat(2,1fr)}}.meta .wide{{grid-column:span 2}}.summary{{grid-template-columns:1fr}}.analysis-grid,.decision-grid{{grid-template-columns:1fr}}.analysis-block:nth-child(odd){{padding-right:0;border-right:0}}.analysis-block.performance{{grid-column:auto}}.rating-components{{grid-template-columns:repeat(2,1fr)}}}}
@media(max-width:600px){{h1{{font-size:48px;line-height:1.08}}.report-page{{width:min(100% - 24px,1420px)}}.meta{{grid-template-columns:1fr 1fr}}.metric-table-wrap{{overflow-x:auto}}table{{min-width:670px}}.chat-launcher{{right:12px;bottom:12px}}}}
@media(prefers-reduced-motion:reduce){{.reveal-ready .scroll-reveal{{opacity:1;filter:none;transform:none;transition:none}}}}
@media print{{@page{{size:A4 landscape;margin:9mm 11mm 8mm}}html,body{{background:#071513;-webkit-print-color-adjust:exact;print-color-adjust:exact}}body::before,body::after{{position:absolute}}.report-page{{width:100%;height:auto;min-height:0;padding:0;overflow:visible;page-break-after:auto}}.page-one{{page-break-after:always}}.topbar{{min-height:11mm}}.hero{{padding:7mm 0 4mm}}h1{{font-size:34pt}}.grade strong{{font-size:42pt}}.meta div{{min-height:14mm;padding:3mm 1.6mm}}.meta strong{{font-size:8pt}}.summary{{padding:3.2mm 0}}.summary p{{font-size:8.6pt;line-height:1.4}}.metric-heading{{margin:3.4mm 0 1.8mm}}thead{{display:table-header-group}}tr{{break-inside:avoid}}th{{padding:1.05mm 1.4mm 1.05mm 0}}td{{padding:1mm 1.4mm 1mm 0;font-size:7.3pt}}.page-heading{{padding:5mm 0 3mm}}.analysis-block{{padding:2.6mm 0;break-inside:auto}}.analysis-block h2{{font-size:12pt;margin-bottom:1.5mm;break-after:avoid}}.analysis-copy{{font-size:7.5pt;line-height:1.35}}.analysis-line{{margin-top:1.2mm}}.decision-grid{{margin-top:3mm;gap:8mm;break-inside:auto}}.rating-line strong{{font-size:30pt}}.rating-panel>.analysis-copy,.review-panel li{{font-size:7pt;line-height:1.32}}.rating-components{{gap:1.5mm;margin-top:2mm}}.rating-component{{padding-top:1.4mm}}.rating-component span,.rating-component small,.quant-grid span{{font-size:5.8pt}}.rating-component strong,.quant-grid strong{{font-size:7pt}}.quant-grid{{gap:1.5mm;margin-top:2mm}}.quant-grid div{{padding-top:1.4mm}}.tracking-title{{margin-top:2mm}}.report-footer{{break-inside:avoid}}.chat-launcher,.chat-backdrop,.chat-drawer{{display:none!important}}}}
</style>
</head>
<body>
<main>
<section class="report-page page-one" id="page-one">
  <header class="topbar"><a class="brand" href="/"><span class="brand-mark">研</span><span>麦穗终端<small>LEDGER INTELLIGENCE</small></span></a><a class="back-link" href="/">← 返回分析台</a></header>
  <div class="hero scroll-reveal"><div><p class="eyebrow">Financial analysis report / overview</p><h1><span class="company-title" style="--company-title-size:{company_title_size}px">{_h(company_name)}</span><span class="report-title-line">财报智能分析报告</span></h1><p class="filename">{_h(meta.file_name)}</p></div><aside class="grade"><small>FINANCIAL HEALTH</small><strong>{_h(grade)}</strong><span>{_h(score)} · {_h(health.status)}</span></aside></div>
  <section class="meta scroll-reveal"><div>股票代码<strong>{_h(meta.security_code or '未获取')}</strong></div><div>报告期间<strong>{_h(report_period)}</strong></div><div>报告类型<strong>{_h(_report_type_label(meta.report_type))}</strong></div><div>行业配置<strong>{_h(_industry_label(meta.industry_profile))}</strong></div><div>会计准则<strong>{_h(meta.accounting_standard)}</strong></div><div>文本覆盖<strong class="compact">原生 {meta.native_text_ratio:.1%}<br>OCR {ocr_coverage:.1%}</strong></div><div class="wide">运行 ID / 状态<strong class="compact">{_h(bundle.run_id)}<br>{_h(_agent_status(bundle.llm_analysis))}</strong></div></section>
  <section class="summary scroll-reveal"><h2>报告摘要</h2><div><p>{_trend_markup(content['summary'])}</p><small class="summary-source">关键数据证据页：{_h(summary_pages)}</small></div></section>
  <div class="metric-heading scroll-reveal"><h2 class="section-title">{_h(metric_title)}</h2><div class="signal-row">{_finding_strip(bundle)}</div></div>
  <div class="metric-table-wrap scroll-reveal"><table><thead><tr>{metric_headers}</tr></thead><tbody>{metric_rows}</tbody></table></div>
  <footer class="report-footer"><span>所有数值来自确定性抽取与程序计算；模型不重算基础数字。</span><span>OVERVIEW</span></footer>
</section>
<section class="report-page page-two" id="page-two">
  <header class="topbar"><a class="brand" href="/"><span class="brand-mark">研</span><span>麦穗终端<small>LEDGER INTELLIGENCE</small></span></a><span>{_h(meta.company_name or meta.file_name)}</span></header>
  <div class="page-heading scroll-reveal"><h1>经营判断与风险研判</h1><span>ANALYSIS</span></div>
  <section class="analysis-grid">
    <article class="analysis-block performance scroll-reveal"><small>Performance</small><h2>业绩变动简要分析</h2><div class="analysis-copy">{_analysis_markup(content['performance'])}</div></article>
    <article class="analysis-block scroll-reveal"><small>Non-recurring</small><h2>非经常性损益扰动</h2><div class="analysis-copy">{_analysis_markup(content['nonrecurring'])}</div></article>
    <article class="analysis-block scroll-reveal"><small>Accounting policy</small><h2>会计口径变化</h2><div class="analysis-copy">{_analysis_markup(content['accounting'])}</div></article>
    <article class="analysis-block scroll-reveal"><small>Cash conversion</small><h2>利润与现金流背离</h2><div class="analysis-copy">{_analysis_markup(content['cashflow'])}</div></article>
    <article class="analysis-block scroll-reveal"><small>Other risks</small><h2>其他潜在风险</h2><div class="analysis-copy">{_analysis_markup(content['other_risks'])}</div></article>
  </section>
  <section class="decision-grid scroll-reveal"><article class="rating-panel"><small class="eyebrow">Risk & rating</small><h2 class="section-title">风险提示与财务健康评级</h2><div class="rating-line"><strong>{_h(grade)}</strong><span>{_h(score)} · 覆盖率 {_h(health.coverage_ratio * Decimal('100'))}%</span></div><div class="analysis-copy">{_analysis_markup(content['risk_hint'])}</div><div class="rating-components">{health_components_html}</div><p class="rating-disclaimer">该评级是可解释的内部筛查意见，不是信用评级或投资建议。</p></article><article class="review-panel"><small class="eyebrow">Deterministic review</small><h2 class="section-title">程序复算与质量校验</h2><div class="quant-grid">{quant_review_html}</div><h3 class="tracking-title">持续跟踪指标</h3><ol>{tracking_html}</ol></article></section>
  <footer class="report-footer"><span>全部分析依据来自原始财报 PDF，未引入外部预计算数据库指标；完整证据链、公式与日志保存在运行目录。生成时间：{_h(generated_at)}</span><span>ANALYSIS COMPLETE</span></footer>
</section>
</main>
<button class="chat-launcher" id="chat-launcher" type="button">证据问答</button><div class="chat-backdrop" id="chat-backdrop"></div>
<aside class="chat-drawer" id="chat-drawer" data-run-id="{_h(bundle.run_id)}" aria-hidden="true"><div class="chat-head"><div><small>EVIDENCE-GROUNDED DIALOGUE</small><h2>证据问答</h2><p>回答绑定当前报告、页码与运行日志</p></div><div class="chat-actions"><button id="chat-reset" type="button">＋</button><button id="chat-close" type="button">×</button></div></div><div class="chat-quick"><button type="button">业绩变化的主要原因是什么？</button><button type="button">利润与现金流是否背离？</button><button type="button">有哪些值得关注的风险？</button></div><div class="chat-stream" id="chat-stream"><article class="chat-message assistant"><span class="chat-role">EVIDENCE ASSISTANT</span><p class="chat-copy">你可以针对当前报告继续提问。我会检索结构化事实和财报原文，再返回带页码的回答。</p></article></div><div><div class="chat-thinking" id="chat-thinking">检索证据 · 核验页码 · 生成回答</div><form class="chat-form" id="chat-form"><label for="chat-question">针对当前财报提问</label><div class="chat-compose"><textarea id="chat-question" maxlength="2000" placeholder="例如：毛利率下降的原因是什么？" required></textarea><button type="submit">发送</button></div></form></div></aside>
<script>
const reduceMotion=window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const revealTargets=Array.from(document.querySelectorAll(".scroll-reveal"));document.documentElement.classList.add("reveal-ready");
if(reduceMotion||!("IntersectionObserver" in window)){{revealTargets.forEach(item=>item.classList.add("is-visible"));}}else{{const observer=new IntersectionObserver(entries=>entries.forEach(entry=>entry.target.classList.toggle("is-visible",entry.isIntersecting)),{{threshold:.12,rootMargin:"0px 0px -8% 0px"}});revealTargets.forEach(item=>observer.observe(item));}}
const launcher=document.getElementById("chat-launcher"),drawer=document.getElementById("chat-drawer"),backdrop=document.getElementById("chat-backdrop"),closeButton=document.getElementById("chat-close"),resetButton=document.getElementById("chat-reset"),form=document.getElementById("chat-form"),question=document.getElementById("chat-question"),stream=document.getElementById("chat-stream"),thinking=document.getElementById("chat-thinking"),submit=form.querySelector('button[type="submit"]'),runId=drawer.dataset.runId,storageKey=`fin-agent-chat:${{runId}}`;let sessionId=localStorage.getItem(storageKey)||null,historyLoaded=false;
const setOpen=open=>{{drawer.classList.toggle("open",open);backdrop.classList.toggle("open",open);drawer.setAttribute("aria-hidden",String(!open));if(open){{loadHistory();setTimeout(()=>question.focus(),220);}}}};
const addMessage=(role,content,citations=[],mode="")=>{{const article=document.createElement("article");article.className=`chat-message ${{role}}`;const label=document.createElement("span");label.className="chat-role";label.textContent=role==="user"?"YOUR QUESTION":"EVIDENCE ASSISTANT";article.appendChild(label);const copy=document.createElement("p");copy.className="chat-copy";copy.textContent=content;article.appendChild(copy);if(citations.length){{const group=document.createElement("div");group.className="chat-citations";citations.forEach((item,index)=>{{const details=document.createElement("details"),summary=document.createElement("summary"),body=document.createElement("p");summary.textContent=`证据${{index+1}} · ${{item.page?`第${{item.page}}页`:(item.label||"程序证据")}}`;body.textContent=item.text||"";details.append(summary,body);group.appendChild(details);}});article.appendChild(group);}}stream.appendChild(article);stream.scrollTop=stream.scrollHeight;}};
const loadHistory=async()=>{{if(historyLoaded||!sessionId)return;historyLoaded=true;try{{const query=new URLSearchParams({{run_id:runId,session_id:sessionId}}),response=await fetch(`/api/chat/history?${{query}}`);if(!response.ok)return;const payload=await response.json();(payload.messages||[]).forEach(item=>addMessage(item.role,item.content,item.citations||[],item.mode||""));}}catch(error){{historyLoaded=false;}}}};
launcher.addEventListener("click",()=>setOpen(true));closeButton.addEventListener("click",()=>setOpen(false));backdrop.addEventListener("click",()=>setOpen(false));document.addEventListener("keydown",event=>{{if(event.key==="Escape")setOpen(false);}});resetButton.addEventListener("click",()=>{{sessionId=null;historyLoaded=true;localStorage.removeItem(storageKey);stream.replaceChildren();addMessage("assistant","已开始新会话。你可以提出新的问题。");}});document.querySelectorAll(".chat-quick button").forEach(button=>button.addEventListener("click",()=>{{question.value=button.textContent;question.focus();}}));question.addEventListener("keydown",event=>{{if(event.key==="Enter"&&!event.shiftKey){{event.preventDefault();form.requestSubmit();}}}});
form.addEventListener("submit",async event=>{{event.preventDefault();const value=question.value.trim();if(!value||submit.disabled)return;addMessage("user",value);question.value="";submit.disabled=true;thinking.classList.add("visible");try{{const response=await fetch("/api/chat",{{method:"POST",headers:{{"Content-Type":"application/json","Accept":"application/json"}},body:JSON.stringify({{run_id:runId,session_id:sessionId,question:value}})}}),payload=await response.json();if(!response.ok)throw new Error(payload.error||"问答请求失败");sessionId=payload.session_id;localStorage.setItem(storageKey,sessionId);addMessage("assistant",payload.answer,payload.citations||[],payload.mode||"");}}catch(error){{addMessage("assistant",`问答失败：${{error.message}}`);}}finally{{submit.disabled=false;thinking.classList.remove("visible");question.focus();}}}});
</script>
</body></html>"""
    output_path.write_text(document, encoding="utf-8")
