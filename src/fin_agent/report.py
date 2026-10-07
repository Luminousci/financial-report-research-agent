from __future__ import annotations

import html
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import AnalysisBundle


def _h(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _format_decimal(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _fact_rows(bundle: AnalysisBundle) -> str:
    rows = []
    for fact in bundle.facts:
        source = fact.source
        rows.append(
            "<tr>"
            f"<td>{_h(fact.metric_name)}</td>"
            f"<td>{_h(fact.period_label)}</td>"
            f"<td>{_h(fact.comparison_kind)}</td>"
            f"<td class='num'>{_h(_format_decimal(fact.normalized_value))}</td>"
            f"<td>{_h(fact.currency)} / {_h(fact.unit)}</td>"
            f"<td>{_h(source.page if source else '')}</td>"
            f"<td class='evidence'>{_h(source.evidence_text if source else '')}</td>"
            "</tr>"
        )
    return "\n".join(rows) or "<tr><td colspan='7'>未提取到标准化财务字段</td></tr>"


def _calculation_rows(bundle: AnalysisBundle) -> str:
    rows = []
    for item in bundle.calculations:
        value = "n.a." if item.value is None else _format_decimal(item.value)
        rows.append(
            "<tr>"
            f"<td>{_h(item.name)}</td><td>{_h(item.period_label)}</td>"
            f"<td class='num'>{_h(value)} {_h(item.unit)}</td>"
            f"<td>{_h(item.formula)}</td><td>{_h(json.dumps(item.inputs, ensure_ascii=False))}</td>"
            "</tr>"
        )
    return "\n".join(rows) or "<tr><td colspan='5'>无可执行计算</td></tr>"


def _finding_cards(bundle: AnalysisBundle) -> str:
    cards = []
    for item in bundle.findings:
        ref_labels = []
        for ref in item.evidence_refs:
            if not isinstance(ref, dict):
                ref_labels.append(str(ref))
            elif ref.get("page") is not None:
                ref_labels.append(f"{ref.get('file_name', '源文件')} 第{ref['page']}页")
            elif ref.get("code"):
                ref_labels.append(f"计算结果 {ref['code']}")
            else:
                ref_labels.append("计算结果")
        refs = ", ".join(ref_labels)
        cards.append(
            f"<article class='finding {item.severity}'>"
            f"<h3>{_h(item.title)} <span>{_h(item.severity)}</span></h3>"
            f"<p>{_h(item.description)}</p>"
            f"<p><strong>反向核验：</strong>{_h(item.counter_evidence or '无')}</p>"
            f"<p class='source'><strong>证据：</strong>{_h(refs)}</p>"
            "</article>"
        )
    return "\n".join(cards) or "<p>未触发已配置的异常规则。</p>"


def _validation_rows(bundle: AnalysisBundle) -> str:
    rows = []
    for item in bundle.validations:
        rows.append(
            f"<tr><td>{_h(item.severity)}</td><td>{_h(item.code)}</td><td>{_h(item.message)}</td></tr>"
        )
    return "\n".join(rows) or "<tr><td colspan='3'>未发现结构化校验问题</td></tr>"


def _llm_section(analysis: dict[str, Any] | None) -> str:
    if analysis is None:
        return "<p>本次运行未启用DeepSeek，报告仅包含确定性提取、计算与规则结果。</p>"
    return f"<pre>{_h(json.dumps(analysis, ensure_ascii=False, indent=2))}</pre>"


def _narrative_rows(bundle: AnalysisBundle) -> str:
    rows = []
    for item in bundle.narrative_evidence:
        rows.append(
            "<tr>"
            f"<td>{_h(item.category)}</td><td>{_h(item.source.page)}</td>"
            f"<td class='num'>{item.score:.2f}</td><td class='evidence'>{_h(item.text)}</td>"
            "</tr>"
        )
    return "\n".join(rows) or "<tr><td colspan='4'>未检索到高相关叙事证据</td></tr>"


def _nonrecurring_rows(bundle: AnalysisBundle) -> str:
    rows = []
    for item in bundle.nonrecurring_items:
        rows.append(
            "<tr>"
            f"<td>{_h(item.item_name)}</td><td>{_h(item.period_label)}</td>"
            f"<td class='num'>{_h(_format_decimal(item.normalized_amount))}</td>"
            f"<td>{_h(item.currency)} / {_h(item.unit)}</td><td>{item.source.page}</td>"
            "</tr>"
        )
    return "\n".join(rows) or "<tr><td colspan='5'>未提取到非经常性损益明细</td></tr>"


def _business_metric_rows(bundle: AnalysisBundle) -> str:
    rows = []
    for item in bundle.business_metrics:
        rows.append(
            "<tr>"
            f"<td>{_h(item.metric_name)}</td><td>{_h(item.period_label)}</td>"
            f"<td class='num'>{_h(item.display_value)}</td><td>{_h(item.comparison_kind)}</td>"
            f"<td>{_h(item.source.page)}</td><td class='evidence'>{_h(item.source.evidence_text)}</td>"
            "</tr>"
        )
    return "\n".join(rows) or "<tr><td colspan='6'>未提取到区间型经营指标</td></tr>"


def _health_section(bundle: AnalysisBundle) -> str:
    health = bundle.health_assessment
    if health.profile == "business_update":
        limitations = "".join(f"<li>{_h(item)}</li>" for item in health.limitations)
        return (
            "<div class='health-score'><strong>不适用</strong>"
            "<span>业务更新公告不执行完整财报健康评分</span></div>"
            f"<p>{_h(health.methodology)}</p><ul>{limitations}</ul>"
        )
    score = "n.a." if health.score is None else f"{health.score} / 100"
    rows = []
    for item in health.components:
        rows.append(
            "<tr>"
            f"<td>{_h(item.name)}</td><td class='num'>{_h(item.observed_value)}</td>"
            f"<td class='num'>{_h(item.score)} / {_h(item.max_score)}</td>"
            f"<td>{_h(item.rationale)}</td>"
            "</tr>"
        )
    limitations = "".join(f"<li>{_h(item)}</li>" for item in health.limitations)
    return (
        f"<div class='health-score'><strong>{_h(health.grade)}</strong><span>{_h(score)}</span>"
        f"<span>覆盖率 {_h(health.coverage_ratio * Decimal('100'))}%</span><span>{_h(health.status)}</span></div>"
        f"<p>{_h(health.methodology)}</p>"
        "<table><thead><tr><th>评分项</th><th>观测值</th><th>得分</th><th>透明规则</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=\"4\">指标不足，未形成评分</td></tr>'}</tbody></table>"
        f"<ul>{limitations}</ul>"
    )


def render_html(bundle: AnalysisBundle, output_path: Path) -> None:
    meta = bundle.document
    is_business_update = meta.report_type == "business_update"
    stamp_title = "BUSINESS UPDATE" if is_business_update else "FINANCIAL HEALTH"
    stamp_grade = "经营快报" if is_business_update else bundle.health_assessment.grade
    stamp_status = "非完整三表" if is_business_update else bundle.health_assessment.status
    ocr_coverage = len(set(meta.ocr_pages)) / max(1, meta.page_count)
    limitations = "".join(f"<li>{_h(item)}</li>" for item in meta.limitations) or "<li>无</li>"
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#071718">
<title>{_h(meta.company_name or meta.file_name)} {'经营快报分析' if is_business_update else '财报分析'}</title>
<style>
:root{{--paper:#FFF6D8;--surface:#FAFAF7;--ink:#213333;--accent:#356859;--clay:#C86B4A;--olive:#50723C;--muted:#6F7469;--line:#D9D9D2;--soft:#F1F2EC;--red:#A94236;--amber:#A96621;--radius:8px;--shadow:none}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{margin:0;min-height:100vh;color:var(--ink);background:#071718;font:14px/1.65 "Segoe UI","Microsoft YaHei",Arial,sans-serif;letter-spacing:.01em}}
body::before{{content:"";position:fixed;inset:0;pointer-events:none;background-image:linear-gradient(90deg,rgba(5,18,18,.92),rgba(5,20,21,.66)),linear-gradient(180deg,rgba(5,15,16,.18),rgba(6,18,18,.48)),url('/assets/market-analysis-background.jpg');background-size:cover;background-position:center 48%;z-index:-2}}
body::after{{content:"";position:fixed;inset:0;pointer-events:none;opacity:.12;background-image:repeating-radial-gradient(circle at 0 0,rgba(255,246,216,.45) 0 1px,transparent 1px 5px);background-size:9px 9px;mix-blend-mode:screen;z-index:-1}}
*::selection{{background:var(--clay);color:#fff}}
a{{color:inherit;text-decoration:none}}
a{{-webkit-tap-highlight-color:transparent}}
a:focus-visible{{outline:2px solid #F3FFC9;outline-offset:3px}}
.shell{{width:min(1280px,calc(100% - 40px));margin:0 auto}}
.topbar{{min-height:78px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,.24);color:#FFFDF4}}
.brand{{display:flex;align-items:center;gap:12px;font-weight:800;letter-spacing:.08em}}
.brand-mark{{display:grid;place-items:center;width:38px;height:38px;border-radius:50% 50% 46% 54%;background:rgba(255,253,244,.92);color:var(--ink);border:1px solid rgba(255,255,255,.55);font-family:Georgia,serif;font-size:19px;transform:rotate(-4deg)}}
.brand small{{display:block;color:#C8D4CF;font-size:10px;letter-spacing:.2em;font-weight:700}}
.back-link{{display:inline-flex;align-items:center;gap:10px;min-height:38px;padding:8px 12px;border:1px solid #4A5D58;border-radius:8px;background:#131B1A;color:#FFFDF4;font-weight:800;font-size:12px;backdrop-filter:none;box-shadow:none;transition:background .16s,color .16s,border-color .16s,transform .16s}}
.back-link::before{{content:"←";padding-right:9px;border-right:1px solid rgba(255,255,255,.28)}}
.back-link:hover{{background:#FFFDF4;border-color:#FFFDF4;color:#131517;transform:translateY(-1px)}}
.report-hero{{display:grid;grid-template-columns:minmax(0,1fr) 210px;gap:44px;align-items:end;padding:58px 0 34px}}
.eyebrow{{display:flex;align-items:center;gap:12px;margin:0 0 16px;color:#A8DCC8;font-size:11px;font-weight:800;letter-spacing:.18em;text-transform:uppercase}}
.eyebrow::before{{content:"";width:38px;height:2px;background:var(--clay)}}
h1{{margin:0;color:#FFFDF4;text-shadow:0 5px 28px rgba(0,0,0,.3);font:600 clamp(35px,5vw,60px)/1.12 Georgia,"Songti SC","SimSun",serif;letter-spacing:-.035em}}
.file-name{{margin:16px 0 0;color:#D6E0DB;font-size:13px;overflow-wrap:anywhere}}
.report-stamp{{position:relative;overflow:hidden;min-height:164px;padding:23px;border:1px solid rgba(255,255,255,.14);border-radius:6px;background:var(--ink);color:var(--surface);box-shadow:none}}
.report-stamp::after{{content:"";position:absolute;width:116px;height:116px;border:20px solid rgba(255,246,216,.07);border-radius:50%;right:-54px;top:-54px}}
.report-stamp small{{display:block;color:#E5DDBF;letter-spacing:.14em}}
.report-stamp strong{{display:block;margin-top:15px;color:var(--paper);font:54px/1 Georgia,serif}}
.report-stamp span{{display:block;margin-top:10px;color:#E9A077;font-weight:800}}
.meta{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-bottom:20px}}
.meta div{{position:relative;min-height:94px;padding:19px 16px 16px;background:rgba(250,250,247,.94);border:1px solid rgba(255,255,255,.62);border-top:3px solid #91CFB5;border-radius:6px;box-shadow:none;backdrop-filter:blur(12px);color:var(--muted);font-size:11px;letter-spacing:.04em}}
.meta div:nth-child(2n){{border-top-color:#F19447}}.meta div:nth-child(3n){{border-top-color:#68CC58}}
.meta strong{{display:block;margin-top:9px;color:var(--ink);font:600 16px/1.3 Georgia,"Songti SC",serif;overflow-wrap:anywhere}}
.report-nav{{position:sticky;top:0;z-index:20;display:flex;gap:6px;margin:0 0 24px;padding:8px;background:rgba(250,250,247,.94);border:1px solid var(--line);border-radius:8px;box-shadow:none;backdrop-filter:blur(12px);overflow-x:auto}}
.report-nav a{{flex:0 0 auto;padding:8px 11px;border:1px solid transparent;border-radius:8px;color:var(--muted);font-size:11px;font-weight:700;transition:.16s}}
.report-nav a:hover{{background:#232326;border-color:#232326;color:#fff}}
.report-section{{scroll-margin-top:82px;margin:0 0 26px}}
.section-head{{display:flex;justify-content:space-between;align-items:end;gap:20px;margin:0 4px 10px}}
h2{{margin:0;color:#FFFDF4;text-shadow:0 3px 16px rgba(0,0,0,.3);font:600 25px/1.2 Georgia,"Songti SC","SimSun",serif;letter-spacing:-.02em}}
.section-kicker{{color:var(--clay);font:14px/1 Georgia,serif}}
h3{{margin:0 0 8px}}
.panel{{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:20px;overflow:auto;box-shadow:none}}
table{{border-collapse:collapse;width:100%;min-width:780px}}
th{{background:var(--ink);color:var(--surface);text-align:left;font-size:11px;letter-spacing:.04em}}
th:first-child{{border-radius:9px 0 0 0}}th:last-child{{border-radius:0 9px 0 0}}
th,td{{padding:11px 12px;border-bottom:1px solid #E2D7B8;vertical-align:top}}
tbody tr:nth-child(even){{background:#FBF6E7}}tbody tr:hover{{background:var(--soft)}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}td.evidence{{max-width:430px;color:var(--muted)}}
.finding{{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:6px;padding:18px 20px;margin:10px 0;box-shadow:none}}
.finding.high{{border-left-color:var(--red)}}.finding.medium{{border-left-color:var(--amber)}}
.finding h3 span{{display:inline-block;margin-left:7px;padding:2px 7px;border-radius:999px;background:var(--soft);color:var(--muted);font-size:10px;font-weight:700}}
.finding p{{margin:7px 0}}.source{{color:var(--muted)}}
pre{{white-space:pre-wrap;margin:0;background:var(--ink);color:#EDE9DC;padding:20px;border-radius:6px;overflow:auto;font:12px/1.7 Consolas,"Microsoft YaHei",monospace}}
.health-score{{display:flex;gap:20px;align-items:baseline;flex-wrap:wrap;margin-bottom:14px;padding-bottom:14px;border-bottom:1px dashed var(--line)}}
.health-score strong{{font:52px/1 Georgia,serif;color:var(--clay)}}.health-score span{{font-weight:800}}
code{{color:var(--accent)}}
/* Solid dark report surfaces */
.meta div{{background:#0D2522;border-color:#2D4540;color:#AAB8B2;box-shadow:none;backdrop-filter:none}}
.meta strong{{color:#FFFDF4}}
.report-nav{{background:#0B211F;border-color:#2D4540;backdrop-filter:none}}
.report-nav a{{color:#B7C4BE}}.report-nav a:hover{{background:#F3FFC9;border-color:#F3FFC9;color:#131517}}
.panel,.finding{{background:#0B211F;border-color:#2D4540;color:#ECF2ED;box-shadow:none;backdrop-filter:none}}
.finding{{border-left-color:#91CFB5}}.finding.high{{border-left-color:#E47C6B}}.finding.medium{{border-left-color:#F1A45D}}
.finding h3 span{{background:#17302C;color:#BFCBC5}}
.source,td.evidence{{color:#AAB8B2}}
th{{background:#F3FFC9;color:#17211E}}th,td{{border-bottom-color:rgba(255,255,255,.11)}}
tbody tr:nth-child(even){{background:#102724}}tbody tr:hover{{background:#17352F}}
.health-score{{border-bottom-color:rgba(255,255,255,.14)}}code{{color:#A8DCC8}}
.chat-launcher{{position:fixed;right:24px;bottom:24px;z-index:60;display:flex;align-items:center;gap:10px;min-height:48px;padding:0 18px;border:1px solid #F3FFC9;border-radius:8px;background:#F3FFC9;color:#131517;font:800 13px/1 "Segoe UI","Microsoft YaHei",sans-serif;cursor:pointer;box-shadow:0 12px 30px rgba(0,0,0,.28);transition:transform .16s,background .16s}}
.chat-launcher:hover{{transform:translateY(-2px);background:#91CFB5}}.chat-launcher i{{width:8px;height:8px;border-radius:50%;background:#356859;box-shadow:0 0 0 4px rgba(53,104,89,.14)}}
.chat-backdrop{{position:fixed;inset:0;z-index:70;background:rgba(2,12,12,.64);opacity:0;visibility:hidden;transition:opacity .2s,visibility .2s}}
.chat-backdrop.open{{opacity:1;visibility:visible}}
.chat-drawer{{position:fixed;top:0;right:0;z-index:71;width:min(520px,100%);height:100dvh;display:grid;grid-template-rows:auto auto minmax(0,1fr) auto;background:#071B19;border-left:1px solid #39514B;color:#EDF3EE;transform:translateX(102%);transition:transform .28s cubic-bezier(.22,1,.36,1);box-shadow:-24px 0 60px rgba(0,0,0,.3)}}
.chat-drawer.open{{transform:translateX(0)}}.chat-head{{display:flex;align-items:start;justify-content:space-between;gap:18px;padding:22px;border-bottom:1px solid #29413B;background:#0B211F}}
.chat-head small{{display:block;margin-bottom:4px;color:#91CFB5;font-size:10px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}}.chat-head h2{{font:600 24px/1.2 Georgia,"Songti SC","SimSun",serif}}
.chat-head p{{margin:7px 0 0;color:#AAB8B2;font-size:11px}}.chat-actions{{display:flex;gap:7px}}.chat-icon-button{{width:36px;height:36px;border:1px solid #3B534D;border-radius:7px;background:#142B27;color:#F2F4EF;cursor:pointer;font-weight:800}}.chat-icon-button:hover{{background:#F3FFC9;color:#131517}}
.chat-quick{{display:flex;gap:7px;padding:12px 18px;border-bottom:1px solid #29413B;overflow-x:auto}}.chat-quick button{{flex:0 0 auto;padding:7px 10px;border:1px solid #365049;border-radius:999px;background:#102724;color:#C7D4CE;font-size:11px;cursor:pointer}}.chat-quick button:hover{{border-color:#91CFB5;color:#F3FFC9}}
.chat-stream{{min-height:0;padding:20px 18px 28px;overflow-y:auto;scroll-behavior:smooth}}.chat-message{{max-width:92%;margin:0 0 16px;padding:13px 15px;border:1px solid #2F4942;border-radius:8px;background:#0B211F}}.chat-message.user{{margin-left:auto;background:#17352F;border-color:#486A60}}.chat-message.assistant{{margin-right:auto}}.chat-role{{display:block;margin-bottom:6px;color:#91CFB5;font-size:9px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}}.chat-copy{{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;color:#ECF2ED;font:13px/1.75 "Segoe UI","Microsoft YaHei",sans-serif}}
.chat-citations{{margin-top:10px;border-top:1px solid rgba(255,255,255,.1)}}.chat-citations details{{padding:8px 0;border-bottom:1px solid rgba(255,255,255,.07)}}.chat-citations summary{{cursor:pointer;color:#F3FFC9;font-size:11px;font-weight:700}}.chat-citations p{{margin:7px 0 0;color:#AAB8B2;font-size:11px;line-height:1.65;white-space:pre-wrap}}.chat-mode{{display:inline-block;margin-top:9px;color:#82948D;font-size:9px;letter-spacing:.1em;text-transform:uppercase}}
.chat-thinking{{display:none;align-items:center;gap:8px;margin:0 18px 10px;color:#AAB8B2;font-size:11px}}.chat-thinking.visible{{display:flex}}.chat-thinking i{{width:7px;height:7px;border-radius:50%;background:#F1A45D;animation:chat-pulse 1s ease-in-out infinite}}@keyframes chat-pulse{{50%{{opacity:.35;transform:scale(.7)}}}}
.chat-form{{padding:14px 18px 18px;border-top:1px solid #29413B;background:#0B211F}}.chat-form label{{display:block;margin-bottom:7px;color:#AAB8B2;font-size:10px}}.chat-compose{{display:grid;grid-template-columns:minmax(0,1fr) 88px;gap:8px}}.chat-compose textarea{{min-height:72px;max-height:160px;resize:vertical;padding:12px;border:1px solid #3A544D;border-radius:7px;background:#102724;color:#F5F7F3;font:13px/1.5 "Segoe UI","Microsoft YaHei",sans-serif}}.chat-compose textarea:focus{{outline:2px solid #91CFB5;outline-offset:1px}}.chat-compose button{{border:1px solid #F3FFC9;border-radius:7px;background:#F3FFC9;color:#131517;font-weight:800;cursor:pointer}}.chat-compose button:disabled{{opacity:.5;cursor:wait}}.chat-footnote{{margin:8px 0 0;color:#82948D;font-size:9px}}
/* OddCommon-inspired borderless editorial system */
body::before{{background-image:linear-gradient(90deg,rgba(4,14,13,.975),rgba(5,17,16,.91)),url('/assets/market-analysis-background.jpg');background-position:center 42%}}
body::after{{opacity:.05}}
.shell{{width:min(1420px,calc(100% - 64px))}}
.topbar{{min-height:90px;border-bottom:0}}
.brand-mark{{width:34px;height:34px;border:0;background:transparent;color:#F3FFC9;font:800 21px/1 Arial,sans-serif;transform:none}}
.brand small{{color:#87958F}}
.back-link{{padding:7px 0;border:0;border-bottom:1px solid #82938C;border-radius:0;background:transparent}}
.back-link::before{{border-right:0;padding-right:2px}}.back-link:hover{{background:transparent;border-color:#F3FFC9;color:#F3FFC9}}
.report-hero{{min-height:62vh;grid-template-columns:minmax(0,1fr) 260px;align-items:center;padding:70px 0 80px;border-bottom:1px solid rgba(255,255,255,.3)}}
h1{{max-width:1000px;font:800 clamp(52px,7vw,104px)/.9 Arial,"Microsoft YaHei",sans-serif;letter-spacing:-.07em}}
.file-name{{margin-top:30px;color:#9EAAA5}}
.report-stamp{{min-height:auto;padding:0;border:0;border-radius:0;background:transparent;text-align:right}}
.report-stamp::after{{display:none}}.report-stamp strong{{font:800 72px/.9 Arial,"Microsoft YaHei",sans-serif;color:#F3FFC9;letter-spacing:-.06em}}
.meta{{gap:0;margin:0 0 48px;border-bottom:1px solid rgba(255,255,255,.2)}}
.meta div{{min-height:116px;padding:24px 18px;background:transparent;border:0;border-top:1px solid rgba(255,255,255,.32);border-radius:0;backdrop-filter:none}}
.meta div+div{{border-left:1px solid rgba(255,255,255,.12)}}.meta div:nth-child(n){{border-top-color:rgba(255,255,255,.32)}}
.meta strong{{margin-top:18px;font:700 17px/1.2 Arial,"Microsoft YaHei",sans-serif;letter-spacing:-.02em}}
.report-nav{{top:0;margin-bottom:70px;padding:12px 0;background:#071718;border:0;border-top:1px solid rgba(255,255,255,.26);border-bottom:1px solid rgba(255,255,255,.26);border-radius:0;backdrop-filter:none}}
.report-nav a{{padding:7px 10px 7px 0;margin-right:12px;border:0;border-radius:0}}.report-nav a:hover{{background:transparent;border:0;color:#F3FFC9}}
.report-section{{margin-bottom:74px}}.section-head{{align-items:start;margin:0 0 21px;padding-top:5px}}
.section-head h2{{font:700 clamp(31px,3.5vw,52px)/1 Arial,"Microsoft YaHei",sans-serif;letter-spacing:-.045em}}
.section-kicker{{color:#F3FFC9}}
.panel,.finding{{padding:23px 0;background:transparent;border:0;border-top:1px solid rgba(255,255,255,.34);border-radius:0;color:#ECF2ED}}
.finding{{margin:0;padding:27px 0}}.finding+.finding{{border-top-color:rgba(255,255,255,.16)}}
.finding.high,.finding.medium{{border-left:0}}
.finding h3 span{{border-radius:999px;background:transparent;border:1px solid rgba(255,255,255,.24)}}
th{{background:transparent;color:#F3FFC9;border-bottom:1px solid rgba(255,255,255,.36)}}
tbody tr:nth-child(even),tbody tr:hover{{background:transparent}}
tbody tr:hover{{color:#F3FFC9}}th,td{{padding:14px 12px 14px 0}}
pre{{padding:20px 0;background:transparent;border-radius:0;color:#DCE5E0}}
.health-score strong{{font-family:Arial,"Microsoft YaHei",sans-serif;color:#F3FFC9}}
.chat-launcher{{border-radius:999px;box-shadow:none}}
.chat-drawer{{background:#071513;box-shadow:none}}
.chat-head,.chat-form{{background:transparent}}
.chat-message,.chat-message.user{{max-width:100%;margin:0;padding:17px 0;background:transparent;border:0;border-top:1px solid rgba(255,255,255,.16);border-radius:0}}
.chat-message.user{{padding-left:13%;color:#F3FFC9}}.chat-quick button{{background:transparent}}
.chat-compose textarea{{background:transparent;border:0;border-bottom:1px solid #789087;border-radius:0;padding-left:0}}
.chat-compose button{{border-radius:999px}}
.reveal-ready .scroll-reveal{{opacity:0;filter:blur(5px);transform:translateY(24px) scale(.985);transition:opacity .62s cubic-bezier(.22,1,.36,1),transform .62s cubic-bezier(.22,1,.36,1),filter .5s ease;transition-delay:var(--reveal-delay,0ms)}}
.reveal-ready .scroll-reveal.is-visible{{opacity:1;filter:blur(0);transform:translateY(0) scale(1)}}
footer{{display:flex;justify-content:space-between;gap:24px;margin-top:40px;padding:22px 0 36px;border-top:1px solid rgba(255,255,255,.25);color:#CCD7D1;font-size:11px;overflow-wrap:anywhere;text-shadow:0 2px 12px rgba(0,0,0,.35)}}
@media(max-width:1050px){{.shell{{width:min(100% - 38px,1420px)}}.meta{{grid-template-columns:repeat(3,1fr)}}.meta div:nth-child(4){{border-left:0}}}}
@media(max-width:900px){{.report-hero{{grid-template-columns:1fr}}.report-stamp{{min-height:130px}}.meta{{grid-template-columns:repeat(2,1fr)}}}}
@media(max-width:600px){{.shell{{width:min(100% - 24px,1420px)}}.topbar{{min-height:68px}}.brand small{{display:none}}.report-hero{{min-height:auto;padding:50px 0 60px;gap:34px}}h1{{font-size:50px}}.report-stamp{{text-align:left}}.report-stamp strong{{font-size:52px}}.meta{{grid-template-columns:1fr 1fr}}.meta div{{min-height:92px;padding:16px 10px}}.meta div:nth-child(odd){{border-left:0}}.panel{{padding:16px 0}}footer{{flex-direction:column}}.chat-launcher{{right:12px;bottom:12px;padding:0 14px}}.chat-drawer{{width:100%}}.chat-head{{padding:17px}}.chat-stream{{padding:16px 12px 24px}}.chat-form{{padding:12px}}}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}.reveal-ready .scroll-reveal{{opacity:1;filter:none;transform:none;transition:none}}}}
</style>
</head>
<body>
<header class="shell topbar"><a class="brand" href="/"><span class="brand-mark">研</span><span>麦穗终端<small>LEDGER INTELLIGENCE</small></span></a><a class="back-link" href="/">返回分析台</a></header>
<main class="shell">
<section class="report-hero"><div><p class="eyebrow">Financial analysis report / {_h(meta.report_type)}</p><h1>{_h(meta.company_name or '上市公司')}<br>{'经营快报分析' if is_business_update else '财报分析'}</h1><p class="file-name">{_h(meta.file_name)}</p></div><aside class="report-stamp"><small>{_h(stamp_title)}</small><strong>{_h(stamp_grade)}</strong><span>{_h(stamp_status)}</span></aside></section>
<section class="meta"><div>报告类型<strong>{_h(meta.report_type)}</strong></div><div>行业配置<strong>{_h(meta.industry_profile)}</strong></div><div>会计准则<strong>{_h(meta.accounting_standard)}</strong></div><div>原生文本率<strong>{meta.native_text_ratio:.1%}</strong></div><div>OCR 覆盖率<strong>{ocr_coverage:.1%}</strong></div><div>运行 ID<strong>{_h(bundle.run_id)}</strong></div></section>
<nav class="report-nav" aria-label="报告章节"><a href="#signals">异常信号</a><a href="#business-metrics">经营指标</a><a href="#health">{'适用性' if is_business_update else '健康评级'}</a><a href="#facts">财务事实</a><a href="#calculations">程序计算</a><a href="#nonrecurring">非经常性损益</a><a href="#narrative">叙事证据</a><a href="#validation">质量校验</a><a href="#deepseek">DeepSeek</a><a href="#limits">适用边界</a></nav>
<section class="report-section" id="signals"><div class="section-head"><h2>异常信号</h2><span class="section-kicker">01</span></div>{_finding_cards(bundle)}</section>
<section class="report-section" id="business-metrics"><div class="section-head"><h2>经营更新指标</h2><span class="section-kicker">02 · {len(bundle.business_metrics)} ITEMS</span></div><div class="panel"><table><thead><tr><th>指标</th><th>期间</th><th>披露区间</th><th>比较口径</th><th>页码</th><th>原始证据</th></tr></thead><tbody>{_business_metric_rows(bundle)}</tbody></table></div></section>
<section class="report-section" id="health"><div class="section-head"><h2>{'评分适用性' if is_business_update else '财务健康评级'}</h2><span class="section-kicker">03</span></div><div class="panel">{_health_section(bundle)}</div></section>
<section class="report-section" id="facts"><div class="section-head"><h2>结构化财务事实</h2><span class="section-kicker">03 · {len(bundle.facts)} ITEMS</span></div><div class="panel"><table><thead><tr><th>指标</th><th>期间</th><th>数据类型</th><th>标准化数值</th><th>币种/原单位</th><th>页码</th><th>原始证据</th></tr></thead><tbody>{_fact_rows(bundle)}</tbody></table></div></section>
<section class="report-section" id="calculations"><div class="section-head"><h2>程序化计算</h2><span class="section-kicker">04</span></div><div class="panel"><table><thead><tr><th>指标</th><th>期间</th><th>结果</th><th>公式</th><th>输入</th></tr></thead><tbody>{_calculation_rows(bundle)}</tbody></table></div></section>
<section class="report-section" id="nonrecurring"><div class="section-head"><h2>非经常性损益明细</h2><span class="section-kicker">05</span></div><div class="panel"><table><thead><tr><th>项目</th><th>期间</th><th>标准化金额</th><th>币种/原单位</th><th>页码</th></tr></thead><tbody>{_nonrecurring_rows(bundle)}</tbody></table></div></section>
<section class="report-section" id="narrative"><div class="section-head"><h2>叙事证据检索</h2><span class="section-kicker">06</span></div><div class="panel"><table><thead><tr><th>主题</th><th>页码</th><th>相关度</th><th>原文片段</th></tr></thead><tbody>{_narrative_rows(bundle)}</tbody></table></div></section>
<section class="report-section" id="validation"><div class="section-head"><h2>质量校验</h2><span class="section-kicker">08</span></div><div class="panel"><table><thead><tr><th>等级</th><th>代码</th><th>说明</th></tr></thead><tbody>{_validation_rows(bundle)}</tbody></table></div></section>
<section class="report-section" id="deepseek"><div class="section-head"><h2>DeepSeek 分析</h2><span class="section-kicker">09</span></div><div class="panel">{_llm_section(bundle.llm_analysis)}</div></section>
<section class="report-section" id="limits"><div class="section-head"><h2>适用边界</h2><span class="section-kicker">10</span></div><div class="panel"><ul>{limitations}</ul><p>本报告区分披露事实、程序计算和规则推论，不构成投资建议。</p></div></section>
<footer><span>源文件 SHA256：{_h(meta.sha256)}</span><span>完整过程见同目录 manifest.json 与 events.jsonl</span></footer>
</main>
<button class="chat-launcher" id="chat-launcher" type="button" aria-controls="chat-drawer" aria-expanded="false"><i></i>证据问答</button>
<div class="chat-backdrop" id="chat-backdrop"></div>
<aside class="chat-drawer" id="chat-drawer" data-run-id="{_h(bundle.run_id)}" aria-hidden="true" aria-label="财报证据问答">
  <div class="chat-head"><div><small>Evidence-grounded dialogue</small><h2>证据问答</h2><p>回答绑定当前报告、页码与运行日志</p></div><div class="chat-actions"><button class="chat-icon-button" id="chat-reset" type="button" title="新会话">＋</button><button class="chat-icon-button" id="chat-close" type="button" title="关闭">×</button></div></div>
  <div class="chat-quick" aria-label="快捷问题"><button type="button">业绩变化的主要原因是什么？</button><button type="button">利润与现金流是否背离？</button><button type="button">有哪些值得关注的风险？</button></div>
  <div class="chat-stream" id="chat-stream" aria-live="polite"><article class="chat-message assistant"><span class="chat-role">Evidence assistant</span><p class="chat-copy">你可以针对当前报告继续提问。我会先检索结构化事实和财报原文，再返回带页码的回答。</p></article></div>
  <div><div class="chat-thinking" id="chat-thinking"><i></i><span>检索证据 · 核验页码 · 生成回答</span></div><form class="chat-form" id="chat-form"><label for="chat-question">针对当前财报提问</label><div class="chat-compose"><textarea id="chat-question" maxlength="2000" placeholder="例如：毛利率下降的原因是什么？" required></textarea><button type="submit">发送</button></div><p class="chat-footnote">DeepSeek 不可用时自动返回纯检索证据；所有问答写入当前运行目录。</p></form></div>
</aside>
<script>
const reduceMotion=window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const revealTargets=Array.from(document.querySelectorAll(".report-hero>div,.report-stamp,.meta>div,.report-nav,.section-head,.panel,.finding,footer"));
document.documentElement.classList.add("reveal-ready");
revealTargets.forEach((element,index)=>{{element.classList.add("scroll-reveal");element.style.setProperty("--reveal-delay",`${{(index%4)*65}}ms`);}});
if(reduceMotion||!("IntersectionObserver" in window)){{
  revealTargets.forEach(element=>element.classList.add("is-visible"));
}}else{{
  const revealObserver=new IntersectionObserver(entries=>{{entries.forEach(entry=>{{if(entry.isIntersecting){{entry.target.classList.add("is-visible");revealObserver.unobserve(entry.target);}}}});}},{{threshold:.12,rootMargin:"0px 0px -8% 0px"}});
  revealTargets.forEach(element=>revealObserver.observe(element));
}}
const chatLauncher=document.getElementById("chat-launcher");
const chatDrawer=document.getElementById("chat-drawer");
const chatBackdrop=document.getElementById("chat-backdrop");
const chatClose=document.getElementById("chat-close");
const chatReset=document.getElementById("chat-reset");
const chatForm=document.getElementById("chat-form");
const chatQuestion=document.getElementById("chat-question");
const chatStream=document.getElementById("chat-stream");
const chatThinking=document.getElementById("chat-thinking");
const chatSubmit=chatForm.querySelector('button[type="submit"]');
const chatRunId=chatDrawer.dataset.runId;
const chatStorageKey=`fin-agent-chat:${{chatRunId}}`;
let chatSessionId=window.localStorage.getItem(chatStorageKey)||null;
let chatHistoryLoaded=false;
const setChatOpen=(open)=>{{
  chatDrawer.classList.toggle("open",open);chatBackdrop.classList.toggle("open",open);
  chatDrawer.setAttribute("aria-hidden",String(!open));chatLauncher.setAttribute("aria-expanded",String(open));
  if(open){{loadChatHistory();window.setTimeout(()=>chatQuestion.focus(),220);}}
}};
const appendChatMessage=(role,content,citations=[],mode="")=>{{
  const article=document.createElement("article");article.className=`chat-message ${{role}}`;
  const roleLabel=document.createElement("span");roleLabel.className="chat-role";roleLabel.textContent=role==="user"?"Your question":"Evidence assistant";article.appendChild(roleLabel);
  const copy=document.createElement("p");copy.className="chat-copy";copy.textContent=content;article.appendChild(copy);
  if(citations&&citations.length){{
    const group=document.createElement("div");group.className="chat-citations";
    citations.forEach((item,index)=>{{
      const details=document.createElement("details");const summary=document.createElement("summary");
      const anchor=item.page?`第${{item.page}}页`:(item.label||"程序证据");summary.textContent=`证据${{index+1}} · ${{anchor}} · ${{item.label||item.kind}}`;details.appendChild(summary);
      const excerpt=document.createElement("p");excerpt.textContent=item.text||"";details.appendChild(excerpt);group.appendChild(details);
    }});article.appendChild(group);
  }}
  if(mode){{const badge=document.createElement("span");badge.className="chat-mode";badge.textContent=mode==="deepseek"?"DeepSeek · evidence constrained":"Retrieval fallback";article.appendChild(badge);}}
  chatStream.appendChild(article);chatStream.scrollTop=chatStream.scrollHeight;
}};
const loadChatHistory=async()=>{{
  if(chatHistoryLoaded||!chatSessionId)return;chatHistoryLoaded=true;
  try{{
    const query=new URLSearchParams({{run_id:chatRunId,session_id:chatSessionId}});
    const response=await fetch(`/api/chat/history?${{query.toString()}}`,{{headers:{{"Accept":"application/json"}}}});
    if(!response.ok)return;const payload=await response.json();
    (payload.messages||[]).forEach(item=>appendChatMessage(item.role,item.content,item.citations||[],item.mode||""));
  }}catch(error){{chatHistoryLoaded=false;}}
}};
chatLauncher.addEventListener("click",()=>setChatOpen(true));chatClose.addEventListener("click",()=>setChatOpen(false));chatBackdrop.addEventListener("click",()=>setChatOpen(false));
document.addEventListener("keydown",event=>{{if(event.key==="Escape")setChatOpen(false);}});
chatReset.addEventListener("click",()=>{{chatSessionId=null;chatHistoryLoaded=true;window.localStorage.removeItem(chatStorageKey);chatStream.replaceChildren();appendChatMessage("assistant","已开始新会话。历史记录仍保存在审计目录中，你可以提出新的问题。");chatQuestion.focus();}});
document.querySelectorAll(".chat-quick button").forEach(button=>button.addEventListener("click",()=>{{chatQuestion.value=button.textContent;chatQuestion.focus();}}));
chatQuestion.addEventListener("keydown",event=>{{if(event.key==="Enter"&&!event.shiftKey){{event.preventDefault();chatForm.requestSubmit();}}}});
chatForm.addEventListener("submit",async event=>{{
  event.preventDefault();const question=chatQuestion.value.trim();if(!question||chatSubmit.disabled)return;
  appendChatMessage("user",question);chatQuestion.value="";chatSubmit.disabled=true;chatThinking.classList.add("visible");
  try{{
    const response=await fetch("/api/chat",{{method:"POST",headers:{{"Content-Type":"application/json","Accept":"application/json"}},body:JSON.stringify({{run_id:chatRunId,session_id:chatSessionId,question}})}});
    const payload=await response.json();if(!response.ok)throw new Error(payload.error||"问答请求失败");
    chatSessionId=payload.session_id;window.localStorage.setItem(chatStorageKey,chatSessionId);appendChatMessage("assistant",payload.answer,payload.citations||[],payload.mode||"");
  }}catch(error){{appendChatMessage("assistant",`问答失败：${{error.message}}`);}}
  finally{{chatSubmit.disabled=false;chatThinking.classList.remove("visible");chatQuestion.focus();}}
}});
</script>
</body>
</html>"""
    output_path.write_text(document, encoding="utf-8")


# The compact renderer is the current competition-facing implementation.  The
# legacy helpers above remain import-compatible for archived reports, while all
# new runs use the two-page, agent-structured report.
from .report_compact import render_html as render_html
