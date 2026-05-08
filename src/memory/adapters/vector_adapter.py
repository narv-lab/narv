"""vector_adapter — Management of LongTermMemory (Σ_long)

Provides long-term memory with vector similarity search using ChromaDB as the backend.
L1 Definition: LongTermMemory (Σ_long) — Vector DB entries
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from src.core.logger import setup_logger

logger = setup_logger("memory.vector_adapter")


class VectorAdapter:
    """ChromaDB-based Long-Term Memory adapter.

    Uses ChromaDB's local persistence mode and operates without external processes.
    Degrades to SQLite fallback on connection failure.
    """

    # ChromaDB collection name
    COLLECTION_NAME = "narv_long_term_memory"

    def __init__(self, persist_dir: str, cosine_threshold: float = 0.75) -> None:
        self._persist_dir = persist_dir
        self._cosine_threshold = cosine_threshold
        self._client: Any = None
        self._collection: Any = None
        self._using_fallback = False
        self._connect()

    # ------------------------------------------------------------------
    # 接続管理
    # ------------------------------------------------------------------
    def _connect(self) -> None:
        """Attempts to connect to ChromaDB."""
        try:
            import chromadb
            from chromadb.config import Settings

            self._client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            self._using_fallback = False
            logger.info(
                "ChromaDB connected: %s (entries=%d)",
                self._persist_dir,
                self._collection.count(),
            )
        except Exception as exc:
            logger.warning(
                "ChromaDB connection failed (%s). Vector search will be unavailable.", exc
            )
            self._using_fallback = True

    def is_healthy(self) -> bool:
        """Checks if the vector DB is operational."""
        if self._using_fallback:
            return False
        try:
            self._collection.count()
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
        """Saves an event to long-term memory (vector DB)."""
        if self._using_fallback:
            logger.debug("VectorAdapter in fallback mode — skipping store.")
            return

        now = datetime.now(timezone.utc).isoformat()
        metadata = {
            "event_type": event_type,
            "importance": importance,
            "created_at": now,
            "session_id": session_id or "",
        }
        document = json.dumps(payload, ensure_ascii=False, default=str)

        try:
            add_kwargs: dict[str, Any] = {
                "ids": [event_id],
                "documents": [document],
                "metadatas": [metadata],
            }
            if embedding:
                add_kwargs["embeddings"] = [embedding]

            self._collection.add(**add_kwargs)
            logger.debug("VectorAdapter stored event_id=%s", event_id)
        except Exception as exc:
            logger.error("VectorAdapter store failed: %s", exc)

    def query_by_vector(
        self,
        query_vector: list[float],
        limit: int = 5,
    ) -> list[dict]:
        """Executes a vector similarity search.

        ChromaDB uses cosine distance internally and returns in ascending order of distance.
        Distance = 1 - Similarity, so threshold 0.75 → filters by distance <= 0.25.
        """
        if self._using_fallback:
            return []

        try:
            max_distance = 1.0 - self._cosine_threshold
            results = self._collection.query(
                query_embeddings=[query_vector],
                n_results=limit,
            )

            entries = []
            if results and results["ids"] and results["ids"][0]:
                for i, eid in enumerate(results["ids"][0]):
                    distance = results["distances"][0][i] if results.get("distances") else 0.0
                    if distance > max_distance:
                        continue  # Exclude below threshold

                    doc_text = results["documents"][0][i] if results.get("documents") else "{}"
                    try:
                        payload = json.loads(doc_text)
                    except (json.JSONDecodeError, TypeError):
                        payload = {"text": doc_text}

                    meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                    entries.append({
                        "id": eid,
                        "event_type": meta.get("event_type", "unknown"),
                        "payload": payload,
                        "importance": meta.get("importance", 0.0),
                        "created_at": meta.get("created_at", ""),
                        "session_id": meta.get("session_id", ""),
                        "embedding": None,  # Raw vectors are not returned externally
                        "similarity": round(1.0 - distance, 4),
                    })
            logger.debug("VectorAdapter query_by_vector found %d entries", len(entries))
            return entries
        except Exception as exc:
            logger.error("VectorAdapter query_by_vector failed: %s", exc)
            return []

    def query_by_keywords(self, keywords: list[str], limit: int = 5) -> list[dict]:
        """Executes a keyword-based full-text search."""
        if self._using_fallback:
            return []

        try:
            query_text = " ".join(keywords)
            results = self._collection.query(
                query_texts=[query_text],
                n_results=limit,
            )

            entries = []
            if results and results["ids"] and results["ids"][0]:
                for i, eid in enumerate(results["ids"][0]):
                    doc_text = results["documents"][0][i] if results.get("documents") else "{}"
                    try:
                        payload = json.loads(doc_text)
                    except (json.JSONDecodeError, TypeError):
                        payload = {"text": doc_text}

                    meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                    entries.append({
                        "id": eid,
                        "event_type": meta.get("event_type", "unknown"),
                        "payload": payload,
                        "importance": meta.get("importance", 0.0),
                        "created_at": meta.get("created_at", ""),
                        "session_id": meta.get("session_id", ""),
                        "embedding": None,
                    })
            logger.debug("VectorAdapter query_by_keywords found %d entries", len(entries))
            return entries
        except Exception as exc:
            logger.error("VectorAdapter query_by_keywords failed: %s", exc)
            return []

    def get_oldest(self, limit: int = 5) -> list[dict]:
        """Retrieves the oldest entries (for anti_recency)."""
        if self._using_fallback:
            return []

        try:
            # Since ChromaDB does not natively support sorting by date,
            # retrieve all metadata and sort by created_at.
            all_data = self._collection.get(
                include=["documents", "metadatas"],
            )
            if not all_data or not all_data["ids"]:
                return []

            items = []
            for i, eid in enumerate(all_data["ids"]):
                meta = all_data["metadatas"][i] if all_data.get("metadatas") else {}
                doc_text = all_data["documents"][i] if all_data.get("documents") else "{}"
                try:
                    payload = json.loads(doc_text)
                except (json.JSONDecodeError, TypeError):
                    payload = {"text": doc_text}
                items.append({
                    "id": eid,
                    "event_type": meta.get("event_type", "unknown"),
                    "payload": payload,
                    "importance": meta.get("importance", 0.0),
                    "created_at": meta.get("created_at", ""),
                    "session_id": meta.get("session_id", ""),
                    "embedding": None,
                })

            items.sort(key=lambda x: x.get("created_at", ""))
            import random
            oldest_pool = items[: min(100, len(items))]
            return random.sample(oldest_pool, min(limit, len(oldest_pool)))
        except Exception as exc:
            logger.error("VectorAdapter get_oldest failed: %s", exc)
            return []

    def count(self) -> int:
        """Returns the current number of LTM entries."""
        if self._using_fallback:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0

    def evict_low_importance(
        self,
        max_entries: int,
        batch_size: int,
        threshold: float,
    ) -> int:
        """Deletes low-importance entries to free up capacity.

        Expected to be called after consolidate_memory in the Sleep Phase.
        Until total count is below max_entries, deletes batch_size entries with 
        importance < threshold, starting from the oldest.

        If all entries are above the threshold, the oldest ones are deleted.

        Returns:
            Number of entries deleted
        """
        if self._using_fallback:
            return 0

        try:
            current = self._collection.count()
            if current <= max_entries:
                return 0

            # Retrieve all metadata and sort by importance and created_at
            all_data = self._collection.get(include=["metadatas"])
            if not all_data or not all_data.get("ids"):
                return 0

            items: list[tuple[str, float, str]] = []
            for i, eid in enumerate(all_data["ids"]):
                meta = all_data["metadatas"][i] if all_data.get("metadatas") else {}
                imp = float(meta.get("importance", 0.0))
                created = meta.get("created_at", "")
                items.append((eid, imp, created))

            # Sort by importance (asc) -> oldest first if same importance (highest deletion priority)
            items.sort(key=lambda x: (x[1], x[2]))

            to_delete: list[str] = []
            for eid, imp, _ in items:
                if len(to_delete) >= batch_size:
                    break
                if imp < threshold:
                    to_delete.append(eid)

            # If no entries are below threshold, delete starting from the oldest
            if not to_delete:
                to_delete = [eid for eid, _, _ in items[:batch_size]]

            if to_delete:
                self._collection.delete(ids=to_delete)
                logger.info(
                    "ChromaDB capacity eviction: removed %d entries "
                    "(current=%d, max=%d, threshold=%.2f)",
                    len(to_delete), current, max_entries, threshold,
                )

            return len(to_delete)
        except Exception as exc:
            logger.error("VectorAdapter evict_low_importance failed: %s", exc)
            return 0

    def close(self) -> None:
        """Releases resources."""
        # ChromaDB PersistentClient は明示的な close を必要としない
        pass
