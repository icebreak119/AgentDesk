"""Run the browser-friendly live demo."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentDesk live orchestration demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--enterprise-url", default="http://127.0.0.1:8770")
    parser.add_argument("--wecom-url", default="http://127.0.0.1:8771")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8780")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    if str(args.host) not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("refusing to bind non-localhost host; use 127.0.0.1")
    from .app import create_app
    import uvicorn

    uvicorn.run(
        create_app(
            repo_root=args.repo_root,
            enterprise_url=args.enterprise_url,
            wecom_url=args.wecom_url,
            gateway_url=args.gateway_url,
        ),
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
