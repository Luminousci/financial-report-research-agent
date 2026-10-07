from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import stable_hash, utc_now_iso, write_json
from .models import (
    BusinessMetric,
    CalculatedMetric,
    FinancialFact,
    Finding,
    HealthAssessment,
    NarrativeEvidence,
    NonRecurringItem,
    ParsedDocument,
    ValidationIssue,
)


PROMPT_VERSION = "financial-multi-agent-v7"
CHAT_PROMPT_VERSION = "financial-chat-v1"


class DeepSeekError(RuntimeError):
    pass


def _post_json(url: str, payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise DeepSeekError(f"DeepSeek HTTP {exc.code}: {error_body[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise DeepSeekError(f"DeepSeek network error: {exc.reason}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise DeepSeekError(f"DeepSeek returned non-JSON transport content: {body[:1000]}") from exc


class DeepSeekClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_call_metadata: dict[str, Any] = {}
        self.system_prompt = (
            settings.project_root / "agent_assets" / "prompts" / "analysis_system.txt"
        ).read_text(encoding="utf-8")

    @staticmethod
    def _parse_json_content(content: Any) -> dict[str, Any]:
        if isinstance(content, dict):
            return content
        text = str(content or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("agent response root must be a JSON object")
        return parsed

    def _call_agent(
        self,
        *,
        agent_id: str,
        prompt_file: str,
        input_payload: dict[str, Any],
        cache_dir: Path,
        max_tokens: int,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        specialist_prompt = (
            self.settings.project_root / "agent_assets" / "prompts" / prompt_file
        ).read_text(encoding="utf-8")
        request_payload = {
            "model": self.settings.deepseek_model,
            "messages": [
                {"role": "system", "content": f"{self.system_prompt}\n\n{specialist_prompt}"},
                {
                    "role": "user",
                    "content": "【受控输入】\n" + json.dumps(input_payload, ensure_ascii=False, indent=2),
                },
            ],
            "thinking": {"type": "disabled"},
            "temperature": self.settings.deepseek_temperature,
            "response_format": {"type": "json_object"},
            "max_tokens": min(self.settings.deepseek_max_tokens, max_tokens),
            "stream": False,
        }
        request_hash = stable_hash(request_payload)
        cache_path = cache_dir / f"agent_{agent_id}_{request_hash}.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            metadata = cached["metadata"] | {"cache_hit": True, "status": "passed"}
            return cached["analysis"], metadata

        started_at = utc_now_iso()
        started = time.perf_counter()
        base_metadata = {
            "agent_id": agent_id,
            "provider": "deepseek",
            "requested_model": self.settings.deepseek_model,
            "prompt_version": PROMPT_VERSION,
            "request_hash": request_hash,
            "started_at": started_at,
            "cache_hit": False,
        }
        try:
            response = _post_json(
                f"{self.settings.deepseek_base_url}/chat/completions",
                request_payload,
                self.settings.deepseek_api_key,
                self.settings.deepseek_timeout_seconds,
            )
            analysis = self._parse_json_content(response["choices"][0]["message"]["content"])
        except Exception as exc:
            metadata = base_metadata | {
                "status": "failed",
                "finished_at": utc_now_iso(),
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "error_type": type(exc).__name__,
                "error": str(exc)[:600],
            }
            return None, metadata

        metadata = base_metadata | {
            "status": "passed",
            "returned_model": response.get("model"),
            "response_hash": stable_hash(response),
            "response_id": response.get("id"),
            "usage": response.get("usage"),
            "system_fingerprint": response.get("system_fingerprint"),
            "finished_at": utc_now_iso(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        cache_dir.mkdir(parents=True, exist_ok=True)
        write_json(cache_path, {"analysis": analysis, "metadata": metadata})
        return analysis, metadata

    def answer_question(
        self,
        *,
        question: str,
        evidence: list[dict[str, Any]],
        history: list[dict[str, str]],
        cache_dir: Path,
    ) -> tuple[str, dict[str, Any]]:
        system_prompt = (
            self.settings.project_root / "agent_assets" / "prompts" / "chat_system.txt"
        ).read_text(encoding="utf-8")
        evidence_for_prompt = []
        for index, item in enumerate(evidence, start=1):
            evidence_for_prompt.append({
                "reference": f"证据{index}",
                "kind": item.get("kind"),
                "label": item.get("label"),
                "page": item.get("page"),
                "file_name": item.get("file_name"),
                "text": item.get("text"),
            })
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(
            {"role": item.get("role", "user"), "content": str(item.get("content") or "")[:6000]}
            for item in history[-12:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        )
        messages.append({
            "role": "user",
            "content": (
                "【受控证据】\n"
                + json.dumps(evidence_for_prompt, ensure_ascii=False, indent=2)
                + f"\n\n【当前问题】\n{question}"
            ),
        })
        request_payload = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "thinking": {"type": "disabled"},
            "temperature": 0.0,
            "max_tokens": min(self.settings.deepseek_max_tokens, 2200),
            "stream": False,
        }
        request_hash = stable_hash(request_payload)
        cache_path = cache_dir / f"chat_{request_hash}.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            metadata = cached["metadata"] | {"cache_hit": True, "status": "passed"}
            self.last_call_metadata = metadata
            return str(cached["answer"]), metadata

        started_at = utc_now_iso()
        started = time.perf_counter()
        self.last_call_metadata = {
            "provider": "deepseek", "status": "running",
            "requested_model": self.settings.deepseek_model,
            "prompt_version": CHAT_PROMPT_VERSION, "request_hash": request_hash,
            "started_at": started_at, "cache_hit": False,
        }
        try:
            response = _post_json(
                f"{self.settings.deepseek_base_url}/chat/completions",
                request_payload,
                self.settings.deepseek_api_key,
                self.settings.deepseek_timeout_seconds,
            )
            answer = str(response["choices"][0]["message"]["content"]).strip()
            if not answer:
                raise DeepSeekError("DeepSeek returned an empty chat answer")
        except (KeyError, IndexError, TypeError) as exc:
            self.last_call_metadata.update({
                "status": "failed", "finished_at": utc_now_iso(),
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "error_type": type(exc).__name__,
            })
            raise DeepSeekError("Invalid DeepSeek chat response") from exc
        except Exception as exc:
            self.last_call_metadata.update({
                "status": "failed", "finished_at": utc_now_iso(),
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "error_type": type(exc).__name__,
            })
            raise
        metadata = {
            "provider": "deepseek", "status": "passed",
            "requested_model": self.settings.deepseek_model,
            "returned_model": response.get("model"),
            "prompt_version": CHAT_PROMPT_VERSION, "request_hash": request_hash,
            "response_hash": stable_hash(response), "response_id": response.get("id"),
            "usage": response.get("usage"), "system_fingerprint": response.get("system_fingerprint"),
            "started_at": started_at, "finished_at": utc_now_iso(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "cache_hit": False,
        }
        self.last_call_metadata = metadata
        cache_dir.mkdir(parents=True, exist_ok=True)
        write_json(cache_path, {"answer": answer, "metadata": metadata})
        return answer, metadata

    def analyze(
        self,
        parsed: ParsedDocument,
        facts: list[FinancialFact],
        calculations: list[CalculatedMetric],
        validations: list[ValidationIssue],
        findings: list[Finding],
        narrative_evidence: list[NarrativeEvidence],
        nonrecurring_items: list[NonRecurringItem],
        health_assessment: HealthAssessment,
        business_metrics: list[BusinessMetric],
        cache_dir: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        cache_dir.mkdir(parents=True, exist_ok=True)
        document_payload = parsed.meta.to_dict()
        for internal_key in ("document_id", "path", "sha256"):
            document_payload.pop(internal_key, None)
        core_codes = {
            "revenue", "operating_revenue_total", "operating_cost", "net_profit_parent",
            "net_profit_excl_nonrecurring", "operating_cash_flow", "weighted_roe",
            "total_assets", "total_liabilities", "current_assets", "current_liabilities",
            "accounts_receivable", "accounts_payable", "prepayments", "contract_liabilities",
            "inventory", "cash_and_equivalents",
        }
        fact_payload = [
            {
                "metric_code": fact.metric_code,
                "metric_name": fact.metric_name,
                "value_normalized": str(fact.normalized_value),
                "unit": fact.unit,
                "currency": fact.currency,
                "period": fact.period_label,
                "comparison_kind": fact.comparison_kind,
                "page": fact.source.page if fact.source else None,
                "raw": fact.source.evidence_text if fact.source else None,
            }
            for fact in facts
            if fact.metric_code in core_codes
        ]
        calculation_payload = [item.to_dict() for item in calculations]
        narrative_by_category = {
            category: [item.to_dict() for item in narrative_evidence if item.category == category]
            for category in {item.category for item in narrative_evidence}
        }
        shared = {
            "prompt_version": PROMPT_VERSION,
            "document": document_payload,
            "facts": fact_payload,
            "calculations": calculation_payload,
            "business_metrics": [item.to_dict() for item in business_metrics],
            "validation_issues": [item.to_dict() for item in validations],
            "rule_findings": [item.to_dict() for item in findings],
        }
        specialist_specs: dict[str, tuple[str, dict[str, Any], int]] = {
            "performance": (
                "agent_performance.txt",
                shared | {"narrative_evidence": narrative_by_category.get("performance_drivers", [])},
                1800,
            ),
            "nonrecurring": (
                "agent_nonrecurring.txt",
                {
                    "document": document_payload,
                    "facts": [item for item in fact_payload if item["metric_code"] in {"net_profit_parent", "net_profit_excl_nonrecurring"}],
                    "calculations": [item for item in calculation_payload if "nonrecurring" in item["code"]],
                    "nonrecurring_items": [item.to_dict() for item in nonrecurring_items],
                    "narrative_evidence": narrative_by_category.get("nonrecurring", []),
                },
                1000,
            ),
            "accounting": (
                "agent_accounting.txt",
                {
                    "document": document_payload,
                    "narrative_evidence": narrative_by_category.get("accounting_policy", []),
                    "validation_issues": [item.to_dict() for item in validations],
                },
                900,
            ),
            "cashflow": (
                "agent_cashflow.txt",
                {
                    "document": document_payload,
                    "facts": [item for item in fact_payload if item["metric_code"] in {
                        "net_profit_parent", "operating_cash_flow", "accounts_receivable", "inventory",
                        "prepayments", "accounts_payable", "contract_liabilities",
                    }],
                    "calculations": [item for item in calculation_payload if any(token in item["code"] for token in ("cash", "working_capital", "profit_cash"))],
                    "rule_findings": [item.to_dict() for item in findings if "cash" in item.rule_id],
                    "narrative_evidence": narrative_by_category.get("cash_flow", []),
                },
                1300,
            ),
            "other_risks": (
                "agent_other_risks.txt",
                shared | {
                    "narrative_evidence": narrative_by_category.get("risk", []),
                },
                1300,
            ),
        }
        fallbacks: dict[str, dict[str, Any]] = {
            "performance": {"summary_text": "DeepSeek业绩专项分析未返回，已保留程序化指标。", "performance_analysis": "未获取", "key_points": [], "evidence_pages": []},
            "nonrecurring": {"analysis": "未获取相关数据", "signal": "unavailable", "evidence_pages": []},
            "accounting": {"analysis": "本期未识别会计政策、会计估计及追溯调整事项", "changed": False, "evidence_pages": []},
            "cashflow": {"analysis": "部分现金流背离指标未获取，暂无法完整判断。", "divergence": "partial", "evidence_pages": []},
            "other_risks": {"analysis": "本期未识别该类风险信号", "risk_items": [], "evidence_pages": []},
        }
        specialist_outputs: dict[str, dict[str, Any]] = {}
        call_metadata: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="deepseek-agent") as executor:
            futures = {
                executor.submit(
                    self._call_agent,
                    agent_id=agent_id,
                    prompt_file=prompt_file,
                    input_payload=payload,
                    cache_dir=cache_dir,
                    max_tokens=max_tokens,
                ): agent_id
                for agent_id, (prompt_file, payload, max_tokens) in specialist_specs.items()
            }
            for future in as_completed(futures):
                agent_id = futures[future]
                output, metadata = future.result()
                specialist_outputs[agent_id] = output or fallbacks[agent_id]
                call_metadata.append(metadata)

        rating_output, rating_metadata = self._call_agent(
            agent_id="rating",
            prompt_file="agent_rating.txt",
            input_payload={
                "document": document_payload,
                "specialist_outputs": specialist_outputs,
                "health_assessment": health_assessment.to_dict(),
                "rule_findings": [item.to_dict() for item in findings],
            },
            cache_dir=cache_dir,
            max_tokens=1200,
        )
        call_metadata.append(rating_metadata)
        rating_output = rating_output or {
            "risk_hint": "DeepSeek风险归纳未返回，请结合规则异常复核。",
            "rating_note": f"系统参考等级{health_assessment.grade}；覆盖率{health_assessment.coverage_ratio}。",
            "tracking_metrics": [],
        }

        editor_input = {
            "document": document_payload,
            "specialist_outputs": specialist_outputs,
            "rating_output": rating_output,
            "core_metrics": fact_payload,
            "health_assessment": health_assessment.to_dict(),
        }
        editor_output, editor_metadata = self._call_agent(
            agent_id="report_editor",
            prompt_file="agent_report_editor.txt",
            input_payload=editor_input,
            cache_dir=cache_dir,
            max_tokens=1800,
        )
        call_metadata.append(editor_metadata)
        editor_output = editor_output or {
            "summary_text": specialist_outputs["performance"].get("summary_text", "未获取"),
            "performance_analysis": specialist_outputs["performance"].get("performance_analysis", "未获取"),
            "nonrecurring_analysis": specialist_outputs["nonrecurring"].get("analysis", "未获取"),
            "accounting_change_analysis": specialist_outputs["accounting"].get("analysis", "未获取"),
            "cash_flow_divergence_analysis": specialist_outputs["cashflow"].get("analysis", "未获取"),
            "other_risk_analysis": specialist_outputs["other_risks"].get("analysis", "未获取"),
            "risk_hint": rating_output.get("risk_hint", "未获取"),
            "rating_note": rating_output.get("rating_note", "未获取"),
            "tracking_metrics": rating_output.get("tracking_metrics", []),
        }
        analysis = {
            "schema_version": "financial-report-v1",
            **editor_output,
            "agent_results": specialist_outputs | {"rating": rating_output},
            "agent_trace": [
                {
                    "agent_id": item.get("agent_id"),
                    "status": item.get("status"),
                    "duration_ms": item.get("duration_ms"),
                    "cache_hit": item.get("cache_hit", False),
                }
                for item in sorted(call_metadata, key=lambda value: str(value.get("agent_id")))
            ],
        }
        passed = sum(item.get("status") == "passed" for item in call_metadata)
        failed = len(call_metadata) - passed
        metadata = {
            "provider": "deepseek",
            "status": "passed" if failed == 0 else ("partial" if passed else "failed"),
            "requested_model": self.settings.deepseek_model,
            "prompt_version": PROMPT_VERSION,
            "orchestration": "5-specialists-parallel -> rating -> report-editor",
            "call_count": len(call_metadata),
            "passed_count": passed,
            "failed_count": failed,
            "calls": sorted(call_metadata, key=lambda value: str(value.get("agent_id"))),
        }
        self.last_call_metadata = metadata
        return analysis, metadata
