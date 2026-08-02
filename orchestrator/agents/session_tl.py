"""SessionTL - structured multi-Agent orchestration and task state tracking."""

from __future__ import annotations

from pathlib import Path

from orchestrator.agents.duty_manager import DutyManager
from orchestrator.agents.workers import act_verify, case_learning, channel_ingress, triage_guard
from orchestrator.models.conversation_ledger import ConversationLedger
from orchestrator.models.task_context import TaskContext
from orchestrator.models.trace import TraceWriter, input_hash


class SessionTL:
    """AgentTeams Team Leader mapping for one customer task lifecycle."""

    def __init__(
        self,
        *,
        conversation_ledger: ConversationLedger | None = None,
        knowledge_path: Path | None = None,
    ) -> None:
        self.conversation_ledger = conversation_ledger or ConversationLedger()
        self.knowledge_path = knowledge_path or case_learning.DEFAULT_KNOWLEDGE_PATH

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

    def publish_case(self, ctx: TaskContext, trace: TraceWriter) -> TaskContext:
        digest = case_learning.publish(ctx, path=self.knowledge_path)
        ctx.case_digest = digest
        trace.emit(
            ctx.task_id,
            "CaseLearning",
            skill="CaseDigest",
            status="ok",
            output={
                "case_id": digest.get("case_id"),
                "resolution": digest.get("resolution"),
                "privacy_checked": digest.get("privacy", {}).get("contains_customer_content") is False,
            },
        )
        return ctx

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
            output={
                "session_id": session_event.get("session_id"),
                "source_event_id": session_event.get("source_event_id"),
                "canonical_customer_id": session_event.get("canonical_customer_id"),
            },
        )

        dedupe_result = self.conversation_ledger.register(ctx.task_id, session_event)
        ctx.dedupe_result = dedupe_result
        if not dedupe_result.get("accepted"):
            trace.emit(
                ctx.task_id,
                "ChannelIngress",
                event="duplicate_linked",
                status="deduplicated",
                output={
                    "duplicate_of_task_id": dedupe_result.get("duplicate_of_task_id"),
                    "duplicate_of_channel": dedupe_result.get("duplicate_of_channel"),
                },
            )
            self._transition(ctx, trace, "triaging", "deduplicated")
            return ctx

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

        knowledge_hits = case_learning.retrieve(
            session_event,
            triage_result,
            path=self.knowledge_path,
        )
        ctx.knowledge_hits = knowledge_hits
        trace.emit(
            ctx.task_id,
            "CaseLearning",
            skill="CaseKnowledgeRetrieve",
            status="ok",
            output={"hit_count": len(knowledge_hits)},
        )

        self._transition(ctx, trace, "triaging", "planning")
        reply_draft = triage_guard.plan(session_event, triage_result, knowledge_hits)
        ctx.reply_draft = reply_draft
        trace.emit(
            ctx.task_id,
            "TriageGuard",
            skill="ReplyPlan",
            status="ok",
            input_hash=input_hash({"triage": triage_result, "knowledge_hits": knowledge_hits}),
            output={
                "action_type": reply_draft.get("action_type"),
                "citation_count": len(reply_draft.get("citations") or []),
            },
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
        start_state = "suspended" if ctx.state == "approved" else from_state
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
            self._transition(ctx, trace, "acting", "failed")
            trace.emit(ctx.task_id, "SessionTL", event="pipeline_failed", status="failed")
            return self.publish_case(ctx, trace)

        self._transition(ctx, trace, "acting", "verifying")
        verify_result = act_verify.verify(ctx)
        ctx.verify_result = verify_result
        trace.emit(
            ctx.task_id,
            "ActVerify",
            skill="OutcomeVerify",
            status="ok" if verify_result.get("pass") else "failed",
            output={
                "pass": verify_result.get("pass"),
                "evidence_type": verify_result.get("evidence_type"),
            },
        )
        if not verify_result.get("pass"):
            self._transition(ctx, trace, "verifying", "failed")
            return self.publish_case(ctx, trace)

        self._transition(ctx, trace, "verifying", "confirming")
        confirm_result = act_verify.confirm_customer(ctx)
        ctx.customer_confirm_result = confirm_result
        confirmation_state = str(confirm_result.get("confirmation_state") or "awaiting_feedback")
        trace.emit(
            ctx.task_id,
            "ActVerify",
            skill="CustomerConfirm",
            status=confirmation_state,
            output={"needs_follow_up": confirm_result.get("needs_follow_up")},
        )
        if confirmation_state == "confirmed":
            self._transition(ctx, trace, "confirming", "done")
            return self.publish_case(ctx, trace)
        if confirmation_state == "needs_follow_up":
            self._transition(ctx, trace, "confirming", "escalated")
            return self.publish_case(ctx, trace)

        self._transition(ctx, trace, "confirming", "awaiting_customer_confirmation")
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
        return self.execute_send_verify(ctx, trace, from_state="suspended", live=live, base_url=base_url)
