"""DutyManager — 任务创建与审批闸门。"""

from __future__ import annotations

from orchestrator.models.task_context import TaskContext


class DutyManager:
    def create_task(
        self,
        *,
        task_id: str,
        profile_id: str,
        channel: str,
        raw_event: dict,
        mode: str = "mock",
    ) -> TaskContext:
        session_id = str(
            raw_event.get("conversation_id")
            or raw_event.get("session_id")
            or ""
        )
        return TaskContext(
            task_id=task_id,
            profile_id=profile_id,
            session_id=session_id,
            channel=channel,
            raw_event=dict(raw_event),
            mode=mode,
            state="pending",
        )

    def needs_approval(self, ctx: TaskContext) -> bool:
        triage = ctx.triage_result or {}
        return bool(triage.get("need_approval"))

    def suspend_for_approval(self, ctx: TaskContext) -> None:
        ctx.need_approval = True
        ctx.state = "suspended"

    def grant_approval(self, ctx: TaskContext, token: str) -> None:
        if not str(token or "").strip():
            raise ValueError("approval_token 不能为空")
        ctx.approval_token = str(token).strip()
        ctx.state = "approved"

    def reject(self, ctx: TaskContext) -> None:
        ctx.state = "failed"
        ctx.approval_token = None
