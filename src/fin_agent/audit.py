from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .io_utils import stable_hash, utc_now_iso


class AuditLogger:
    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(
        self,
        step_id: str,
        status: str,
        *,
        tool: str,
        input_data: Any | None = None,
        output_data: Any | None = None,
        elapsed_ms: int | None = None,
        message: str | None = None,
    ) -> None:
        record = {
            "timestamp": utc_now_iso(),
            "run_id": self.run_id,
            "step_id": step_id,
            "status": status,
            "tool": tool,
            "input_hash": stable_hash(input_data) if input_data is not None else None,
            "output_hash": stable_hash(output_data) if output_data is not None else None,
            "elapsed_ms": elapsed_ms,
            "message": message,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    @contextmanager
    def step(self, step_id: str, tool: str, input_data: Any | None = None) -> Iterator[dict]:
        started = time.perf_counter()
        state: dict[str, Any] = {}
        self.event(step_id, "started", tool=tool, input_data=input_data)
        try:
            yield state
        except Exception as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.event(
                step_id,
                "failed",
                tool=tool,
                input_data=input_data,
                elapsed_ms=elapsed,
                message=f"{type(exc).__name__}: {exc}",
            )
            raise
        else:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.event(
                step_id,
                "completed",
                tool=tool,
                input_data=input_data,
                output_data=state.get("output"),
                elapsed_ms=elapsed,
                message=state.get("message"),
            )

