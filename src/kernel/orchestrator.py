"""kernel_orchestrator module — interface: kernel_orchestration_v1

Decides system inference routing (Gate) and governs cycles.
"""
from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

from src.core.exceptions import BudgetExceededError, CognitiveFailureError
from src.core.logger import setup_logger
from src.core.types import SystemState
from src.core.config import config

if TYPE_CHECKING:
    from src.kernel.state_manager import KernelStateManager
    from src.kernel.mediator import KernelMediator

logger = setup_logger("orchestrator")

class KernelOrchestrator:
    """Responsible for decision-making and governance. Implements the kernel_orchestration_v1 interface."""

    def __init__(
        self,
        state_manager: "KernelStateManager",
        mediator: "KernelMediator"
    ) -> None:
        self._state = state_manager
        self._mediator = mediator
        # Synchronize current SessionID with Mediator
        self._mediator.set_session_id(self._state.session_id)
        
        self._cycle_count: int = 0
        self._cognition_failure_count: int = 0
        self._force_sleep_next_cycle: bool = False
        self._idle_cycle_count: int = 0
        self._idle_mode: str = "DMN"
        self._last_perception_timestamp: Optional[str] = self._state.get_current_state().get("last_perception_timestamp")
        self._goal_last_updated_epoch: float = time.time()
        self._intrinsic_urgency: float = 0.0  # Ω_selfgen: Intrinsic urgency accumulated during IDLE cycles
        self._last_perceptions: list[dict] = []  # Retention of perceived events (for "already read" processing in the next cycle)
        self._last_graph_event_ids: list[str] = []  # Actual event_id existing in Neo4j (for causal links)
        # L0 Scenario 3: Context correction hint passed to next cycle upon contradiction detection (FINDING-003)
        self._pending_contradiction_hint: Optional[dict] = None

    def _get_filtered_session_context(self, session_memory: list[dict], limit: Optional[int] = None) -> list[dict]:
        """Suppresses large amounts of internal thought events from IDLE cycles and prioritizes important external events within limited context.
        Slims down payloads of old internal events based on L1 conceptual design line 185 (dynamic compression).
        """
        if limit is None:
            limit = config.system_context_window_size
            
        return session_memory[-limit:] if limit > 0 else []

    # ------------------------------------------------------------------
    # Internal Phases
    # ------------------------------------------------------------------

    def _phase_collect_perceptions(self, include_idle_entropy: bool = False) -> list[dict]:
        sources = ["CLOCK", "FILE", "USER_INPUT"]
        idle_context: Optional[dict] = None

        if include_idle_entropy:
            sources.append("IDLE_ENTROPY")
            idle_context = {
                "idle_cycle_count": self._idle_cycle_count,
                "goal_last_updated_epoch": self._goal_last_updated_epoch,
                "emotion_mu": self._state.emotion_mu,
            }

        res = self._mediator.route_action("perceptor", "gather_perceptions", {
            "sources": sources,
            "since_timestamp": self._last_perception_timestamp,
            "idle_context": idle_context,
        })
        
        if not res["success"]:
            logger.error("Perceptor failed: %s", res.get("error", "Unknown error"))
            return []

        result = res["result"]
        self._last_perception_timestamp = result.get("timestamp")
        perceptions = result.get("perceptions", [])
        
        # Exclude already processed events (Option 2: Event consumption logic)
        handled_ids = self._state.handled_event_ids
        if handled_ids:
            original_count = len(perceptions)
            perceptions = [p for p in perceptions if p.get("id") not in handled_ids]
            if len(perceptions) < original_count:
                logger.debug("Filtered out %d already handled perceptions", original_count - len(perceptions))

        # Persist perceived events
        # USER_INPUT is also saved to Neo4j as importance=0.8 (>= 0.7).
        # Append while maintaining existing _last_graph_event_ids
        new_event_ids = []
        for p in perceptions:
            if p.get("source") == "USER_INPUT":
                res = self._mediator.route_action("memory", "store_event", {
                    "event_type": "USER_INPUT",
                    "payload": p.get("payload", {}),
                    "importance": 0.8,
                    "session_id": self._state.session_id,
                })
                if res.get("success") and isinstance(res.get("result"), dict):
                    eid = res["result"].get("event_id")
                    if eid:
                        # IMPORTANT: Replace Perceptor's temporary ID with the actual ID on Neo4j
                        p["id"] = eid
                        new_event_ids.append(eid)
        
        self._last_graph_event_ids.extend(new_event_ids)

        # Update internal state after all ID updates are complete (avoids Bug 9)
        self._last_perceptions = perceptions

        return perceptions


    def _phase_idle_cycle(self, idle_duration: float, idle_perceptions: Optional[list[dict]] = None) -> None:
        action_executed_in_idle = False
        
        if self._idle_mode == "DMN":
            logger.info("DMN cycle triggered.")
            # Retrieve Anti-recency
            ar_res = self._mediator.route_action("memory", "query_memory", {"limit": 3, "strategy": "anti_recency"})
            anti_recency_context = ar_res["result"].get("results", []) if ar_res["success"] else []
            
            dmn_response = self._mediator.run_cognition_with_resumption(
                "run_dmn_cycle",
                {
                    "idle_duration": idle_duration,
                    "emotion_mu": self._state.emotion_mu,
                    "value_v": self._state.value_v,
                    "cognitive_load": self._state.cognitive_load,
                    "urgency": self._state.urgency,
                    "anti_recency_context": anti_recency_context,
                },
                temperature=1.0
            )
            # FINDING-005: Expand final_result
            dmn_result = dmn_response.get("final_result") or {}

            # Reflect benefits (DMN)
            if "integration_delta" in dmn_result:
                delta = dmn_result["integration_delta"]
                # Reflect here if DMN results might affect emotions or values
                # Currently primarily adding new perspectives, but preparing for interface extensions
            
            # Goal integration logic
            diverged_thoughts = dmn_result.get("diverged_thoughts", [])
            if diverged_thoughts and isinstance(diverged_thoughts[0], dict):
                goal_delta = diverged_thoughts[0].get("potential_goal_delta", {})
                if goal_delta.get("description") and float(goal_delta.get("priority", 0.0)) >= 0.4:
                    self._state.goal_omega = {
                        "description": goal_delta["description"],
                        "achievement_condition": goal_delta.get("achievement_condition", ""),
                        "progress": float(goal_delta.get("progress", 0.0)),
                        "sub_steps": goal_delta.get("sub_steps", []),
                    }
                    self._goal_last_updated_epoch = time.time()
                    # Ω_selfgen(C): Intrinsic urgency injection — triggers process_cognition in the next cycle
                    self._intrinsic_urgency = max(self._intrinsic_urgency, float(goal_delta.get("priority", 0.3)))
                    logger.info("Ω_selfgen: intrinsic urgency injected=%.2f from DMN goal_delta", self._intrinsic_urgency)
            
            self._idle_mode = "REFLECTION"
        else:
            logger.info("Reflection cycle triggered.")
            # Execute Reflection
            # Retrieve session memory (passing about the latest 20 events as context)
            memory_res = self._mediator.route_action("memory", "get_session_memory", {})
            session_memory = memory_res["result"] if memory_res["success"] else []
            if isinstance(session_memory, list):
                limit = config.system_context_window_size
                session_memory = {"events": self._get_filtered_session_context(session_memory, limit=limit)}

            events = session_memory.get("events", [])
            logger.info("Reflection triggered with %d events in context.", len(events))
            
            # Build verification ID list (session memory IDs only)
            self._last_graph_event_ids = [e.get("id") for e in events if e.get("id")]

            reflection_response = self._mediator.run_cognition_with_resumption(
                "run_reflection_cycle",
                {
                    "session_memory": session_memory,
                    "current_goal": self._state.goal_omega,
                    "capabilities": self._get_capabilities(),
                    "emotion_mu": self._state.emotion_mu,
                    "value_v": self._state.value_v,
                    "cognitive_load": self._state.cognitive_load,
                    "urgency": self._state.urgency,
                },
                temperature=0.45
            )
            # FINDING-005: Expand final_result
            reflection_result = reflection_response.get("final_result") or {}
            
            # (A) omega_meta connection: Ω reflection → goal_omega sub-step integration
            if "omega_meta" in reflection_result:
                omega_meta = reflection_result["omega_meta"]
                if not isinstance(omega_meta, dict):
                    omega_meta = {}
                
                cc = omega_meta.get("consistency_check_result", {})
                if not isinstance(cc, dict):
                    cc = {}
                
                af_alignment = float(cc.get("af_v_alignment", 0.0))
                session_coherence = float(cc.get("session_coherence", 0.0))
                if af_alignment > 0.6 and session_coherence > 0.5:
                    prioritized = omega_meta.get("prioritized_goals", [])
                    emergent = omega_meta.get("emergent_directions", [])
                    if prioritized or emergent:
                        # Integrate into existing sub_steps (append instead of overwrite)
                        current_subs = list(self._state.goal_omega.get("sub_steps", []))
                        existing_descs = {s.get("description", "") for s in current_subs if isinstance(s, dict)}
                        for goal_desc in prioritized:
                            if isinstance(goal_desc, str) and goal_desc not in existing_descs:
                                current_subs.append({
                                    "description": goal_desc,
                                    "achievement_condition": "",
                                    "status": "PENDING",
                                })
                        for direction in emergent:
                            desc = direction if isinstance(direction, str) else str(direction)
                            if desc not in existing_descs:
                                current_subs.append({
                                    "description": desc,
                                    "achievement_condition": "",
                                    "status": "PENDING",
                                })
                        self._state.goal_omega = {
                            **self._state.goal_omega,
                            "sub_steps": current_subs,
                        }
                        self._goal_last_updated_epoch = time.time()
                        # Intrinsic urgency injection: New sub-steps added, process in the next cycle
                        self._intrinsic_urgency = max(self._intrinsic_urgency, 0.3)
                        logger.info(
                            "Ω_selfgen: omega_meta integrated. af_alignment=%.2f session_coherence=%.2f new_subs=%d",
                            af_alignment, session_coherence,
                            len(prioritized) + len(emergent),
                        )
                else:
                    logger.debug(
                        "omega_meta skipped: af_alignment=%.2f session_coherence=%.2f (thresholds: >0.6, >0.5)",
                        af_alignment, session_coherence,
                    )

            # (B-1) Reflect internal_state_delta (Proposal: Correct goals, emotions, etc.)
            if "internal_state_delta" in reflection_result:
                delta = reflection_result["internal_state_delta"]
                if "goal_omega" in delta:
                    self._state.goal_omega = delta["goal_omega"]
                    self._goal_last_updated_epoch = time.time()
                if "urgency" in delta:
                    self._state.urgency = float(delta.get("urgency", self._state.urgency))
                if "emotion_mu" in delta:
                    self._state.emotion_mu = float(delta.get("emotion_mu", self._state.emotion_mu))
                if "value_v" in delta:
                    self._state.value_v = float(delta.get("value_v", self._state.value_v))
                if delta.get("reset_dmn_context") is True:
                    self._state.reset_dmn_context()
                logger.info("Reflection applied internal_state_delta: %s", list(delta.keys()))

            # (B-2) φ_corr: Limited execution of correction_steps
            reflection_delta = reflection_result.get("reflection_delta", {})
            if not isinstance(reflection_delta, dict):
                reflection_delta = {"confidence": 0.0}

            correction_steps = reflection_result.get("correction_steps") or []
            if not isinstance(correction_steps, list):
                correction_steps = []

            reflection_confidence = float(reflection_delta.get("confidence", 0.0))
            if correction_steps and reflection_confidence > 0.7:
                # Relaxed previous constraint of NOTIFY only to allow internal state corrections (excluding destructive external changes)
                safe_steps = [s for s in correction_steps if isinstance(s, dict) and s.get("action_type") not in ("FILE_WRITE", "FILE_DELETE", "EXECUTE_COMMAND")]
                if safe_steps:
                    self._execute_plan({"steps": safe_steps}, reflection_result)
                    logger.info("φ_corr: executed %d safe correction_steps (confidence=%.2f)", len(safe_steps), reflection_confidence)
                    action_executed_in_idle = True

            # MetaCog monitoring
            meta_cog = reflection_delta.get("meta_cog_eval", {})
            contradiction = 0.0
            if isinstance(meta_cog, dict):
                contradiction = float(meta_cog.get("contradiction_rate", 0.0))
            
            if contradiction > 0.3:
                self._force_sleep_next_cycle = True
                
            self._idle_mode = "DMN"
            
        if action_executed_in_idle:
            self._state.cognitive_load += 0.02
        else:
            self._state.cognitive_load += 0.005
            
        self._state.cognitive_load = max(0.0, self._state.cognitive_load)

    def _phase_sleep(self) -> None:
        """SleepPhase (Memory Consolidation & Homeostasis)"""
        logger.info("SleepPhase triggered. Beginning memory consolidation...")
        self._state.update_state(SystemState.SLEEP_PHASE, last_perception_timestamp=self._last_perception_timestamp)
        
        # Collect session memory (retrieve latest ~200 events from Redis for consolidation)
        memory_res = self._mediator.route_action("memory", "query_memory", {
            "limit": config.system_session_memory_keep_count,
            "strategy": None  # Use default (latest session)
        })
        session_memory = memory_res.get("result", {}).get("results", []) if memory_res.get("success") else []
        
        # Update actual ID list for hybrid ID validation
        self._last_graph_event_ids = [e.get("id") for e in session_memory if e.get("id")]
        
        # Insight extraction by cognitive engine (complete LLM interaction via Mediator)
        sleep_response = self._mediator.run_cognition_with_resumption(
            "run_sleep_phase",
            {
                "current_session": {"events": session_memory},
                "cognitive_load": self._state.cognitive_load,
                "emotion_mu": self._state.emotion_mu,
                "value_v": self._state.value_v,
                "urgency": self._state.urgency,
                "capabilities": self._get_capabilities()
            }
        )
        
        # Success criteria: final_result exists and no error_code is present
        cognition_success = True
        sleep_result = sleep_response.get("final_result") or {}
        if not sleep_result or "error_code" in sleep_result:
            error_msg = sleep_result.get("message", "Cognition failed during SleepPhase")
            logger.error("SleepPhase cognition failure: %s. Proceeding with prune-only.", error_msg)
            sleep_result = {}
            cognition_success = False

        # (1) Prepare for memory consolidation (generate enriched data to fix key_insights into LTM)
        delta = sleep_result.get("consolidated_memory_delta") or {}
        key_insights = delta.get("key_insights") or []
        
        enriched_insights = []
        if key_insights:
            logger.info("SleepPhase: Consolidating %d key insights to LTM.", len(key_insights))
            for insight in key_insights:
                try:
                    if isinstance(insight, dict):
                        content = insight.get("content") or insight.get("payload", {}).get("content", str(insight))
                        raw_links = insight.get("causal_links", [])
                    else:
                        content = str(insight)
                        raw_links = []

                    if not isinstance(raw_links, list):
                        raw_links = [str(raw_links)] if raw_links else []
                    
                    # Only use verified existing links (exclude hallucinations)
                    causal_links = [str(cid) for cid in raw_links if cid in self._last_graph_event_ids]

                    embed_res = self._mediator.route_action("llm_gateway", "generate_embedding", {"text": content})
                    embedding = embed_res["result"].get("embedding") if (embed_res and embed_res.get("success")) else None
                    
                    enriched = {
                        "content": content,
                        "causal_links": causal_links,
                        "embedding": embedding
                    }
                    enriched_insights.append(enriched)
                except Exception as insight_exc:
                    logger.warning("SleepPhase: Skipping a malformed insight: %s", insight_exc)
        
        # (2) Execute memory consolidation and pruning
        prune_success = False
        try:
            self._mediator.route_action("memory", "consolidate_memory", {
                "session_id": self._state.session_id,
                "abstraction_level": 0.5,
                "insights": enriched_insights,
                "keep_count": config.system_session_memory_keep_count
            })
            prune_success = True
        except Exception as cons_exc:
            logger.error("SleepPhase: consolidate_memory failed: %s. Load will NOT be reset.", cons_exc)

        # (3) Save dreams (simulation results)
        dream_results = sleep_result.get("dream_simulation_results", [])
        if dream_results:
            logger.info("SleepPhase: Dream simulation results: %s", dream_results)
            self._mediator.route_action("memory", "store_event", {
                "event_type": "dream_result",
                "payload": {"simulations": dream_results},
                "importance": 0.4,
                "session_id": self._state.session_id,
            })

        # Value optimization (maintain numeric-based structure)
        if "optimized_value_form" in sleep_result:
            ovf = sleep_result["optimized_value_form"]
            if isinstance(ovf, dict) and "score" in ovf:
                # Avoid crash due to float(None)
                new_score = ovf.get("score")
                new_delta = ovf.get("delta")
                self._state.value_form_v = {
                    "score": float(new_score if new_score is not None else self._state.value_v),
                    "delta": float(new_delta if new_delta is not None else 0.0)
                }
                logger.info("SleepPhase: value_form optimized (numeric).")
            else:
                logger.warning("SleepPhase: optimized_value_form format invalid, skipping update.")
            
        # Completion criteria: Reset load only if both pruning and insight extraction (cognition) were successful
        if prune_success and cognition_success:
            logger.info("SleepPhase: Homeostasis maintained and insights consolidated. Cognitive load reset.")
            self._state.cognitive_load = 0.0
            self._idle_cycle_count = 0
            self._state.refresh_session_id()
            self._mediator.set_session_id(self._state.session_id)
            self._state.update_state(SystemState.IDLE, last_perception_timestamp=self._last_perception_timestamp)
        else:
            if not cognition_success:
                logger.warning("SleepPhase: Insights extraction failed. Load will be kept high for retry.")
            self._state.update_state(SystemState.IDLE, last_perception_timestamp=self._last_perception_timestamp)
            self._idle_cycle_count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_cycle(self, context: Optional[dict] = None) -> dict:
        """L2: kernel_orchestration_v1.execute_cycle implementation

        output_schema (kernel_orchestration_v1.yaml):
          { outcome: string, cycle_id: string, summary: { mode_selected, perception_urgency,
            intrinsic_urgency, cognitive_load, actions_executed, sleep_triggered } }
        error_schema: CYCLE_TIMEOUT | GATE_ERROR | COGNITION_FAILED | BUDGET_SUSPENDED
        """
        self._cycle_count += 1
        cycle_id = f"cycle_{self._cycle_count}"
        logger.info("=== execute_cycle START: %s state=%s ===", cycle_id, self._state.system_state)

        mode_selected: str = "IDLE"
        actions_executed: bool = False
        sleep_triggered: bool = False
        perception_urgency: float = 0.0

        try:
            # Safety mechanism: Forced sleep
            if self._force_sleep_next_cycle:
                try:
                    self._phase_sleep()
                    sleep_triggered = True
                except Exception as force_sleep_exc:
                    logger.error("Forced SleepPhase failure: %s. Resetting flag to avoid crash loop.", force_sleep_exc)
                    self._state.update_state(SystemState.IDLE, last_perception_timestamp=self._last_perception_timestamp)
                finally:
                    self._force_sleep_next_cycle = False

                if sleep_triggered:
                    self._state.save_state()
                    return {
                        "outcome": "SLEEP_TRIGGERED",
                        "cycle_id": cycle_id,
                        "summary": {
                            "mode_selected": "SLEEP_PHASE",
                            "perception_urgency": 0.0,
                            "intrinsic_urgency": self._intrinsic_urgency,
                            "cognitive_load": self._state.cognitive_load,
                            "actions_executed": False,
                            "sleep_triggered": True,
                        },
                    }

            # 1. Collect perceptions
            perceptions = self._phase_collect_perceptions()
            perception_urgency = max([p.get("urgency", 0.0) for p in perceptions]) if perceptions else 0.0
            # (C) Ω_selfgen: Integration of intrinsic urgency and perceptual urgency
            self._state.urgency = max(perception_urgency, self._intrinsic_urgency)
            selfgen_triggered = False
            if self._intrinsic_urgency > 0.0:
                logger.info("Ω_selfgen: intrinsic_urgency=%.2f merged with perception_urgency=%.2f -> urgency=%.2f",
                            self._intrinsic_urgency, perception_urgency, self._state.urgency)
                selfgen_triggered = True
                self._intrinsic_urgency = 0.0  # Consumed

            # 2. Gate determination
            if self._state.urgency == 0.0:
                self._idle_cycle_count += 1
                self._phase_idle_cycle(float(self._idle_cycle_count))
                mode_selected = "IDLE"
            else:
                if perception_urgency > 0.0:
                    if self._idle_cycle_count >= (config.system_idle_cycles_for_sleep / 2):
                        logger.info("Returned from long idle (cycles=%d). Refreshing session_id.", self._idle_cycle_count)
                        self._state.refresh_session_id()
                        self._mediator.set_session_id(self._state.session_id)
                    self._idle_cycle_count = 0
                else:
                    self._idle_cycle_count += 1
                    
                if self._state.urgency > config.cognitive_urgency_threshold_fast:
                    mode_selected = "FAST"
                    self._state.update_state(SystemState.PROCESSING_SYSTEM1, last_perception_timestamp=self._last_perception_timestamp)
                else:
                    mode_selected = "SLOW"
                    self._state.update_state(SystemState.PROCESSING_SYSTEM2, last_perception_timestamp=self._last_perception_timestamp)
                
                self._state.cognitive_load += 0.02

                # 3. Execute cognition
                memory_res = self._mediator.route_action("memory", "get_session_memory", {})
                session_memory = memory_res["result"] if memory_res["success"] else {"events": []}
                if isinstance(session_memory, list):
                    limit = config.system_context_window_size
                    session_memory = {"events": self._get_filtered_session_context(session_memory, limit=limit)}

                # Build verification ID list (perception IDs + session memory IDs)
                session_ids = [e.get("id") for e in session_memory.get("events", []) if e.get("id")]
                perception_ids = [p["id"] for p in perceptions if p.get("id")]
                self._last_graph_event_ids = list(set(session_ids + perception_ids))

                # Scenario 3: Inject contradiction resolution hint (FINDING-003 / Addressing L0 Scenario 3)
                contradiction_hint: Optional[dict] = getattr(self, "_pending_contradiction_hint", None)
                cognition_params: dict = {
                    "perceptions": perceptions,
                    "current_internal_state": {
                        "system_state": self._state.system_state.value,
                        "cognitive_load": self._state.cognitive_load,
                        "urgency": self._state.urgency,
                        "emotion_mu": self._state.emotion_mu,
                        "value_v": self._state.value_v,
                        "goal_omega": self._state.goal_omega,
                    },
                    "session_memory": session_memory,
                    "system_mode": mode_selected,
                    "capabilities": self._get_capabilities(),
                    "emotion_flow_mu": self._state.emotion_flow_mu,
                    "value_form_v": self._state.value_form_v,
                    "selfgen_triggered": selfgen_triggered,
                }
                if contradiction_hint:
                    cognition_params["contradiction_resolution_hint"] = contradiction_hint
                    self._pending_contradiction_hint = None
                    logger.info("Injecting contradiction_resolution_hint into next cognition cycle.")

                cognition_response = self._mediator.run_cognition_with_resumption(
                    "process_cognition",
                    cognition_params,
                )
                logger.info("Cognition triggered with emotion_flow_mu=%s, value_form_v=%s",
                            self._state.emotion_flow_mu, self._state.value_form_v)
                # FINDING-005: Expand final_result
                cognition_result = cognition_response.get("final_result") or {}

                # Manage cognition failure count (FINDING-002 / Addressing L0 Scenario 2)
                if not cognition_result or ("plan" not in cognition_result and "internal_state_delta" not in cognition_result):
                    self._cognition_failure_count += 1
                    logger.warning(
                        "Cognition produced no valid result: failure_count=%d", self._cognition_failure_count
                    )
                    # FINDING-REPAIR-003: Report as immediate failure
                    if self._cognition_failure_count < 3:
                        self._state.save_state()
                        return {
                            "outcome": "COGNITION_FAILED",
                            "cycle_id": cycle_id,
                            "summary": {
                                "mode_selected": mode_selected,
                                "perception_urgency": perception_urgency,
                                "intrinsic_urgency": self._intrinsic_urgency,
                                "cognitive_load": self._state.cognitive_load,
                                "actions_executed": False,
                                "sleep_triggered": False,
                            },
                        }
                else:
                    self._cognition_failure_count = 0  # Reset on success

                if self._cognition_failure_count >= 3:
                    logger.error(
                        "L0 scenario-2: 3 consecutive cognition failures detected. "
                        "Resetting state to IDLE/SLEEP."
                    )
                    self._cognition_failure_count = 0
                    # High load: SLEEP_PHASE; Otherwise: Reset to IDLE
                    if self._state.cognitive_load > (config.cognitive_load_max * 0.6):
                        self._phase_sleep()
                        sleep_triggered = True
                    else:
                        self._state.update_state(SystemState.IDLE, last_perception_timestamp=self._last_perception_timestamp)
                    # User intervention notification (via actor.execute_actions(NOTIFY))
                    self._mediator.route_action("actor", "execute_actions", {
                        "actions": [{
                            "action_type": "NOTIFY",
                            "params": {"message": "[NARV] Cognition loop failed to converge. Resetting system (appending error fact to memory)."},
                        }]
                    })
                    # Feedback to self (storing facts in Working Memory)
                    self._mediator.route_action("memory", "store_event", {
                        "event_type": "SYSTEM_FEEDBACK",
                        "payload": {
                            "message": "The previous cycle failed three consecutive times due to JSON generation failure (likely exceeding character limit), and the system performed a forced reset. In future thoughts, keep character counts down and aim for a concise JSON structure that ensures completion."
                        },
                        "importance": 0.9,
                    })
                    self._state.save_state()
                    return {
                        "outcome": "COGNITION_FAILED",
                        "cycle_id": cycle_id,
                        "summary": {
                            "mode_selected": "SLEEP_PHASE" if sleep_triggered else mode_selected,
                            "perception_urgency": perception_urgency,
                            "intrinsic_urgency": 0.0,
                            "cognitive_load": self._state.cognitive_load,
                            "actions_executed": False,
                            "sleep_triggered": sleep_triggered,
                        },
                    }

                # Reflect benefits
                if "internal_state_delta" in cognition_result:
                    delta = cognition_result["internal_state_delta"]
                    self._state.urgency = float(delta.get("urgency", self._state.urgency))

                if "emotion_flow_mu" in cognition_result:
                    self._state.emotion_flow_mu = cognition_result["emotion_flow_mu"]

                if "value_form_v" in cognition_result:
                    self._state.value_form_v = cognition_result["value_form_v"]

                if "goal_omega" in cognition_result:
                    self._state.goal_omega = cognition_result["goal_omega"]
                    self._goal_last_updated_epoch = time.time()

                # Scenario 3: Detection of contradiction with memory → Set context injection hint for next cycle
                # (FINDING-003 / L0_Core.md L50-51)
                contradiction_delta = cognition_result.get("internal_state_delta", {}).get("contradiction_detected")
                if contradiction_delta:
                    logger.warning(
                        "L0 scenario-3: contradiction detected. Scheduling memory-priority hint for next cycle."
                    )
                    mem_res = self._mediator.route_action("memory", "query_memory",
                                                          {"limit": 5, "strategy": "recency"})
                    truth_source = mem_res.get("result", {}).get("results", []) if mem_res.get("success") else []
                    self._pending_contradiction_hint = {
                        "memory_is_source_of_truth": True,
                        "conflicting_state": contradiction_delta,
                        "truth_from_memory": truth_source,
                    }

                # 4. Execution
                if "plan" in cognition_result:
                    self._execute_plan(cognition_result["plan"], cognition_result)
                    actions_executed = True
                    self._state.cognitive_load += 0.05  # Action execution load
                else:
                    self._state.cognitive_load += 0.02  # Thought-only load

                # 5. Register consumed events (executed regardless of success or failure)
                for p in self._last_perceptions:
                    if p.get("id"):
                        self._state.add_handled_event_id(p["id"])

                if self._last_perceptions:
                    logger.debug("Events marked as handled: %s", [p.get("id") for p in self._last_perceptions])

                steps = (cognition_result.get("plan") or {}).get("steps") or []
                if any(s.get("action_type") == "NOTIFY" for s in steps):
                    self._state.urgency = 0.0
                    self._intrinsic_urgency = 0.0
                    logger.info("Urgency and intrinsic_urgency full reset after user notification.")



            # Sleep determination (high load or idle limit exceeded)
            if self._state.cognitive_load > config.cognitive_load_max or self._idle_cycle_count >= config.system_idle_cycles_for_sleep:
                if self._idle_cycle_count >= config.system_idle_cycles_for_sleep:
                    logger.info("Periodic maintenance triggered by idle_cycle_count=%d", self._idle_cycle_count)
                try:
                    self._phase_sleep()
                    sleep_triggered = True
                except Exception as sleep_exc:
                    logger.error("SleepPhase CRITICAL FAILURE: %s. Reverting to IDLE for safety.", sleep_exc)
                    self._state.update_state(SystemState.IDLE, last_perception_timestamp=self._last_perception_timestamp)
                    # L1 compliance: Load is not lowered if sleep fails. Only idle count is reset to avoid immediate loops.
                    self._idle_cycle_count = 0

            # Persist state
            self._state.save_state()

            return {
                "outcome": "SUCCESS",
                "cycle_id": cycle_id,
                "summary": {
                    "mode_selected": mode_selected,
                    "perception_urgency": perception_urgency,
                    "intrinsic_urgency": self._intrinsic_urgency,
                    "cognitive_load": self._state.cognitive_load,
                    "actions_executed": actions_executed,
                    "sleep_triggered": sleep_triggered,
                },
            }

        # FINDING-001: BudgetExceededError → SUSPENDED transition (L0_Core.md L43)
        except BudgetExceededError as budget_exc:
            logger.error("L0 scenario-1: BudgetExceededError received. Transitioning to SUSPENDED. detail=%s",
                         budget_exc)
            self._state.update_state(SystemState.SUSPENDED, last_perception_timestamp=self._last_perception_timestamp)
            self._state.save_state()
            return {
                "outcome": "BUDGET_SUSPENDED",
                "cycle_id": cycle_id,
                "error_code": "BUDGET_SUSPENDED",
                "message": str(budget_exc),
                "summary": {
                    "mode_selected": mode_selected,
                    "perception_urgency": perception_urgency,
                    "intrinsic_urgency": self._intrinsic_urgency,
                    "cognitive_load": self._state.cognitive_load,
                    "actions_executed": False,
                    "sleep_triggered": False,
                },
            }
        except Exception as exc:
            logger.error("Error in execute_cycle: %s", exc)
            # FINDING-REPAIR-002: L2 compliance. Map to GATE_ERROR or COGNITION_FAILED
            error_code = "GATE_ERROR"
            if "cognition" in str(exc).lower() or "llm" in str(exc).lower():
                error_code = "COGNITION_FAILED"
            
            self._state.save_state()
            
            return {
                "outcome": error_code,
                "cycle_id": cycle_id,
                "error_code": error_code,
                "message": str(exc),
                "summary": {
                    "mode_selected": mode_selected,
                    "perception_urgency": perception_urgency,
                    "intrinsic_urgency": self._intrinsic_urgency,
                    "cognitive_load": self._state.cognitive_load,
                    "actions_executed": False,
                    "sleep_triggered": False,
                },
            }

    def _execute_plan(self, plan: dict, cognition_result: dict) -> None:
        """Executes the action plan and persists results and notifications."""
        steps = plan.get("steps") or []
        if not steps:
            return

        # Calculate causal links (hybrid method) before executing actions
        # (A) _last_graph_event_ids: Actual Neo4j IDs confirmed by Orchestrator (USER_INPUT for this cycle)
        # (B) cognition_result["causal_links"]: Event ID groups from the past declared as "affected" by the LLM
        llm_causal_links: list[str] = cognition_result.get("causal_links", []) or []
        if not isinstance(llm_causal_links, list):
            llm_causal_links = []
        llm_causal_links = [cid for cid in llm_causal_links if isinstance(cid, str) and cid]

        # Only use verified existing links (exclude hallucinations)
        causal_links: list[str] = [cid for cid in llm_causal_links if cid in self._last_graph_event_ids]
        
        if llm_causal_links and not causal_links:
            logger.warning("All LLM-suggested causal_links were hallucinated and filtered out: %s", llm_causal_links)
        
        logger.debug("causal_links filtered: %d confirmed / %d original", len(causal_links), len(llm_causal_links))

        # Inject causal links into each action
        for step in steps:
            action_type = step.get("action_type")
            if action_type == "STORE":
                params = step.get("params", {})
                store_params = params.get("STORE_PARAMS", {})
                
                # Retrieve and merge existing action-specific links
                existing_links = store_params.get("causal_links", [])
                if not isinstance(existing_links, list):
                    existing_links = [str(existing_links)] if existing_links else []
                
                final_links = list(causal_links)
                seen_final = set(final_links)
                for elink in existing_links:
                    if elink not in seen_final:
                        final_links.append(elink)
                        seen_final.add(elink)
                
                store_params["causal_links"] = final_links
                params["STORE_PARAMS"] = store_params
                step["params"] = params

        # Execute actions (causal links injected)
        exec_res = self._mediator.route_action("actor", "execute_actions", {"actions": steps})
        
        actor_result = exec_res.get("result", {}) if exec_res.get("success") else {}
        execution_status = actor_result.get("execution_result", "FAILED") if isinstance(actor_result, dict) else "FAILED"


        # Record events
        self._mediator.route_action("memory", "store_event", {
            "event_type": "action_result",
            "payload": {"plan": plan, "result": execution_status},
            "importance": 0.6,
            "session_id": self._state.session_id,
            "causal_links": causal_links if causal_links else None,
        })
        
        # Record NOTIFY actions as SYSTEM_NOTIFY events for Dashboard display
        for action in steps:
            if action.get("action_type") == "NOTIFY":
                self._mediator.route_action("memory", "store_event", {
                    "event_type": "SYSTEM_NOTIFY",
                    "payload": action.get("params", {}),
                    "importance": 0.7,
                    "session_id": self._state.session_id,
                    "causal_links": causal_links if causal_links else None,
                })

    def _get_capabilities(self) -> dict:
        """Retrieves capabilities from the Actor."""
        res = self._mediator.route_action("actor", "get_capabilities", {})
        return res["result"] if res["success"] else {}

    def start(self, max_cycles: Optional[int] = None) -> dict:
        """L2: kernel_orchestration_v1.start implementation

        output_schema (kernel_orchestration_v1.yaml):
          { total_cycles_executed: number, final_state: string }
        error_schema: STARTUP_FAILURE | LOOP_ABORTED
        """
        logger.info("KernelOrchestrator started. max_cycles=%s", max_cycles)
        cycle = 0
        try:
            while True:
                if max_cycles is not None and cycle >= max_cycles:
                    break
                if self._state.system_state == SystemState.SUSPENDED:
                    time.sleep(60)
                    continue

                self.execute_cycle()
                cycle += 1
                time.sleep(config.system_cycle_interval_seconds)

            return {
                "total_cycles_executed": cycle,
                "final_state": self._state.system_state.value,
            }
        except KeyboardInterrupt:
            logger.info("KernelOrchestrator: loop aborted by signal. cycles=%d", cycle)
            return {
                "total_cycles_executed": cycle,
                "final_state": self._state.system_state.value,
            }
        except Exception as exc:
            logger.error("KernelOrchestrator start() failed: %s", exc)
            return {
                "total_cycles_executed": cycle,
                "final_state": self._state.system_state.value,
                "error_code": "LOOP_ABORTED",
                "message": str(exc),
            }
