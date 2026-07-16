"""llm_gateway module — interface: llm_gateway_v1

Unified LLM API adapter using litellm.
- Budget cutoff (daily_request_count >= 1000) checked before each call
- Retry via Exponential Backoff fallback (up to 3 attempts); litellm built-in retry disabled
- Token usage and request counts persisted to a JSON file (budget management across restarts)
"""
from __future__ import annotations

import json
import time
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import litellm

from src.core.config import config
from src.core.exceptions import BudgetExceededError, LLMGatewayError
from src.core.logger import setup_logger

logger = setup_logger("llm_gateway")

# -------------------------------------------------------------------------
# litellm global settings
# -------------------------------------------------------------------------
# Configure litellm auto-retry / log level
litellm.num_retries = 0  # Retry is managed manually
litellm.drop_params = True  # Safely ignore unsupported parameters

# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------
BUDGET_FILE = Path(os.getenv("BUDGET_FILE", ".budget_state.json"))


# -------------------------------------------------------------------------
# Budget state persistence helpers
# -------------------------------------------------------------------------
def _load_budget_state() -> dict:
    """Load today's budget state from a JSON file. Resets when the date changes."""
    today = str(date.today())
    if BUDGET_FILE.exists():
        try:
            with BUDGET_FILE.open("r") as f:
                state = json.load(f)
            if state.get("date") == today:
                return state
        except (json.JSONDecodeError, KeyError):
            pass
    return {"date": today, "daily_request_count": 0, "total_token_usage": 0, "last_request_timestamp": None}


def _save_budget_state(state: dict) -> None:
    """Write budget state to a JSON file."""
    state["last_request_timestamp"] = datetime.utcnow().isoformat()
    with BUDGET_FILE.open("w") as f:
        json.dump(state, f)


# -------------------------------------------------------------------------
# LLMGateway class
# -------------------------------------------------------------------------
class LLMGateway:
    """litellm-based LLM API gateway. Implements the llm_gateway_v1 interface."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._budget_limit: int = config.openrouter_budget_daily
        self._api_base: str = config.litellm_api_base
        self._embedding_api_base: str = config.litellm_embedding_api_base
        self._state: dict = _load_budget_state()

        logger.info(
            "LLMGateway initialized (litellm). daily_request_count=%d / %d, api_base=%s",
            self._state["daily_request_count"],
            self._budget_limit,
            self._api_base or "(default)",
        )

    # ------------------------------------------------------------------
    # Internal: budget check
    # ------------------------------------------------------------------
    def _check_budget(self) -> None:
        """Raise BudgetExceededError when the budget is exceeded."""
        self._state = _load_budget_state()  # Reload the latest state from disk
        if self._state["daily_request_count"] >= self._budget_limit:
            raise BudgetExceededError(
                f"BUDGET_EXCEEDED: daily_request_count={self._state['daily_request_count']} "
                f">= limit={self._budget_limit}"
            )

    def _increment_count(self, total_tokens: int = 0) -> None:
        """Increment the counter and persist to disk."""
        self._state["daily_request_count"] += 1
        self._state["total_token_usage"] += total_tokens
        _save_budget_state(self._state)

    # ------------------------------------------------------------------
    # Internal: caller_id validation (FINDING-02)
    # ------------------------------------------------------------------
    def _validate_caller_id(self, operation: str, caller_id: Optional[str]) -> None:
        """L2 security_requirements: internal use only — must be called only by the kernel module."""
        if caller_id != "kernel":
            logger.warning(
                "[SECURITY] %s called with unexpected caller_id=%r (expected 'kernel')",
                operation,
                caller_id,
            )

    # ------------------------------------------------------------------
    # Internal: litellm call helper (with retry)
    # ------------------------------------------------------------------
    def _completion_with_retry(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> dict:
        """Execute litellm.completion with Exponential Backoff retry."""
        delay = config.api_retry_base_delay_sec
        max_retries = config.api_max_retries
        last_exc: Exception = RuntimeError("Unknown error")

        # Configure api_base
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timeout": 120,
        }
        if self._api_base:
            kwargs["api_base"] = self._api_base

        for attempt in range(max_retries):
            try:
                response = litellm.completion(**kwargs)
                return response
            except litellm.RateLimitError as exc:
                logger.warning(
                    "Rate limit on attempt %d/%d. Waiting %.1fs. %s",
                    attempt + 1, max_retries, delay, exc,
                )
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                last_exc = exc
            except litellm.ServiceUnavailableError as exc:
                logger.warning(
                    "Service unavailable on attempt %d/%d. Waiting %.1fs. %s",
                    attempt + 1, max_retries, delay, exc,
                )
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                last_exc = exc
            except litellm.InternalServerError as exc:
                logger.warning(
                    "Server error on attempt %d/%d. Waiting %.1fs. %s",
                    attempt + 1, max_retries, delay, exc,
                )
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                last_exc = exc
            except litellm.APIConnectionError as exc:
                logger.warning(
                    "Connection error on attempt %d/%d. Waiting %.1fs. %s",
                    attempt + 1, max_retries, delay, exc,
                )
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                last_exc = exc
            except litellm.APIError as exc:
                logger.warning(
                    "API error on attempt %d/%d: %s",
                    attempt + 1, max_retries, exc,
                )
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                last_exc = exc
            except Exception as exc:
                logger.warning(
                    "Unexpected error on attempt %d/%d: %s",
                    attempt + 1, max_retries, exc,
                )
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                last_exc = exc

        raise LLMGatewayError(
            LLMGatewayError.API_ERROR,
            f"All {max_retries} retries failed. Last error: {last_exc}",
        ) from last_exc

    # ------------------------------------------------------------------
    # Public API (llm_gateway_v1)
    # ------------------------------------------------------------------
    def query_litellm(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        caller_id: Optional[str] = None,
    ) -> dict:
        """Text generation (query_litellm operation).

        Performs text generation using litellm.
        The model name from config.yaml is passed directly to litellm.
        Must follow litellm's model naming convention (e.g., "openrouter/openai/gpt-5-nano",
        "gemini/gemini-2.0-flash", "anthropic/claude-sonnet-4-20250514", etc.).

        Returns:
            { response: str, usage: { prompt_tokens, completion_tokens, total_tokens } }
        Raises:
            BudgetExceededError: When daily budget is exceeded
            LLMGatewayError: API_ERROR (when all retries are exhausted)
        """
        self._validate_caller_id("query_litellm", caller_id)
        self._check_budget()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        actual_model = model or config.api_model_slow

        logger.debug("query_litellm caller_id=%s model=%s", caller_id, actual_model)

        response = self._completion_with_retry(
            model=actual_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # Extract results from litellm ModelResponse
        choice = response.choices[0]
        finish_reason = choice.finish_reason
        content = choice.message.content or ""

        logger.debug(
            "LLM response stats: finish_reason=%s, content_len=%d, prompt_tokens=%d, completion_tokens=%d",
            finish_reason,
            len(content),
            response.usage.prompt_tokens if response.usage else 0,
            response.usage.completion_tokens if response.usage else 0,
        )
        if finish_reason == "length":
            logger.warning("LLM response was likely truncated due to token limit (finish_reason='length')")

        usage = response.usage
        total_tokens = usage.total_tokens if usage else 0
        self._increment_count(total_tokens)

        return {
            "response": content,
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": total_tokens,
            },
        }

    def generate_embedding(
        self,
        text: str,
        model: Optional[str] = None,
        caller_id: Optional[str] = None,
    ) -> dict:
        """Embedding vector generation (generate_embedding operation).

        Generates embedding vectors using litellm.embedding().

        Returns:
            { embedding: list[float], usage: { total_tokens } }
        Raises:
            BudgetExceededError, LLMGatewayError
        """
        self._validate_caller_id("generate_embedding", caller_id)
        self._check_budget()

        embed_model = model or config.api_model_embed

        logger.debug("generate_embedding caller_id=%s model=%s", caller_id, embed_model)

        delay = config.api_retry_base_delay_sec
        max_retries = config.api_max_retries
        last_exc: Exception = RuntimeError("Unknown error")

        kwargs: dict[str, Any] = {
            "model": embed_model,
            "input": [text],
            "timeout": 120,
        }
        if self._embedding_api_base:
            kwargs["api_base"] = self._embedding_api_base

        for attempt in range(max_retries):
            try:
                response = litellm.embedding(**kwargs)

                usage = response.usage if response.usage else None
                total_tokens = usage.total_tokens if usage else 0
                self._increment_count(total_tokens)

                return {
                    "embedding": response.data[0]["embedding"],
                    "usage": {"total_tokens": total_tokens},
                }
            except (litellm.RateLimitError, litellm.ServiceUnavailableError,
                    litellm.InternalServerError, litellm.APIConnectionError) as exc:
                logger.warning(
                    "Embedding error on attempt %d/%d: %s. Waiting %.1fs.",
                    attempt + 1, max_retries, exc, delay,
                )
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                last_exc = exc
            except Exception as exc:
                logger.warning(
                    "Unexpected embedding error on attempt %d/%d: %s",
                    attempt + 1, max_retries, exc,
                )
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                last_exc = exc

        raise LLMGatewayError(
            LLMGatewayError.API_ERROR,
            f"All {max_retries} retries failed for generate_embedding. Last error: {last_exc}",
        ) from last_exc

    def get_usage_status(self, caller_id: Optional[str] = None) -> dict:
        """Return the current usage status (get_usage_status operation).

        Returns:
            { daily_request_count, limit, remaining_requests }
        """
        self._validate_caller_id("get_usage_status", caller_id)
        self._state = _load_budget_state()
        count = self._state["daily_request_count"]
        return {
            "daily_request_count": count,
            "limit": self._budget_limit,
            "remaining_requests": max(0, self._budget_limit - count),
        }
