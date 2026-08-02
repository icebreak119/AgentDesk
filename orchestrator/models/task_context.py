"""TaskContext — 跨 Agent 共享上下文。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TaskContext:
    task_id: str
    profile_id: str
    session_id: str
    channel: str
    session_event: dict[str, Any] | None = None
    triage_result: dict[str, Any] | None = None
    reply_draft: dict[str, Any] | None = None
    send_receipt: dict[str, Any] | None = None
    verify_result: dict[str, Any] | None = None
    dedupe_result: dict[str, Any] | None = None
    knowledge_hits: list[dict[str, Any]] = field(default_factory=list)
    customer_confirm_result: dict[str, Any] | None = None
    case_digest: dict[str, Any] | None = None
    state: str = "pending"
    need_approval: bool = False
    approval_token: str | None = None
    mode: str = "mock"
    raw_event: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskContext:
        return cls(
            task_id=str(data["task_id"]),
            profile_id=str(data["profile_id"]),
            session_id=str(data.get("session_id") or ""),
            channel=str(data.get("channel") or "douyin"),
            session_event=data.get("session_event"),
            triage_result=data.get("triage_result"),
            reply_draft=data.get("reply_draft"),
            send_receipt=data.get("send_receipt"),
            verify_result=data.get("verify_result"),
            dedupe_result=data.get("dedupe_result"),
            knowledge_hits=list(data.get("knowledge_hits") or []),
            customer_confirm_result=data.get("customer_confirm_result"),
            case_digest=data.get("case_digest"),
            state=str(data.get("state") or "pending"),
            need_approval=bool(data.get("need_approval")),
            approval_token=data.get("approval_token"),
            mode=str(data.get("mode") or "mock"),
            raw_event=dict(data.get("raw_event") or {}),
        )
