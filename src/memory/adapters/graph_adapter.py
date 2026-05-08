"""graph_adapter — Management of StructuralMemory (Σ_causal)

Provides causal relationships and structural memory with Neo4j as the backend.
L1 Definition: StructuralMemory (Σ_causal) — Graph DB nodes and edges (Neo4j)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from src.core.logger import setup_logger

logger = setup_logger("memory.graph_adapter")


class GraphAdapter:
    """Neo4j-based Structural Memory adapter.

    Saves causal relationships and executes graph_traversal queries.
    If the connection fails, graph functionality is disabled to maintain overall system availability.
    """

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._driver: Any = None
        self._available = False
        self._connect()

    # ------------------------------------------------------------------
    # Connection Management
    # ------------------------------------------------------------------
    def _connect(self) -> None:
        """Attempts to connect to Neo4j."""
        try:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(
                self._uri, auth=(self._user, self._password)
            )
            # Verify connectivity
            self._driver.verify_connectivity()
            self._available = True
            self._ensure_indexes()
            removed = self.cleanup_unknown_nodes()
            if removed > 0:
                logger.info("Neo4j startup cleanup: removed %d unknown nodes.", removed)
            logger.info("Neo4j connected: %s", self._uri)
        except Exception as exc:
            logger.warning(
                "Neo4j connection failed (%s). Graph features will be unavailable.",
                exc,
            )
            self._driver = None
            self._available = False

    def _ensure_indexes(self) -> None:
        """Creates indexes on the first startup."""
        if not self._available:
            return
        try:
            with self._driver.session() as session:
                session.run(
                    "CREATE INDEX event_id_index IF NOT EXISTS FOR (e:Event) ON (e.event_id)"
                )
                session.run(
                    "CREATE INDEX event_type_index IF NOT EXISTS FOR (e:Event) ON (e.event_type)"
                )
            logger.debug("Neo4j indexes ensured.")
        except Exception as exc:
            logger.warning("Neo4j index creation failed: %s", exc)

    def is_healthy(self) -> bool:
        """Checks if the connection is alive."""
        if not self._available:
            return False
        try:
            self._driver.verify_connectivity()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def store_event_node(
        self,
        event_id: str,
        event_type: str,
        payload: dict,
        importance: float,
        session_id: Optional[str] = None,
        causal_links: Optional[list[str]] = None,
    ) -> None:
        """Saves an event as a graph node and generates causal edges."""
        if not self._available:
            return

        now = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)

        try:
            with self._driver.session() as session:
                # Create node
                session.run(
                    """
                    MERGE (e:Event {event_id: $event_id})
                    SET e.event_type = $event_type,
                        e.payload = $payload,
                        e.importance = $importance,
                        e.created_at = $created_at,
                        e.session_id = $session_id
                    """,
                    event_id=event_id,
                    event_type=event_type,
                    payload=payload_json,
                    importance=importance,
                    created_at=now,
                    session_id=session_id or "",
                )

                # Generate causal edges
                # Note: Use MATCH (MERGE is forbidden because it creates empty nodes for non-existent dst_id).
                # If linked_id does not exist in the DB, silently ignore it without creating an edge.
                if causal_links:
                    for linked_id in causal_links:
                        session.run(
                            """
                            MATCH (src:Event {event_id: $src_id})
                            MATCH (dst:Event {event_id: $dst_id})
                            MERGE (src)-[:CAUSED_BY]->(dst)
                            """,
                            src_id=event_id,
                            dst_id=linked_id,
                        )

            logger.debug(
                "GraphAdapter stored node event_id=%s causal_links=%s",
                event_id,
                causal_links,
            )
        except Exception as exc:
            logger.error("GraphAdapter store_event_node failed: %s", exc)

    def query_traversal(
        self,
        keywords: Optional[list[str]] = None,
        limit: int = 5,
    ) -> list[dict]:
        """Queries causally related memories using graph traversal.

        Starting from nodes matching keywords, follows causal edges up to 2 hops
        and returns the set of related nodes.
        """
        if not self._available:
            return []

        try:
            with self._driver.session() as session:
                if keywords:
                    # Search starting from nodes containing keywords
                    keyword_pattern = "|".join(keywords)
                    result = session.run(
                        """
                        MATCH (start:Event)
                        WHERE any(kw IN $keywords WHERE start.payload CONTAINS kw
                              OR start.event_type CONTAINS kw)
                        OPTIONAL MATCH path = (start)-[:CAUSED_BY*1..2]-(related:Event)
                        WITH start, collect(DISTINCT related) AS related_nodes
                        UNWIND ([start] + related_nodes) AS node
                        WITH DISTINCT node
                        RETURN node.event_id AS event_id,
                               node.event_type AS event_type,
                               node.payload AS payload,
                               node.importance AS importance,
                               node.created_at AS created_at,
                               node.session_id AS session_id
                        ORDER BY node.importance DESC
                        LIMIT $limit
                        """,
                        keywords=keywords,
                        limit=limit,
                    )
                else:
                    # No keywords: Return nodes with the highest importance and their causal network
                    result = session.run(
                        """
                        MATCH (e:Event)
                        OPTIONAL MATCH path = (e)-[:CAUSED_BY*1..2]-(related:Event)
                        WITH e, collect(DISTINCT related) AS related_nodes
                        UNWIND ([e] + related_nodes) AS node
                        WITH DISTINCT node
                        RETURN node.event_id AS event_id,
                               node.event_type AS event_type,
                               node.payload AS payload,
                               node.importance AS importance,
                               node.created_at AS created_at,
                               node.session_id AS session_id
                        ORDER BY node.importance DESC
                        LIMIT $limit
                        """,
                        limit=limit,
                    )

                entries = []
                for record in result:
                    payload_raw = record["payload"] or "{}"
                    try:
                        payload = json.loads(payload_raw)
                    except (json.JSONDecodeError, TypeError):
                        payload = {"text": payload_raw}

                    entries.append({
                        "id": record["event_id"],
                        "event_type": record["event_type"] or "unknown",
                        "payload": payload,
                        "importance": record["importance"] or 0.0,
                        "created_at": record["created_at"] or "",
                        "session_id": record["session_id"] or "",
                        "embedding": None,
                        "source": "graph_traversal",
                    })

                logger.debug(
                    "GraphAdapter query_traversal found %d entries", len(entries)
                )
                return entries

        except Exception as exc:
            logger.error("GraphAdapter query_traversal failed: %s", exc)
            return []

    def store_consolidated_insight(
        self,
        event_id: str,
        event_type: str,
        payload: dict,
        importance: float,
        session_id: Optional[str] = None,
        causal_links: Optional[list[str]] = None,
    ) -> None:
        """Additionally saves integrated insights as graph nodes."""
        self.store_event_node(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            importance=importance,
            session_id=session_id,
            causal_links=causal_links,
        )

    def get_all_graph_data(self, limit: int = 200) -> dict:
        """For dashboard visualization: Returns all nodes and links.

        Returns:
            {
                "nodes": [{ "id", "event_type", "importance", "created_at", "payload_summary" }, ...],
                "links": [{ "source", "target", "type" }, ...]
            }
        """
        if not self._available:
            return {"nodes": [], "links": []}

        try:
            with self._driver.session() as session:
                # Retrieve nodes
                node_result = session.run(
                    """
                    MATCH (e:Event)
                    RETURN e.event_id AS id,
                           e.event_type AS event_type,
                           e.importance AS importance,
                           e.created_at AS created_at,
                           e.payload AS payload
                    ORDER BY e.created_at DESC
                    LIMIT $limit
                    """,
                    limit=limit,
                )
                nodes = []
                node_ids = set()
                for record in node_result:
                    node_id = record["id"]
                    node_ids.add(node_id)
                    # Generate payload summary (first 80 characters)
                    payload_raw = record["payload"] or "{}"
                    try:
                        payload_obj = json.loads(payload_raw)
                        # Extract summary from text-based keys
                        summary_keys = [
                            "response_to_user", "message", "text", "thought",
                            "internal_thought", "rationale", "content", "summary",
                        ]
                        payload_summary = ""
                        for sk in summary_keys:
                            if sk in payload_obj and isinstance(payload_obj[sk], str):
                                payload_summary = payload_obj[sk][:80]
                                break
                        if not payload_summary:
                            payload_summary = payload_raw[:80]
                    except (json.JSONDecodeError, TypeError):
                        payload_summary = str(payload_raw)[:80]

                    nodes.append({
                        "id": node_id,
                        "event_type": record["event_type"] or "unknown",
                        "importance": record["importance"] or 0.0,
                        "created_at": record["created_at"] or "",
                        "payload_summary": payload_summary,
                    })

                # Retrieve edges: Limited to those included in the retrieved node list
                link_result = session.run(
                    """
                    MATCH (a:Event)-[r:CAUSED_BY]->(b:Event)
                    WHERE a.event_id IN $node_ids AND b.event_id IN $node_ids
                    RETURN a.event_id AS source, b.event_id AS target, type(r) AS rel_type
                    LIMIT $limit
                    """,
                    node_ids=list(node_ids),
                    limit=limit * 5,
                )
                links = []
                for record in link_result:
                    src = record["source"]
                    tgt = record["target"]
                    if src in node_ids and tgt in node_ids:
                        links.append({
                            "source": src,
                            "target": tgt,
                            "type": record["rel_type"] or "CAUSED_BY",
                        })

                logger.debug(
                    "GraphAdapter get_all_graph_data: %d nodes, %d links",
                    len(nodes), len(links),
                )
                return {"nodes": nodes, "links": links}

        except Exception as exc:
            logger.error("GraphAdapter get_all_graph_data failed: %s", exc)
            return {"nodes": [], "links": []}

    def count(self) -> int:
        """Returns the current number of graph nodes."""
        if not self._available:
            return 0
        try:
            with self._driver.session() as session:
                result = session.run("MATCH (e:Event) RETURN count(e) AS cnt")
                record = result.single()
                return record["cnt"] if record else 0
        except Exception:
            return 0

    def cleanup_unknown_nodes(self) -> int:
        """Deletes isolated nodes (Unknown nodes) where event_type is NULL.

        Removes property-deficient nodes generated by MERGE when an event_id that does not exist 
        in causal_links was passed. Called at connection and manually as needed.

        Returns:
            Number of nodes deleted
        """
        if not self._available:
            return 0
        try:
            with self._driver.session() as session:
                count_result = session.run(
                    "MATCH (e:Event) WHERE e.event_type IS NULL RETURN count(e) AS cnt"
                )
                record = count_result.single()
                cnt = record["cnt"] if record else 0

                if cnt > 0:
                    session.run(
                        "MATCH (e:Event) WHERE e.event_type IS NULL DETACH DELETE e"
                    )
                    logger.info(
                        "cleanup_unknown_nodes: removed %d unknown (event_type=NULL) nodes.", cnt
                    )
                return cnt
        except Exception as exc:
            logger.error("cleanup_unknown_nodes failed: %s", exc)
            return 0

    def compress_old_sessions(
        self,
        archive_threshold: int,
        min_importance: float,
    ) -> int:
        """Compresses low-importance nodes of old sessions into session summary nodes.

        Executed when nodes exceeding archive_threshold exist.
        Merges low-importance (< min_importance) nodes within the same session into 
        one session_summary node and reconnects causal edges.
        This reduces the number of nodes while maintaining the graph topology.

        Returns:
            Number of nodes deleted
        """
        if not self._available:
            return 0

        try:
            current = self.count()
            if current <= archive_threshold:
                return 0

            with self._driver.session() as session:
                # Retrieve up to 3 old sessions with many low-importance nodes
                result = session.run(
                    """
                    MATCH (e:Event)
                    WHERE e.importance < $min_importance
                      AND e.event_type <> 'session_summary'
                    WITH e.session_id AS sid,
                         collect(e.event_id) AS event_ids,
                         count(e) AS cnt,
                         min(e.created_at) AS oldest
                    WHERE cnt >= 5
                    ORDER BY oldest ASC
                    LIMIT 3
                    RETURN sid, event_ids, cnt
                    """,
                    min_importance=min_importance,
                )

                records = list(result)
                total_removed = 0

                for record in records:
                    sid = record["sid"]
                    event_ids: list[str] = list(record["event_ids"])
                    cnt = record["cnt"]

                    if not sid or not event_ids:
                        continue

                    import uuid as _uuid
                    summary_id = f"summary_{sid}_{_uuid.uuid4().hex[:8]}"

                    # Create summary node
                    session.run(
                        """
                        CREATE (s:Event {
                            event_id: $summary_id,
                            event_type: 'session_summary',
                            importance: $importance,
                            session_id: $sid,
                            payload: $payload,
                            created_at: $created_at
                        })
                        """,
                        summary_id=summary_id,
                        sid=sid,
                        importance=min_importance,
                        payload=f'{{"merged_count": {cnt}, "source_session": "{sid}"}}',
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )

                    # Port outgoing edges from target nodes to the summary node
                    session.run(
                        """
                        MATCH (old:Event)-[r:CAUSED_BY]->(target:Event)
                        WHERE old.event_id IN $ids
                          AND NOT target.event_id IN $ids
                        MATCH (s:Event {event_id: $summary_id})
                        MERGE (s)-[:CAUSED_BY]->(target)
                        DELETE r
                        """,
                        ids=event_ids,
                        summary_id=summary_id,
                    )

                    # Port incoming edges to target nodes to the summary node
                    session.run(
                        """
                        MATCH (source:Event)-[r:CAUSED_BY]->(old:Event)
                        WHERE old.event_id IN $ids
                          AND NOT source.event_id IN $ids
                        MATCH (s:Event {event_id: $summary_id})
                        MERGE (source)-[:CAUSED_BY]->(s)
                        DELETE r
                        """,
                        ids=event_ids,
                        summary_id=summary_id,
                    )

                    # Delete old nodes (DETACH also removes remaining edges)
                    session.run(
                        """
                        MATCH (e:Event)
                        WHERE e.event_id IN $ids
                        DETACH DELETE e
                        """,
                        ids=event_ids,
                    )

                    total_removed += len(event_ids)
                    logger.info(
                        "Neo4j compression: session=%s merged %d nodes into %s",
                        sid, len(event_ids), summary_id,
                    )

                if total_removed > 0:
                    logger.info(
                        "Neo4j compress_old_sessions: total removed=%d "
                        "(was=%d, threshold=%d)",
                        total_removed, current, archive_threshold,
                    )
                return total_removed

        except Exception as exc:
            logger.error("GraphAdapter compress_old_sessions failed: %s", exc)
            return 0

    def close(self) -> None:
        """Closes the connection."""
        if self._driver:
            try:
                self._driver.close()
            except Exception:
                pass
