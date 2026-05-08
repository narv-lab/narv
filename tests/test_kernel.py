from src.kernel.kernel import Kernel, KernelStateTransitionError
import pytest
from unittest.mock import MagicMock

@pytest.fixture
def kernel_env():
    # Mocking all dependencies
    llm = MagicMock()
    ce = MagicMock()
    mem = MagicMock()
    perceptor = MagicMock()
    actor = MagicMock()
    
    # Kernel starts in IDLE, and it checks perceptor on first execute_cycle (which defaults to checking PERCEPTIONS)
    perceptor.gather_perceptions.return_value = {"perceptions": []}
    
    k = Kernel(
        llm_gateway=llm,
        cognitive_engine=ce,
        memory=mem,
        perceptor=perceptor,
        actor=actor
    )
    
    return k

def test_kernel_initial_state(kernel_env):
    k = kernel_env
    state = k.get_system_state()
    assert "IDLE" in str(state["current_state"])
    assert "history_summary" in state

def test_kernel_execute_cycle_idle(kernel_env):
    k = kernel_env
    k._llm.get_usage_status.return_value = {"remaining_requests": 1000}
    # First cycle should handle state transition. IDLE -> PERCEPTION usually.
    res = k.execute_cycle()
    assert "next_state" in res
    assert "cycle_summary" in res
