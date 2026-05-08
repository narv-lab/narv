import os
import tempfile
from pathlib import Path
from src.perceptor.perceptor import Perceptor
import pytest

@pytest.fixture
def perceptor_env():
    with tempfile.TemporaryDirectory() as temp_dir:
        watch_dir = Path(temp_dir)
        p = Perceptor(watch_dirs=[str(watch_dir)])
        yield p, watch_dir

def test_perceptor_clock(perceptor_env):
    p, _ = perceptor_env
    res = p.gather_perceptions(sources=["CLOCK"], caller_id="kernel")
    assert "perceptions" in res
    assert len(res["perceptions"]) == 1
    assert res["perceptions"][0]["source"] == "CLOCK"

def test_perceptor_file_anomaly(perceptor_env):
    p, watch_dir = perceptor_env
    test_file = watch_dir / "test.txt"
    test_file.write_text("This is an ERROR test")
    
    res = p.gather_perceptions(sources=["FILE"], caller_id="kernel")
    perceptions = res["perceptions"]
    assert len(perceptions) == 1
    assert perceptions[0]["source"] == "FILE"
    assert perceptions[0]["urgency"] == 0.9  # config.perceptor_urgency_anomaly defaults to 0.9

def test_perceptor_idle_entropy(perceptor_env):
    p, _ = perceptor_env
    idle_context = {
        "idle_cycle_count": 5,
        "emotion_mu": 0.5
    }
    res = p.gather_perceptions(sources=["IDLE_ENTROPY"], caller_id="kernel", idle_context=idle_context)
    perceptions = res["perceptions"]
    assert len(perceptions) == 1
    assert perceptions[0]["source"] == "IDLE_ENTROPY"
    assert perceptions[0]["payload"]["idle_cycle_count"] == 5

def test_perceptor_caller_id_validation(perceptor_env):
    p, _ = perceptor_env
    with pytest.raises(RuntimeError) as exc:
        p.gather_perceptions(sources=["CLOCK"], caller_id="malicious")
    assert "SECURITY_VIOLATION" in str(exc.value)

def test_perceptor_abstract(perceptor_env):
    p, _ = perceptor_env
    raw_data = {"text": "A FATAL exception occurred"}
    res = p.abstract_perception(raw_data=raw_data, caller_id="kernel")
    concepts = res["abstract_concepts"]
    assert len(concepts) == 1
    assert concepts[0]["urgency"] == 0.9
