"""llm_gateway module

Adapter for the OpenRouter API.
- Determines budget cutoff (daily_request_count >= 1000) before invocation.
- Retries with Exponential Backoff on 429/5xx errors (up to 3 times: 1s, 2s, 4s).
- Persists token usage and request count to a JSON file (budget management across restarts).
"""
from __future__ import annotations

import json
import time
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import requests

from src.core.config import config
from src.core.exceptions import BudgetExceededError, LLMGatewayError
from src.core.logger import setup_logger

logger = setup_logger("llm_gateway")

# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------
BUDGET_FILE = Path(os.getenv("BUDGET_FILE", ".budget_state.json"))
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# -------------------------------------------------------------------------
# Budget State Persistence Helpers
# -------------------------------------------------------------------------
def _load_budget_state() -> dict:
    """Loads today's budget state from the JSON file. Resets if the date has changed."""
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
    """Writes the budget state to the JSON file."""
    state["last_request_timestamp"] = datetime.utcnow().isoformat()
    with BUDGET_FILE.open("w") as f:
        json.dump(state, f)


# -------------------------------------------------------------------------
# LLMGateway Class
# -------------------------------------------------------------------------
class LLMGateway:
    """OpenRouter API Gateway. Implements the llm_gateway_v1 interface."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key: str = api_key or config.openrouter_api_key
        self._budget_limit: int = config.openrouter_budget_daily
        self._state: dict = _load_budget_state()
        logger.info(
            "LLMGateway initialized. daily_request_count=%d / %d",
            self._state["daily_request_count"],
            self._budget_limit,
        )

    # ------------------------------------------------------------------
    # Internal: Budget checks
    # ------------------------------------------------------------------
    def _check_budget(self) -> None:
        """Raises BudgetExceededError when the budget is exceeded."""
        self._state = _load_budget_state()  # Load latest from disk
        if self._state["daily_request_count"] >= self._budget_limit:
            raise BudgetExceededError(
                f"BUDGET_EXCEEDED: daily_request_count={self._state['daily_request_count']} "
                f">= limit={self._budget_limit}"
            )

    def _increment_count(self, total_tokens: int = 0) -> None:
        """Increments the counter and saves to disk."""
        self._state["daily_request_count"] += 1
        self._state["total_token_usage"] += total_tokens
        _save_budget_state(self._state)

    # ------------------------------------------------------------------
    # Internal: HTTP Helpers
    # ------------------------------------------------------------------
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/narv",
            "X-Title": "narv",
        }

    def _post_with_retry(self, endpoint: str, body: dict) -> dict:
        """POST request with Exponential Backoff. Up to api_max_retries times."""
        url = f"{OPENROUTER_BASE_URL}{endpoint}"
        delay = config.api_retry_base_delay_sec
        max_retries = config.api_max_retries
        last_exc: Exception = RuntimeError("Unknown error")
        
        # FINDING-LLMGW-002: Set timeout individually and control wait between retries
        # Extended requests timeout to 120s to avoid blocking the entire call for too long
        timeout = 120 

        for attempt in range(max_retries):
            try:
                resp = requests.post(url, headers=self._headers(), json=body, timeout=timeout)
                if resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        "Retryable HTTP %d on attempt %d/%d. Waiting %.1fs.",
                        resp.status_code, attempt + 1, max_retries, delay,
                    )
                    time.sleep(delay)
                    delay *= 2
                    last_exc = requests.HTTPError(f"HTTP {resp.status_code}")
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                logger.warning("Request error on attempt %d/%d: %s", attempt + 1, max_retries, exc)
                # Retry on network errors as well
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                last_exc = exc
        
        raise LLMGatewayError(
            LLMGatewayError.API_ERROR,
            f"All {max_retries} retries failed. Last error: {last_exc}",
        ) from last_exc

    # ------------------------------------------------------------------
    # Internal: caller_id validation (FINDING-02)
    # ------------------------------------------------------------------
    def _validate_caller_id(self, operation: str, caller_id: Optional[str]) -> None:
        """L2 security_requirements: Internal use only — called only by the kernel module"""
        if caller_id != "kernel":
            logger.warning(
                "[SECURITY] %s called with unexpected caller_id=%r (expected 'kernel')",
                operation,
                caller_id,
            )

    # ------------------------------------------------------------------
    # Public API (llm_gateway_v1)
    # ------------------------------------------------------------------
    def query_googleai(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        caller_id: Optional[str] = None,
    ) -> dict:
        """Text generation (query_googleai operation).

        Returns:
            { response: str, usage: { prompt_tokens, completion_tokens, total_tokens } }
        Raises:
            BudgetExceededError: When daily budget is exceeded
            RuntimeError: API_ERROR (When all retries fail)
        """
        self._validate_caller_id("query_googleai", caller_id)
        self._check_budget()
        
        api_key = config.google_api_key
        if not api_key:
            raise LLMGatewayError(LLMGatewayError.API_ERROR, "GOOGLE_API_KEY is not set.")
            
        actual_model = model or config.api_model_slow
        if actual_model.startswith("openai/") or actual_model.startswith("anthropic/"):
            actual_model = "gemini-1.5-pro" if "4" in actual_model else "gemini-1.5-flash"
        elif actual_model.startswith("google/"):
            actual_model = actual_model[len("google/"):]
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{actual_model}:generateContent?key={api_key}"
        
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        body = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            }
        }
        if system_prompt:
            body["systemInstruction"] = {
                "role": "model",
                "parts": [{"text": system_prompt}]
            }

        logger.debug("query_googleai caller_id=%s model=%s maxOutputTokens=%s body_chars=%d", caller_id, actual_model, max_tokens, len(json.dumps(body)))
        
        delay = config.api_retry_base_delay_sec
        max_retries = config.api_max_retries
        timeout = 120
        last_exc: Exception = RuntimeError("Unknown error")
        
        for attempt in range(max_retries):
            try:
                resp = requests.post(url, headers={"Content-Type": "application/json"}, json=body, timeout=timeout)
                if resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        "Retryable HTTP %d on attempt %d/%d (Google). Waiting %.1fs.",
                        resp.status_code, attempt + 1, max_retries, delay,
                    )
                    time.sleep(delay)
                    delay *= 2
                    last_exc = requests.HTTPError(f"HTTP {resp.status_code}")
                    continue
                
                resp.raise_for_status()
                data = resp.json()
                
                content_text = ""
                candidates = data.get("candidates", [])
                if candidates:
                    finish_reason = candidates[0].get("finishReason")
                    if finish_reason and finish_reason != "STOP":
                        logger.warning("Google AI response finishReason: %s", finish_reason)
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        content_text = parts[0].get("text", "")
                        
                usage = data.get("usageMetadata", {})
                total_tokens = usage.get("totalTokenCount", 0)
                self._increment_count(total_tokens)
                
                return {
                    "response": content_text,
                    "usage": {
                        "prompt_tokens": usage.get("promptTokenCount", 0),
                        "completion_tokens": usage.get("candidatesTokenCount", 0),
                        "total_tokens": total_tokens,
                    },
                }

            except requests.RequestException as exc:
                logger.warning("Request error on attempt %d/%d (Google): %s", attempt + 1, max_retries, exc)
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                last_exc = exc
                
        raise LLMGatewayError(
            LLMGatewayError.API_ERROR,
            f"All {max_retries} retries failed for query_googleai. Last error: {last_exc}",
        ) from last_exc

    def query_openrouter(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        caller_id: Optional[str] = None,
    ) -> dict:
        """Text generation (query_openrouter operation).

        Returns:
            { response: str, usage: { prompt_tokens, completion_tokens, total_tokens } }
        Raises:
            BudgetExceededError: When daily budget is exceeded
            RuntimeError: API_ERROR (When all retries fail)
        """
        self._validate_caller_id("query_openrouter", caller_id)
        self._check_budget()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        actual_model = model or config.api_model_slow

        body = {
            "model": actual_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        logger.debug("query_openrouter caller_id=%s model=%s", caller_id, actual_model)
        data = self._post_with_retry("/chat/completions", body)

        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        
        # Debug log for investigating partial disconnects
        if "choices" not in data or not data["choices"]:
            raise LLMGatewayError(
                LLMGatewayError.API_ERROR,
                f"OpenRouter response missing 'choices': {data.get('error', data)}"
            )

        choice = data["choices"][0]
        finish_reason = choice.get("finish_reason")
        # Ensure it's a string in case models like gpt-5-nano return content: null
        raw_content = choice.get("message", {}).get("content")
        content = raw_content if raw_content is not None else ""
        
        logger.debug(
            "OpenRouter response stats: finish_reason=%s, content_len=%d, prompt_tokens=%d, completion_tokens=%d",
            finish_reason, len(content), usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        )
        if finish_reason == "length":
            logger.warning("LLM response was likely truncated due to token limit (finish_reason='length')")

        self._increment_count(total_tokens)

        return {
            "response": content,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
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

        Returns:
            { embedding: list[float], usage: { total_tokens } }
        Raises:
            BudgetExceededError, RuntimeError
        """
        self._validate_caller_id("generate_embedding", caller_id)
        self._check_budget()
        embed_model = model or config.api_model_embed
        body = {"model": embed_model, "input": text}
        logger.debug("generate_embedding caller_id=%s model=%s", caller_id, embed_model)
        data = self._post_with_retry("/embeddings", body)

        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        self._increment_count(total_tokens)

        return {
            "embedding": data["data"][0]["embedding"],
            "usage": {"total_tokens": total_tokens},
        }

    def get_usage_status(self, caller_id: Optional[str] = None) -> dict:
        """Returns the current usage status (get_usage_status operation).

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
