"""SessionTL — 会话编排与状态机。"""

from __future__ import annotations

from orchestrator.agents.duty_manager import DutyManager
from orchestrator.agents.workers import act_verify, channel_ingress, triage_guard
from orchestrator.models.task_context import TaskContext
from orchestrator.models.trace import TraceWriter, input_hash


class SessionTL:
    def _transition(
        self,
        ctx: TaskContext,
        trace: TraceWriter,
        from_state: str,
        to_state: str,
    ) -> None:
        ctx.state = to_state
        trace.emit(
            ctx.task_id,
            "SessionTL",
            event="state_transition",
            **{"from": from_state, "to": to_state},
        )

    def run_until_gate(
        self,
        ctx: TaskContext,
        trace: TraceWriter,
        duty_manager: DutyManager,
    ) -> TaskContext:
        self._transition(ctx, trace, "pending", "triaging")

        raw = dict(ctx.raw_event)
        session_event = channel_ingress.normalize(ctx.channel, ctx.profile_id, raw)
        ctx.session_event = session_event
        trace.emit(
            ctx.task_id,
            "ChannelIngress",
            skill="SessionNormalize",
            status="ok",
            input_hash=input_hash(raw),
            output={"session_id": session_event.get("session_id")},
        )

        triage_result = triage_guard.triage(session_event)
        ctx.triage_result = triage_result
        trace.emit(
            ctx.task_id,
            "TriageGuard",
            skill="IntentTriage",
            status="ok",
            need_approval=triage_result.get("need_approval"),
            input_hash=input_hash(session_event),
            output={
                "intent": triage_result.get("intent"),
                "priority": triage_result.get("priority"),
            },
        )

        self._transition(ctx, trace, "triaging", "planning")

        reply_draft = triage_guard.plan(session_event, triage_result)
        ctx.reply_draft = reply_draft
        trace.emit(
            ctx.task_id,
            "TriageGuard",
            skill="ReplyPlan",
            status="ok",
            input_hash=input_hash({"triage": triage_result}),
            output={"action_type": reply_draft.get("action_type")},
        )

        if duty_manager.needs_approval(ctx):
            duty_manager.suspend_for_approval(ctx)
            self._transition(ctx, trace, "planning", "suspended")
            trace.emit(
                ctx.task_id,
                "DutyManager",
                event="approval_required",
                status="suspended",
                output={"risk_tag": triage_result.get("risk_tag")},
            )
            return ctx

        return self.execute_send_verify(ctx, trace, from_state="planning")

    def execute_send_verify(
        self,
        ctx: TaskContext,
        trace: TraceWriter,
        *,
        from_state: str,
        live: bool = False,
        base_url: str = "http://127.0.0.1:8765",
    ) -> TaskContext:
        if ctx.state == "approved":
            start_state = "suspended"
        else:
            start_state = from_state

        self._transition(ctx, trace, start_state, "acting")

        send_receipt = act_verify.send(ctx, live=live or ctx.mode == "live", base_url=base_url)
        ctx.send_receipt = send_receipt
        trace.emit(
            ctx.task_id,
            "ActVerify",
            skill="ChannelSend",
            status=send_receipt.get("status"),
            mode=send_receipt.get("mode"),
            input_hash=input_hash({"draft": (ctx.reply_draft or {}).get("draft_text")}),
            output={"send_id": send_receipt.get("send_id")},
        )

        if send_receipt.get("status") != "ok":
            ctx.state = "failed"
            trace.emit(ctx.task_id, "SessionTL", event="pipeline_failed", status="failed")
            return ctx

        self._transition(ctx, trace, "acting", "verifying")

        verify_result = act_verify.verify(ctx)
        ctx.verify_result = verify_result
        trace.emit(
            ctx.task_id,
            "ActVerify",
            skill="OutcomeVerify",
            status="ok" if verify_result.get("pass") else "failed",
            output={"pass": verify_result.get("pass")},
        )

        final_state = "done" if verify_result.get("pass") else "failed"
        self._transition(ctx, trace, "verifying", final_state)
        return ctx

    def resume_after_approval(
        self,
        ctx: TaskContext,
        trace: TraceWriter,
        duty_manager: DutyManager,
        *,
        live: bool = False,
        base_url: str = "http://127.0.0.1:8765",
    ) -> TaskContext:
        if ctx.state not in {"suspended", "approved"}:
            raise RuntimeError(f"任务状态不可恢复: {ctx.state}")
        if not ctx.approval_token:
            raise RuntimeError("缺少 approval_token")
        trace.emit(
            ctx.task_id,
            "DutyManager",
            event="approval_granted",
            status="ok",
            output={"approval_token": ctx.approval_token[:8] + "..."},
        )
        return self.execute_send_verify(
            ctx, trace, from_state="suspended", live=live, base_url=base_url
        )
