"""
AI Client
=========
Thin wrapper around OpenAI (and Groq fallback) for the AI Workflow Builder.
Handles structured JSON responses, retries, and token counting.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Pricing (USD per 1K tokens) — approximate 2026 figures
_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o":        {"input": 0.0025, "output": 0.010},
    "gpt-4o-mini":   {"input": 0.00015,"output": 0.0006},
    "gpt-4-turbo":   {"input": 0.010,  "output": 0.030},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    "gpt-5":         {"input": 0.005,  "output": 0.020},
}


class AIClient:
    """
    Unified AI client with fallback from OpenAI → Groq.
    Returns structured JSON or plain text.
    """

    def __init__(
        self,
        model:       str  = "gpt-4o-mini",
        temperature: float = 0.2,
        max_tokens:  int   = 4096,
    ) -> None:
        self.model       = os.environ.get("OPENAI_MODEL", model)
        self.temperature = temperature
        self.max_tokens  = max_tokens
        self._client     = None
        self._total_input_tokens  = 0
        self._total_output_tokens = 0
        self._total_cost_usd      = 0.0

    def _get_client(self):
        if self._client is None:
            import openai
            provider = os.environ.get("AI_PROVIDER", "openai").lower()
            if provider == "groq":
                self._client = openai.OpenAI(
                    base_url="https://api.groq.com/openai/v1",
                    api_key=os.environ.get("GROQ_API_KEY")
                )
            else:
                self._client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        return self._client

    def chat(
        self,
        messages:     List[Dict[str, str]],
        response_format: str = "text",   # "text" | "json"
        temperature:  Optional[float] = None,
        max_tokens:   Optional[int]   = None,
    ) -> str:
        """Send a chat completion request. Returns response content string."""
        client = self._get_client()
        kw: Dict[str, Any] = {
            "model":       self.model,
            "messages":    messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens":  max_tokens  if max_tokens  is not None else self.max_tokens,
        }
        if response_format == "json":
            kw["response_format"] = {"type": "json_object"}

        for attempt in range(3):
            try:
                resp  = client.chat.completions.create(**kw)
                usage = resp.usage
                if usage:
                    self._total_input_tokens  += usage.prompt_tokens
                    self._total_output_tokens += usage.completion_tokens
                    self._track_cost(usage.prompt_tokens, usage.completion_tokens)
                return resp.choices[0].message.content or ""
            except Exception as exc:
                logger.warning("AI attempt %d/%d failed: %s", attempt + 1, 3, exc)
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    raise

    def chat_json(self, messages: List[Dict[str, str]], **kw: Any) -> Dict[str, Any]:
        """Send a chat request expecting a JSON object response."""
        text = self.chat(messages, response_format="json", **kw)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code fence
            import re
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                return json.loads(m.group(1))
            raise ValueError(f"AI did not return valid JSON: {text[:300]}")

    def _track_cost(self, input_tokens: int, output_tokens: int) -> None:
        pricing = _PRICING.get(self.model, {"input": 0.001, "output": 0.002})
        cost    = (input_tokens / 1000 * pricing["input"]) + (output_tokens / 1000 * pricing["output"])
        self._total_cost_usd += cost

    @property
    def session_cost_usd(self) -> float:
        return self._total_cost_usd

    @property
    def session_tokens(self) -> Dict[str, int]:
        return {"input": self._total_input_tokens, "output": self._total_output_tokens}

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~4 chars per token."""
        return max(1, len(text) // 4)

    def cost_for_tokens(self, input_tokens: int, output_tokens: int) -> float:
        pricing = _PRICING.get(self.model, {"input": 0.001, "output": 0.002})
        return (input_tokens / 1000 * pricing["input"]) + (output_tokens / 1000 * pricing["output"])
