"""Trace-adjacent artifact paths for isolated demo runs."""

from __future__ import annotations

from pathlib import Path


def sidecar_path(trace_path: Path, default_name: str) -> Path:
    """Keep the default filenames while isolating custom trace runs."""
    trace_path = Path(trace_path)
    if trace_path.name == "trace.jsonl":
        return trace_path.with_name(default_name)
    return trace_path.with_name(f"{trace_path.stem}.{default_name}")
