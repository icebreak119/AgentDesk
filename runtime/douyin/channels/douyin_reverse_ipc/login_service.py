"""Background login collection jobs for the HTTP runtime."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from channels.douyin_reverse_ipc.errors import RpcError

_RUNTIME_ROOT = Path(__file__).resolve().parents[2]
_REVERSE_RUNTIME_ROOT = (
    _RUNTIME_ROOT / "channels" / "douyin_all_user" / "reverse_runtime"
).resolve()

_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_LATEST_BY_ACCOUNT: dict[str, str] = {}


def _require_account_code(account_code: str) -> str:
    code = str(account_code or "").strip()
    if not code:
        raise RpcError("account_required", "account_code is required")
    return code


def _safe_output(text: str, limit: int = 4000) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    return raw[-limit:]


def _job_view(job: dict[str, Any]) -> dict[str, Any]:
    view = dict(job)
    view.pop("thread", None)
    return view


def start_login_job(
    db_path: str,
    account_code: str,
    *,
    timeout_seconds: int = 300,
    browser_channel: str = "",
    headless: bool = False,
    skip_health_check: bool = False,
) -> dict[str, Any]:
    code = _require_account_code(account_code)
    timeout = max(30, min(int(timeout_seconds or 300), 1800))

    with _LOCK:
        latest_id = _LATEST_BY_ACCOUNT.get(code)
        latest = _JOBS.get(latest_id or "")
        if latest and latest.get("status") in {"queued", "running"}:
            return _job_view(latest)

        job_id = f"login_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        job: dict[str, Any] = {
            "job_id": job_id,
            "account_code": code,
            "status": "queued",
            "started_at": "",
            "finished_at": "",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "error": "",
            "db_path": str(Path(db_path).expanduser().resolve()),
            "headless": bool(headless),
        }
        _JOBS[job_id] = job
        _LATEST_BY_ACCOUNT[code] = job_id

    def _runner() -> None:
        started = time.time()
        with _LOCK:
            job["status"] = "running"
            job["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        cmd = [
            sys.executable,
            "-m",
            "channels.douyin_all_user.reverse_runtime.dy_apis.collect_im_account_credentials",
            "--account-code",
            code,
            "--db-path",
            job["db_path"],
            "--timeout",
            str(timeout),
        ]
        if browser_channel.strip():
            cmd.extend(["--browser-channel", browser_channel.strip()])
        if headless:
            cmd.append("--headless")
        if skip_health_check:
            cmd.append("--skip-health-check")

        env = os.environ.copy()
        extra_paths = [str(_RUNTIME_ROOT), str(_REVERSE_RUNTIME_ROOT)]
        old_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join([*extra_paths, old_pythonpath] if old_pythonpath else extra_paths)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(_RUNTIME_ROOT),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout + 60,
                check=False,
            )
            status = "succeeded" if proc.returncode == 0 else "failed"
            with _LOCK:
                job.update(
                    {
                        "status": status,
                        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "returncode": proc.returncode,
                        "stdout_tail": _safe_output(proc.stdout),
                        "stderr_tail": _safe_output(proc.stderr),
                        "elapsed_seconds": round(time.time() - started, 2),
                    }
                )
        except subprocess.TimeoutExpired as exc:
            with _LOCK:
                job.update(
                    {
                        "status": "failed",
                        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "error": f"login collection timed out after {timeout}s",
                        "stdout_tail": _safe_output(exc.stdout or ""),
                        "stderr_tail": _safe_output(exc.stderr or ""),
                        "elapsed_seconds": round(time.time() - started, 2),
                    }
                )
        except Exception as exc:
            with _LOCK:
                job.update(
                    {
                        "status": "failed",
                        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "error": str(exc) or type(exc).__name__,
                        "elapsed_seconds": round(time.time() - started, 2),
                    }
                )

    thread = threading.Thread(target=_runner, name=f"dy-login-collect-{code}", daemon=True)
    with _LOCK:
        job["thread"] = thread
    thread.start()
    return get_login_job(job_id)


def get_login_job(job_id: str) -> dict[str, Any]:
    key = str(job_id or "").strip()
    with _LOCK:
        job = _JOBS.get(key)
        if not job:
            raise RpcError("not_found", f"login job not found: {key}")
        return _job_view(job)


def get_latest_login_job(account_code: str) -> dict[str, Any]:
    code = _require_account_code(account_code)
    with _LOCK:
        job_id = _LATEST_BY_ACCOUNT.get(code)
    if not job_id:
        return {"account_code": code, "status": "not_started"}
    return get_login_job(job_id)
