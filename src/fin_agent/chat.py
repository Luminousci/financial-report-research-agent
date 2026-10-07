from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .config import Settings
from .deepseek import DeepSeekClient, DeepSeekError
from .io_utils import read_json, stable_hash, utc_now_iso, write_json


SESSION_RE = re.compile(r"^[a-f0-9]{32}$")
SPACE_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff%]+")
STOP_GRAMS = {
    "公司", "财报", "报告", "情况", "分析", "什么", "为什么", "如何", "是否", "这个", "这些",
    "本期", "当前", "主要", "可以", "进行", "以及", "相关", "其中", "年度", "季度",
}
DOMAIN_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "营收": ("营业收入", "主营业务收入", "收入增长"),
    "收入": ("营业收入", "主营业务收入", "收入增长"),
    "净利": ("净利润", "归属于母公司股东的净利润", "扣除非经常性损益"),
    "利润": ("净利润", "归母净利润", "利润总额", "扣非净利润"),
    "毛利": ("毛利率", "营业成本", "收入和成本"),
    "现金流": ("经营活动产生的现金流量净额", "经营现金流", "现金转换"),
    "应收": ("应收账款", "应收款项", "信用减值"),
    "存货": ("存货", "存货跌价", "库存"),
    "负债": ("负债合计", "资产负债率", "有息负债"),
    "非经常": ("非经常性损益", "政府补助", "资产处置", "公允价值"),
    "风险": ("风险", "不确定性", "诉讼", "担保", "减值", "持续经营"),
    "异常": ("异常信号", "质量校验", "风险", "背离"),
    "分红": ("现金分红", "利润分配", "股利"),
    "研发": ("研发投入", "研发费用", "研发人员"),
    "原因": ("主要原因", "变动原因", "原因说明", "增长原因", "下降原因"),
}


class ChatError(ValueError):
    pass


@dataclass(slots=True)
class ChatEvidence:
    evidence_id: str
    kind: str
    label: str
    text: str
    page: int | None
    file_name: str
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize(value: str) -> str:
    return NON_WORD_RE.sub("", value).lower()


def _chunks(text: str, size: int = 720, overlap: int = 100) -> list[str]:
    cleaned = SPACE_RE.sub(" ", text).strip()
    if not cleaned:
        return []
    sentences = [item.strip() for item in re.split(r"(?<=[。！？；])|\n+", text) if item.strip()]
    result: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > size:
            head, sentence = sentence[:size], sentence[size - overlap:]
            if current:
                result.append(current)
                current = ""
            result.append(head)
        if len(current) + len(sentence) + 1 <= size:
            current = f"{current}\n{sentence}".strip()
        else:
            if current:
                result.append(current)
            current = f"{current[-overlap:]}\n{sentence}".strip() if current else sentence
    if current:
        result.append(current)
    return result


def _query_features(question: str) -> tuple[set[str], set[str]]:
    normalized = _normalize(question)
    terms: set[str] = set()
    for key, expansions in DOMAIN_EXPANSIONS.items():
        if key in normalized:
            terms.add(key)
            terms.update(_normalize(item) for item in expansions)
    raw_parts = [part for part in NON_WORD_RE.split(question) if len(part) >= 2]
    terms.update(_normalize(part) for part in raw_parts if _normalize(part) not in STOP_GRAMS)
    grams: set[str] = set()
    for width in (2, 3, 4):
        for index in range(max(0, len(normalized) - width + 1)):
            gram = normalized[index:index + width]
            if gram not in STOP_GRAMS and not gram.isdigit():
                grams.add(gram)
    return terms, grams


def _score_candidate(candidate: ChatEvidence, terms: set[str], grams: set[str]) -> float:
    haystack = _normalize(f"{candidate.label}{candidate.text}")
    label = _normalize(candidate.label)
    score = 0.0
    for term in terms:
        if not term:
            continue
        if term in label:
            score += 7.0
        occurrences = haystack.count(term)
        if occurrences:
            score += 4.0 + min(occurrences, 3) * 0.8 + min(len(term), 10) * 0.15
    matched_grams = sum(1 for gram in grams if gram in haystack)
    if grams:
        score += 8.0 * matched_grams / len(grams)
    if candidate.kind != "page" and score:
        score += 1.5
    return round(score, 6)


def _source_values(source: dict[str, Any] | None, fallback_name: str) -> tuple[int | None, str, str]:
    source = source or {}
    page = source.get("page")
    try:
        page = int(page) if page is not None else None
    except (TypeError, ValueError):
        page = None
    return page, str(source.get("file_name") or fallback_name), str(source.get("evidence_text") or "")


def build_candidates(document: dict[str, Any], bundle: dict[str, Any]) -> list[ChatEvidence]:
    meta = document.get("meta") or bundle.get("document") or {}
    file_name = str(meta.get("file_name") or "受控财报")
    candidates: list[ChatEvidence] = []
    for page_record in document.get("pages", []):
        try:
            page = int(page_record.get("page"))
        except (TypeError, ValueError):
            continue
        for chunk_index, chunk in enumerate(_chunks(str(page_record.get("text") or ""))):
            if len(_normalize(chunk)) < 30:
                continue
            candidates.append(ChatEvidence(
                evidence_id=f"page-{page}-{chunk_index}", kind="page", label="财报原文",
                text=chunk, page=page, file_name=file_name,
            ))
    for index, fact in enumerate(bundle.get("facts", [])):
        source = fact.get("source")
        page, source_file, raw = _source_values(source, file_name)
        text = (
            f"{fact.get('metric_name', fact.get('metric_code', '财务指标'))}；期间 {fact.get('period_label', '')}；"
            f"数值 {fact.get('value', '')} {fact.get('unit', '')}；口径 {fact.get('comparison_kind', '')}。"
        )
        if raw:
            text += f" 原始披露：{raw}"
        candidates.append(ChatEvidence(
            evidence_id=f"fact-{index}", kind="fact",
            label=str(fact.get("metric_name") or fact.get("metric_code") or "结构化事实"),
            text=text, page=page, file_name=source_file,
        ))
    for index, item in enumerate(bundle.get("business_metrics", [])):
        page, source_file, raw = _source_values(item.get("source"), file_name)
        text = (
            f"{item.get('metric_name', '经营指标')}；期间 {item.get('period_label', '')}；"
            f"区间 {item.get('lower_value', '')}–{item.get('upper_value', '')}{item.get('unit', '')}。 {raw}"
        )
        candidates.append(ChatEvidence(
            evidence_id=f"business-{index}", kind="business_metric",
            label=str(item.get("metric_name") or "经营指标"), text=text,
            page=page, file_name=source_file,
        ))
    for index, item in enumerate(bundle.get("calculations", [])):
        candidates.append(ChatEvidence(
            evidence_id=f"calculation-{index}", kind="calculation",
            label=str(item.get("name") or item.get("code") or "程序计算"),
            text=(
                f"{item.get('name', '')}；期间 {item.get('period_label', '')}；结果 {item.get('value')} {item.get('unit', '')}；"
                f"公式 {item.get('formula', '')}；输入 {json.dumps(item.get('inputs', {}), ensure_ascii=False)}；状态 {item.get('status', '')}。"
            ), page=None, file_name=file_name,
        ))
    for index, item in enumerate(bundle.get("narrative_evidence", [])):
        page, source_file, _ = _source_values(item.get("source"), file_name)
        candidates.append(ChatEvidence(
            evidence_id=f"narrative-{index}", kind="narrative",
            label=str(item.get("category") or "叙事证据"), text=str(item.get("text") or ""),
            page=page, file_name=source_file,
        ))
    for index, item in enumerate(bundle.get("nonrecurring_items", [])):
        page, source_file, raw = _source_values(item.get("source"), file_name)
        candidates.append(ChatEvidence(
            evidence_id=f"nonrecurring-{index}", kind="nonrecurring",
            label=str(item.get("item_name") or "非经常性损益"),
            text=(
                f"{item.get('item_name', '')}；期间 {item.get('period_label', '')}；"
                f"金额 {item.get('amount', '')} {item.get('unit', '')}。 {raw}"
            ), page=page, file_name=source_file,
        ))
    for index, item in enumerate(bundle.get("findings", [])):
        candidates.append(ChatEvidence(
            evidence_id=f"finding-{index}", kind="finding",
            label=str(item.get("title") or "异常信号"),
            text=(
                f"分类 {item.get('classification', '')}；等级 {item.get('severity', '')}；"
                f"{item.get('description', '')} 反向核验：{item.get('counter_evidence') or '无'}"
            ), page=None, file_name=file_name,
        ))
    return candidates


def retrieve(candidates: list[ChatEvidence], question: str, limit: int = 7) -> list[ChatEvidence]:
    terms, grams = _query_features(question)
    scored: list[ChatEvidence] = []
    for candidate in candidates:
        score = _score_candidate(candidate, terms, grams)
        if score <= 0:
            continue
        scored.append(ChatEvidence(**(candidate.to_dict() | {"score": score})))
    scored.sort(key=lambda item: (-item.score, item.page is None, item.page or 0, item.evidence_id))
    selected: list[ChatEvidence] = []
    per_page: dict[int, int] = {}
    seen_text: set[str] = set()
    for item in scored:
        fingerprint = _normalize(item.text)[:180]
        if fingerprint in seen_text:
            continue
        if item.page is not None and per_page.get(item.page, 0) >= 2:
            continue
        seen_text.add(fingerprint)
        if item.page is not None:
            per_page[item.page] = per_page.get(item.page, 0) + 1
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


class ChatService:
    def __init__(self, settings: Settings, *, offline: bool = False):
        self.settings = settings
        self.offline = offline
        self._lock = threading.RLock()
        self._candidate_cache: dict[tuple[str, int, int], list[ChatEvidence]] = {}

    def _run_dir(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ChatError("无效的运行 ID")
        run_dir = (self.settings.output_dir / run_id).resolve()
        if not run_dir.is_relative_to(self.settings.output_dir.resolve()):
            raise ChatError("运行目录越界")
        if not (run_dir / "analysis_bundle.json").is_file() or not (run_dir / "document.json").is_file():
            raise ChatError("该报告缺少问答所需的分析产物")
        return run_dir

    @staticmethod
    def _session_id(value: str | None) -> str:
        if not value:
            return secrets.token_hex(16)
        value = value.strip().lower()
        if not SESSION_RE.fullmatch(value):
            raise ChatError("无效的会话 ID")
        return value

    def _session_path(self, run_dir: Path, session_id: str) -> Path:
        return run_dir / "chat" / f"{session_id}.jsonl"

    def _load_records(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records[-40:]

    def _candidates(self, run_dir: Path) -> list[ChatEvidence]:
        document_path = run_dir / "document.json"
        bundle_path = run_dir / "analysis_bundle.json"
        key = (str(run_dir), document_path.stat().st_mtime_ns, bundle_path.stat().st_mtime_ns)
        with self._lock:
            if key not in self._candidate_cache:
                self._candidate_cache = {
                    cached_key: value for cached_key, value in self._candidate_cache.items()
                    if cached_key[0] != str(run_dir)
                }
                self._candidate_cache[key] = build_candidates(read_json(document_path), read_json(bundle_path))
            return self._candidate_cache[key]

    def history(self, run_id: str, session_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        session_id = self._session_id(session_id)
        with self._lock:
            records = self._load_records(self._session_path(run_dir, session_id))
        messages: list[dict[str, Any]] = []
        for record in records:
            messages.append({
                "role": "user", "content": record.get("question", ""),
                "timestamp": record.get("timestamp"),
            })
            messages.append({
                "role": "assistant", "content": record.get("answer", ""),
                "citations": record.get("citations", []), "mode": record.get("mode"),
                "timestamp": record.get("timestamp"),
            })
        return {
            "run_id": run_id,
            "session_id": session_id,
            "messages": messages,
        }

    def ask(self, run_id: str, question: str, session_id: str | None = None) -> dict[str, Any]:
        question = SPACE_RE.sub(" ", str(question or "")).strip()
        if not question:
            raise ChatError("问题不能为空")
        if len(question) > 2000:
            raise ChatError("问题不能超过 2000 个字符")
        run_dir = self._run_dir(run_id)
        session_id = self._session_id(session_id)
        session_path = self._session_path(run_dir, session_id)
        with self._lock:
            records = self._load_records(session_path)
        search_question = question
        if records and (len(_normalize(question)) < 12 or any(token in question for token in ("这", "该", "它", "上述"))):
            search_question = f"{records[-1].get('question', '')} {question}".strip()
        started = time.perf_counter()
        trace_id = secrets.token_hex(8)
        hits = retrieve(self._candidates(run_dir), search_question)
        citations = [item.to_dict() for item in hits]
        history_messages: list[dict[str, str]] = []
        for record in records[-6:]:
            history_messages.extend((
                {"role": "user", "content": str(record.get("question") or "")},
                {"role": "assistant", "content": str(record.get("answer") or "")},
            ))
        mode = "retrieval_fallback"
        model_metadata: dict[str, Any] = {"status": "skipped"}
        if not hits:
            answer = "现有受控证据不足以回答该问题。请尝试加入更具体的指标、期间或业务名称。"
        elif not self.offline and self.settings.llm_configured():
            try:
                client = DeepSeekClient(self.settings)
                answer, model_metadata = client.answer_question(
                    question=question,
                    evidence=citations,
                    history=history_messages,
                    cache_dir=run_dir / "cache",
                )
                mode = "deepseek"
            except DeepSeekError as exc:
                model_metadata = client.last_call_metadata | {"status": "failed", "error": str(exc)}
                answer = self._fallback_answer(hits, "DeepSeek 当前不可用")
        else:
            reason = "离线模式" if self.offline else "未配置 DeepSeek"
            answer = self._fallback_answer(hits, reason)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        record = {
            "timestamp": utc_now_iso(), "run_id": run_id, "session_id": session_id,
            "trace_id": trace_id, "question": question, "search_question": search_question,
            "answer": answer, "mode": mode, "citations": citations,
            "retrieval_hash": stable_hash(citations), "model": model_metadata,
            "elapsed_ms": elapsed_ms,
        }
        with self._lock:
            session_path.parent.mkdir(parents=True, exist_ok=True)
            with session_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            AuditLogger(run_dir / "events.jsonl", run_id).event(
                f"chat_{trace_id}", "completed", tool="controlled_evidence_chat",
                input_data={"session_id": session_id, "question": question},
                output_data={
                    "mode": mode, "citation_ids": [item["evidence_id"] for item in citations],
                    "answer_hash": stable_hash(answer), "model": model_metadata,
                }, elapsed_ms=int(elapsed_ms),
            )
            manifest_path = run_dir / "manifest.json"
            if manifest_path.is_file():
                manifest = read_json(manifest_path)
                outputs = list(manifest.get("outputs", []))
                if "chat/" not in outputs:
                    outputs.append("chat/")
                manifest["outputs"] = outputs
                manifest["chat"] = {
                    "session_count": len(list(session_path.parent.glob("*.jsonl"))),
                    "last_trace_id": trace_id,
                    "last_updated_at": record["timestamp"],
                    "prompt_version": model_metadata.get("prompt_version", "financial-chat-v1"),
                }
                write_json(manifest_path, manifest)
        return {
            "run_id": run_id, "session_id": session_id, "trace_id": trace_id,
            "answer": answer, "mode": mode, "citations": citations,
            "elapsed_ms": elapsed_ms,
        }

    @staticmethod
    def _fallback_answer(hits: list[ChatEvidence], reason: str) -> str:
        lines = [f"{reason}，以下仅返回与问题最相关的受控证据："]
        for index, hit in enumerate(hits, start=1):
            anchor = f"第{hit.page}页" if hit.page is not None else hit.label
            excerpt = SPACE_RE.sub(" ", hit.text).strip()
            if len(excerpt) > 360:
                excerpt = excerpt[:360].rstrip() + "…"
            lines.append(f"\n{index}. [证据{index}·{anchor}] {excerpt}")
        lines.append("\n以上为检索结果，不构成投资建议。")
        return "\n".join(lines)
