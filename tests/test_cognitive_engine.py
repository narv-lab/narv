from src.cognitive_engine.cognitive_engine import CognitiveEngine
import pytest

@pytest.fixture
def ce():
    return CognitiveEngine()

def test_ce_process_cognition_system1(ce):
    perceptions = [{"source": "USER", "payload": "hello", "urgency": 0.9}]
    current_internal_state = {"emotion_mu": 0.5, "value_v": -0.1, "goal_omega": {"description": "None", "priority": 0}}
    session_memory = {}
    
    res = ce.process_cognition(
        perceptions=perceptions,
        current_internal_state=current_internal_state,
        session_memory=session_memory,
        system_mode="FAST",
        llm_results=None,
        memory_results=None,
        capabilities={"allowed_actions": ["NOTIFY"]}
    )
    
    assert "llm_requests" in res
    assert len(res["llm_requests"]) > 0
    assert "prompt" in res["llm_requests"][0]

def test_ce_run_dmn_cycle(ce):
    res = ce.run_dmn_cycle(
        idle_duration=100.0,
        emotion_mu=0.5,
        value_v=0.1,
        anti_recency_context=[],
        llm_results=None,
        memory_results=None
    )
    assert "llm_requests" in res
    assert len(res["llm_requests"]) > 0
    assert "prompt" in res["llm_requests"][0]

def test_ce_run_reflection_cycle(ce):
    res = ce.run_reflection_cycle(
        session_memory={"messages": []},
        current_goal={"description": "testing", "priority": 1},
        capabilities={"allowed_actions": ["NOTIFY"]},
        llm_results=None,
        memory_results=None
    )
    assert "llm_requests" in res
    assert len(res["llm_requests"]) > 0

def test_ce_run_sleep_phase(ce):
    res = ce.run_sleep_phase(
        current_session={"messages": []},
        cognitive_load=0.9,
        capabilities={},
        llm_results=None,
        memory_results=None
    )
    assert "llm_requests" in res
