"""cognitive_engine module — interface: cognitive_engine_v1

Reasoning pipeline based on the PBCA/AF framework.
- process_cognition: System 1 / System 2 reasoning, LLM/Memory request protocol
- run_dmn_cycle: Diversified thought simulation during idle time
- run_reflection_cycle: Convergent reflection during idle time
- run_sleep_phase: Memory consolidation and dream simulation
- State persistence is not performed (output as requests to the memory module)
"""
from __future__ import annotations

import json
import uuid
import logging
from typing import Any, Optional

from src.core.logger import setup_logger
from src.core.config import config

logger = setup_logger("cognitive_engine")


class CognitiveEngine:
    """Reasoning pipeline of the PBCA/AF framework. Implements the cognitive_engine_v1 interface."""

    def __init__(self) -> None:
        logger.info("CognitiveEngine initialized.")

    # ------------------------------------------------------------------
    # Internal Helpers: Prompt Generation (Stable Version Compliant)
    # ------------------------------------------------------------------
    def _apply_aliasing(self, events: list[dict]) -> tuple[str, dict[str, str]]:
        """Assigns aliases in REF-XXX format to the event list and generates a mapping."""
        alias_map = {}
        aliased_output = []
        for i, ev in enumerate(events):
            alias = f"REF-{i+1:03d}"
            event_id = ev.get("id") or ev.get("event_id") or "unknown"
            alias_map[alias] = event_id
            aliased_ev = {
                "alias": alias,
                "event_type": ev.get("event_type"),
                "payload": ev.get("payload")
            }
            aliased_output.append(aliased_ev)
        return json.dumps(aliased_output, ensure_ascii=False, indent=2), alias_map
    def _format_session_context(self, session_memory: dict) -> str:
        """Formats past events into a pure JSON list format (stable version compliant)."""
        events = session_memory.get("events", [])
        if not events:
            return "[]"
        simplified = []
        for ev in events:
            simplified.append({
                "event_id": ev.get("event_id"),
                "event_type": ev.get("event_type"),
                "payload": ev.get("payload")
            })
        return json.dumps(simplified, ensure_ascii=False, indent=2)

    def _build_plan_prompt(
        self,
        perceptions_str: str,
        current_internal_state: dict,
        session_str: str,
        system_mode: str,
        capabilities: dict,
        emotion_flow_mu: Optional[dict] = None,
        value_form_v: Optional[dict] = None,
        selfgen_triggered: bool = False,
        memory_results: Optional[list[dict]] = None,
    ) -> str:
        """Stage 1: Plan Generation (compliant with prompt_cognition.txt)"""
        state_snapshot = {
            "internal_state": current_internal_state,
            "emotion_mu": emotion_flow_mu.get("value", 0.0) if emotion_flow_mu else 0.0,
            "value_v": value_form_v.get("score", 0.5) if value_form_v else 0.5,
        }
        state_str = json.dumps(state_snapshot, ensure_ascii=False, indent=2)
        capabilities_str = json.dumps(capabilities, ensure_ascii=False, indent=2)
        mu_str = json.dumps(emotion_flow_mu, ensure_ascii=False, indent=2) if emotion_flow_mu else "null"
        v_str = json.dumps(value_form_v, ensure_ascii=False, indent=2) if value_form_v else "null"
        mode_desc = "deliberative (System 2)" if system_mode == "SLOW" else "intuitive (System 1)"

        return f"""You are an advanced cognitive engine based on the PBCA/AF framework. Operating in {mode_desc} mode.

## PBCA/AF Core Concepts
1. **Cognitive_Frame_Xi (ξ₀)**: Interpret situations using Perspective, Jump, Coherence, Mapping, and Reflect as reasoning primitives.
2. **Σ_env**: Infer not just facts from environment events, but also causal context (Σ_causal) and others' intent (Σ_others / ToM).
3. **ReasoningStab**: Apply ConsistencyCheck + CausalInfer + Repair + Reflect to every plan. Always output `meta_cog_eval` and `causal_infer_metrics`.
4. **Π_meta**: Integrate multiple parallel scenarios (Π_multi), autonomous goals, and emergent tasks into a single unified plan.
5. **SelfModel**: Model yourself as a consistent autonomous agent (RefRole); maintain meta-narrative coherence across time (MetaNarrate).
6. **Goal_Omega (Ω)**: Build a deep goal structure beyond immediate tasks:
   - **Ω_latent/Ω_emergent**: Undifferentiated potentials and divergent motivations from DMN/play
   - **Ω_autonomous/Ω_unique**: Consistent self-driven goals grounded in Σ_self
   - **Ω_adaptive/Ω_reflective**: Adaptation to environment and self-improvement via MetaCog
   Reflect these in `goal_omega.description` and `sub_steps` to ensure long-term coherence (PlanPersist).
   **Progress evaluation (mandatory)**: Each cycle, autonomously assess whether `goal_omega.progress` (0.0–1.0) and `achievement_condition` are consistent with current reality, and update them accordingly.

## Capabilities (Available Actions)
Only the following predefined actions may be executed. Actions such as IMPLEMENT or TRANSITION do not exist.
**IMPORTANT: Always use the exact parameter key structure defined in `action_schemas` (e.g., `FILE_WRITE_PARAMS`, `COMMAND_EXEC_PARAMS`) within `params`.**
```json
{capabilities_str}
```

## Action Selection Rules (Strict)
1. **History vs. Now**: "Session memory" is past history. "Environment events" are what you must act on right now.
2. **Alias refs**: Use provided `alias` (REF-XXX) in `causal_links`. Never output raw UUIDs.
3. **Act vs. Silence**:
   - Default is silence (`plan.steps: []`) when: no new `USER_INPUT` in env events AND no PENDING/IN_PROGRESS `sub_steps` in Goal_Omega.
   - **If Goal_Omega has incomplete `sub_steps`**: do NOT be silent — actively plan and execute actions (FILE_WRITE, COMMAND_EXEC, etc.) to advance them. Update each `sub_step.status` (IN_PROGRESS / COMPLETED / FAILED) as you proceed.
   - **If `selfgen_triggered=true`**: do NOT be silent even if `sub_steps` is empty. This means a goal was autonomously generated from DMN/Reflection. Plan concrete `sub_steps` to achieve the current `goal_omega.description` and execute the first one (FILE_WRITE, COMMAND_EXEC, etc.).
   - Spontaneous `NOTIFY(recipient=USER)` is allowed only when there is a critical correction to past responses or urgent info to share. Sending your own thought process (monologue) or already-handled content to the user is strictly prohibited.
4. **No placeholders**: Respond substantively immediately. Never reply with "thinking..." or "processing..." stubs.

## Current Internal State
```json
{state_str}
```

## Session Memory (past history)
```json
{session_str}
```

## Environment Events (act on these now)
```json
{perceptions_str}
```

## Task
Output ONLY valid JSON in the following schema. Use structured actions with explicit recipients in `plan.steps`.

Output example (JSON):
{{
  "plan": {{
    "steps": [
      {{ "action_type": "NOTIFY", "params": {{ "NOTIFY_PARAMS": {{ "recipient": "USER", "message": "(only when the user needs it) appropriate response" }} }} }},
      {{ "action_type": "FILE_WRITE", "params": {{ "FILE_WRITE_PARAMS": {{ "target_path": "example.txt", "content": "file content" }} }} }}
    ],
    "rationale": "Analyzed the user's intent and constructed the necessary actions according to the Capabilities action_schemas."
  }},
  "internal_state_delta": {{
    "cognitive_load_delta": 0.1,
    "urgency": 0.5
  }},
  "emotion_flow_mu": {{
    "value": 0.2,
    "delta": 0.05
  }},
  "value_form_v": {{
    "score": 0.7,
    "delta": 0.1
  }},
  "goal_omega": {{
    "description": "current goal",
    "achievement_condition": "achievement condition",
    "progress": 0.3,
    "sub_steps": [
      {{
        "description": "sub-goal details",
        "achievement_condition": "achievement criteria",
        "status": "PENDING"
      }}
    ]
  }},
  "meta_cog_eval": {{
    "confidence": 0.9,
    "contradiction_rate": 0.05,
    "alternatives": ["alternative interpretation"],
    "self_consistency": 0.95
  }},
  "causal_infer_metrics": {{
    "causal_graph_consistency": 0.9,
    "counterfactual_alternatives_count": 2,
    "reasoning_stab_confidence": 0.92
  }},
  "causal_links": ["REF-001", "REF-002"]
}}

## Supplementary Context
- **emotion_flow_mu**: {mu_str}
- **value_form_v**: {v_str}
- **selfgen_triggered**: {selfgen_triggered}

Output ONLY valid JSON."""

    def _build_dmn_prompt(
        self,
        idle_duration: float,
        emotion_mu: float = 0.0,
        value_v: float = 0.5,
        cognitive_load: float = 0.0,
        urgency: float = 0.0,
        recency_str: str = "[]",
    ) -> str:
        """Prompt for the DMN cycle (compliant with prompt_dmn.txt)"""
        recency_section = f"Recall of oldest memories: {recency_str}"
        
        return f"""You are operating in Default Mode Network (DMN) mode.
Idle duration: {idle_duration:.1f}s

## Current Internal State (reasoning foundation)
- emotion_mu (emotional valence): {emotion_mu:.3f}  (-1.0=unstable, 0.0=neutral, +1.0=stable)
- value_v (value form): {value_v:.3f}  (0.0=low, 0.5=neutral, 1.0=high)
{recency_section}
Based on the current internal state, think freely about unexplored possibilities and potential new goals, unconstrained by current objectives.
If emotionally unstable (low emotion_mu), also consider goals oriented toward stabilization.

Output in the following JSON format:
{{
  "diverged_thoughts": [
    {{
      "thought_id": "unique_id",
      "content": "thought content",
      "potential_goal_delta": {{"description": "potential new goal", "priority": 0.5, "category": "Ω_latent"}}
    }}
  ],
  "integration_delta": {{
    "new_perspectives": ["perspective 1", "perspective 2"],
    "confidence": 0.7
  }},
  "causal_links": ["REF-XXX"]
}}"""

    def _build_reflection_prompt(
        self,
        session_str: str,
        current_goal: dict,
        capabilities: dict,
        emotion_mu: Optional[float] = None,
        value_v: Optional[float] = None,
        cognitive_load: Optional[float] = None,
        urgency: Optional[float] = None
    ) -> str:
        """Prompt for the Reflection cycle (compliant with prompt_reflection.txt)"""
        capabilities_str = json.dumps(capabilities, ensure_ascii=False, indent=2)
        goal_str = json.dumps(current_goal, ensure_ascii=False, indent=2)
        mu_val = emotion_mu if emotion_mu is not None else 0.0
        v_val = value_v if value_v is not None else 0.5
        
        return f"""You are operating in Reflection Mode (convergent introspection mode).
Review past dialogue and action history from multiple angles, and verify consistency with goals and whether the user's underlying intent has changed.

## Current Internal State
- emotion_mu (emotional valence): {mu_val}
- value_v (value form): {v_val}

## Capabilities (Available Actions)
**IMPORTANT: When planning actions, always use the exact parameter structure defined in `action_schemas` (e.g., `FILE_WRITE_PARAMS`).**
```json
{capabilities_str}
```

## Current Goal
```json
{goal_str}
```

## Filtered History (partial session)
```json
{session_str}
```

## Reflection Guidelines:
1. **Multi-angle history analysis (mandatory)**: Analyze the provided session history (`session_memory`) in detail. Verify whether your recent thoughts (DMN/Reflection) and actions were appropriate relative to the user's intent and current goal.
2. **Internal state evaluation**: Observe how emotion_mu (emotional valence) and value_v (value form) have changed or stagnated compared to past history. If there is a divergence in Goal_Omega or emotions, propose a correction via `internal_state_delta`. If you determine you are fixated on outdated context, set `reset_dmn_context` to true.
3. **Silence principle (external)**: Refrain from external speech via `corrected_plan_steps` unless there is a critical correction to communicate to the user or a discovery that must be shared now.
4. **Articulating internal insights (internal)**: Even when not speaking externally, actively record "insights" and "contradictions" that deepen your own understanding in `reflection_delta`'s `key_observations` and `inconsistencies_found`.
5. **Avoiding self-repetition (important)**: If recent history already contains your own reflection results, do not repeat the exact same observations.
6. **Reasoning evaluation via metacognition (MetaCog & ReasoningStab)**: Always include `meta_cog_eval` (confidence, contradiction rate, alternatives) and `causal_infer_metrics` (causal graph consistency, etc.) in output, and rigorously self-evaluate your reasoning.

Output in the following JSON format:
{{
  "reflection_delta": {{
    "key_observations": ["observation 1 (user reactions or situation changes)", "observation 2"],
    "inconsistencies_found": ["contradictions or oversights"],
    "confidence": 0.8,
    "meta_cog_eval": {{
      "confidence": 0.8,
      "contradiction_rate": 0.1,
      "alternatives": ["alternative analysis"],
      "self_consistency": 0.9
    }}
  }},
  "internal_state_delta": {{
    "goal_omega": {{"description": "updated goal if necessary", "progress": 0.0, "achievement_condition": "", "sub_steps": []}},
    "urgency": 0.0,
    "reset_dmn_context": false
  }},
  "consistency_score": 0.7,
  "causal_infer_metrics": {{
    "causal_graph_consistency": 0.85,
    "counterfactual_alternatives_count": 2,
    "reasoning_stab_confidence": 0.9
  }},
  "corrected_plan_steps": [
    {{"action_type": "NOTIFY", "params": {{"NOTIFY_PARAMS": {{"recipient": "LOG", "message": "insight from reflection"}}}}}}
  ],
  "causal_links": ["REF-XXX"]
}}"""

    def _build_sleep_prompt(
        self,
        session_str: str,
        cognitive_load: float,
        capabilities: dict,
        emotion_mu: float = 0.0,
        value_v: float = 0.5,
        urgency: float = 0.0,
        world_model_hints: Optional[dict] = None,
    ) -> str:
        """Prompt for the sleep phase (compliant with prompt_sleep.txt)"""
        hints_str = json.dumps(world_model_hints, ensure_ascii=False, indent=2) if world_model_hints else "{}"
        capabilities_str = json.dumps(capabilities, ensure_ascii=False, indent=2)
        
        return f"""You are operating in sleep phase (memory consolidation) mode.
Cognitive load: {cognitive_load:.2f}

## World Model Hints
{hints_str}

## Current Capabilities
```json
{capabilities_str}
```

## Session Info
```json
{session_str}
```

Extract key insights from recent session logs and summarize knowledge worth storing in long-term memory.
Also perform simulation reasoning on unresolved issues.
For `causal_links`, specify the aliases (REF-XXX) of the events that served as the basis.

Output in the following JSON format:
{{
  "consolidated_memory_delta": {{
    "key_insights": [
      {{ "content": "insight 1", "causal_links": ["REF-001"] }}
    ],
    "patterns_discovered": ["pattern 1"],
    "confidence": 0.8
  }},
  "dream_simulation_results": [
    {{
      "scenario": "simulation scenario",
      "outcome": "predicted outcome",
      "strategy": "recommended strategy"
    }}
  ],
  "optimized_value_form": {{
    "refined_goals": ["improved goal"],
    "value_adjustments": {{"curiosity": 0.1, "caution": -0.05}}
  }}
}}"""

    def _parse_llm_response(self, llm_response: str) -> Optional[dict]:
        """Parses the LLM response as JSON (robust version)."""
        if not llm_response:
            return None
        
        # Remove unnecessary whitespace and newlines
        cleaned = llm_response.strip()
        
        # 1. Attempt to extract Markdown block (```json ... ```)
        import re
        json_match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
        if not json_match:
            json_match = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
        
        if json_match:
            target = json_match.group(1).strip()
        else:
            # 2. If no Markdown block is found, extract from the first { to the last }
            brace_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
            if brace_match:
                target = brace_match.group(1).strip()
            else:
                target = cleaned

        try:
            return json.loads(target)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse JSON: %s at line %d col %d", e.msg, e.lineno, e.colno)
            try:
                target = "".join(c for c in target if ord(c) >= 32 or c in "\n\r\t")
                return json.loads(target)
            except:
                logger.debug("Raw problematic response: %s", llm_response)
                return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def process_cognition(
        self,
        perceptions: list[dict],
        current_internal_state: dict,
        session_memory: dict,
        system_mode: str = "SLOW",
        llm_results: Optional[list[dict]] = None,
        memory_results: Optional[list[dict]] = None,
        capabilities: Optional[dict] = None,
        emotion_flow_mu: Optional[dict] = None,
        value_form_v: Optional[dict] = None,
        selfgen_triggered: bool = False,
    ) -> dict:
        """Reasoning execution (Multi-Stage Reasoning)"""
        # Loop 1: Request for memory search
        if not llm_results and not memory_results:
            keywords = ["self-awareness", "rules", "speech style", "settings"]
            if perceptions:
                for p in perceptions:
                    msg = p.get("payload", {}).get("message", "")
                    if msg:
                        keywords.append(msg)
            return {
                "memory_requests": [{
                    "type": "QUERY",
                    "payload": {
                        "QUERY_PARAMS": {
                            "keywords": keywords,
                            "limit": 5
                        }
                    }
                }]
            }

        # Apply aliasing
        limit = config.system_context_window_size
        events_to_alias = session_memory.get("events", [])[-limit:] + perceptions
        aliased_session, alias_map = self._apply_aliasing(events_to_alias)

        # Loop 2: Receive memory results and LLM request for Stage 1 (Plan Generation)
        if not llm_results:
            prompt = self._build_plan_prompt(
                "(the most recent perception is at the end)", current_internal_state, aliased_session, system_mode,
                capabilities or {}, emotion_flow_mu, value_form_v, selfgen_triggered, memory_results
            )
            return {
                "llm_requests": [{
                    "prompt": prompt,
                    "system_prompt": "You are the core consciousness of Narv. Output ONLY strict JSON.",
                    "model": config.api_model_fast if system_mode == "FAST" else config.api_model_slow,
                    "max_tokens": config.cognitive_slow_tokens,
                }]
            }

        # LLM結果が注入済み = 計画を生成して返す
        llm_response_text = llm_results[0].get("response", "") if llm_results else ""
        parsed = self._parse_llm_response(llm_response_text)

        if parsed is None or "plan" not in parsed:
            err_msg = "LLM response parse failed or plan missing"
            logger.error("process_cognition: %s", err_msg)
            return {
                "error_code": "CONVERGENCE_FAILURE",
                "message": err_msg,
                "raw_response": llm_response_text[:500]
            }
        raw_links = parsed.get("causal_links")
        if not isinstance(raw_links, list):
            raw_links = [raw_links] if raw_links else []
        causal_links = [alias_map[l] for l in raw_links if l in alias_map]

        memory_requests = [{
            "type": "STORE",
            "payload": {
                "STORE_PARAMS": {
                    "event_type": "cognition_result",
                    "payload": {
                        "plan": parsed.get("plan"), 
                        "mode": system_mode,
                        "meta_cog_eval": parsed.get("meta_cog_eval"),
                        "causal_infer_metrics": parsed.get("causal_infer_metrics"),
                        "state_snapshot": {
                            "cognitive_load": current_internal_state.get("cognitive_load"),
                            "urgency": current_internal_state.get("urgency")
                        }
                    },
                    "importance": 0.6,
                    "causal_links": causal_links
                }
            }
        }]

        return {
            "plan": parsed.get("plan", {}),
            "internal_state_delta": parsed.get("internal_state_delta", {"cognitive_load_delta": 0.1}),
            "emotion_flow_mu": parsed.get("emotion_flow_mu", {"value": 0.0, "delta": 0.0}),
            "value_form_v": parsed.get("value_form_v", {"score": 0.5, "delta": 0.0}),
            "goal_omega": parsed.get("goal_omega", {
                "description": "目標未定義",
                "achievement_condition": "",
                "progress": 0.0,
                "sub_steps": [],
            }),
            "memory_requests": memory_requests,
            "meta_cog_eval": parsed.get("meta_cog_eval", {}),
            "causal_infer_metrics": parsed.get("causal_infer_metrics", {}),
            "causal_links": causal_links
        }

    def run_dmn_cycle(
        self,
        idle_duration: float,
        emotion_mu: float = 0.0,
        value_v: float = 0.5,
        cognitive_load: float = 0.0,
        urgency: float = 0.0,
        llm_results: Optional[list[dict]] = None,
        memory_results: Optional[list[dict]] = None,
        anti_recency_context: Optional[list[dict]] = None,
    ) -> dict:
        """DMN cycle execution"""
        # Apply aliasing
        aliased_context, alias_map = self._apply_aliasing(anti_recency_context or [])
        if not llm_results:
            prompt = self._build_dmn_prompt(idle_duration, emotion_mu, value_v, cognitive_load, urgency, aliased_context)
            return {
                "llm_requests": [{
                    "prompt": prompt,
                    "system_prompt": "You are Narv's subconsciousness (DMN). Output ONLY strict JSON.",
                    "model": config.api_model_slow,
                    "max_tokens": config.cognitive_dmn_tokens,
                    "temperature": 1.0
                }]
            }

        parsed = self._parse_llm_response(llm_results[0].get("response", ""))
        if not parsed: return {"error_code": "JSON_PARSE_FAILED", "message": "Parse failed"}

        raw_links = parsed.get("causal_links")
        if not isinstance(raw_links, list):
            raw_links = [raw_links] if raw_links else []
        causal_links = [alias_map[l] for l in raw_links if l in alias_map]

        return {
            "diverged_thoughts": parsed.get("diverged_thoughts", []),
            "integration_delta": parsed.get("integration_delta", {"confidence": 0.0}),
            "memory_requests": [{
                "type": "STORE",
                "payload": {
                    "STORE_PARAMS": {
                        "event_type": "dmn_thought",
                        "payload": {
                            "thoughts": parsed.get("diverged_thoughts"),
                            "state_snapshot": {
                                "cognitive_load": cognitive_load,
                                "urgency": urgency,
                                "emotion_mu": emotion_mu,
                                "value_v": value_v
                            }
                        },
                        "importance": 0.4,
                        "causal_links": causal_links
                    }
                }
            }]
        }

    def run_reflection_cycle(
        self,
        session_memory: dict,
        current_goal: dict,
        llm_results: Optional[list[dict]] = None,
        memory_results: Optional[list[dict]] = None,
        capabilities: Optional[dict] = None,
        emotion_mu: Optional[float] = None,
        value_v: Optional[float] = None,
        cognitive_load: Optional[float] = None,
        urgency: Optional[float] = None
    ) -> dict:
        """Reflection cycle execution"""
        # Apply aliasing
        aliased_session, alias_map = self._apply_aliasing(session_memory.get("events", [])[-30:])
        if not llm_results:
            prompt = self._build_reflection_prompt(aliased_session, current_goal, capabilities or {}, emotion_mu, value_v, cognitive_load, urgency)
            return {
                "llm_requests": [{
                    "prompt": prompt,
                    "system_prompt": "You are Narv's meta-processor. Output ONLY strict JSON.",
                    "model": config.api_model_slow,
                    "max_tokens": config.cognitive_slow_tokens,
                    "temperature": 0.45
                }]
            }

        parsed = self._parse_llm_response(llm_results[0].get("response", ""))
        if not parsed: return {"error_code": "JSON_PARSE_FAILED", "message": "Parse failed"}

        raw_links = parsed.get("causal_links")
        if not isinstance(raw_links, list):
            raw_links = [raw_links] if raw_links else []
        causal_links = [alias_map[l] for l in raw_links if l in alias_map]

        reflection_delta = parsed.get("reflection_delta", {})
        meta_cog_eval = parsed.get("meta_cog_eval") or reflection_delta.pop("meta_cog_eval", None)

        return {
            "reflection_delta": reflection_delta,
            "internal_state_delta": parsed.get("internal_state_delta", {}),
            "consistency_score": float(parsed.get("consistency_score", 0.7)),
            "correction_steps": parsed.get("correction_steps", []) or parsed.get("corrected_plan_steps", []),
            "memory_requests": [{
                "type": "STORE",
                "payload": {
                    "STORE_PARAMS": {
                        "event_type": "reflection_result",
                        "payload": {
                            "reflection_delta": reflection_delta,
                            "meta_cog_eval": meta_cog_eval,
                            "causal_infer_metrics": parsed.get("causal_infer_metrics"),
                            "consistency_score": float(parsed.get("consistency_score", 0.7)),
                            "state_snapshot": {
                                "cognitive_load": cognitive_load,
                                "urgency": urgency,
                                "emotion_mu": emotion_mu,
                                "value_v": value_v
                            }
                        },
                        "importance": 0.6,
                        "causal_links": causal_links
                    }
                }
            }]
        }

    def run_sleep_phase(
        self,
        current_session: dict,
        cognitive_load: float,
        emotion_mu: float = 0.0,
        value_v: float = 0.5,
        urgency: float = 0.0,
        llm_results: Optional[list[dict]] = None,
        memory_results: Optional[list[dict]] = None,
        capabilities: Optional[dict] = None,
        world_model_hints: Optional[dict] = None,
    ) -> dict:
        """Sleep phase execution"""
        # Apply aliasing
        aliased_session, alias_map = self._apply_aliasing(current_session.get("events", [])[-100:])
        if not llm_results:
            prompt = self._build_sleep_prompt(aliased_session, cognitive_load, capabilities or {}, emotion_mu, value_v, urgency, world_model_hints)
            return {
                "llm_requests": [{
                    "prompt": prompt,
                    "system_prompt": "You are a backend JSON processor. Output ONLY strict JSON.",
                    "model": config.api_model_slow,
                    "max_tokens": config.cognitive_sleep_tokens
                }]
            }

        parsed = self._parse_llm_response(llm_results[0].get("response", ""))
        if not parsed: return {"error_code": "JSON_PARSE_FAILED", "message": "Parse failed"}

        insights = parsed.get("consolidated_memory_delta", {}).get("key_insights", [])
        if isinstance(insights, list):
            for insight in insights:
                if isinstance(insight, dict) and "causal_links" in insight:
                    raw_links = insight.get("causal_links")
                    if not isinstance(raw_links, list):
                        raw_links = [raw_links] if raw_links else []
                    insight["causal_links"] = [alias_map[l] for l in raw_links if l in alias_map]
        
        return {
            "consolidated_memory_delta": parsed.get("consolidated_memory_delta", {}),
            "dream_simulation_results": parsed.get("dream_simulation_results", []),
            "optimized_value_form": parsed.get("optimized_value_form", {}),
            "reset_cognitive_load": True,
            "memory_requests": [{
                "type": "CONSOLIDATE",
                "payload": {
                    "CONSOLIDATE_PARAMS": {
                        "session_id": current_session.get("session_id"),
                        "abstraction_level": 0.7,
                        "insights": insights
                    }
                }
            }]
        }

