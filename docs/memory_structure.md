### 1. Memory Structure (Hierarchical Hybrid Memory Architecture)

Memory is classified into the following three layers based on speed and characteristics:

* **Working Memory**: Uses Redis. Retains the short-term history of the current session.
* **Long-Term Memory**: Uses ChromaDB (Vector DB). Stores high-importance events and summarized perceptions, enabling vector similarity searches.
* **Structural Memory**: Uses Neo4j (Graph DB). Maintains causal relationships and structural connections between events.

```mermaid
graph TD
    subgraph Memory_System [Hybrid Memory System]
        A[Memory Facade] --> B[(Redis: Working Memory)]
        A --> C[(ChromaDB: Long-Term Memory)]
        A --> D[(Neo4j: Structural Memory)]
    end

    B --- B1[Recent Events / Session History]
    C --- C1[Important Events / Vector Similarity Search]
    D --- D1[Causal Relations / Graph Inference / Knowledge Structure]

    style A fill:#f9f,stroke:#333,stroke-width:2px
    style B fill:#e1f5fe,stroke:#01579b
    style C fill:#fff3e0,stroke:#e65100
    style D fill:#f1f8e9,stroke:#33691e
```

---

### 2. Memory Operations in Each Cognitive Phase

During each phase (System 1/2, DMN, Reflection, Sleep), the memory modules are accessed using different strategies.

#### Summary of Memory Operations

| Cognitive Phase | Primary Memory Operations | Description of Operations |
| :--- | :--- | :--- |
| **System 1 (FAST)** | `get_recent` (Redis) / `store_event` | Refers only to recent history and writes results immediately. |
| **System 2 (SLOW)** | `query_memory` (Hybrid) / `store_event` | Recalls related memories via vector/keyword search and records details comprehensively. |
| **DMN (Divergent)** | `query_memory` (anti_recency) | Intentionally extracts the "oldest memories" randomly to diversify thoughts. |
| **Reflection** | `get_session_memory` (Redis) | Reviews recent action history to find contradictions or perform self-correction. |
| **Sleep (Consolidation)** | `consolidate_memory` | Abstracts session memory, anchors insights into long-term and graph memory, and prunes Redis. |

#### Interaction Flow Between Cognitive Phases and Memory

```mermaid
sequenceDiagram
    participant C as Cognitive Engine
    participant K as Kernel / Mediator
    participant M as Memory (Facade)
    participant R as Redis (Working)
    participant V as Vector (Long-Term)
    participant G as Graph (Structural)

    Note over C, G: --- Normal Reasoning (System 1/2) ---
    %% 修正点: Kernelからの明確なトリガーと、メモリ要請の往復を明記
    K->>C: process_cognition (Trigger Reasoning)
    C->>K: memory_requests (Request context)
    K->>M: query_memory (Hybrid/Recent)
    M-->>K: Return related context
    K->>C: Provide context (Resumption)
    C-->>K: Return Generated Plan & State Delta
    
    K->>M: store_event (Record reasoning/action results)
    M->>R: Always save
    M->>V: Save if importance >= 0.7
    M->>G: Save if causal links exist / importance >= 0.5

    Note over C, G: --- DMN (Divergent Thinking) ---
    K->>M: query_memory (strategy: anti_recency)
    M->>V: Randomly extract old memories
    V-->>M: Recalled past events
    M-->>K: Seeds for free association
    K->>C: run_dmn_cycle (Trigger DMN)
    C-->>K: Return diverged thoughts

    Note over C, G: --- Sleep Phase (Consolidation) ---
    K->>C: run_sleep_phase (Trigger insight extraction)
    C-->>K: Return key_insights
    K->>M: consolidate_memory
    M->>V: Save insights to Long-Term Memory
    M->>G: Integrate as structural/causal links
    M->>R: Prune session memory
```

### Supplementary: Specific Roles and Operations of Each Storage
* **Redis**: Constantly written via `store_event` and used to maintain the current context via `get_recent`.
* **ChromaDB**: Targeted for saving events with an `importance` of 0.7 or higher, and is also used for unearthing old memories via the `anti_recency` strategy.
* **Neo4j**: Structurally saves events when `causal_links` are present. Used for causal exploration via the `graph_traversal` strategy.
