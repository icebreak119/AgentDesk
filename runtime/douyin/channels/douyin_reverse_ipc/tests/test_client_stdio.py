from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

from channels.douyin_reverse_ipc.client import DouyinReverseClient


def test_client_ping_with_mocked_popen(tmp_path: Path, monkeypatch):
    db = tmp_path / "im.db"
    db.write_text("", encoding="utf-8")

    responses = [
        json.dumps({"id": "WILL_REPLACE", "ok": True, "data": {"pong": True}}) + "\n",
    ]
    written = []

    class FakeStdout:
        def __init__(self):
            self._lines = responses
            self._i = 0
            self._lock = threading.Lock()

        def readline(self):
            with self._lock:
                if self._i >= len(self._lines):
                    return ""
                line = self._lines[self._i]
                self._i += 1
                # Fix id to match last written request
                if written:
                    req = json.loads(written[-1])
                    obj = json.loads(line)
                    obj["id"] = req["id"]
                    return json.dumps(obj) + "\n"
                return line

    class FakeStdin:
        def write(self, data):
            written.append(data)
            return len(data)

        def flush(self):
            return None

        def close(self):
            return None

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.stdin = FakeStdin()
    fake_proc.stdout = FakeStdout()
    fake_proc.stderr = MagicMock()

    monkeypatch.setattr(
        "channels.douyin_reverse_ipc.client.subprocess.Popen",
        lambda *a, **k: fake_proc,
    )

    client = DouyinReverseClient(str(db))
    try:
        data = client.ping()
        assert data.get("pong") is True
    finally:
        client.close()
