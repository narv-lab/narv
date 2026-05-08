"""memory module — interface: memory_access_v1 (v1.4.0)

Facade implementation of the layered hybrid memory architecture (Proposal 1).
Delegates processing to three sub-storage adapters:
  - RedisAdapter   : Working Memory (SessionMemory / Σ_session)
  - VectorAdapter  : Long-Term Memory (LongTermMemory / Σ_long)
  - GraphAdapter   : Structural Memory (StructuralMemory / Σ_causal)

Public API:
  - store_event        : Writing to all memory substrates
  - query_memory       : Hybrid search (vector / keyword / anti_recency / graph_traversal)
  - consolidate_memory : SleepPhase integration (Consolidate + Prune)
  - get_session_memory : Snapshot acquisition of session memory (for kernel internal use)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from src.core.logger import setup_logger
from src.core.config import config
from src.memory.adapters.redis_adapter import RedisAdapter
from src.memory.adapters.vector_adapter import VectorAdapter
from src.memory.adapters.graph_adapter import GraphAdapter

logger = setup_logger("memory")


class MemoryError(Exception):
    """Custom exception class compliant with the L2 error schema."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class Memory:
    """Layered hybrid memory management class. Implements memory_access_v1 (v1.4.0)."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        chroma_persist_dir: Optional[str] = None,
        neo4j_uri: Optional[str] = None,
        neo4j_user: Optional[str] = None,
        neo4j_password: Optional[str] = None,
    ) -> None:
        # ------------------------------------------------------------------
        # Adapter initialization
        # ------------------------------------------------------------------
        self._redis = RedisAdapter(
            redis_url=redis_url or config.memory_redis_url,
            max_entries=config.memory_redis_max_entries,
            ttl_seconds=config.memory_redis_ttl_seconds,
        )
        self._vector = VectorAdapter(
            persist_dir=chroma_persist_dir or config.memory_chroma_persist_dir,
            cosine_threshold=config.memory_cosine_threshold,
        )
        self._graph = GraphAdapter(
            uri=neo4j_uri or config.memory_neo4j_uri,
            user=neo4j_user or config.memory_neo4j_user,
            password=neo4j_password or config.memory_neo4j_password,
        )
        logger.info(
            "Memory initialized (hybrid). Redis=%s Vector=%s Graph=%s",
            self._redis.is_healthy(),
            self._vector.is_healthy(),
            self._graph.is_healthy(),
        )

    # ------------------------------------------------------------------
    # Internal: caller_id validation
    # ------------------------------------------------------------------
    def _validate_caller_id(self, caller_id: str) -> None:
        """Validates that caller_id is a recognized caller in the system."""
        allowed = {"kernel", "memory"}
        if caller_id not in allowed:
            raise PermissionError(
                f"Unauthorized caller_id='{caller_id}'. Allowed: {allowed}"
            )

    # ------------------------------------------------------------------
    # Public API (memory_access_v1 v1.4.0)
    # ------------------------------------------------------------------
    def store_event(
        self,
        caller_id: str,
        event_type: str,
        payload: dict,
        importance: float,
        embedding: Optional[list[float]] = None,
        session_id: Optional[str] = None,
        causal_links: Optional[list[str]] = None,
        event_id: Optional[str] = None,
    ) -> dict:
        """Saves an event to all memory substrates (store_event operation).

        Returns:
            { event_id: str, status: 'stored' }
        Raises:
            PermissionError: Unauthorized caller_id
            MemoryError: WRITE_FAILURE
        """
        self._validate_caller_id(caller_id)
        event_id = event_id or str(uuid.uuid4())
        
        # Normalization of causal_links (L2 compliance / crash prevention)
        if causal_links:
            if not isinstance(causal_links, list):
                causal_links = [str(causal_links)]
            causal_links = [str(cl) for cl in causal_links if cl]
            if not causal_links:
                causal_links = None

        try:
            # 1. SessionMemory (Redis / Working Memory)
            self._redis.store(
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                importance=importance,
                embedding=embedding,
                session_id=session_id,
            )

            # 2. LongTermMemory (ChromaDB / Vector DB) — Importance >= threshold
            if importance >= 0.7:
                self._vector.store(
                    event_id=event_id,
                    event_type=event_type,
                    payload=payload,
                    importance=importance,
                    embedding=embedding,
                    session_id=session_id,
                )

            # 3. StructuralMemory (Neo4j / Graph DB) — If causal links exist
            if causal_links:
                self._graph.store_event_node(
                    event_id=event_id,
                    event_type=event_type,
                    payload=payload,
                    importance=importance,
                    session_id=session_id,
                    causal_links=causal_links,
                )
            elif importance >= 0.5:
                # High-importance events are saved as nodes even without links
                self._graph.store_event_node(
                    event_id=event_id,
                    event_type=event_type,
                    payload=payload,
                    importance=importance,
                    session_id=session_id,
                    causal_links=None,
                )

            logger.debug(
                "store_event event_id=%s importance=%.2f causal_links=%s",
                event_id,
                importance,
                causal_links,
            )
            return {"event_id": event_id, "status": "stored"}

        except MemoryError:
            raise
        except Exception as exc:
            raise MemoryError("WRITE_FAILURE", f"Failed to store event: {exc}") from exc

    def query_memory(
        self,
        caller_id: str,
        query_vector: Optional[list[float]] = None,
        keywords: Optional[list[str]] = None,
        limit: int = 5,
        strategy: Optional[str] = None,
    ) -> dict:
        """Hybrid search of memory and return results (query_memory operation).

        strategy:
            None            — Vector or keyword or latest session
            'anti_recency'  — Random extraction of oldest memories (prevents DMN convergence)
            'graph_traversal' — Neo4j graph inference / causal exploration

        Returns:
            { results: list[dict] }
        Raises:
            PermissionError: Unauthorized caller_id
            MemoryError: QUERY_TIMEOUT
        """
        self._validate_caller_id(caller_id)
        try:
            results: list[dict] = []

            # -------------------------------------------------------
            # strategy: anti_recency
            # -------------------------------------------------------
            if strategy == "anti_recency":
                # Randomly extract old entries from the vector DB
                results = self._vector.get_oldest(limit=limit)
                if not results:
                    # Fallback: Old memories from Redis
                    results = self._redis.get_oldest(limit=limit)
                logger.debug("query_memory anti_recency: %d entries", len(results))
                return {"results": results}

            # -------------------------------------------------------
            # strategy: graph_traversal
            # -------------------------------------------------------
            if strategy == "graph_traversal":
                results = self._graph.query_traversal(
                    keywords=keywords,
                    limit=limit,
                )
                logger.debug("query_memory graph_traversal: %d entries", len(results))
                return {"results": results}

            # -------------------------------------------------------
            # Normal Search: Vector similarity / Keyword / Latest session
            # -------------------------------------------------------
            if query_vector:
                results = self._vector.query_by_vector(
                    query_vector=query_vector,
                    limit=limit,
                )
            elif keywords:
                results = self._vector.query_by_keywords(
                    keywords=keywords,
                    limit=limit,
                )
            else:
                # Return latest session memory
                results = self._redis.get_recent(limit=limit)

            logger.debug("query_memory found %d results", len(results))
            return {"results": results}

        except MemoryError:
            raise
        except Exception as exc:
            raise MemoryError("QUERY_TIMEOUT", f"Query failed: {exc}") from exc

    def consolidate_memory(
        self,
        caller_id: str,
        session_id: str,
        abstraction_level: float,
        insights: list[dict],
        keep_count: int = 20,
    ) -> dict:
        """Integrates insights into long-term memory and prunes old session memory (consolidate_memory operation).

        Returns:
            { consolidated_logs: list, removed_noise_count: int }
        Raises:
            PermissionError: Unauthorized caller_id
            MemoryError: CONSOLIDATION_FAILURE
        """
        self._validate_caller_id(caller_id)
        try:
            consolidated_logs = []

            for insight in insights:
                event_id = str(uuid.uuid4())
                if isinstance(insight, dict):
                    insight_payload = insight.get("payload", insight)
                    importance = float(insight.get("importance", abstraction_level))
                    event_type = insight.get("event_type", "consolidated")
                else:
                    insight_payload = {"content": str(insight)}
                    importance = abstraction_level
                    event_type = "consolidated"
                
                embedding = insight.get("embedding") if isinstance(insight, dict) else None

                # Integrate into vector DB
                self._vector.store(
                    event_id=event_id,
                    event_type=event_type,
                    payload=insight_payload if isinstance(insight_payload, dict) else {"content": str(insight_payload)},
                    importance=importance,
                    embedding=embedding,
                    session_id=session_id,
                )

                # Also save to graph DB as an integrated node
                self._graph.store_consolidated_insight(
                    event_id=event_id,
                    event_type=event_type,
                    payload=insight_payload if isinstance(insight_payload, dict) else {"content": str(insight_payload)},
                    importance=importance,
                    session_id=session_id,
                    causal_links=insight.get("causal_links") if isinstance(insight, dict) else None,
                )

                consolidated_logs.append({
                    "event_id": event_id,
                    "session_id": session_id,
                })

            # Prune session memory (Redis)
            removed_count = self._redis.prune(
                keep_count=keep_count,
                session_id=session_id,
            )

            # ChromaDB capacity management: Evict low-importance entries when over limit
            evicted_chroma = self._vector.evict_low_importance(
                max_entries=config.memory_chroma_max_entries,
                batch_size=config.memory_chroma_eviction_batch,
                threshold=config.memory_chroma_eviction_threshold,
            )

            # Neo4j capacity management: Compress old sessions when over archive threshold
            compressed_neo4j = self._graph.compress_old_sessions(
                archive_threshold=config.memory_neo4j_archive_threshold,
                min_importance=config.memory_neo4j_min_importance,
            )

            logger.info(
                "consolidate_memory session_id=%s insights=%d "
                "redis_pruned=%d chroma_evicted=%d neo4j_compressed=%d",
                session_id,
                len(insights),
                removed_count,
                evicted_chroma,
                compressed_neo4j,
            )
            return {
                "consolidated_logs": consolidated_logs,
                "removed_noise_count": removed_count
            }

        except MemoryError:
            raise
        except Exception as exc:
            raise MemoryError(
                "CONSOLIDATION_FAILURE", f"Failed to consolidate memory: {exc}"
            ) from exc

    def get_session_memory(self) -> list[dict]:
        """Returns in-memory session memory (for kernel internal use)."""
        return self._redis.get_recent(limit=config.system_session_memory_keep_count)

    def close(self) -> None:
        """Closes connections for all adapters."""
        self._redis.close()
        self._vector.close()
        self._graph.close()
        logger.info("Memory closed.")
