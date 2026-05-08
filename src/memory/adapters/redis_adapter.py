"""redis_adapter — Management of Working Memory (SessionMemory)

Provides short-term memory management with Redis as the backend.
L1 Definition: SessionMemory (Σ_session) — JSON log / KVS (Redis)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from src.core.logger import setup_logger

logger = setup_logger("memory.redis_adapter")


class RedisAdapter:
    """Redis-based Working Memory adapter.

    Falls back to an in-memory dict if the connection fails,
    maintaining overall system availability.
    """

    def __init__(self, redis_url: str, max_entries: int = 500, ttl_seconds: int = 86400) -> None:
        self._redis_url = redis_url
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._client: Any = None
        self._fallback_cache: list[dict] = []
        self._using_fallback = False
        self._connect()

    # ------------------------------------------------------------------
    # 接続管理
    # ------------------------------------------------------------------
    def _connect(self) -> None:
        """Attempts to connect to Redis. Switches to fallback mode on failure."""
        try:
            import redis
            self._client = redis.Redis.from_url(
                self._redis_url, decode_responses=True
            )
            self._client.ping()
            self._using_fallback = False
            logger.info("Redis connected: %s", self._redis_url)
        except Exception as exc:
            logger.warning(
                "Redis connection failed (%s). Using in-memory fallback.", exc
            )
            self._client = None
            self._using_fallback = True

    def is_healthy(self) -> bool:
        """Checks if the connection is alive."""
        if self._using_fallback:
            return True  # Fallback is always healthy
        try:
            self._client.ping()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------
    def store(
        self,
        event_id: str,
        event_type: str,
        payload: dict,
        importance: float,
        embedding: Optional[list[float]] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Saves an event to session memory."""
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "id": event_id,
            "event_type": event_type,
            "payload": payload,
            "importance": importance,
            "embedding": embedding,
            "created_at": now,
            "session_id": session_id,
        }

        if self._using_fallback:
            self._fallback_cache.append(entry)
            # Apply count limit even during fallback
            if len(self._fallback_cache) > self._max_entries:
                overflow = len(self._fallback_cache) - self._max_entries
                self._fallback_cache = self._fallback_cache[overflow:]
            return

        try:
            key = f"session:{event_id}"
            self._client.set(key, json.dumps(entry, default=str))
            # TTL setting: Set individual entries to be automatically deleted after ttl_seconds
            self._client.expire(key, self._ttl_seconds)
            # Time-series index: sorted set (score = timestamp)
            ts_score = datetime.now(timezone.utc).timestamp()
            self._client.zadd("session:timeline", {event_id: ts_score})

            # Count limit check: Delete overflow entries using FIFO
            total = self._client.zcard("session:timeline")
            if total > self._max_entries:
                overflow = total - self._max_entries
                old_ids = self._client.zrange("session:timeline", 0, overflow - 1)
                for eid in old_ids:
                    self._client.delete(f"session:{eid}")
                if old_ids:
                    self._client.zrem("session:timeline", *old_ids)
                    logger.debug(
                        "Redis capacity eviction: removed %d old entries (max=%d)",
                        len(old_ids), self._max_entries,
                    )
        except Exception as exc:
            logger.warning("Redis store failed, using fallback: %s", exc)
            self._fallback_cache.append(entry)

    def get_recent(self, limit: int = 100) -> list[dict]:
        """Returns the most recent session memory entries."""
        if self._using_fallback:
            return list(self._fallback_cache[-limit:])

        try:
            # Retrieve the latest 'limit' event_ids
            ids = self._client.zrevrange("session:timeline", 0, limit - 1)
            results = []
            for eid in ids:
                raw = self._client.get(f"session:{eid}")
                if raw:
                    entry = json.loads(raw)
                    # Deserialize if the payload is also a JSON string
                    if isinstance(entry.get("payload"), str):
                        try:
                            entry["payload"] = json.loads(entry["payload"])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    results.append(entry)
            # Restore to chronological order
            results.reverse()
            return results
        except Exception as exc:
            logger.warning("Redis get_recent failed: %s", exc)
            return list(self._fallback_cache[-limit:])

    def get_oldest(self, limit: int = 5) -> list[dict]:
        """Returns the oldest session memory entries (for anti_recency)."""
        if self._using_fallback:
            return list(self._fallback_cache[:limit])

        try:
            ids = self._client.zrange("session:timeline", 0, limit - 1)
            results = []
            for eid in ids:
                raw = self._client.get(f"session:{eid}")
                if raw:
                    entry = json.loads(raw)
                    if isinstance(entry.get("payload"), str):
                        try:
                            entry["payload"] = json.loads(entry["payload"])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    results.append(entry)
            return results
        except Exception as exc:
            logger.warning("Redis get_oldest failed: %s", exc)
            return list(self._fallback_cache[:limit])

    def prune(self, keep_count: int = 20, session_id: Optional[str] = None) -> int:
        """Deletes old session memory. Keeps 'keep_count' entries."""
        if self._using_fallback:
            if len(self._fallback_cache) <= keep_count:
                return 0
            removed = len(self._fallback_cache) - keep_count
            self._fallback_cache = self._fallback_cache[-keep_count:]
            return removed

        try:
            total = self._client.zcard("session:timeline")
            if total <= keep_count:
                return 0
            remove_count = total - keep_count
            # Retrieve and delete IDs of the oldest entries
            old_ids = self._client.zrange("session:timeline", 0, remove_count - 1)
            for eid in old_ids:
                self._client.delete(f"session:{eid}")
            if old_ids:
                self._client.zrem("session:timeline", *old_ids)
            return len(old_ids)
        except Exception as exc:
            logger.warning("Redis prune failed: %s", exc)
            return 0

    def count(self) -> int:
        """Returns the current number of session memory entries."""
        if self._using_fallback:
            return len(self._fallback_cache)
        try:
            return self._client.zcard("session:timeline")
        except Exception:
            return len(self._fallback_cache)

    def close(self) -> None:
        """Closes the connection."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
