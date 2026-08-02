"""Run the local enterprise business simulator."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentDesk enterprise business simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument(
        "--evidence-log",
        type=Path,
        default=Path("orchestrator/output/enterprise_business_evidence.jsonl"),
    )
    args = parser.parse_args(argv)
    host = str(args.host or "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("refusing to bind non-localhost host; use 127.0.0.1")
    from .app import create_app
    import uvicorn

    uvicorn.run(create_app(args.evidence_log), host=host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
