from src.llm_gateway.llm_gateway import LLMGateway, LLMGatewayError
from src.core.exceptions import BudgetExceededError
import pytest
import os
from unittest.mock import patch, MagicMock

@pytest.fixture
def llm_gateway():
    os.environ["OPENROUTER_API_KEY"] = "dummy_key"
    return LLMGateway(api_key="dummy_key")

@patch('src.llm_gateway.llm_gateway._load_budget_state')
def test_llm_gateway_get_usage_status(mock_load, llm_gateway):
    # _check_budget loads from disk, so we mock it
    mock_load.return_value = {"daily_request_count": 1000}
    with pytest.raises(BudgetExceededError):
        # We simulate exhausted budget
        llm_gateway._budget_limit = 1000
        # This will raise BudgetExceededError when called
        llm_gateway._check_budget()

@patch('requests.post')
def test_llm_gateway_query_success(mock_post, llm_gateway):
    mock_post.return_value = MagicMock(status_code=200)
    mock_post.return_value.json.return_value = {
        "choices": [{"message": {"content": "Test response"}}],
        "usage": {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}
    }
    
    res = llm_gateway.query_openrouter(
        prompt="Hello",
        system_prompt="System",
        caller_id="kernel",
        model="test-model"
    )
    
    assert res["response"] == "Test response"
    assert res["usage"]["total_tokens"] == 10
