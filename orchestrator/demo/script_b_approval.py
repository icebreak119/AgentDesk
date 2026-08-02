"""剧本 B：高风险审批 — 退款/改账户挂起后放行。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orchestrator.agents.duty_manager import DutyManager
from orchestrator.agents.session_tl import SessionTL
from orchestrator.models.trace import TraceWriter

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "orchestrator" / "output" / "trace.jsonl"


def run(
    *,
    live: bool = False,
    output: Path = DEFAULT_OUTPUT,
    base_url: str = "http://127.0.0.1:8765",
    business_action_backend: str = "jsonl",
    enterprise_url: str = "http://127.0.0.1:8770",
    reject: bool = False,
    inject_verify_failure: bool = False,
):
    duty_manager = DutyManager()
    session_tl = SessionTL(
        knowledge_path=output.with_name("case_knowledge.jsonl"),
        business_action_path=output.with_name("business_actions.jsonl"),
    )
    mode = "live" if live else "mock"
    scenario = "reject" if reject else "rollback" if inject_verify_failure else "success"
    task_id = "task_002" if scenario == "success" else f"task_002_{scenario}"

    ctx = duty_manager.create_task(
        task_id=task_id,
        profile_id="d6a26b9e-demo",
        channel="douyin",
        raw_event={
            "conversation_id": "0:1:1550776822954327:4345741094434680",
            "event_id": f"dy-demo-refund-{scenario}",
            "customer_id": "customer-demo-002",
            "ts": "2026-08-02T09:05:00+08:00",
            "text": "我要退款，改一下账户",
            "sender_name": "李女士",
            "order_id": "order-demo-001",
            "amount": "199.00",
            "currency": "CNY",
            "refund_reason": "商品售后退款",
            "customer_feedback": "谢谢，已经收到处理进度。" if not live else "",
            "inject_business_action_verify_failure": inject_verify_failure,
        },
        mode=mode,
    )

    with TraceWriter(output) as trace:
        ctx = session_tl.run_until_gate(ctx, trace, duty_manager)
        if ctx.state != "suspended":
            raise RuntimeError("剧本 B 应进入 suspended 状态")

        if reject:
            duty_manager.reject(ctx)
            trace.emit(ctx.task_id, "DutyManager", event="approval_rejected", status="failed")
            session_tl.publish_case(ctx, trace)
            print(f"trace written: {output}")
            print(json.dumps(ctx.to_dict(), ensure_ascii=False, indent=2))
            return ctx

        duty_manager.grant_approval(ctx, "appr_token_demo_001")
        ctx = session_tl.resume_after_approval(
            ctx,
            trace,
            duty_manager,
            live=live,
            base_url=base_url,
            business_action_backend=business_action_backend,
            enterprise_base_url=enterprise_url,
        )

    print(f"trace written: {output}")
    print(json.dumps(ctx.to_dict(), ensure_ascii=False, indent=2))
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentDesk 剧本 B：高风险审批")
    parser.add_argument("--live", action="store_true", help="调用 8765 真实发送")
    parser.add_argument(
        "--business-action-backend",
        choices=("jsonl", "http"),
        default="jsonl",
        help="退款动作后端：jsonl 离线适配器或 http 企业系统模拟器",
    )
    parser.add_argument("--enterprise-url", default="http://127.0.0.1:8770")
    parser.add_argument("--reject", action="store_true", help="模拟审批拒绝")
    parser.add_argument(
        "--inject-verify-failure",
        action="store_true",
        help="模拟退款动作核验失败并触发补偿回滚",
    )
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    ctx = run(
        live=args.live,
        output=args.output,
        reject=args.reject,
        base_url=args.base_url,
        business_action_backend=args.business_action_backend,
        enterprise_url=args.enterprise_url,
        inject_verify_failure=args.inject_verify_failure,
    )
    if args.reject:
        return 0 if ctx.state == "failed" else 1
    if args.inject_verify_failure:
        return 0 if ctx.state == "failed" else 1
    return 0 if ctx.state in {"done", "awaiting_customer_confirmation"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
