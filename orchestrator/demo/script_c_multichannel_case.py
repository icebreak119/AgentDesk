"""Scenario C: cross-channel aggregation, customer confirmation and case reuse."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orchestrator.agents.duty_manager import DutyManager
from orchestrator.agents.session_tl import SessionTL
from orchestrator.models.conversation_ledger import ConversationLedger
from orchestrator.models.trace import TraceWriter

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "orchestrator" / "output" / "trace_c_multichannel.jsonl"


def _run_event(
    duty_manager: DutyManager,
    session_tl: SessionTL,
    trace: TraceWriter,
    *,
    task_id: str,
    channel: str,
    profile_id: str,
    raw_event: dict,
):
    ctx = duty_manager.create_task(
        task_id=task_id,
        profile_id=profile_id,
        channel=channel,
        raw_event=raw_event,
        mode="mock",
    )
    return session_tl.run_until_gate(ctx, trace, duty_manager)


def run(*, output: Path = DEFAULT_OUTPUT, knowledge_output: Path | None = None):
    knowledge_path = knowledge_output or output.with_name("case_knowledge_demo.jsonl")
    knowledge_path.unlink(missing_ok=True)
    duty_manager = DutyManager()
    session_tl = SessionTL(
        conversation_ledger=ConversationLedger(),
        knowledge_path=knowledge_path,
    )

    with TraceWriter(output) as trace:
        primary = _run_event(
            duty_manager,
            session_tl,
            trace,
            task_id="task_c_001",
            channel="douyin",
            profile_id="demo-shop-001",
            raw_event={
                "conversation_id": "dy-conv-001",
                "event_id": "dy-msg-001",
                "customer_id": "customer-cross-channel-001",
                "sender_name": "客户 A（脱敏）",
                "text": "在吗，想了解价格",
                "ts": "2026-08-02T10:00:00+08:00",
                "customer_feedback": "收到，谢谢，价格已经清楚了。",
            },
        )
        duplicate = _run_event(
            duty_manager,
            session_tl,
            trace,
            task_id="task_c_002",
            channel="qywx",
            profile_id="wecom-demo-001",
            raw_event={
                "conversation_id": "wecom-conv-009",
                "event_id": "wecom-msg-009",
                "external_userid": "customer-cross-channel-001",
                "sender_name": "客户 A（脱敏）",
                "content": "在吗，想了解价格",
                "ts": "2026-08-02T10:00:00+08:00",
            },
        )
        follow_up = _run_event(
            duty_manager,
            session_tl,
            trace,
            task_id="task_c_003",
            channel="qywx",
            profile_id="wecom-demo-001",
            raw_event={
                "conversation_id": "wecom-conv-009",
                "event_id": "wecom-msg-010",
                "external_userid": "customer-cross-channel-001",
                "sender_name": "客户 A（脱敏）",
                "content": "价格怎么收费？",
                "ts": "2026-08-02T10:02:00+08:00",
                "customer_feedback": "明白了，谢谢。",
            },
        )

    result = {
        "primary": primary.to_dict(),
        "duplicate": duplicate.to_dict(),
        "follow_up": follow_up.to_dict(),
        "knowledge_path": str(knowledge_path),
    }
    print(f"trace written: {output}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return primary, duplicate, follow_up, knowledge_path


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentDesk 剧本 C：跨渠道聚合与案例复盘")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--knowledge-output", type=Path)
    args = parser.parse_args()
    primary, duplicate, follow_up, _ = run(
        output=args.output,
        knowledge_output=args.knowledge_output,
    )
    return 0 if (
        primary.state == "done"
        and duplicate.state == "deduplicated"
        and follow_up.state == "done"
        and follow_up.knowledge_hits
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
