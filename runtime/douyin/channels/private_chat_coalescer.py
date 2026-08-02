"""Inbound private-chat message coalescing for the standalone runtime.

The original desktop host provided this helper from its shared channel layer.
AgentDesk keeps a small local copy so the Douyin runtime can run without that
host package.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Hashable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def private_ai_coalesce_window_ms(default: int = 3000) -> int:
    raw = os.getenv("PRIVATE_AI_COALESCE_WINDOW_MS", "").strip()
    if not raw:
        raw = os.getenv("YUNDUO_PRIVATE_AI_COALESCE_WINDOW_MS", "").strip()
    try:
        value = int(raw) if raw else int(default)
    except (TypeError, ValueError):
        value = int(default)
    return max(0, value)


@dataclass(frozen=True)
class CoalescedInboundMessage:
    msg_id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CoalescedInboundBatch:
    key: Tuple[Hashable, ...]
    messages: List[CoalescedInboundMessage]
    generation: int
    fired_at: float = field(default_factory=time.time)

    @property
    def msg_ids(self) -> List[str]:
        return [m.msg_id for m in self.messages if m.msg_id]

    @property
    def merged_text(self) -> str:
        return "\n".join(m.text.strip() for m in self.messages if m.text.strip()).strip()

    @property
    def last_msg_id(self) -> str:
        ids = self.msg_ids
        return ids[-1] if ids else ""

    @property
    def last_metadata(self) -> Dict[str, Any]:
        if not self.messages:
            return {}
        return dict(self.messages[-1].metadata or {})


@dataclass
class _PendingBucket:
    messages: List[CoalescedInboundMessage] = field(default_factory=list)
    generation: int = 0
    timer: Optional[threading.Timer] = None
    on_flush: Optional["FlushCallback"] = None


FlushCallback = Callable[[CoalescedInboundBatch], None]


class InboundAiCoalescer:
    """Debounce inbound messages by conversation and flush one merged batch."""

    def __init__(self, *, window_ms: Optional[int] = None, name: str = "private-ai"):
        self.window_ms = private_ai_coalesce_window_ms() if window_ms is None else max(0, int(window_ms))
        self.name = name
        self._lock = threading.RLock()
        self._buckets: Dict[Tuple[Hashable, ...], _PendingBucket] = {}

    def ingest(
        self,
        key: Tuple[Hashable, ...],
        *,
        text: str,
        msg_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        on_flush: FlushCallback,
    ) -> None:
        stable_key = tuple(key)
        inbound_text = (text or "").strip()
        if not stable_key or not inbound_text:
            return
        item = CoalescedInboundMessage(
            msg_id=(msg_id or "").strip(),
            text=inbound_text,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            bucket = self._buckets.get(stable_key)
            if bucket is None:
                bucket = _PendingBucket()
                self._buckets[stable_key] = bucket
            bucket.messages.append(item)
            bucket.generation += 1
            bucket.on_flush = on_flush
            generation = bucket.generation
            if bucket.timer is not None:
                bucket.timer.cancel()
            if self.window_ms <= 0:
                bucket.timer = None
                batch = self._pop_batch_locked(stable_key, generation)
            else:
                timer = threading.Timer(
                    self.window_ms / 1000.0,
                    self._fire,
                    args=(stable_key, generation, on_flush),
                )
                timer.daemon = True
                bucket.timer = timer
                batch = None
                timer.start()
        if batch is not None:
            self._deliver(batch, on_flush)

    def flush(self, key: Tuple[Hashable, ...], on_flush: FlushCallback) -> bool:
        stable_key = tuple(key)
        with self._lock:
            bucket = self._buckets.get(stable_key)
            if bucket is None:
                return False
            batch = self._pop_batch_locked(stable_key, bucket.generation)
        if batch is None:
            return False
        self._deliver(batch, on_flush)
        return True

    def shutdown(self, *, flush: bool = True) -> None:
        with self._lock:
            pending = [(key, bucket, bucket.on_flush) for key, bucket in self._buckets.items()]
            self._buckets.clear()
        for key, bucket, callback in pending:
            if bucket.timer is not None:
                bucket.timer.cancel()
            if flush and callback is not None and bucket.messages:
                self._deliver(
                    CoalescedInboundBatch(
                        key=key,
                        messages=list(bucket.messages),
                        generation=bucket.generation,
                    ),
                    callback,
                )

    def _fire(
        self,
        key: Tuple[Hashable, ...],
        generation: int,
        on_flush: FlushCallback,
    ) -> None:
        with self._lock:
            batch = self._pop_batch_locked(key, generation)
        if batch is not None:
            self._deliver(batch, on_flush)

    def _pop_batch_locked(
        self,
        key: Tuple[Hashable, ...],
        generation: int,
    ) -> Optional[CoalescedInboundBatch]:
        bucket = self._buckets.get(key)
        if bucket is None or bucket.generation != generation:
            return None
        if bucket.timer is not None:
            bucket.timer.cancel()
            bucket.timer = None
        self._buckets.pop(key, None)
        messages = list(bucket.messages)
        if not messages:
            return None
        return CoalescedInboundBatch(key=key, messages=messages, generation=generation)

    def _deliver(self, batch: CoalescedInboundBatch, on_flush: FlushCallback) -> None:
        try:
            logger.info(
                "private AI coalesced batch: name=%s key=%s size=%s window_ms=%s",
                self.name,
                batch.key,
                len(batch.messages),
                self.window_ms,
            )
            on_flush(batch)
        except Exception:
            logger.exception("private AI coalesced callback failed: name=%s key=%s", self.name, batch.key)


_DEFAULT_COALESCER = InboundAiCoalescer()


def get_private_ai_coalescer() -> InboundAiCoalescer:
    return _DEFAULT_COALESCER
