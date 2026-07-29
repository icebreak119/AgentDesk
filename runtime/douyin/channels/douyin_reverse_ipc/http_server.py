"""Run Douyin reverse IPC as HTTP on 127.0.0.1."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Douyin reverse IPC HTTP server (localhost only)")
    parser.add_argument("--db-path", required=True, help="Runtime SQLite path")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default 8765)")
    args = parser.parse_args(argv)

    host = str(args.host or "127.0.0.1").strip() or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("refusing to bind non-localhost host; use 127.0.0.1")

    db_path = str(Path(args.db_path).expanduser().resolve())
    from channels.douyin_reverse_ipc.http_api import create_app
    import uvicorn

    app = create_app(db_path)
    uvicorn.run(app, host=host, port=int(args.port), log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
