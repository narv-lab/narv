"""kernel_state_manager module — interface: kernel_state_v1

Maintenance and persistence of deterministic system state transitions (state machine).
"""
from __future__ import annotations

import uuid
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.core.logger import setup_logger
from src.core.types import SystemState

logger = setup_logger("state_manager")

STATE_FILE = Path("data/kernel_state.json")

class KernelStateManager:
    """Responsible for state management. Implements the kernel_state_v1 interface."""

    def __init__(self) -> None:
        self._system_state: SystemState = SystemState.IDLE
        self._state_history: list[dict] = []
        
        # Internal state variables (part of the deterministic state machine)
        self._session_id: str = str(uuid.uuid4())[:8]
        self._cognitive_load: float = 0.0
        self._urgency: float = 0.0
        self._emotion_mu: float = 0.0
        self._value_v: float = 0.5
        self._emotion_flow_mu: dict = {"value": 0.0, "delta": 0.0}
        self._value_form_v: dict = {"score": 0.5, "delta": 0.0}
        self._goal_omega: dict = {
            "description": "Initializing",
            "achievement_condition": "",
            "progress": 0.0,
            "sub_steps": []
        }
        # L2 kernel_state_v1: Persistence management of last_perception_timestamp (CHANGE-design_refinement-20260420)
        self._last_perception_timestamp: Optional[str] = None
        
        # Management of handled event IDs (to avoid Bugs 11/12)
        self._handled_event_ids: set[str] = set()
        
        self.load_state()
        
    @property
    def system_state(self) -> SystemState:
        return self._system_state

    @property
    def session_id(self) -> str:
        return self._session_id

    def refresh_session_id(self) -> None:
        """Updates the session ID and resets context freshness upon recovery from long idle periods or completion of SleepPhase."""
        old_id = self._session_id
        self._session_id = str(uuid.uuid4())[:8]
        logger.info("Session ID refreshed: %s -> %s", old_id, self._session_id)

    @property
    def cognitive_load(self) -> float:
        return self._cognitive_load

    @cognitive_load.setter
    def cognitive_load(self, value: float) -> None:
        self._cognitive_load = max(0.0, min(1.0, value))

    @property
    def urgency(self) -> float:
        return self._urgency

    @urgency.setter
    def urgency(self, value: float) -> None:
        self._urgency = max(0.0, min(1.0, value))

    @property
    def emotion_mu(self) -> float:
        return self._emotion_mu

    @emotion_mu.setter
    def emotion_mu(self, value: float) -> None:
        self._emotion_mu = max(-1.0, min(1.0, value))
        self._emotion_flow_mu["value"] = self._emotion_mu

    @property
    def value_v(self) -> float:
        return self._value_v

    @value_v.setter
    def value_v(self, value: float) -> None:
        self._value_v = max(0.0, min(1.0, value))
        self._value_form_v["score"] = self._value_v

    @property
    def emotion_flow_mu(self) -> dict:
        return self._emotion_flow_mu

    @emotion_flow_mu.setter
    def emotion_flow_mu(self, value: dict) -> None:
        self._emotion_flow_mu = value
        if "value" in value:
            self._emotion_mu = float(value["value"])

    @property
    def value_form_v(self) -> dict:
        return self._value_form_v

    @value_form_v.setter
    def value_form_v(self, value: dict) -> None:
        self._value_form_v = value
        if "score" in value:
            self._value_v = float(value["score"])

    @property
    def goal_omega(self) -> dict:
        return self._goal_omega

    @goal_omega.setter
    def goal_omega(self, value: dict) -> None:
        self._goal_omega = value

    @property
    def handled_event_ids(self) -> set[str]:
        return self._handled_event_ids

    def add_handled_event_id(self, event_id: str) -> None:
        if event_id:
            self._handled_event_ids.add(event_id)
            # Limit to approximately 500 recent items to prevent unbounded growth
            if len(self._handled_event_ids) > 500:
                # Since set is unordered, it's difficult to remove the oldest item, but
                # simply remove the first item (leveraging the ordering properties of dict/set in Python 3.7+)
                it = iter(self._handled_event_ids)
                next(it)
                self._handled_event_ids.remove(next(iter(self._handled_event_ids)))

    def get_current_state(self) -> dict:
        """L2: kernel_state_v1.get_current_state implementation

        output_schema (kernel_state_v1.yaml):
          system_state, cognitive_load, urgency, emotion_mu, value_v,
          goal_omega, last_perception_timestamp
        """
        return {
            "system_state": self._system_state.value,
            "cognitive_load": self._cognitive_load,
            "urgency": self._urgency,
            "emotion_mu": self._emotion_mu,
            "value_v": self._value_v,
            "goal_omega": self._goal_omega,
            "last_perception_timestamp": self._last_perception_timestamp,
            # Backward compatibility fields (maintaining compatibility with existing callers)
            "current_state": self._system_state.value,
            "history": self._state_history[-20:],
        }

    def update_state(
        self,
        new_state: SystemState,
        context: Optional[dict] = None,
        last_perception_timestamp: Optional[str] = None,
        caller_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """L2: kernel_state_v1.update_state implementation

        input_schema  (kernel_state_v1.yaml): caller_id?, new_state,
                       last_perception_timestamp?, metadata?
        output_schema (kernel_state_v1.yaml): { success: boolean }
        error_schema  (kernel_state_v1.yaml): INVALID_TRANSITION | STATE_WRITE_FAILURE
        """
        from_state = self._system_state

        # State transition validation (simplified version; refine as necessary)
        # Example: Explicit recovery may be required from SUSPENDED, etc.

        try:
            self._system_state = new_state

            # Retention of last_perception_timestamp (update only when specified)
            if last_perception_timestamp is not None:
                self._last_perception_timestamp = last_perception_timestamp

            # Record history
            ctx = context or metadata or {}
            if caller_id:
                ctx = {"caller_id": caller_id, **ctx}
            entry = {
                "from_state": from_state.value,
                "to_state": new_state.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context": ctx,
            }
            self._state_history.append(entry)
            if len(self._state_history) > 100:
                self._state_history = self._state_history[-100:]

            logger.info("State transition: %s -> %s", from_state.value, new_state.value)
            return {"success": True}
        except Exception as exc:
            logger.error("update_state failed: %s", exc)
            return {"success": False, "error_code": "STATE_WRITE_FAILURE", "message": str(exc)}

    def save_state(self) -> None:
        """Persists the system state to a file."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            state_data = {
                "system_state": self._system_state.value,
                "session_id": self._session_id,
                "cognitive_load": self._cognitive_load,
                "urgency": self._urgency,
                "emotion_mu": self._emotion_mu,
                "value_v": self._value_v,
                "emotion_flow_mu": self._emotion_flow_mu,
                "value_form_v": self._value_form_v,
                "goal_omega": self._goal_omega,
                # FINDING-009: Persist last_perception_timestamp (CHANGE-design_refinement-20260420)
                "last_perception_timestamp": self._last_perception_timestamp,
                "handled_event_ids": list(self._handled_event_ids),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed to save state: %s", e)

    def load_state(self) -> None:
        """Loads the persisted system state from a file."""
        if not STATE_FILE.exists():
            return

        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._session_id = data.get("session_id", self._session_id)
            self._cognitive_load = float(data.get("cognitive_load", self._cognitive_load))
            self._urgency = float(data.get("urgency", self._urgency))

            emotion_mu = data.get("emotion_mu")
            if emotion_mu is not None:
                self.emotion_mu = float(emotion_mu)

            value_v = data.get("value_v")
            if value_v is not None:
                self.value_v = float(value_v)

            self._emotion_flow_mu = data.get("emotion_flow_mu", self._emotion_flow_mu)
            self._value_form_v = data.get("value_form_v", self._value_form_v)
            self._goal_omega = data.get("goal_omega", self._goal_omega)

            # FINDING-009: Restore last_perception_timestamp (CHANGE-design_refinement-20260420)
            self._last_perception_timestamp = data.get("last_perception_timestamp", None)
            
            # Restore handled IDs
            ids = data.get("handled_event_ids", [])
            self._handled_event_ids = set(ids)

            # NOTE: system_state should start from IDLE at startup, so it is not restored
            logger.info("Loaded persisted state from %s", STATE_FILE)
        except Exception as e:
            logger.error("Failed to load state: %s", e)

    def restore_state_from_memory(self, memory: "Memory") -> dict:
        """Restores core internal state from past session memory.

        output_schema (kernel_state_v1.yaml):
          { restored: boolean, restored_fields: array }
        error_schema  (kernel_state_v1.yaml): RESTORE_FAILURE
        """
        try:
            events = memory.get_session_memory()
            restored_fields: list[str] = []
            for event in reversed(events):
                if event.get("event_type") == "system_state_snapshot":
                    payload = event.get("payload", {})
                    if "emotion_mu" in payload:
                        self.emotion_mu = float(payload["emotion_mu"])
                        restored_fields.append("emotion_mu")
                    if "value_v" in payload:
                        self.value_v = float(payload["value_v"])
                        restored_fields.append("value_v")
                    if "emotion_flow_mu" in payload:
                        self.emotion_flow_mu = payload["emotion_flow_mu"]
                        restored_fields.append("emotion_flow_mu")
                    if "value_form_v" in payload:
                        self.value_form_v = payload["value_form_v"]
                        restored_fields.append("value_form_v")
                    if "goal_omega" in payload:
                        self.goal_omega = payload["goal_omega"]
                        restored_fields.append("goal_omega")
                    if "cognitive_load" in payload:
                        self.cognitive_load = float(payload["cognitive_load"])
                        restored_fields.append("cognitive_load")
                    if "last_perception_timestamp" in payload:
                        self._last_perception_timestamp = payload["last_perception_timestamp"]
                        restored_fields.append("last_perception_timestamp")
                    logger.info(
                        "Restored internal state from memory: event_id=%s fields=%s",
                        event.get("id"), restored_fields,
                    )
                    return {"restored": True, "restored_fields": restored_fields}
            # In case a snapshot does not exist
            return {"restored": False, "restored_fields": []}
        except Exception as exc:
            logger.error("restore_state_from_memory failed: %s", exc)
            return {"restored": False, "restored_fields": [],
                    "error_code": "RESTORE_FAILURE", "message": str(exc)}

    def reset_dmn_context(self) -> None:
        """Resets the DMN context upon cognitive failure."""
        logger.warning("RESET_DMN_CONTEXT triggered. Resetting goal_omega, emotion_mu, value_v to defaults.")
        self.goal_omega = {"description": "Initializing", "achievement_condition": "", "progress": 0.0, "sub_steps": []}
        self.emotion_mu = 0.0
        self.value_v = 0.5
