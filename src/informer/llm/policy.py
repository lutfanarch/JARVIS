"""Provider policy and routing definitions for the LLM layer.

This module defines the allowed providers for JARVIS Phase 2 and the
deterministic routing of each LLM role (screener, analyst, critic,
arbiter) to a specific provider.  Only providers listed in
``ALLOWED_PROVIDERS`` may be used.  Adding or removing providers
requires changing both the policy and role routing tables.

The routing scheme is:

- Stage A screener calls use OpenAI (GPT)
- Stage B analyst calls use OpenAI (GPT)
- Stage B critic calls use Google (Gemini)
- Stage B arbiter calls use OpenAI (GPT)

If a provider other than "openai" or "google" is configured or
referenced for a role, the system will raise a ``ValueError`` at
initialisation time to avoid inadvertent calls to unsupported
services.
"""

from __future__ import annotations

from typing import Dict, Set

__all__ = ["ALLOWED_PROVIDERS", "ROLE_ROUTING"]

# Allowed providers for Phase 2.  OpenAI refers to the GPT series
# (e.g., gpt-3.5, gpt-4) and Google refers to Gemini models.  Other
# providers are explicitly disallowed.
ALLOWED_PROVIDERS: Set[str] = {"openai", "google"}

# Deterministic routing table mapping each LLM purpose to a provider.
# Do not modify without updating the policy documentation.  Keys
# correspond to the ``purpose`` argument supplied to LLMClient.complete
# calls in the decision pipeline.  Values must be present in
# ``ALLOWED_PROVIDERS``.
ROLE_ROUTING: Dict[str, str] = {
    # Stage A
    "screener": "openai",
    # Stage B analyst
    "analyst": "openai",
    # Stage B critic
    "critic": "google",
    # Stage B arbiter
    "arbiter": "openai",
}

def validate_providers(configured: Set[str]) -> None:
    """Validate that the configured providers are a subset of allowed providers.

    Args:
        configured: The set of provider names that are configured for use.

    Raises:
        ValueError: If any configured provider is not allowed.
    """
    invalid = configured - ALLOWED_PROVIDERS
    if invalid:
        raise ValueError(
            f"Unknown providers configured: {', '.join(sorted(invalid))}. "
            f"Allowed providers are: {', '.join(sorted(ALLOWED_PROVIDERS))}"
        )