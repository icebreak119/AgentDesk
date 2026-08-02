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
        """Mark the approval requirement; Team Leader owns state transitions."""
        ctx.need_approval = True

    def grant_approval(self, ctx: TaskContext, token: str) -> None:
        if not str(token or "").strip():
            raise ValueError("approval_token 不能为空")
        if ctx.state != "suspended" or not ctx.need_approval:
            raise RuntimeError("只有 suspended 且需要审批的任务才能放行")
        token = str(token).strip()
        if (ctx.triage_result or {}).get("requested_action") == "refund":
            from orchestrator.agents.workers.act_verify import business_action_request
            from orchestrator.models.approval import approval_scope, validate_approval_token

            request = business_action_request(ctx)
            if not validate_approval_token(request, token):
                raise ValueError("approval_token_scope_invalid")
            ctx.approval_scope = approval_scope(request)
        ctx.approval_token = token
        ctx.state = "approved"

    def reject(self, ctx: TaskContext) -> None:
        ctx.state = "failed"
        ctx.approval_token = None
        ctx.approval_scope = None
