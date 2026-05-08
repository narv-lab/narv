import os
import tempfile
import sqlite3
from pathlib import Path
from src.memory.memory import Memory, MemoryError
import pytest

@pytest.fixture
def memory_env():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test_memory.db"
        mem = Memory(db_path=db_path)
        yield mem, db_path
        mem.close()

def test_memory_store_and_query(memory_env):
    mem, _ = memory_env
    
    # Store event
    res = mem.store_event(
        caller_id="kernel",
        event_type="test_event",
        payload={"msg": "hello"},
        importance=0.8,
        embedding=[0.1, 0.2, 0.3]
    )
    assert res["status"] == "stored"
    
    # Query event
    q_res = mem.query_memory(caller_id="kernel", query_vector=[0.1, 0.2, 0.31])  # slight diff
    assert len(q_res["results"]) > 0
    assert q_res["results"][0]["payload"]["msg"] == "hello"

def test_memory_anti_recency(memory_env):
    mem, _ = memory_env
    for i in range(10):
        mem.store_event(
            caller_id="kernel",
            event_type="test_event",
            payload={"idx": i},
            importance=0.8
        )
    
    res = mem.query_memory(caller_id="kernel", strategy="anti_recency", limit=3)
    assert len(res["results"]) == 3

def test_memory_consolidate(memory_env):
    mem, _ = memory_env
    # Add session data
    for i in range(30):
        mem.store_event(
            caller_id="kernel",
            event_type="session_event",
            payload={"idx": i},
            importance=0.4
        )
    
    assert len(mem.get_session_memory()) == 30
    
    # Consolidate
    insights = [{"payload": "distilled knowledge", "importance": 0.9}]
    res = mem.consolidate_memory(
        caller_id="kernel",
        session_id=None,
        abstraction_level=0.5,
        insights=insights
    )
    
    assert res["removed_noise_count"] > 0
    assert len(mem.get_session_memory()) <= 20  # Keeps up to 20 raw

def test_memory_caller_id_validation(memory_env):
    mem, _ = memory_env
    with pytest.raises(PermissionError):
        mem.store_event(
            caller_id="malicious_actor",
            event_type="test",
            payload={},
            importance=0.5
        )
