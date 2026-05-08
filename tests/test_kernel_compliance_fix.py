import pytest
from unittest.mock import MagicMock, patch
from src.kernel.kernel import Kernel, SystemState
from src.core.types import SystemState as StateEnum
from src.core.exceptions import BudgetExceededError, LLMGatewayError

@pytest.fixture
def kernel_env():
    llm = MagicMock()
    ce = MagicMock()
    mem = MagicMock()
    perceptor = MagicMock()
    actor = MagicMock()
    
    llm.get_usage_status.return_value = {"remaining_requests": 1000}
    perceptor.gather_perceptions.return_value = {"perceptions": [], "timestamp": "now"}
    mem.get_session_memory.return_value = []
    
    k = Kernel(
        llm_gateway=llm,
        cognitive_engine=ce,
        memory=mem,
        perceptor=perceptor,
        actor=actor
    )
    return k

def test_budget_exceeded_during_cognition_transitions_to_suspended(kernel_env):
    """FINDING-002: 予算切れが認知フェーズで発生した場合に SUSPENDED へ遷移すること"""
    k = kernel_env
    # Mock CE to trigger BudgetExceededError via _run_cognition_with_resume
    # _run_cognition_with_resume calls cognition_fn (mock_ce.process_cognition)
    k._cognitive.process_cognition.side_effect = BudgetExceededError("Budget limit reached")
    
    # Force urgency to trigger cognition
    k._perceptor.gather_perceptions.return_value = {
        "perceptions": [{"source": "test", "urgency": 0.9}],
        "timestamp": "now"
    }
    
    # Execute cycle
    with pytest.raises(BudgetExceededError):
        k.execute_cycle()
    
    # State should be SUSPENDED
    assert k._system_state == StateEnum.SUSPENDED
    # Should not be IDLE (which would happen if error was swallowed and None returned)
    assert k._system_state != StateEnum.IDLE

def test_llm_gateway_error_import_exists(kernel_env):
    """FINDING-001: LLMGatewayError がインポートされており NameError が発生しないこと"""
    k = kernel_env
    # Mock CE to trigger LLMGatewayError
    k._cognitive.process_cognition.side_effect = LLMGatewayError("API_ERROR", "Test error")
    
    # Force urgency to trigger cognition
    k._perceptor.gather_perceptions.return_value = {
        "perceptions": [{"source": "test", "urgency": 0.9}],
        "timestamp": "now"
    }
    
    # Execute cycle - should NOT raise NameError
    try:
        k.execute_cycle()
    except LLMGatewayError:
        pass # Expected
    except NameError as e:
        pytest.fail(f"NameError raised: {e}")
    except Exception:
        pass # Other errors are fine for this test

if __name__ == "__main__":
    pytest.main([__file__])
