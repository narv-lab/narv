from enum import Enum
from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime

class SystemState(str, Enum):
    IDLE = "IDLE"
    PROCESSING_SYSTEM1 = "PROCESSING_SYSTEM1"
    PROCESSING_SYSTEM2 = "PROCESSING_SYSTEM2"
    SLEEP_PHASE = "SLEEP_PHASE"
    SUSPENDED = "SUSPENDED"

class StandardizedEvent(BaseModel):
    id: str
    timestamp: datetime
    source: str
    urgency: float = Field(ge=0.0, le=1.0)
    payload: Any

class ActionCommand(BaseModel):
    action_type: str  # e.g., "FILE_WRITE", "COMMAND_EXEC", "NOTIFY"
    params: dict[str, Any]

class ActionResult(BaseModel):
    success: bool
    execution_result: str
    error_details: Optional[str] = None
