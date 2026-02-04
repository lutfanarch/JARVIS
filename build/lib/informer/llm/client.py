"""LLM client abstraction for Informer.

This module defines a simple protocol for language model clients and
provides helper functions to parse JSON responses.  A deterministic
``FakeLLMClient`` is implemented for offline testing and does not
perform any network calls.  It interprets structured summaries from
the pipeline to produce screener, analyst, critic and arbiter
decisions.
"""

from __future__ import annotations

import json
import re
import os
from typing import Any, Dict, List, Type, TypeVar, Protocol, Optional

from pydantic import BaseModel

from .models import (
    DECISION_SCHEMA_VERSION_DEFAULT,
    ScreenerCandidate,
    ScreenerOutput,
    AnalystPlan,
    CriticReview,
    CandidateEvaluation,
    ArbiterDecision,
)


class LLMClient(Protocol):
    """Protocol for language model clients.

    Concrete implementations must provide a ``complete`` method
    accepting a purpose identifier, a system prompt and a user prompt.
    The method should return a raw textual response.
    """

    def complete(self, *, purpose: str, system: str, user: str) -> str:
        raise NotImplementedError

    def complete_json(self, *, purpose: str, payload: Dict[str, Any]) -> Dict[str, Any]:  # pragma: no cover
        """Return a JSON response for the given purpose and payload.

        This optional helper method invokes :meth:`complete` using a
        serialised JSON string for the ``user`` argument and parses the
        returned raw text into a Python dictionary.  Subclasses may
        override this for efficiency; by default it calls ``complete``
        and attempts to load the result as JSON.

        Args:
            purpose: Purpose of the LLM call (e.g., screener, analyst).
            payload: Structured payload to be serialised as JSON.

        Returns:
            A dictionary parsed from the LLM's JSON response.

        Raises:
            ValueError: If the response cannot be parsed as JSON.
        """
        import json  # local import to avoid polluting module namespace

        text = self.complete(purpose=purpose, system="", user=json.dumps(payload))
        try:
            return json.loads(text)
        except Exception as exc:
            raise ValueError(f"Failed to parse JSON response: {exc}")


T = TypeVar("T", bound=BaseModel)


def parse_json_response(text: str, model: Type[T]) -> T:
    """Extract a JSON object from a raw LLM response and parse into a model.

    The response may contain fenced code blocks (````json ... ```), or
    mixed prose and JSON.  This helper attempts to locate the first
    top‑level JSON object by balancing braces and then validates it
    against the provided Pydantic model.  Raises ``ValueError`` on
    failure.
    """
    if not isinstance(text, str):
        raise ValueError("LLM response must be a string")
    # Strip fenced code markers if present
    fenced_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = None
    if fenced_match:
        candidate = fenced_match.group(1)
    else:
        # Find the first '{' and attempt to extract a balanced JSON object
        start = text.find("{")
        if start == -1:
            raise ValueError("No JSON object found in response")
        stack = 0
        end = None
        for idx in range(start, len(text)):
            ch = text[idx]
            if ch == "{":
                stack += 1
            elif ch == "}":
                stack -= 1
                if stack == 0:
                    end = idx + 1
                    break
        if end is None:
            raise ValueError("Unbalanced JSON in response")
        candidate = text[start:end]
    try:
        data = json.loads(candidate)
    except Exception as exc:
        raise ValueError(f"Failed to parse JSON: {exc}")
    # Validate against the target model
    return model.model_validate(data)  # type: ignore[arg-type]


class FakeLLMClient:
    """Deterministic fake LLM for offline testing.

    The fake client responds to screener, analyst, critic and arbiter
    prompts by applying simple heuristics to the structured data
    provided in the user message.  It never accesses network or file
    resources and ensures repeatable outputs.
    """

    def complete(self, *, purpose: str, system: str, user: str) -> str:
        # Parse the user content as JSON.  All pipeline calls should
        # encode structured data in the user field.
        try:
            payload = json.loads(user)
        except Exception:
            payload = {}
        if purpose == "screener":
            return self._screener(payload)
        if purpose == "analyst":
            return self._analyst(payload)
        if purpose == "critic":
            return self._critic(payload)
        if purpose == "arbiter":
            return self._arbiter(payload)
        raise ValueError(f"Unknown purpose: {purpose}")

    # Provide a default complete_json implementation to satisfy the
    # LLMClient protocol.  It simply forwards to ``complete`` with a
    # JSON‑encoded payload and parses the result using Python's
    # built‑in JSON loader.  This helper is useful for tests that
    # verify provider routing and does not change the behaviour of
    # existing callers, which continue to use the ``complete`` method.
    def complete_json(self, *, purpose: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        import json
        text = self.complete(purpose=purpose, system="", user=json.dumps(payload))
        return json.loads(text)

    def _screener(self, payload: Dict[str, Any]) -> str:
        packets: List[Dict[str, Any]] = payload.get("packets", [])
        max_candidates: int = payload.get("max_candidates", 2)
        # Filter candidates: prefer uptrend and non‑high volatility and QA passed
        candidates: List[str] = []
        for item in packets:
            sym = item.get("symbol")
            trend = item.get("trend_regime")
            vol = item.get("vol_regime")
            qa_passed = item.get("qa_passed")
            if not sym or not qa_passed:
                continue
            if trend == "uptrend" and vol != "high":
                candidates.append(sym)
        # Limit to max candidates
        candidates = candidates[:max_candidates]
        if not candidates:
            result = ScreenerOutput(
                schema_version=DECISION_SCHEMA_VERSION_DEFAULT,
                action="NO_TRADE",
                candidates=[],
                notes="NO_CANDIDATES",
            )
        else:
            cand_objs = [ScreenerCandidate(symbol=s) for s in candidates]
            result = ScreenerOutput(
                schema_version=DECISION_SCHEMA_VERSION_DEFAULT,
                action="CANDIDATES",
                candidates=cand_objs,
                notes=None,
            )
        return result.model_dump_json()

    def _analyst(self, payload: Dict[str, Any]) -> str:
        symbol: str = payload.get("symbol")
        latest_close: float | None = payload.get("latest_close")
        atr14: float | None = payload.get("atr14")
        # If missing key data, reject
        if latest_close is None or atr14 is None:
            plan = AnalystPlan(
                action="REJECT",
                entry=None,
                stop=None,
                targets=[],
                confidence=None,
                reason_codes=["MISSING_FEATURES"],
                notes=None,
            )
            return plan.model_dump_json()
        entry = latest_close
        stop = latest_close - atr14
        targets = [latest_close + 2 * atr14]
        plan = AnalystPlan(
            action="PROPOSE_TRADE",
            entry=entry,
            stop=stop,
            targets=targets,
            confidence=0.55,
            reason_codes=["FAKE_DEMO"],
            notes=None,
        )
        return plan.model_dump_json()

    def _critic(self, payload: Dict[str, Any]) -> str:
        vol: str | None = payload.get("vol_regime")
        qa_passed: bool | None = payload.get("qa_passed")
        issues: List[str] = []
        verdict = "APPROVE"
        if vol == "high":
            verdict = "REJECT"
            issues.append("VOL_HIGH")
        if not qa_passed:
            verdict = "REJECT"
            issues.append("QA_FAIL")
        review = CriticReview(
            verdict=verdict,
            issues=issues,
            reason_codes=[],
            notes=None,
        )
        return review.model_dump_json()

    def _arbiter(self, payload: Dict[str, Any]) -> str:
        # Payload: {"evaluations": [{"symbol":..., "analyst": {...}, "critic": {...}}, ...]}
        evaluations = payload.get("evaluations", [])
        decision_action = "NO_TRADE"
        chosen_symbol: str | None = None
        entry: float | None = None
        stop: float | None = None
        targets: List[float] = []
        confidence: float | None = None
        for ev in evaluations:
            analyst = ev.get("analyst", {})
            critic = ev.get("critic", {})
            if (
                analyst.get("action") == "PROPOSE_TRADE"
                and critic.get("verdict") == "APPROVE"
            ):
                decision_action = "TRADE"
                chosen_symbol = ev.get("symbol")
                entry = analyst.get("entry")
                stop = analyst.get("stop")
                targets = analyst.get("targets", [])
                confidence = analyst.get("confidence")
                break
        if decision_action == "NO_TRADE":
            arb = ArbiterDecision(
                action="NO_TRADE",
                symbol=None,
                entry=None,
                stop=None,
                targets=[],
                confidence=None,
                reason_codes=["NO_APPROVED_CANDIDATE"],
                notes=None,
            )
        else:
            arb = ArbiterDecision(
                action="TRADE",
                symbol=chosen_symbol,
                entry=entry,
                stop=stop,
                targets=targets,
                confidence=confidence,
                reason_codes=["FAKE_DEMO"],
                notes=None,
            )
        return arb.model_dump_json()


class OpenAIClient:
    """Client for OpenAI's GPT models.

    This client reads the API key from the ``OPENAI_API_KEY`` environment
    variable by default.  The ``complete`` method is not implemented
    here to avoid accidental live calls during tests or CI.  In
    production, this class should be extended to perform HTTP requests
    against the OpenAI API.
    """

    def __init__(self, *, api_key: Optional[str] = None, model: str = "gpt-3.5-turbo") -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY environment variable must be set for OpenAIClient")

    def complete(self, *, purpose: str, system: str, user: str) -> str:
        """Perform a chat completion request against the OpenAI API.

        Network calls are only permitted when LLM_MODE=live.  If invoked in
        any other mode or if the API call fails, a RuntimeError is raised.

        Args:
            purpose: Ignored by this client; routing is handled by the caller.
            system: System prompt prefix.
            user: User prompt or JSON payload.

        Returns:
            The raw content of the assistant's message.

        Raises:
            RuntimeError: On non-live mode or API errors.
        """
        # Guard against accidental invocation outside live mode
        if os.getenv("LLM_MODE", "fake").lower() != "live":
            raise RuntimeError("OpenAIClient called when LLM_MODE is not 'live'")
        import requests
        # Prepare Chat Completions request payload
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 512,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            # Extract the first choice's content
            choices = data.get("choices")
            if not choices:
                raise RuntimeError("OpenAI API returned no choices")
            content = choices[0].get("message", {}).get("content")
            if content is None:
                raise RuntimeError("OpenAI API response missing content")
            return content
        except Exception as exc:
            raise RuntimeError(f"OpenAI API call failed: {exc}")

    # Basic complete_json that defers to complete.  Users may override.
    def complete_json(self, *, purpose: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        import json
        text = self.complete(purpose=purpose, system="", user=json.dumps(payload))
        return json.loads(text)


class GeminiClient:
    """Client for Google's Gemini models (formerly Bard).

    Reads the API key from ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``).
    Like ``OpenAIClient``, this stub does not implement network
    communication in the test environment.  Production implementations
    should override the ``complete`` method to call the Gemini API.
    """

    def __init__(self, *, api_key: Optional[str] = None, model: str = "gemini-pro") -> None:
        # Support both GEMINI_API_KEY and GOOGLE_API_KEY for flexibility
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.model = model
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY must be set for GeminiClient")

    def complete(self, *, purpose: str, system: str, user: str) -> str:
        """Perform a content generation request against Google Gemini API.

        Network calls are only permitted when LLM_MODE=live.  If invoked in
        any other mode or if the API call fails, a RuntimeError is raised.

        Args:
            purpose: Ignored by this client; routing is handled by the caller.
            system: System prompt prefix.
            user: User prompt or JSON payload.

        Returns:
            The raw text content from the API response.

        Raises:
            RuntimeError: On non-live mode or API errors.
        """
        if os.getenv("LLM_MODE", "fake").lower() != "live":
            raise RuntimeError("GeminiClient called when LLM_MODE is not 'live'")
        import requests
        # Build request to the Gemini generative model API
        # Use the official generative language API endpoint
        url = f"https://generativelanguage.googleapis.com/v1beta1/models/{self.model}:generateContent"
        # Compose system and user prompts into one content; Gemini API may not support system role
        prompt = system + "\n" + user if system else user
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
        }
        params = {"key": self.api_key}
        try:
            resp = requests.post(
                url,
                params=params,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            # Extract generated text; the exact structure may vary
            candidates = data.get("candidates")
            if not candidates:
                # Some responses may use a different structure; attempt fallback
                # Accept a fallback of top-level 'text' key
                content = data.get("text")
                if content is None:
                    raise RuntimeError("Gemini API returned no candidates")
                return content
            # Candidate may have content under 'content' or 'text'
            content = candidates[0].get("content") or candidates[0].get("output")
            if isinstance(content, dict):
                # Compose parts
                parts = content.get("parts") or []
                text_parts = [p.get("text", "") for p in parts]
                content = "".join(text_parts)
            if content is None:
                raise RuntimeError("Gemini API response missing content")
            return content
        except Exception as exc:
            raise RuntimeError(f"Gemini API call failed: {exc}")

    def complete_json(self, *, purpose: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        import json
        text = self.complete(purpose=purpose, system="", user=json.dumps(payload))
        return json.loads(text)


class RoleRouterLLMClient:
    """Composite LLM client that routes calls to provider‑specific clients.

    The routing is determined by the ``ROLE_ROUTING`` mapping defined in
    ``informer.llm.policy``.  Each call to ``complete`` selects the
    appropriate provider based on the ``purpose`` and delegates to the
    underlying client.  If the selected provider is not available in
    ``clients``, a ``ValueError`` is raised.  Critic calls will
    optionally fall back to the OpenAI provider when
    ``fallback_critic`` is set to ``True``.
    """

    def __init__(self, *, clients: Dict[str, LLMClient], fallback_critic: bool = False) -> None:
        from .policy import ALLOWED_PROVIDERS, ROLE_ROUTING
        # Validate that all provided client keys are allowed
        invalid = set(clients.keys()) - ALLOWED_PROVIDERS
        if invalid:
            raise ValueError(
                f"Unknown providers configured: {', '.join(sorted(invalid))}. "
                f"Allowed providers are: {', '.join(sorted(ALLOWED_PROVIDERS))}"
            )
        self.clients: Dict[str, LLMClient] = clients
        self.fallback_critic = fallback_critic
        # Store routing mapping
        self._routing = ROLE_ROUTING

    def complete(self, *, purpose: str, system: str, user: str) -> str:
        # Determine which provider handles this purpose
        provider = self._routing.get(purpose)
        if provider is None:
            raise ValueError(f"Unknown LLM purpose: {purpose}")
        client = self.clients.get(provider)
        if not client:
            raise ValueError(f"Provider '{provider}' is not configured for role '{purpose}'")
        try:
            return client.complete(purpose=purpose, system=system, user=user)
        except Exception as exc:
            # If critic fails and fallback is enabled, fall back to openai
            if purpose == "critic" and self.fallback_critic:
                fallback_provider = "openai"
                fallback_client = self.clients.get(fallback_provider)
                if fallback_client:
                    return fallback_client.complete(purpose=purpose, system=system, user=user)
            # Re-raise the original exception to be handled by the caller
            raise

    def complete_json(self, *, purpose: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        import json
        # The system prompt is irrelevant for raw JSON; use empty string
        text = self.complete(purpose=purpose, system="", user=json.dumps(payload))
        return json.loads(text)
