"""TraceEvent — 编排链路可观测写入。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, TextIO


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat(timespec="seconds")


def input_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class TraceWriter:
    def __init__(
        self,
        path: Path,
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO | None = None
        self.on_event = on_event

    def __enter__(self) -> TraceWriter:
        self._fh = self.path.open("w", encoding="utf-8")
        return self

    def __exit__(self, *args: object) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def emit(self, task_id: str, agent: str, **fields: Any) -> dict[str, Any]:
        event: dict[str, Any] = {
            "task_id": task_id,
            "agent": agent,
            "ts": _now_iso(),
        }
        event.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(event, ensure_ascii=False)
        if self._fh is None:
            raise RuntimeError("TraceWriter 未打开，请使用 with 语句")
        self._fh.write(line + "\n")
        self._fh.flush()
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                # Observability subscribers must not alter task execution.
                pass
        return event

    def read_lines(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        lines: list[dict[str, Any]] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                lines.append(json.loads(raw))
        return lines
