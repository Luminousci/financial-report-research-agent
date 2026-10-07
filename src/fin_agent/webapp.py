from __future__ import annotations

import base64
import html
import hmac
import io
import json
import mimetypes
import os
import re
import socket
import threading
import unicodedata
import urllib.parse
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .chat import ChatError, ChatService
from .config import Settings
from .document import infer_report_identity
from .pipeline import FinancialReportPipeline


MAX_IMPORTED_REPORT_BYTES = 80 * 1024 * 1024
REPORT_TYPE_LABELS = {
    "annual": "年度报告",
    "semiannual": "半年度报告",
    "q1": "第一季度报告",
    "q3": "第三季度报告",
    "other": "财务报告",
}


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """Fail fast when the demo port is already owned by another process."""

    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class AnalysisJobRegistry:
    """Thread-safe in-memory state for long-running web analysis jobs."""

    def __init__(self, max_jobs: int = 64):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, object]] = {}
        self._max_jobs = max_jobs

    def create(self, filename: str) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            while len(self._jobs) >= self._max_jobs:
                oldest = next(iter(self._jobs))
                self._jobs.pop(oldest, None)
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "filename": filename,
                "message": "任务已进入分析队列",
            }
        return job_id

    def update(self, job_id: str, **changes: object) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(changes)

    def get(self, job_id: str) -> dict[str, object] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None


def render_job_page(job_id: str) -> str:
    safe_job_id = html.escape(job_id, quote=True)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#071718">
<title>正在生成财报分析报告</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#071718;color:#FFFDF4;font:15px/1.7 "Segoe UI","Microsoft YaHei",sans-serif}}
body::before{{content:"";position:fixed;inset:0;background:linear-gradient(90deg,rgba(5,18,18,.94),rgba(5,20,21,.72)),url('/assets/market-analysis-background.jpg') center/cover;z-index:-1}}
.job-shell{{width:min(760px,calc(100% - 40px));padding:44px 0}}.brand{{display:flex;align-items:center;gap:12px;margin-bottom:64px;font-weight:800;letter-spacing:.08em}}
.mark{{display:grid;place-items:center;width:40px;height:40px;border-radius:50%;background:#FFFDF4;color:#213333;font:19px Georgia,serif}}
.eyebrow{{color:#A8DCC8;font-size:12px;font-weight:800;letter-spacing:.2em;text-transform:uppercase}}h1{{margin:12px 0 16px;font:600 clamp(38px,6vw,68px)/1.08 Georgia,"Songti SC","SimSun",serif}}
#job-message{{color:#D7E0DC;font-size:17px}}.progress{{height:4px;margin:34px 0 24px;overflow:hidden;background:rgba(255,255,255,.16)}}.progress i{{display:block;width:42%;height:100%;background:linear-gradient(90deg,#356859,#F4A176,#E3BE6C);animation:move 1.35s ease-in-out infinite}}
.meta{{display:flex;justify-content:space-between;gap:18px;color:#AEBDB7;font:12px Consolas,monospace}}.actions{{display:flex;gap:12px;margin-top:36px}}a,button{{padding:11px 17px;border:1px solid rgba(255,255,255,.45);border-radius:999px;background:transparent;color:#FFFDF4;text-decoration:none;font:inherit;cursor:pointer}}button{{display:none}}
.failed button{{display:inline-block}}.failed .progress i{{animation:none;width:100%;background:#C86B4A}}@keyframes move{{0%{{transform:translateX(-110%)}}100%{{transform:translateX(270%)}}}}
@media(prefers-reduced-motion:reduce){{.progress i{{animation:none;width:100%}}}}
</style>
</head>
<body>
<main class="job-shell" id="job-shell">
  <div class="brand"><span class="mark">研</span><span>麦穗终端</span></div>
  <p class="eyebrow">Financial research agent / processing</p>
  <h1>正在生成<br>财报分析报告</h1>
  <p id="job-message">任务已进入分析队列，页面会自动跳转，无需重复提交。</p>
  <div class="progress" aria-hidden="true"><i></i></div>
  <div class="meta"><span>JOB / {safe_job_id[:12]}</span><span id="job-state">QUEUED</span></div>
  <div class="actions"><a href="/">返回分析台</a><button id="retry" type="button">重新检查</button></div>
</main>
<script>
const jobId={json.dumps(job_id)};
const shell=document.getElementById("job-shell");
const message=document.getElementById("job-message");
const state=document.getElementById("job-state");
const retry=document.getElementById("retry");
let timer=null;
const poll=async()=>{{
  if(timer)window.clearTimeout(timer);
  try{{
    const response=await fetch("/api/job?"+new URLSearchParams({{id:jobId}}),{{cache:"no-store"}});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||"任务状态读取失败");
    state.textContent=String(data.status||"unknown").toUpperCase();
    message.textContent=data.message||"正在处理财报";
    if(data.status==="done"&&data.report_url){{window.location.replace(data.report_url);return;}}
    if(data.status==="failed"){{shell.classList.add("failed");return;}}
    timer=window.setTimeout(poll,1500);
  }}catch(error){{message.textContent="状态连接暂时中断，正在重试……";timer=window.setTimeout(poll,3000);}}
}};
retry.addEventListener("click",poll);poll();
</script>
</body>
</html>"""


def _within(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved.is_relative_to(root.resolve()) for root in roots)


def managed_report_root(settings: Settings) -> Path:
    return settings.project_root / "财报数据"


def _filename_component(value: str, *, field: str, max_length: int) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", normalized)
    normalized = normalized.strip("._")
    if not normalized:
        raise ValueError(f"{field}不能为空")
    return normalized[:max_length]


def standardized_report_filename(
    security_code: str,
    company_name: str,
    report_period: str,
    report_type: str,
) -> str:
    code = _filename_component(security_code.upper(), field="股票代码", max_length=12)
    if not re.fullmatch(r"[0-9A-Z.-]{2,12}", code):
        raise ValueError("股票代码仅允许数字、英文字母、点和连字符")
    company = _filename_component(company_name, field="公司简称", max_length=32)
    period = _filename_component(report_period.upper(), field="报告期", max_length=8)
    if not re.fullmatch(r"20\d{2}(?:FY|H[12]|Q[1-4])", period):
        raise ValueError("报告期应使用 2025FY、2025H1 或 2025Q1 等格式")
    label = REPORT_TYPE_LABELS.get(report_type)
    if label is None:
        raise ValueError("报告类型不受支持")
    return f"{code}_{company}_{period}_{label}.pdf"


def parse_multipart_report(content_type: str, body: bytes) -> tuple[dict[str, str], str, bytes]:
    if "multipart/form-data" not in content_type.lower() or "boundary=" not in content_type.lower():
        raise ValueError("请求必须使用 multipart/form-data")
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=email_policy).parsebytes(header + body)
    if not message.is_multipart():
        raise ValueError("无法解析上传表单")
    fields: dict[str, str] = {}
    upload_name = ""
    upload_bytes = b""
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if name == "report_file" and filename:
            upload_name = filename
            upload_bytes = payload
        elif filename is None:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, errors="replace").strip()
    if not upload_name or not upload_bytes:
        raise ValueError("请选择需要导入的 PDF 财报")
    return fields, upload_name, upload_bytes


def validate_uploaded_pdf(original_name: str, upload_bytes: bytes) -> None:
    if not original_name.lower().endswith(".pdf"):
        raise ValueError("只允许导入 PDF 文件")
    if len(upload_bytes) > MAX_IMPORTED_REPORT_BYTES:
        raise ValueError("PDF 超过 80 MB 限制")
    if not upload_bytes.startswith(b"%PDF-"):
        raise ValueError("文件内容不是有效的 PDF")


def inspect_pdf_report(upload_bytes: bytes, original_name: str) -> dict[str, object]:
    validate_uploaded_pdf(original_name, upload_bytes)
    try:
        reader = PdfReader(io.BytesIO(upload_bytes), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("PDF 已加密，无法自动读取字段")
        page_count = len(reader.pages)
        page_texts: list[str] = []
        for index in range(min(8, page_count)):
            try:
                page_texts.append(reader.pages[index].extract_text() or "")
            except (KeyError, TypeError, ValueError):
                page_texts.append("")
    except ValueError:
        raise
    except (PdfReadError, OSError, TypeError, KeyError) as exc:
        raise ValueError("PDF 结构无法读取，请确认文件完整且未加密") from exc
    identity = infer_report_identity(page_texts, original_name)
    identity.update({
        "page_count": page_count,
        "pages_inspected": len(page_texts),
        "report_type_label": REPORT_TYPE_LABELS.get(str(identity["report_type"]), "财务报告"),
    })
    return identity


def merge_report_fields(fields: dict[str, str], inspection: dict[str, object]) -> dict[str, str]:
    """Keep user edits and fill only missing import fields from PDF inspection."""

    merged = dict(fields)
    for field_name in ("security_code", "company_name", "report_period", "report_type"):
        if not merged.get(field_name, "").strip():
            merged[field_name] = str(inspection.get(field_name) or "").strip()
    return merged


def next_available_report_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    for index in range(2, 1000):
        candidate = directory / f"{stem}_副本{index}.pdf"
        if not candidate.exists():
            return candidate
    raise ValueError("同名财报副本过多，请调整报告期或公司简称")


def list_reports(settings: Settings) -> list[Path]:
    reports: list[Path] = []
    seen: set[Path] = set()
    for root in settings.data_roots:
        if root.exists():
            for report in sorted(root.rglob("*.pdf")):
                resolved = report.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    reports.append(report)
    return reports


def list_example_reports(settings: Settings) -> list[Path]:
    """Return only the PDF examples that are shipped with the project."""

    example_root = (managed_report_root(settings) / "示例").resolve()
    if not example_root.is_dir():
        return []
    return sorted(
        (path.resolve() for path in example_root.rglob("*.pdf") if path.is_file()),
        key=lambda path: path.name,
    )


def basic_auth_matches(header: str | None, username: str, password: str) -> bool:
    """Validate optional HTTP Basic credentials without logging either value."""

    if not username or not password:
        return True
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    supplied_username, separator, supplied_password = decoded.partition(":")
    return bool(separator) and hmac.compare_digest(supplied_username, username) and hmac.compare_digest(
        supplied_password,
        password,
    )


def render_home_page(settings: Settings, offline: bool = False) -> str:
    reports = list_reports(settings)
    example_reports = list_example_reports(settings)
    options = "\n".join(
        f'<option value="{html.escape(str(path))}">{html.escape(path.name)}</option>'
        for path in example_reports
    )
    if not options:
        options = '<option value="">当前环境未找到示例研报</option>'
    recent = sorted(
        (
            path for path in settings.output_dir.glob("*/report.html")
            if path.is_file()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:5]
    recent_links = "".join(
        '<a class="report-item" href="/report?{}"><span class="report-index">{:02d}</span>'
        '<span class="report-copy"><small>ANALYSIS ARCHIVE</small><strong>{}</strong></span>'
        '<span class="report-arrow" aria-hidden="true">↗</span></a>'.format(
            urllib.parse.urlencode({"path": str(path)}),
            index,
            html.escape(path.parent.name),
        )
        for index, path in enumerate(recent, start=1)
    ) or '<div class="empty-state"><span>00</span><p>暂无历史报告<br><small>完成首次分析后将在此归档</small></p></div>'
    if offline:
        mode_controls = """
<input type="hidden" name="ocr" value="never">
<input type="hidden" name="llm" value="never">
<div class="notice"><strong>离线兜底已启用</strong><span>服务端强制关闭 OCR 与 DeepSeek，仅运行确定性解析、计算和校验。</span></div>
"""
        mode_badge = '<span class="mode-badge offline"><i></i>离线兜底</span>'
    else:
        mode_controls = """
<div class="mode-grid">
<label><span>OCR 模式</span><small>扫描页与复杂表格识别</small><select name="ocr"><option value="auto">自动回退</option><option value="never">不使用</option><option value="force">强制 OCR</option></select></label>
<label><span>DeepSeek</span><small>证据约束下的语义分析</small><select name="llm"><option value="auto">已配置时启用</option><option value="never">不使用</option><option value="always">必须使用</option></select></label>
</div>
"""
        mode_badge = '<span class="mode-badge live"><i></i>联网能力可用</span>'
    ocr_status = "已就绪" if settings.ocr_configured() else "未配置"
    llm_status = "已就绪" if settings.llm_configured() else "未配置"
    ocr_class = "ready" if settings.ocr_configured() else "pending"
    llm_class = "ready" if settings.llm_configured() else "pending"
    submit_disabled = " disabled"
    return f"""<!doctype html>
<html lang="zh-CN" data-design-seed="2257504322086257687">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#071718">
<title>麦穗终端 · 证据链财报分析智能体</title>
<style>
:root{{--paper:#FFF6D8;--surface:#FAFAF7;--ink:#213333;--accent:#356859;--clay:#C86B4A;--olive:#50723C;--muted:#6F7469;--line:#D9D9D2;--soft:#F1F2EC;--radius:8px;--gap:14px;--shadow:none}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{margin:0;min-height:100vh;color:var(--ink);background:#071718;font:15px/1.65 "Segoe UI","Microsoft YaHei",Arial,sans-serif;letter-spacing:.01em}}
body::before{{content:"";position:fixed;inset:0;pointer-events:none;background-image:linear-gradient(90deg,rgba(5,18,18,.94) 0%,rgba(5,20,21,.8) 44%,rgba(4,18,20,.5) 100%),linear-gradient(180deg,rgba(5,15,16,.12) 0%,rgba(6,18,18,.4) 100%),url('/assets/market-analysis-background.jpg');background-size:cover;background-position:center 48%;z-index:-2}}
body::after{{content:"";position:fixed;inset:0;pointer-events:none;opacity:.13;background-image:repeating-radial-gradient(circle at 0 0,rgba(255,246,216,.45) 0 1px,transparent 1px 5px);background-size:9px 9px;mix-blend-mode:screen;z-index:-1}}
a{{color:inherit;text-decoration:none}}
button,select{{font:inherit}}
button,a,select{{-webkit-tap-highlight-color:transparent}}
button:focus-visible,a:focus-visible,select:focus-visible{{outline:2px solid #F3FFC9;outline-offset:3px}}
::selection{{background:var(--clay);color:#fff}}
.shell{{width:min(1180px,calc(100% - 40px));margin:0 auto}}
.topbar{{min-height:78px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,.23);color:#FFFDF4}}
.brand{{display:flex;align-items:center;gap:12px;font-weight:800;letter-spacing:.08em}}
.brand-mark{{display:grid;place-items:center;width:38px;height:38px;border-radius:50% 50% 46% 54%;background:rgba(255,253,244,.92);color:var(--ink);border:1px solid rgba(255,255,255,.6);font-family:Georgia,serif;font-size:19px;transform:rotate(-4deg);box-shadow:0 5px 18px rgba(0,0,0,.22)}}
.brand small{{display:block;color:#C8D4CF;font-size:10px;letter-spacing:.2em;font-weight:700}}
.top-meta{{display:flex;align-items:center;gap:16px;color:#D4DED9;font-size:12px}}
.top-meta a{{border-bottom:1px solid var(--muted)}}
.mode-badge{{display:inline-flex;align-items:center;gap:7px;padding:7px 12px;border:1px solid currentColor;border-radius:999px;font-weight:700}}
.mode-badge i{{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 0 4px rgba(80,114,60,.13)}}
.mode-badge.live{{color:#A6D7C3;background:transparent}}.mode-badge.offline{{color:#F5C18B;background:rgba(100,54,22,.42)}}
.hero{{display:grid;grid-template-columns:minmax(340px,.82fr) minmax(540px,1.18fr);gap:clamp(32px,5vw,72px);padding:58px 0 48px;align-items:center}}
.eyebrow{{display:flex;align-items:center;gap:12px;margin:0 0 18px;color:#A8DCC8;font-size:12px;font-weight:800;letter-spacing:.2em;text-transform:uppercase}}
.eyebrow::before{{content:"";width:38px;height:2px;background:var(--clay)}}
h1{{max-width:650px;margin:0;color:#FFFDF4;text-shadow:0 5px 30px rgba(0,0,0,.28);font-family:Georgia,"Songti SC","SimSun",serif;font-size:clamp(44px,5.5vw,72px);line-height:1.05;letter-spacing:-.045em;font-weight:600}}
h1 em{{color:#F4A176;font-style:normal;position:relative;white-space:nowrap}}
h1 em::after{{content:"";position:absolute;left:1%;right:-2%;bottom:4px;height:8px;background:rgba(200,107,74,.18);transform:rotate(-1deg);z-index:-1}}
.hero-copy{{max-width:650px;margin:24px 0 0;color:#D7E0DC;font-size:17px;text-shadow:0 2px 15px rgba(0,0,0,.3)}}
.agent-stage{{position:relative;display:grid;grid-template-columns:48px minmax(0,1fr);height:440px;overflow:hidden;background:#F9FAF5;border:1px solid rgba(255,255,255,.58);border-radius:8px;box-shadow:none;isolation:isolate}}
.agent-stage::after{{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(110deg,transparent 15%,rgba(255,255,255,.5) 44%,transparent 70%);transform:translateX(-120%);animation:stage-sheen 8s ease-in-out infinite;z-index:4}}
.agent-sidebar{{position:relative;z-index:5;display:flex;flex-direction:column;align-items:center;gap:13px;padding:15px 0;background:var(--ink);color:#D8E2DB;border-right:1px solid #111F20}}
.agent-logo{{display:grid;place-items:center;width:28px;height:28px;margin-bottom:5px;border:1px solid rgba(255,255,255,.35);border-radius:8px;color:var(--paper);font:15px/1 Georgia,serif}}
.side-icon{{display:grid;place-items:center;width:28px;height:28px;border-radius:7px;font-size:12px;color:#A8B7B3}}
.side-icon.active{{background:rgba(233,160,119,.18);color:#F3B18C;box-shadow:inset 0 0 0 1px rgba(233,160,119,.2)}}
.agent-app{{position:relative;z-index:2;min-width:0;display:flex;flex-direction:column}}
.agent-toolbar{{display:flex;align-items:center;justify-content:space-between;min-height:56px;padding:0 18px;border-bottom:1px solid #DDE3DA;background:rgba(255,255,255,.78)}}
.agent-title{{display:flex;align-items:center;gap:9px;font-weight:800;font-size:13px}}.agent-title i{{width:9px;height:9px;border-radius:2px;background:var(--clay);transform:rotate(45deg);box-shadow:0 0 0 5px rgba(200,107,74,.1)}}
.agent-context{{display:flex;gap:7px;padding:12px 18px 0;overflow:hidden}}
.context-chip{{flex:0 0 auto;padding:5px 9px;border:1px solid #D6DED7;border-radius:7px;background:#fff;color:#64716B;font-size:9px;font-weight:700}}
.context-chip strong{{color:var(--accent)}}
.run-card{{position:relative;margin:12px 18px 18px;padding:16px 17px 14px;border:1px solid #D8DFD7;border-radius:6px;background:#fff;box-shadow:none;overflow:hidden}}
.run-card::before{{content:"";position:absolute;left:0;top:0;width:100%;height:3px;background:linear-gradient(90deg,var(--accent),var(--clay),#E3BE6C);transform-origin:left;animation:run-progress 8.4s linear infinite}}
.run-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding-bottom:12px;border-bottom:1px solid #E7EBE5}}
.run-kicker{{display:block;color:var(--muted);font-size:9px;font-weight:800;letter-spacing:.15em;text-transform:uppercase}}
.run-file{{display:block;max-width:340px;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}}
.run-state{{display:inline-flex;align-items:center;gap:6px;color:var(--accent);font-size:10px;font-weight:800;white-space:nowrap}}.run-state i{{width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 0 rgba(53,104,89,.35);animation:pulse 1.5s infinite}}
.trace-list{{position:relative;display:grid;grid-template-columns:1fr 1fr;gap:7px 11px;margin:12px 0 0;padding:0;list-style:none}}
.trace-item{{position:relative;display:grid;grid-template-columns:25px minmax(0,1fr) 44px;align-items:center;gap:8px;min-height:43px;padding:7px 8px;border:1px solid transparent;border-radius:5px;color:#8B948F;transition:background .35s,border-color .35s,color .35s,transform .35s}}
.trace-num{{display:grid;place-items:center;width:24px;height:24px;border-radius:7px;background:#EEF1ED;color:#7A8580;font:10px/1 Georgia,serif;transition:.35s}}
.trace-copy{{min-width:0}}.trace-copy strong{{display:block;font-size:10px;white-space:nowrap}}.trace-copy small{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:8px;color:#9AA39E}}
.trace-status{{text-align:right;font-size:8px;font-weight:800;letter-spacing:.04em}}
.trace-item.active{{color:var(--ink);border-color:#D7E2DC;background:#F5F9F5;transform:translateX(2px)}}
.trace-item.active .trace-num{{background:var(--accent);color:#fff;box-shadow:0 0 0 4px rgba(53,104,89,.1)}}
.trace-item.active .trace-status{{color:var(--clay)}}
.trace-item.done{{color:#4F5B55}}.trace-item.done .trace-num{{background:#E1EDE4;color:var(--olive)}}.trace-item.done .trace-status{{color:var(--olive)}}
.stage-output{{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:12px;margin-top:12px;padding:10px 11px;border-radius:5px;background:var(--ink);color:#E7ECE8}}
.output-message{{display:flex;align-items:center;gap:8px;min-width:0;font:9px/1.4 Consolas,"Microsoft YaHei",monospace}}.output-message i{{flex:0 0 auto;width:6px;height:6px;border-radius:50%;background:#E9A077;box-shadow:0 0 0 4px rgba(233,160,119,.13)}}
.output-message span{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.output-proof{{color:#C6D4CC;font-size:8px;white-space:nowrap}}
.stage-caption{{position:absolute;right:18px;bottom:8px;color:#7A8580;font-size:8px;letter-spacing:.12em;text-transform:uppercase}}
@keyframes pulse{{50%{{box-shadow:0 0 0 7px rgba(53,104,89,0)}}}}
@keyframes run-progress{{0%{{transform:scaleX(0)}}100%{{transform:scaleX(1)}}}}
@keyframes stage-sheen{{0%,62%{{transform:translateX(-120%)}}82%,100%{{transform:translateX(120%)}}}}
.metrics{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--gap);margin:4px 0 18px}}
.metric{{position:relative;min-height:134px;padding:24px 20px 19px;background:rgba(250,250,247,.94);border:1px solid rgba(255,255,255,.62);border-radius:6px;box-shadow:none;backdrop-filter:blur(14px);overflow:hidden}}
.metric::before{{content:"";position:absolute;left:0;right:0;top:0;height:3px;background:#C86B4A}}
.metric:nth-child(2)::before{{background:#68CC58}}.metric:nth-child(3)::before{{background:#91CFB5}}
.metric::after{{display:none}}
.metric-label{{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}}
.metric-label i{{width:9px;height:9px;margin-top:4px;border-radius:50%;background:var(--clay)}}
.metric-label i.ready{{background:var(--olive)}}.metric-label i.pending{{background:#B49A64}}
.metric strong{{display:block;margin-top:17px;color:var(--ink);font:700 clamp(24px,3vw,34px)/1 Georgia,"Songti SC",serif}}
.workspace{{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(300px,.72fr);gap:var(--gap);padding:0 0 68px}}
.panel{{background:rgba(250,250,247,.97);border:1px solid rgba(255,255,255,.66);border-radius:8px;box-shadow:none;backdrop-filter:blur(16px)}}
.analysis-panel{{padding:30px}}
.section-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:22px}}
.section-number{{color:var(--clay);font:18px/1 Georgia,serif}}
h2{{margin:0;font:600 28px/1.2 Georgia,"Songti SC","SimSun",serif;letter-spacing:-.02em}}
.section-head p{{margin:7px 0 0;color:var(--muted);font-size:13px}}
.file-label{{display:block;margin:0 0 18px}}
.file-label>span,.mode-grid label>span,.import-field>span{{display:block;margin-bottom:4px;font-weight:800}}
.file-label small,.mode-grid label small,.import-field small{{display:block;margin-bottom:8px;color:var(--muted);font-weight:400;font-size:12px}}
select{{width:100%;appearance:none;padding:13px 42px 13px 14px;color:var(--ink);background-color:#fff;background-image:linear-gradient(45deg,transparent 50%,var(--accent) 50%),linear-gradient(135deg,var(--accent) 50%,transparent 50%);background-position:calc(100% - 17px) 19px,calc(100% - 11px) 19px;background-size:6px 6px,6px 6px;background-repeat:no-repeat;border:1px solid #C9CBC4;border-radius:8px;outline:none;transition:border-color .2s,box-shadow .2s}}
select:focus{{border-color:var(--accent);box-shadow:0 0 0 4px rgba(53,104,89,.12)}}
.import-box{{margin:0 0 30px;padding:22px 0;border-top:1px solid rgba(255,255,255,.18);border-bottom:1px solid rgba(255,255,255,.18)}}
.source-switch{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:0 0 24px}}.source-choice{{min-height:76px;padding:14px 18px;border:1px solid rgba(255,255,255,.28);border-radius:8px;background:#102522;color:#EEF3EF;text-align:left;font:inherit;font-size:14px;font-weight:800;cursor:pointer;transition:.2s}}
.source-choice small{{display:block;margin-top:5px;color:#AAB8B2;font-size:11px;font-weight:400}}
.source-choice:hover,.source-choice.active{{border-color:#F3FFC9;background:#F3FFC9;color:#13211E}}.source-choice:focus-visible{{outline:2px solid #91CFB5;outline-offset:3px}}
.source-panel[hidden]{{display:none!important}}.source-panel .file-label{{margin-bottom:0}}
.import-grid{{display:grid;grid-template-columns:1.15fr .65fr 1fr .75fr;gap:14px}}.import-field{{min-width:0}}
.import-field input{{width:100%;min-height:47px;padding:12px 0;border:0;border-bottom:1px solid rgba(255,255,255,.38);outline:none;background:transparent;color:inherit;font:inherit}}
.import-field input[type=file]{{padding:9px 0;font-size:12px}}.import-field input[type=file]::file-selector-button{{margin-right:12px;padding:8px 11px;border:1px solid rgba(255,255,255,.42);border-radius:999px;background:transparent;color:inherit;font:inherit;font-weight:800;cursor:pointer}}
.import-field input:focus{{border-bottom-color:#F3FFC9}}.import-field input::placeholder{{color:#75847E}}
.import-field input.autofilled,.import-actions select.autofilled{{animation:autoFillPulse .72s ease;border-bottom-color:#F3FFC9}}
.import-actions{{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:18px}}.import-actions p{{margin:0;color:#AAB8B2;font-size:11px}}
.import-actions>div:last-child{{display:flex;align-items:center;gap:10px}}.import-actions select{{min-width:158px;padding-top:10px;padding-bottom:10px}}
.import-actions button{{min-height:39px;padding:8px 15px;border:1px solid #F3FFC9;border-radius:999px;background:transparent;color:#F3FFC9;font:inherit;font-size:12px;font-weight:800;cursor:pointer}}.import-actions button:hover{{background:#F3FFC9;color:#131517}}.import-actions button:disabled{{opacity:.5;cursor:wait}}
.import-status{{display:block;margin-top:9px;color:#A8DCC8;font-size:11px}}.import-status.error{{color:#F4A176}}
@keyframes autoFillPulse{{0%{{background:rgba(243,255,201,.18)}}100%{{background:transparent}}}}
.mode-grid{{display:grid;grid-template-columns:1fr 1fr;gap:var(--gap);padding-top:3px}}
.notice{{display:flex;flex-direction:column;gap:3px;margin:4px 0 16px;padding:14px 16px;background:#F3FFC9;border:1px solid #D3E39B;border-left:4px solid #50723C;border-radius:6px;color:#3F542B}}
.notice span{{font-size:12px}}
.submit-row{{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:22px;margin-top:22px;padding-top:18px;border-top:1px solid #DED4B9}}
.submit-row p{{margin:0;color:var(--muted);font-size:11px;line-height:1.55}}
.action-buttons{{display:flex;align-items:center;justify-content:flex-end;gap:8px}}
.submit-row button,.secondary-action{{position:relative;display:inline-flex;align-items:center;justify-content:center;min-height:42px;padding:10px 13px;border-radius:8px;font-size:12px;font-weight:800;letter-spacing:.015em;white-space:nowrap;cursor:pointer;box-shadow:none;transition:background .16s,color .16s,border-color .16s,transform .16s}}
.submit-row button{{gap:16px;min-width:226px;border:1px solid #131517;background:#131517;color:#fff}}
.submit-row button::after{{content:"↗";display:grid;place-items:center;width:21px;height:21px;margin:-1px -3px -1px 0;border-left:1px solid rgba(255,255,255,.25);padding-left:10px;font-size:13px}}
.submit-row button:hover{{background:#28594E;border-color:#28594E;transform:translateY(-1px)}}
.submit-row button:active,.secondary-action:active{{transform:translateY(0)}}
.submit-row button:disabled{{cursor:not-allowed;opacity:.46;transform:none}}
.secondary-action{{border:1px solid #BEB69F;background:#FFFDF7;color:#303735}}
.secondary-action:hover{{background:#232326;border-color:#232326;color:#fff;transform:translateY(-1px)}}
.archive-panel{{padding:0;overflow:hidden}}
.archive-head{{padding:28px 26px 20px;border-bottom:1px solid #D9D9D2}}
.archive-head p{{margin:6px 0 0;color:var(--muted);font-size:12px}}
.report-list{{display:grid}}
.report-item{{display:grid;grid-template-columns:34px minmax(0,1fr) 24px;align-items:center;gap:10px;padding:17px 22px;border-bottom:1px solid #DFE0DA;transition:background .2s,color .2s}}
.report-item:hover{{background:var(--soft);color:var(--accent)}}
.report-index{{font:16px/1 Georgia,serif;color:var(--clay)}}
.report-copy{{min-width:0}}.report-copy small{{display:block;color:var(--muted);font-size:9px;letter-spacing:.12em}}.report-copy strong{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}}
.report-arrow{{font-size:18px}}
.empty-state{{display:flex;align-items:center;gap:14px;padding:28px;color:var(--muted)}}.empty-state>span{{font:32px/1 Georgia,serif;color:var(--clay)}}.empty-state p{{margin:0}}.empty-state small{{font-size:11px}}
.archive-foot{{display:flex;justify-content:space-between;align-items:center;padding:16px 22px;background:#F1F2EC;border-top:1px solid #DFE0DA;color:var(--muted);font-size:11px}}
.archive-foot a{{display:inline-flex;align-items:center;min-height:32px;padding:6px 9px;border:1px solid #BEB69F;border-radius:8px;background:#FFFDF7;color:#303735;font-weight:800;transition:.16s}}
.archive-foot a:hover{{background:#232326;border-color:#232326;color:#fff}}
/* Solid dark surface system */
.agent-stage{{background:#0A1D1B;border-color:#29413C;box-shadow:none;backdrop-filter:none}}
.agent-sidebar{{background:#071513;border-right-color:#263B36}}
.agent-app{{color:#F3F5EF}}
.agent-toolbar{{background:#102522;border-bottom-color:#29413C}}
.agent-title i{{box-shadow:0 0 0 5px rgba(241,148,71,.11)}}
.context-chip{{background:#142B27;border-color:#314942;color:#BCC9C3}}
.context-chip strong{{color:#A8DCC8}}
.run-card{{background:#081816;border-color:#2A413B;box-shadow:none}}
.run-head{{border-bottom-color:rgba(255,255,255,.11)}}
.run-kicker,.stage-caption{{color:#94A49D}}.run-file{{color:#F4F6F1}}.run-state{{color:#A8DCC8}}
.trace-item{{color:#81918A}}.trace-copy small{{color:#778780}}
.trace-num{{background:rgba(255,255,255,.07);color:#AAB8B2}}
.trace-item.active{{color:#F4F6F1;border-color:rgba(145,207,181,.3);background:rgba(145,207,181,.1)}}
.trace-item.active .trace-num{{background:#91CFB5;color:#10211E;box-shadow:none}}
.trace-item.done{{color:#B8C8C0}}.trace-item.done .trace-num{{background:rgba(104,204,88,.13);color:#A5D99B}}
.stage-output{{background:#030D0C;border:1px solid #21342F}}
.metric{{background:#0D2522;border-color:#2D4540;color:#F3F5EF;box-shadow:none;backdrop-filter:none}}
.metric-label{{color:#B6C3BD}}.metric strong{{color:#FFFDF4}}
.panel{{background:#0B211F;border-color:#2D4540;color:#EEF3EF;box-shadow:none;backdrop-filter:none}}
.panel h2{{color:#FFFDF4}}.section-head p,.file-label small,.mode-grid label small,.import-field small{{color:#AAB8B2}}
.file-label>span,.mode-grid label>span,.import-field>span{{color:#EEF3EF}}
select{{color:#F5F7F3;background-color:#132D29;border-color:#38524B}}
select option{{background:#102522;color:#F5F7F3}}
.notice{{background:#18332B;border-color:#365744;border-left-color:#91CFB5;color:#E7F4D5}}
.submit-row{{border-top-color:rgba(255,255,255,.14)}}.submit-row p{{color:#AAB8B2}}
.submit-row button{{background:#F3FFC9;border-color:#F3FFC9;color:#131517}}
.submit-row button::after{{border-left-color:rgba(19,21,23,.25)}}
.submit-row button:hover{{background:#91CFB5;border-color:#91CFB5}}
.secondary-action{{background:#142B27;border-color:#3B534D;color:#F2F4EF}}
.secondary-action:hover{{background:#FFF;color:#131517;border-color:#FFF}}
.archive-head{{border-bottom-color:rgba(255,255,255,.13)}}.archive-head p{{color:#AAB8B2}}
.report-item{{border-bottom-color:rgba(255,255,255,.1);color:#EEF3EF}}
.report-item:hover{{background:#142D29;color:#F3FFC9}}
.report-copy small{{color:#91A19A}}.report-index{{color:#F4A176}}
.archive-foot{{background:#102724;border-top-color:#2A413B;color:#AAB8B2}}
.archive-foot a{{background:#172F2B;border-color:#3A544D;color:#F2F4EF}}
.archive-foot a:hover{{background:#FFF;border-color:#FFF;color:#131517}}
/* OddCommon-inspired borderless editorial system */
body::before{{background-image:linear-gradient(90deg,rgba(4,14,13,.97) 0%,rgba(5,17,16,.9) 48%,rgba(5,16,16,.78) 100%),url('/assets/market-analysis-background.jpg');background-position:center 42%}}
body::after{{opacity:.055}}
.shell{{width:min(1420px,calc(100% - 64px))}}
.topbar{{min-height:90px;border-bottom:0}}
.brand-mark{{width:34px;height:34px;border:0;background:transparent;color:#F3FFC9;box-shadow:none;font:800 21px/1 Arial,sans-serif;transform:none}}
.brand small{{color:#87958F}}
.mode-badge{{border:0;padding:7px 0;border-radius:0}}
.hero{{min-height:calc(100vh - 90px);grid-template-columns:minmax(380px,.88fr) minmax(520px,1.12fr);padding:74px 0 92px}}
.eyebrow{{margin-bottom:25px;color:#F3FFC9}}.eyebrow::before{{width:58px;background:#F3FFC9}}
h1{{max-width:720px;font:800 clamp(58px,7vw,108px)/1.02 Arial,"Microsoft YaHei",sans-serif;letter-spacing:-.045em;text-transform:none}}
h1 .title-line{{display:block;white-space:nowrap}}
h1 em{{color:#F3FFC9;font-style:normal;text-decoration:none}}
.agent-stage{{grid-template-columns:1fr;height:470px;overflow:visible;background:transparent;border:0;border-radius:0}}
.agent-stage::after,.agent-sidebar{{display:none}}
.agent-toolbar{{padding:0 0 16px;background:transparent;border-bottom:1px solid rgba(255,255,255,.34)}}
.agent-context{{padding:13px 0 0}}
.context-chip{{padding:5px 0;margin-right:16px;border:0;border-radius:0;background:transparent;color:#9AA8A2}}
.run-card{{margin:14px 0 0;padding:18px 0 0;border:0;border-radius:0;background:transparent}}
.run-card::before{{height:1px;background:linear-gradient(90deg,#F3FFC9 0 36%,rgba(255,255,255,.12) 36%)}}
.trace-list{{gap:0 22px}}.trace-item{{padding:9px 0;border:0;border-bottom:1px solid rgba(255,255,255,.1);border-radius:0}}
.trace-item.active{{border-color:#F3FFC9;background:transparent;transform:translateX(7px)}}
.stage-output{{margin-top:15px;padding:13px 0;border:0;border-top:1px solid rgba(255,255,255,.24);border-radius:0;background:transparent}}
.stage-caption{{right:0;bottom:-25px}}
.metrics{{gap:0;margin:0 0 72px;border-bottom:1px solid rgba(255,255,255,.22)}}
.metric{{min-height:150px;padding:23px 22px 28px;background:transparent;border:0;border-top:1px solid rgba(255,255,255,.32);border-radius:0}}
.metric+.metric{{border-left:1px solid rgba(255,255,255,.14)}}.metric::before{{display:none}}
.metric-label{{color:#85938D;font-size:10px}}.metric strong{{margin-top:29px;color:#F5F6F0;font:700 clamp(27px,3vw,40px)/1 Arial,"Microsoft YaHei",sans-serif;letter-spacing:-.04em}}
.workspace{{gap:72px;padding-bottom:110px}}
.panel{{background:transparent;border:0;border-top:1px solid rgba(255,255,255,.38);border-radius:0;backdrop-filter:none}}
.analysis-panel{{padding:35px 0 0}}.section-head{{margin-bottom:38px}}.section-head h2,.archive-head h2{{font:700 clamp(30px,3vw,48px)/1 Arial,"Microsoft YaHei",sans-serif;letter-spacing:-.045em}}
.section-number{{color:#F3FFC9}}
.file-label{{margin-bottom:26px}}.file-label>span,.mode-grid label>span,.import-field>span{{font-size:11px;letter-spacing:.12em;text-transform:uppercase}}
select{{padding:17px 30px 17px 0;background-color:transparent;border:0;border-bottom:1px solid rgba(255,255,255,.42);border-radius:0}}
.mode-grid{{gap:30px}}.mode-grid label{{padding-top:18px;border-top:1px solid rgba(255,255,255,.12)}}
.notice{{padding:14px 0 14px 18px;background:transparent;border:0;border-left:2px solid #F3FFC9;border-radius:0;color:#DCE9D8}}
.submit-row{{margin-top:34px;padding-top:24px;border-top-color:rgba(255,255,255,.28)}}
.submit-row button,.secondary-action{{border-radius:999px}}
.secondary-action{{background:transparent;border-color:rgba(255,255,255,.5)}}
.archive-head{{padding:35px 0 23px;border-bottom-color:rgba(255,255,255,.18)}}
.report-item{{padding:20px 0;border-bottom-color:rgba(255,255,255,.14)}}.report-item:hover{{background:transparent;transform:translateX(8px)}}
.archive-foot{{padding:20px 0;background:transparent;border-top:0}}.archive-foot a{{border:0;border-bottom:1px solid #91CFB5;border-radius:0;background:transparent;padding-inline:0}}
/* Scroll-triggered text and surface reveal */
.reveal-ready .scroll-reveal{{opacity:0;filter:blur(5px);transform:translateY(24px) scale(.985);transition:opacity .62s cubic-bezier(.22,1,.36,1),transform .62s cubic-bezier(.22,1,.36,1),filter .5s ease;transition-delay:var(--reveal-delay,0ms)}}
.reveal-ready .scroll-reveal.is-visible{{opacity:1;filter:blur(0);transform:translateY(0) scale(1)}}
footer{{display:flex;justify-content:space-between;gap:20px;padding:20px 0 34px;border-top:1px solid rgba(255,255,255,.25);color:#CCD7D1;font-size:11px;letter-spacing:.05em;text-shadow:0 2px 12px rgba(0,0,0,.35)}}
@media(max-width:980px){{.shell{{width:min(100% - 38px,1420px)}}.hero{{grid-template-columns:1fr;padding-top:44px}}.agent-stage{{height:430px}}.metrics{{grid-template-columns:repeat(3,1fr)}}.workspace{{grid-template-columns:1fr}}.import-grid{{grid-template-columns:1fr 1fr}}}}
@media(max-width:600px){{.shell{{width:min(100% - 24px,1420px)}}.top-meta>span{{display:none}}.hero{{min-height:auto;padding:36px 0 74px;gap:48px}}h1{{font-size:52px}}.agent-stage{{height:500px}}.agent-context{{padding-left:0}}.run-card{{margin-top:10px;padding-inline:0}}.trace-list{{grid-template-columns:1fr}}.trace-item{{min-height:39px}}.metrics{{grid-template-columns:1fr}}.metric+.metric{{border-left:0}}.metric{{min-height:112px;padding:19px 12px 16px}}.metric strong{{font-size:22px}}.workspace{{gap:48px}}.analysis-panel{{padding-top:28px}}.source-switch{{display:grid;grid-template-columns:1fr 1fr}}.source-choice{{padding-inline:10px}}.import-grid{{grid-template-columns:1fr}}.import-actions{{align-items:flex-start;flex-direction:column}}.mode-grid{{grid-template-columns:1fr}}.submit-row{{grid-template-columns:1fr;align-items:start;gap:13px}}.action-buttons{{display:grid;grid-template-columns:1fr 1fr;width:100%}}.submit-row button,.secondary-action{{width:100%;min-width:0;padding-inline:10px}}footer{{flex-direction:column}}}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}.agent-stage::after,.run-card::before,.run-state i{{animation:none!important}}.trace-item{{transition:none}}.reveal-ready .scroll-reveal{{opacity:1;filter:none;transform:none;transition:none}}}}
</style>
</head>
<body>
<header class="shell">
  <nav class="topbar" aria-label="主导航">
    <a class="brand" href="/"><span class="brand-mark">研</span><span>麦穗终端<small>LEDGER INTELLIGENCE</small></span></a>
    <div class="top-meta">{mode_badge}</div>
  </nav>
  <section class="hero">
    <div>
      <p class="eyebrow">Financial research agent / 01</p>
      <h1><span class="title-line">让每一项判断</span><span class="title-line">都有<em>证据回声</em></span></h1>
    </div>
    <aside class="agent-stage" aria-label="财报分析智能体执行流程演示">
      <div class="agent-sidebar" aria-hidden="true">
        <span class="agent-logo">研</span><span class="side-icon active">◇</span><span class="side-icon">▤</span><span class="side-icon">∑</span><span class="side-icon">⌘</span><span class="side-icon">✓</span><span class="side-icon">≡</span>
      </div>
      <div class="agent-app">
        <div class="agent-toolbar"><span class="agent-title"><i></i>证据链分析器</span></div>
        <div class="agent-context" aria-hidden="true"><span class="context-chip"><strong>SPACE</strong> 受控数据</span><span class="context-chip"><strong>MODE</strong> PAGE TRACE</span><span class="context-chip"><strong>LOG</strong> REPRODUCIBLE</span></div>
        <div class="run-card">
          <div class="run-head"><div><small class="run-kicker">Agent run / live trace</small><strong class="run-file" id="stage-file">{html.escape(reports[0].name if reports else '等待选择受控示例财报')}</strong></div><span class="run-state"><i></i><span id="stage-state">正在演练证据链</span></span></div>
          <ol class="trace-list" id="trace-list">
            <li class="trace-item active" data-progress="登记文件哈希与报告期"><span class="trace-num">01</span><span class="trace-copy"><strong>文件登记</strong><small>SHA-256 · 文档分类</small></span><span class="trace-status">进行中</span></li>
            <li class="trace-item" data-progress="扫描页自动进入 OCR 回退"><span class="trace-num">02</span><span class="trace-copy"><strong>页级解析</strong><small>Native text · OCR</small></span><span class="trace-status">等待</span></li>
            <li class="trace-item" data-progress="统一指标、期间、单位与口径"><span class="trace-num">03</span><span class="trace-copy"><strong>指标结构化</strong><small>Facts · Page anchors</small></span><span class="trace-status">等待</span></li>
            <li class="trace-item" data-progress="同比、环比和勾稽由程序复算"><span class="trace-num">04</span><span class="trace-copy"><strong>确定性计算</strong><small>Formula · Reconcile</small></span><span class="trace-status">等待</span></li>
            <li class="trace-item" data-progress="规则与 DeepSeek 分层解释"><span class="trace-num">05</span><span class="trace-copy"><strong>逻辑核验</strong><small>Rules · DeepSeek</small></span><span class="trace-status">等待</span></li>
            <li class="trace-item" data-progress="报告、日志与证据链同步归档"><span class="trace-num">06</span><span class="trace-copy"><strong>可审计输出</strong><small>Report · Manifest</small></span><span class="trace-status">等待</span></li>
          </ol>
          <div class="stage-output"><span class="output-message"><i></i><span id="stage-message">登记文件哈希与报告期</span></span><span class="output-proof">TRACE ID / 2257</span></div>
        </div>
        <span class="stage-caption">动态演示 · 实际结果以运行日志为准</span>
      </div>
    </aside>
  </section>
</header>
<main class="shell">
  <section class="metrics" aria-label="系统状态">
    <article class="metric"><div class="metric-label"><span>受控示例财报</span><i></i></div><strong id="report-count">{len(reports)} 份</strong></article>
    <article class="metric"><div class="metric-label"><span>AI Studio OCR</span><i class="{ocr_class}"></i></div><strong>{ocr_status}</strong></article>
    <article class="metric"><div class="metric-label"><span>DeepSeek</span><i class="{llm_class}"></i></div><strong>{llm_status}</strong></article>
  </section>
  <section class="workspace">
    <article class="panel analysis-panel">
      <div class="section-head"><div><h2>启动一次分析</h2><p>选择材料与运行策略，系统将自动建立完整证据链。</p></div><span class="section-number">01 / 02</span></div>
      <form method="post" action="/analyze" id="analysis-form">
        <section class="import-box" aria-label="选择财报来源">
          <div class="source-switch" role="tablist" aria-label="财报来源">
            <button class="source-choice" id="source-example" type="button" role="tab" aria-selected="false" aria-controls="example-panel">示例研报</button>
            <button class="source-choice" id="source-local" type="button" role="tab" aria-selected="false" aria-controls="local-panel">本地研报<small>从电脑中选择一份 PDF</small></button>
          </div>
          <div class="source-panel" id="example-panel" role="tabpanel" aria-labelledby="source-example" hidden>
            <label class="file-label"><span>选择示例研报</span><small>当前环境内置 {len(example_reports)} 份示例 PDF</small><select id="report-select" name="file" aria-label="选择示例研报">{options}</select></label>
          </div>
          <div class="source-panel" id="local-panel" role="tabpanel" aria-labelledby="source-local" hidden>
            <div class="import-grid">
              <label class="import-field"><span>本地 PDF</span><input id="report-file" type="file" accept="application/pdf,.pdf"></label>
              <label class="import-field"><span>股票代码</span><small>自动识别，可修改</small><input id="security-code" type="text" maxlength="12" placeholder="等待识别"></label>
              <label class="import-field"><span>公司简称</span><small>自动识别，可修改</small><input id="company-name" type="text" maxlength="32" placeholder="等待识别"></label>
              <label class="import-field"><span>报告期</span><small>FY / H1 / Q1 等</small><input id="report-period" type="text" maxlength="8" placeholder="等待识别"></label>
            </div>
            <div class="import-actions"><div><p>规范名称：股票代码_公司简称_报告期_报告类型.pdf</p><span class="import-status" id="import-status" aria-live="polite">选择 PDF 后将自动识别字段，识别结果可手动修改</span></div><div><select id="report-type" aria-label="报告类型"><option value="annual">年度报告</option><option value="semiannual">半年度报告</option><option value="q1">第一季度报告</option><option value="q3">第三季度报告</option><option value="other">其他财务报告</option></select><button id="import-button" type="button" disabled>确认并使用</button></div></div>
          </div>
        </section>
        {mode_controls}
        <div class="submit-row"><p>分析将在后台执行，提交后自动进入进度页。<br>可安全等待，不会因 Cloudflare 长连接超时而丢失任务。</p><div class="action-buttons"><a class="secondary-action" href="#recent-reports">历史报告</a><button type="submit" data-default-label="生成可审计分析报告" data-initial-disabled="{'true' if not reports else 'false'}"{submit_disabled}>生成可审计分析报告</button></div></div>
      </form>
    </article>
    <aside class="panel archive-panel" id="recent-reports">
      <div class="archive-head"><h2>最近报告</h2><p>按生成时间倒序保留最近五次分析</p></div>
      <div class="report-list">{recent_links}</div>
      <div class="archive-foot"><span>ARCHIVE / 05</span><a href="/health">查看脱敏状态</a></div>
    </aside>
  </section>
</main>
<footer class="shell"><span>FINANCIAL RESEARCH AGENT · 2026</span><span>DESIGN SEED / 2257504322086257687</span></footer>
<script>
const form=document.getElementById("analysis-form");
const traceItems=Array.from(document.querySelectorAll(".trace-item"));
const stageState=document.getElementById("stage-state");
const stageMessage=document.getElementById("stage-message");
const stageFile=document.getElementById("stage-file");
const fileSelect=form?form.querySelector('select[name="file"]'):null;
const importButton=document.getElementById("import-button");
const importStatus=document.getElementById("import-status");
const reportFile=document.getElementById("report-file");
const securityCodeInput=document.getElementById("security-code");
const companyNameInput=document.getElementById("company-name");
const reportPeriodInput=document.getElementById("report-period");
const reportTypeInput=document.getElementById("report-type");
const reportCount=document.getElementById("report-count");
const sourceExample=document.getElementById("source-example");
const sourceLocal=document.getElementById("source-local");
const examplePanel=document.getElementById("example-panel");
const localPanel=document.getElementById("local-panel");
const reduceMotion=window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let traceIndex=0;
let traceTimer=null;
let tracePaused=reduceMotion;
let activeSource="";
let localReportReady=false;
const renderTrace=()=>{{
  traceItems.forEach((item,index)=>{{
    item.classList.toggle("done",index<traceIndex);
    item.classList.toggle("active",index===traceIndex);
    const label=item.querySelector(".trace-status");
    if(label)label.textContent=index<traceIndex?"已验证":index===traceIndex?"进行中":"等待";
  }});
  const active=traceItems[traceIndex];
  if(active&&stageMessage)stageMessage.textContent=active.dataset.progress||"正在执行受控分析";
}};
const stopTrace=()=>{{if(traceTimer){{window.clearInterval(traceTimer);traceTimer=null;}}}};
const startTrace=()=>{{
  stopTrace();
  if(tracePaused||!traceItems.length)return;
  traceTimer=window.setInterval(()=>{{traceIndex=(traceIndex+1)%traceItems.length;renderTrace();}},1400);
}};
const syncStageFile=()=>{{
  if(!fileSelect||!stageFile)return;
  const selected=fileSelect.options[fileSelect.selectedIndex];
  stageFile.textContent=selected?selected.text.split(" / ").pop():"等待选择受控示例财报";
}};
if(fileSelect){{fileSelect.addEventListener("change",syncStageFile);syncStageFile();}}
const updateSubmitAvailability=()=>{{
  const submit=form?.querySelector('button[type="submit"]');
  if(!submit)return;
  const enabled=activeSource==="local"?localReportReady:activeSource==="example"&&Boolean(fileSelect?.value);
  submit.dataset.initialDisabled=enabled?"false":"true";
  submit.disabled=!enabled;
}};
const setReportSource=(source)=>{{
  activeSource=source==="local"?"local":source==="example"?"example":"";
  const showLocal=activeSource==="local";
  const showExample=activeSource==="example";
  if(examplePanel)examplePanel.hidden=!showExample;
  if(localPanel)localPanel.hidden=!showLocal;
  if(sourceExample){{sourceExample.classList.toggle("active",showExample);sourceExample.setAttribute("aria-selected",String(showExample));}}
  if(sourceLocal){{sourceLocal.classList.toggle("active",showLocal);sourceLocal.setAttribute("aria-selected",String(showLocal));}}
  updateSubmitAvailability();
}};
if(sourceExample)sourceExample.addEventListener("click",()=>setReportSource("example"));
if(sourceLocal)sourceLocal.addEventListener("click",()=>setReportSource("local"));
setReportSource("");
let inspectionRequest=0;
const requiredImportFieldsReady=()=>Boolean(
  reportFile?.files?.length&&securityCodeInput?.value.trim()&&companyNameInput?.value.trim()&&reportPeriodInput?.value.trim()
);
const updateImportAvailability=()=>{{
  if(importButton&&!importButton.dataset.busy)importButton.disabled=!requiredImportFieldsReady();
}};
const fillRecognizedField=(field,value)=>{{
  if(!field||!value)return;
  field.value=value;
  field.classList.remove("autofilled");
  window.requestAnimationFrame(()=>field.classList.add("autofilled"));
}};
const inspectSelectedReport=async()=>{{
  const requestId=++inspectionRequest;
  setReportSource("local");
  localReportReady=false;updateSubmitAvailability();
  importStatus.classList.remove("error");
  for(const field of [securityCodeInput,companyNameInput,reportPeriodInput]){{if(field)field.value="";}}
  if(reportTypeInput)reportTypeInput.value="annual";
  if(!reportFile?.files?.length){{importStatus.textContent="选择 PDF 后将自动识别字段，识别结果可手动修改";updateImportAvailability();return;}}
  const selectedFile=reportFile.files[0];
  if(selectedFile.size>80*1024*1024){{importStatus.textContent="PDF 超过 80 MB 限制";importStatus.classList.add("error");updateImportAvailability();return;}}
  const payload=new FormData();payload.append("report_file",selectedFile);
  importButton.dataset.busy="true";importButton.disabled=true;importButton.textContent="正在识别…";importStatus.textContent="正在读取 PDF 并定位公司及报告期";
  try{{
    const response=await fetch("/api/inspect-report",{{method:"POST",body:payload,cache:"no-store"}});
    const data=await response.json();
    if(requestId!==inspectionRequest)return;
    if(!response.ok)throw new Error(data.error||"自动识别失败");
    fillRecognizedField(securityCodeInput,data.security_code);
    fillRecognizedField(companyNameInput,data.company_name);
    fillRecognizedField(reportPeriodInput,data.report_period);
    fillRecognizedField(reportTypeInput,data.report_type||"other");
    const missing=[];
    if(!data.security_code)missing.push("股票代码");
    if(!data.company_name)missing.push("公司简称");
    if(!data.report_period)missing.push("报告期");
    const source=data.source||"PDF 文本";
    const confidence=Number(data.confidence||0);
    if(data.requires_ocr){{
      importStatus.textContent=`正文文本不足，可能需要 OCR；已按可用信息预填 · 置信度 ${{confidence}}% · ${{source}}${{missing.length?` · 请补充${{missing.join("、")}}`:" · 请核对后导入"}}`;
    }}else{{
      importStatus.textContent=`自动识别完成 · 置信度 ${{confidence}}% · 来源：${{source}}${{missing.length?` · 请补充${{missing.join("、")}}`:" · 可修改后导入"}}`;
    }}
  }}catch(error){{
    if(requestId!==inspectionRequest)return;
    importStatus.textContent=`自动识别失败：${{error.message||"请手动填写字段"}}`;
    importStatus.classList.add("error");
  }}finally{{
    if(requestId===inspectionRequest){{delete importButton.dataset.busy;importButton.textContent="确认并使用";updateImportAvailability();}}
  }}
}};
if(reportFile)reportFile.addEventListener("change",inspectSelectedReport);
for(const field of [securityCodeInput,companyNameInput,reportPeriodInput,reportTypeInput]){{
  if(field)field.addEventListener("input",updateImportAvailability);
}}
if(importButton&&fileSelect){{
  importButton.addEventListener("click",async()=>{{
    const fields={{
      security_code:securityCodeInput?.value||"",
      company_name:companyNameInput?.value||"",
      report_period:reportPeriodInput?.value||"",
      report_type:reportTypeInput?.value||"annual"
    }};
    if(!reportFile?.files?.length){{importStatus.textContent="请先选择一份 PDF 财报";importStatus.classList.add("error");return;}}
    const payload=new FormData();
    payload.append("report_file",reportFile.files[0]);
    Object.entries(fields).forEach(([key,value])=>payload.append(key,value));
    importButton.dataset.busy="true";importButton.disabled=true;importButton.textContent="正在导入…";importStatus.textContent="正在校验 PDF 并复制到受控目录";importStatus.classList.remove("error");
    try{{
      const response=await fetch("/api/import-report",{{method:"POST",body:payload}});
      const data=await response.json();
      if(!response.ok)throw new Error(data.error||"导入失败");
      if(data.recognized){{
        fillRecognizedField(securityCodeInput,data.recognized.security_code);
        fillRecognizedField(companyNameInput,data.recognized.company_name);
        fillRecognizedField(reportPeriodInput,data.recognized.report_period);
        fillRecognizedField(reportTypeInput,data.recognized.report_type||"other");
      }}
      const option=new Option(data.label,data.path,true,true);
      fileSelect.add(option);fileSelect.value=data.path;syncStageFile();
      localReportReady=true;updateSubmitAvailability();
      if(reportCount)reportCount.textContent=`${{data.report_count}} 份`;
      importStatus.textContent=`已就绪：${{data.filename}} · 可以开始分析`;
    }}catch(error){{importStatus.textContent=error.message||"导入失败";importStatus.classList.add("error");}}
    finally{{delete importButton.dataset.busy;importButton.textContent="确认并使用";updateImportAvailability();}}
  }});
}}
renderTrace();startTrace();
if(form){{
  const button=form.querySelector("button[type=submit]");
  const restoreButton=()=>{{
    if(!button)return;
    button.textContent=button.dataset.defaultLabel||"生成可审计分析报告";
    button.disabled=button.dataset.initialDisabled==="true";
    if(stageState)stageState.textContent="正在演练证据链";
    traceIndex=0;renderTrace();startTrace();
  }};
  window.addEventListener("pageshow",restoreButton);
  form.addEventListener("submit",()=>{{if(button&&!button.disabled){{button.textContent="正在建立证据链…";button.disabled=true;if(stageState)stageState.textContent="分析任务执行中";traceIndex=0;renderTrace();startTrace();}}}});
}}
const revealTargets=Array.from(document.querySelectorAll(".metric,.section-head,.import-box,.file-label,.mode-grid,.notice,.submit-row,.archive-head,.report-item,.archive-foot,footer"));
document.documentElement.classList.add("reveal-ready");
revealTargets.forEach((element,index)=>{{element.classList.add("scroll-reveal");element.style.setProperty("--reveal-delay",`${{(index%4)*65}}ms`);}});
if(reduceMotion||!("IntersectionObserver" in window)){{
  revealTargets.forEach(element=>element.classList.add("is-visible"));
}}else{{
  const revealObserver=new IntersectionObserver(entries=>{{entries.forEach(entry=>entry.target.classList.toggle("is-visible",entry.isIntersecting));}},{{threshold:.12,rootMargin:"0px 0px -8% 0px"}});
  revealTargets.forEach(element=>revealObserver.observe(element));
}}
</script>
</body>
</html>"""


def serve(
    settings: Settings,
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    offline: bool = False,
    open_browser: bool = False,
) -> None:
    local_report_root = managed_report_root(settings).resolve()
    if all(root.resolve() != local_report_root for root in settings.data_roots):
        settings.data_roots.insert(0, local_report_root)
    (local_report_root / "本地导入").mkdir(parents=True, exist_ok=True)
    pipeline = FinancialReportPipeline(settings)
    chat_service = ChatService(settings, offline=offline)
    analysis_jobs = AnalysisJobRegistry()
    analysis_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="financial-analysis")
    web_username = os.environ.get("FIN_AGENT_WEB_USERNAME", "").strip()
    web_password = os.environ.get("FIN_AGENT_WEB_PASSWORD", "")
    authentication_enabled = bool(web_username and web_password)

    class Handler(BaseHTTPRequestHandler):
        def _send_portal_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")

        def _send(self, content: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200, *, portal_cors: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if content_type.startswith("text/html"):
                self.send_header("Cache-Control", "no-store")
            if portal_cors:
                self._send_portal_cors_headers()
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, payload: object, status: int = 200) -> None:
            content = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _require_authentication(self) -> bool:
            if basic_auth_matches(self.headers.get("Authorization"), web_username, web_password):
                return False
            payload = "需要用户名和密码".encode("utf-8")
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Financial Research Agent", charset="UTF-8"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return True

        def do_OPTIONS(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/health":
                self._send(b"", status=404)
                return
            self.send_response(204)
            self._send_portal_cors_headers()
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self._require_authentication():
                return
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                page = render_home_page(settings, offline=offline)
                self._send(page.encode("utf-8"))
                return
            if parsed.path == "/health":
                payload = {
                    "status": "ok",
                    "mode": "offline" if offline else "live_capable",
                    "pdf_count": len(list_reports(settings)),
                    "ocr_ready": settings.ocr_configured(),
                    "deepseek_ready": settings.llm_configured(),
                }
                self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", portal_cors=True)
                return
            if parsed.path == "/api/job":
                query = urllib.parse.parse_qs(parsed.query)
                job = analysis_jobs.get(query.get("id", [""])[0])
                if job is None:
                    self._send_json({"error": "分析任务不存在或服务已重启"}, status=404)
                    return
                self._send_json(job)
                return
            if parsed.path == "/job":
                query = urllib.parse.parse_qs(parsed.query)
                job_id = query.get("id", [""])[0]
                if analysis_jobs.get(job_id) is None:
                    self._send("分析任务不存在或服务已重启".encode("utf-8"), status=404)
                    return
                self._send(render_job_page(job_id).encode("utf-8"))
                return
            if parsed.path == "/api/chat/history":
                query = urllib.parse.parse_qs(parsed.query)
                try:
                    payload = chat_service.history(
                        query.get("run_id", [""])[0],
                        query.get("session_id", [""])[0],
                    )
                except ChatError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                self._send_json(payload)
                return
            if parsed.path == "/assets/market-analysis-background.jpg":
                asset = settings.project_root / "agent_assets" / "ui" / "market-analysis-background.jpg"
                if not asset.is_file():
                    self._send("Asset not found".encode("utf-8"), status=404)
                    return
                self._send(asset.read_bytes(), "image/jpeg")
                return
            if parsed.path == "/report":
                query = urllib.parse.parse_qs(parsed.query)
                requested = Path(query.get("path", [""])[0])
                if not requested.name == "report.html" or not _within(requested, [settings.output_dir]):
                    self._send("非法路径".encode("utf-8"), status=403)
                    return
                if not requested.exists():
                    self._send("报告不存在".encode("utf-8"), status=404)
                    return
                bundle_path = requested.with_name("analysis_bundle.json")
                if bundle_path.exists():
                    try:
                        from .bundle_io import load_analysis_bundle
                        from .report import render_html

                        render_html(load_analysis_bundle(bundle_path), requested)
                    except (OSError, ValueError, TypeError, KeyError):
                        # A legacy or partially written run remains readable even
                        # when it cannot be upgraded in place.
                        pass
                content_type = mimetypes.guess_type(requested.name)[0] or "text/html"
                self._send(requested.read_bytes(), f"{content_type}; charset=utf-8")
                return
            self._send("Not Found".encode("utf-8"), status=404)

        def do_POST(self) -> None:  # noqa: N802
            if self._require_authentication():
                return
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/inspect-report":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    self._send_json({"error": "上传内容为空"}, status=400)
                    return
                if length > MAX_IMPORTED_REPORT_BYTES + 1024 * 1024:
                    self._send_json({"error": "PDF 超过 80 MB 限制"}, status=413)
                    return
                try:
                    _fields, original_name, upload_bytes = parse_multipart_report(
                        self.headers.get("Content-Type", ""),
                        self.rfile.read(length),
                    )
                    inspection = inspect_pdf_report(upload_bytes, original_name)
                except (OSError, ValueError) as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **inspection})
                return
            if parsed.path == "/api/import-report":
                temporary_path: Path | None = None
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    self._send_json({"error": "上传内容为空"}, status=400)
                    return
                if length > MAX_IMPORTED_REPORT_BYTES + 1024 * 1024:
                    self._send_json({"error": "PDF 超过 80 MB 限制"}, status=413)
                    return
                try:
                    fields, original_name, upload_bytes = parse_multipart_report(
                        self.headers.get("Content-Type", ""),
                        self.rfile.read(length),
                    )
                    validate_uploaded_pdf(original_name, upload_bytes)
                    inspection = inspect_pdf_report(upload_bytes, original_name)
                    fields = merge_report_fields(fields, inspection)
                    standardized_name = standardized_report_filename(
                        fields.get("security_code", ""),
                        fields.get("company_name", ""),
                        fields.get("report_period", ""),
                        fields.get("report_type", ""),
                    )
                    destination_dir = local_report_root / "本地导入"
                    destination_dir.mkdir(parents=True, exist_ok=True)
                    destination = next_available_report_path(destination_dir, standardized_name)
                    temporary_path = destination_dir / f".{uuid.uuid4().hex}.uploading"
                    temporary_path.write_bytes(upload_bytes)
                    os.replace(temporary_path, destination)
                except (OSError, ValueError) as exc:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)
                    self._send_json({"error": str(exc)}, status=400)
                    return
                self._send_json({
                    "ok": True,
                    "path": str(destination.resolve()),
                    "label": f"{destination.parent.name} / {destination.name}",
                    "filename": destination.name,
                    "report_count": len(list_reports(settings)),
                    "recognized": inspection,
                })
                return
            if parsed.path == "/api/chat":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 65536:
                    self._send_json({"error": "请求正文为空或过大"}, status=400)
                    return
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(body, dict):
                        raise ChatError("请求正文必须是 JSON 对象")
                    payload = chat_service.ask(
                        str(body.get("run_id") or ""),
                        str(body.get("question") or ""),
                        str(body["session_id"]) if body.get("session_id") else None,
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send_json({"error": "请求正文不是有效的 JSON"}, status=400)
                    return
                except ChatError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                except Exception as exc:
                    self._send_json({"error": f"问答服务失败：{type(exc).__name__}"}, status=500)
                    return
                self._send_json(payload)
                return
            if parsed.path != "/analyze":
                self._send("Not Found".encode("utf-8"), status=404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            input_path = Path(form.get("file", [""])[0])
            if not _within(input_path, settings.data_roots) or not input_path.is_file() or input_path.suffix.lower() != ".pdf":
                self._send("文件不在允许的数据目录中".encode("utf-8"), status=403)
                return
            requested_ocr = form.get("ocr", ["auto"])[0]
            requested_llm = form.get("llm", ["auto"])[0]
            ocr_mode = "never" if offline else requested_ocr if requested_ocr in {"auto", "never", "force"} else "auto"
            llm_mode = "never" if offline else requested_llm if requested_llm in {"auto", "never", "always"} else "auto"
            job_id = analysis_jobs.create(input_path.name)

            def run_analysis() -> None:
                analysis_jobs.update(job_id, status="running", message="正在解析财报、计算指标并运行分析 Agent")
                try:
                    bundle = pipeline.analyze(
                        input_path,
                        ocr_mode=ocr_mode,
                        llm_mode=llm_mode,
                    )
                    report = Path(bundle.run_dir) / "report.html"
                    report_url = "/report?" + urllib.parse.urlencode({"path": str(report)})
                    analysis_jobs.update(
                        job_id,
                        status="done",
                        message="分析完成，正在打开研报",
                        report_url=report_url,
                    )
                except Exception as exc:
                    error = f"{type(exc).__name__}: {str(exc)}"
                    analysis_jobs.update(
                        job_id,
                        status="failed",
                        message=f"分析失败：{error[:500]}",
                    )
                    print(f"[web] analysis job {job_id} failed: {error}")

            analysis_executor.submit(run_analysis)
            location = "/job?" + urllib.parse.urlencode({"id": job_id})
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            print(f"[web] {self.address_string()} {format % args}")

    server = ExclusiveThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"财报分析智能体已启动：{url}")
    print(f"运行模式：{'离线兜底（OCR和DeepSeek强制关闭）' if offline else '联网能力可用'}")
    print(f"访问认证：{'已启用' if authentication_enabled else '未启用（仅建议本机使用）'}")
    print("按 Ctrl+C 停止。")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        analysis_executor.shutdown(wait=False, cancel_futures=True)
