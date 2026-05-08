class NarvBaseError(Exception):
    """Base exception for all system errors."""
    pass

class BudgetExceededError(NarvBaseError):
    """Raised when the daily API budget is exceeded or rate limit hit without recovery."""
    pass

class CognitiveFailureError(NarvBaseError):
    """Raised when the cognitive engine fails to converge on a plan or fails repeatedly."""
    pass

class StateInconsistencyError(NarvBaseError):
    """Raised when memory and environment state present irreconcilable contradictions."""
    pass

class KernelStateTransitionError(NarvBaseError):
    """Raised when an unexpected error causes a state transition failure.

    kernel_v1.yaml error_schema: { error_code: 'STATE_TRANSITION_ERROR', message: string }
    """
    error_code = "STATE_TRANSITION_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LLMGatewayError(NarvBaseError):
    """Raised when llm_gateway operations fail.

    llm_gateway_v1.yaml error_schema:
        { error_code: 'RATE_LIMIT_EXCEEDED' | 'BUDGET_EXCEEDED' | 'API_ERROR', message: string }
    """
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    API_ERROR = "API_ERROR"

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class StorageReadError(NarvBaseError):
    """Raised when reading system state from storage fails.

    kernel_v1.yaml error_schema: { error_code: 'STORAGE_READ_ERROR', message: string }
    """
    error_code = "STORAGE_READ_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PerceptorError(NarvBaseError):
    """Raised when perceptor operations fail.

    perception_v1.yaml error_schema:
        { error_code: 'SENSOR_TIMEOUT' | 'ABSTRACTION_ERROR', message: string }
    """
    SENSOR_TIMEOUT = "SENSOR_TIMEOUT"
    ABSTRACTION_ERROR = "ABSTRACTION_ERROR"

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
