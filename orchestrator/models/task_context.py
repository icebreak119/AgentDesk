"""TaskContext — 跨 Agent 共享上下文。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from orchestrator.models.privacy import redact_for_transport


@dataclass
class TaskContext:
    task_id: str
    profile_id: str
    session_id: str
    channel: str
    session_event: dict[str, Any] | None = None
    triage_result: dict[str, Any] | None = None
    reply_draft: dict[str, Any] | None = None
    business_action: dict[str, Any] | None = None
    send_receipt: dict[str, Any] | None = None
    verify_result: dict[str, Any] | None = None
    dedupe_result: dict[str, Any] | None = None
    knowledge_hits: list[dict[str, Any]] = field(default_factory=list)
    customer_confirm_result: dict[str, Any] | None = None
    case_digest: dict[str, Any] | None = None
    state: str = "pending"
    need_approval: bool = False
    approval_token: str | None = None
    approval_scope: str | None = None
    mode: str = "mock"
    raw_event: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize the context for transport; sensitive fields are redacted."""
        return self.to_wire_json(indent=indent)

    def to_internal_dict(self) -> dict[str, Any]:
        """Return the in-memory representation for trusted local code only."""
        return asdict(self)

    def to_internal_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_internal_dict(), ensure_ascii=False, indent=indent)

    def to_wire_dict(self) -> dict[str, Any]:
        """Serialize context for Agent handoff without secrets or customer text."""
        return redact_for_transport(asdict(self))

    def to_wire_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_wire_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskContext:
        raw_event = data.get("raw_event")
        approval_token = data.get("approval_token")
        return cls(
            task_id=str(data["task_id"]),
            profile_id=str(data["profile_id"]),
            session_id=str(data.get("session_id") or ""),
            channel=str(data.get("channel") or "douyin"),
            session_event=data.get("session_event"),
            triage_result=data.get("triage_result"),
            reply_draft=data.get("reply_draft"),
            business_action=data.get("business_action"),
            send_receipt=data.get("send_receipt"),
            verify_result=data.get("verify_result"),
            dedupe_result=data.get("dedupe_result"),
            knowledge_hits=list(data.get("knowledge_hits") or []),
            customer_confirm_result=data.get("customer_confirm_result"),
            case_digest=data.get("case_digest"),
            state=str(data.get("state") or "pending"),
            need_approval=bool(data.get("need_approval")),
            approval_token=None if approval_token == "[REDACTED]" else approval_token,
            approval_scope=data.get("approval_scope"),
            mode=str(data.get("mode") or "mock"),
            raw_event=dict(raw_event) if isinstance(raw_event, dict) else {},
        )
