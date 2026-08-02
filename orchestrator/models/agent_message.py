"""Structured handoff envelope for the AgentTeams mapping layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orchestrator.models.privacy import redact_for_transport


@dataclass(frozen=True)
class AgentMessage:
    message_id: str
    task_id: str
    from_agent: str
    to_agent: str
    intent: str
    context_ref: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    expected_state: str = ""
    risk_tag: str = "medium"

    def __post_init__(self) -> None:
        for name in ("message_id", "task_id", "from_agent", "to_agent", "intent", "context_ref"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} 不能为空")
        if not isinstance(self.payload, dict):
            raise TypeError("payload 必须是对象")
        if any(not str(ref).strip() for ref in self.evidence_refs):
            raise ValueError("evidence_refs 不能包含空值")

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "task_id": self.task_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "intent": self.intent,
            "context_ref": self.context_ref,
            "payload": dict(self.payload),
            "evidence_refs": list(self.evidence_refs),
            "expected_state": self.expected_state,
            "risk_tag": self.risk_tag,
        }

    def to_wire_dict(self) -> dict[str, Any]:
        """Serialize the handoff envelope without credentials or customer content."""
        return redact_for_transport(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentMessage:
        return cls(
            message_id=str(data.get("message_id") or ""),
            task_id=str(data.get("task_id") or ""),
            from_agent=str(data.get("from_agent") or ""),
            to_agent=str(data.get("to_agent") or ""),
            intent=str(data.get("intent") or ""),
            context_ref=str(data.get("context_ref") or ""),
            payload=dict(data.get("payload") or {}),
            evidence_refs=tuple(str(ref) for ref in data.get("evidence_refs") or []),
            expected_state=str(data.get("expected_state") or ""),
            risk_tag=str(data.get("risk_tag") or "medium"),
        )
