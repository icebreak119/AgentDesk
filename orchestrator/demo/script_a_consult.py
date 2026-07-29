"""剧本 A：咨询主路径 — 「在吗，想了解价格」。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orchestrator.agents.duty_manager import DutyManager
from orchestrator.agents.session_tl import SessionTL
from orchestrator.models.trace import TraceWriter

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "orchestrator" / "output" / "trace.jsonl"


def run(*, live: bool = False, output: Path = DEFAULT_OUTPUT, base_url: str = "http://127.0.0.1:8765"):
    duty_manager = DutyManager()
    session_tl = SessionTL()
    mode = "live" if live else "mock"

    ctx = duty_manager.create_task(
        task_id="task_001",
        profile_id="d6a26b9e-demo",
        channel="douyin",
        raw_event={
            "conversation_id": "0:1:1550776822954327:4345741094434680",
            "text": "在吗，想了解价格",
            "sender_name": "张先生",
        },
        mode=mode,
    )

    with TraceWriter(output) as trace:
        ctx = session_tl.run_until_gate(ctx, trace, duty_manager)
        if ctx.state != "suspended":
            ctx = session_tl.execute_send_verify(
                ctx,
                trace,
                from_state="planning",
                live=live,
                base_url=base_url,
            )

    print(f"trace written: {output}")
    print(json.dumps(ctx.to_dict(), ensure_ascii=False, indent=2))
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentDesk 剧本 A：咨询主路径")
    parser.add_argument("--live", action="store_true", help="调用 8765 真实发送")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    ctx = run(live=args.live, output=args.output, base_url=args.base_url)
    return 0 if ctx.state == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
