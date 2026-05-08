"""kernel_mediator module — interface: kernel_routing_v1

Mediates all communication between modules and injects the execution context (caller_id).
"""
from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING, Callable

from src.core.exceptions import BudgetExceededError, LLMGatewayError
from src.core.logger import setup_logger
from src.core.config import config

if TYPE_CHECKING:
    from src.llm_gateway.llm_gateway import LLMGateway
    from src.cognitive_engine.cognitive_engine import CognitiveEngine
    from src.memory.memory import Memory
    from src.perceptor.perceptor import Perceptor
    from src.actor.actor import Actor

logger = setup_logger("mediator")

CALLER_ID = "kernel"

class KernelMediator:
    """Responsible for communication mediation. Implements the kernel_routing_v1 interface."""

    def __init__(
        self,
        llm_gateway: "LLMGateway",
        cognitive_engine: "CognitiveEngine",
        memory: "Memory",
        perceptor: "Perceptor",
        actor: "Actor",
    ) -> None:
        self._llm = llm_gateway
        self._cognitive = cognitive_engine
        self._memory = memory
        self._perceptor = perceptor
        self._actor = actor
        self._current_session_id: Optional[str] = None

    def set_session_id(self, session_id: str) -> None:
        """Sets the current session ID."""
        self._current_session_id = session_id

    def _inject_caller_id(self, kwargs: dict) -> dict:
        kwargs["caller_id"] = CALLER_ID
        return kwargs

    def route_action(self, target_module: str, operation_id: str, params: dict) -> dict:
        """L2: kernel_routing_v1.route_action implementation

        output_schema (kernel_routing_v1.yaml): { success: boolean, result: any, error?: string }
        error_schema  (kernel_routing_v1.yaml): ROUTING_FAILED | UNKNOWN_MODULE | OPERATION_NOT_FOUND
        """
        logger.debug("Routing action: %s.%s", target_module, operation_id)

        try:
            # Module map
            module_map = {
                "perceptor": self._perceptor,
                "cognitive_engine": self._cognitive,
                "actor": self._actor,
                "memory": self._memory,
                "llm_gateway": self._llm
            }

            # FINDING-004: Compliant with error_code format (kernel_routing_v1.yaml)
            if target_module not in module_map:
                logger.warning("route_action: unknown module '%s'", target_module)
                return {
                    "success": False,
                    "error_code": "UNKNOWN_MODULE",
                    "message": f"Unknown module: {target_module}",
                }

            target_obj = module_map[target_module]
            method = getattr(target_obj, operation_id, None)

            if method is None:
                logger.warning("route_action: operation '%s' not found on '%s'", operation_id, target_module)
                return {
                    "success": False,
                    "error_code": "OPERATION_NOT_FOUND",
                    "message": f"Operation '{operation_id}' not found on module '{target_module}'",
                }

            # Execute with injected caller_id. Fallback to original arguments if execution fails.
            # (While all public operations should accept caller_id in L1 specs, some internal helper methods might not.)
            try:
                result = method(**self._inject_caller_id(dict(params)))
            except TypeError:
                result = method(**params)

            return {"success": True, "result": result, "caller_id_used": CALLER_ID}

        except Exception as exc:
            logger.error("Routing error (%s.%s): %s", target_module, operation_id, exc)
            return {
                "success": False,
                "error_code": "ROUTING_FAILED",
                "message": str(exc),
            }

    # ------------------------------------------------------------------
    # Common Helper: Resumption (Completion loop)
    # ------------------------------------------------------------------
    def run_cognition_with_resumption(
        self,
        cognition_fn_name: str,
        initial_params: dict,
        temperature: float = 0.7
    ) -> dict:
        """Management of cognitive_engine calls and Resumption (recursive resolution).

        output_schema (kernel_routing_v1.yaml): { final_result: object, resumption_count: number }
        error_schema  (kernel_routing_v1.yaml): RESUMPTION_LIMIT_EXCEEDED | LLM_RESOLUTION_FAILED
                                               | MEMORY_RESOLUTION_FAILED
        """
        params = dict(initial_params)
        llm_results_history = []
        memory_results = None

        max_resume_loop = config.system_max_resume_loop
        loop_count = 0

        for loop_count in range(max_resume_loop):
            if memory_results is not None:
                params["memory_results"] = memory_results

            # Call cognitive engine
            routing_res = self.route_action("cognitive_engine", cognition_fn_name, params)
            if not routing_res["success"]:
                raise LLMGatewayError(LLMGatewayError.API_ERROR, routing_res.get("message", routing_res.get("error", "")))

            result = routing_res["result"]

            # Check completion conditions
            if any(k in result for k in ("plan", "diverged_thoughts", "consolidated_memory_delta",
                                          "reflection_delta", "correction_steps")):
                if "memory_requests" in result:
                    self._resolve_memory_requests(result["memory_requests"])
                # FINDING-005: Return in { final_result, resumption_count } format
                return {"final_result": result, "resumption_count": loop_count}

            if "llm_requests" not in result and "memory_requests" not in result:
                # FINDING-005: Wrap in completion format
                return {"final_result": result, "resumption_count": loop_count}

            # Resolve requests
            if "llm_requests" in result:
                llm_results = self._resolve_llm_requests(result["llm_requests"], temperature)
                llm_results_history.extend(llm_results)
                params["llm_results"] = list(llm_results_history)
                if "memory_requests" not in result:
                    continue

            if "memory_requests" in result:
                memory_results = self._resolve_memory_requests(result["memory_requests"])

        logger.warning("Resumption loop exceeded limit (%d)", max_resume_loop)
        # FINDING-005: error_code format compliant with error_schema
        return {
            "final_result": None,
            "resumption_count": loop_count,
            "error_code": "RESUMPTION_LIMIT_EXCEEDED",
            "message": f"Resumption loop exceeded limit ({max_resume_loop})",
        }

    def _resolve_llm_requests(self, requests: list[dict], temperature: float) -> list[dict]:
        results = []
        for req in requests:
            res = self.route_action("llm_gateway", "query_openrouter", {
                "prompt": req["prompt"],
                "model": req.get("model"),
                "system_prompt": req.get("system_prompt"),
                "max_tokens": req.get("max_tokens", 1024),
                "temperature": req.get("temperature", temperature)
            })
            if not res["success"]:
                err_msg = res.get("message", res.get("error_code", "Unknown error"))
                raise LLMGatewayError(LLMGatewayError.API_ERROR, err_msg)
            results.append(res["result"])
        return results

    def _resolve_memory_requests(self, requests: list[dict]) -> list[dict]:
        results = []
        for req in requests:
            req_type = req.get("type", "")
            payload = req.get("payload", {})
            
            if req_type == "STORE":
                p = payload.get("STORE_PARAMS", {})
                # Type guard for causal_links
                raw_links = p.get("causal_links")
                if raw_links is None:
                    links = None
                elif isinstance(raw_links, list):
                    links = [str(l) for l in raw_links if l]
                else:
                    links = [str(raw_links)] if raw_links else None

                res = self.route_action("memory", "store_event", {
                    "event_type": p.get("event_type", "event"),
                    "payload": p.get("payload", {}),
                    "importance": p.get("importance", 0.5),
                    "session_id": p.get("session_id") or self._current_session_id,
                    "causal_links": links,
                    "event_id": p.get("event_id"),
                })
            elif req_type == "QUERY":
                p = payload.get("QUERY_PARAMS", {})
                res = self.route_action("memory", "query_memory", {
                    "query_vector": p.get("query_vector"),
                    "keywords": p.get("keywords"),
                    "limit": p.get("limit", 5),
                    "strategy": p.get("strategy"),
                })
            elif req_type == "CONSOLIDATE":
                p = payload.get("CONSOLIDATE_PARAMS", {})
                res = self.route_action("memory", "consolidate_memory", {
                    "session_id": p.get("session_id") or self._current_session_id,
                    "abstraction_level": p.get("abstraction_level", 0.5),
                    "insights": p.get("insights", []),
                    "keep_count": config.system_session_memory_keep_count
                })
            else:
                continue
                
            if res["success"]:
                results.append(res["result"])
        return results
